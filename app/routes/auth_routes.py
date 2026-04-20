"""
Rutas de Administración y Control de Accesos
"""
from fastapi import APIRouter, HTTPException, Depends, Form, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timezone
import logging
from app.firebase_config import auth_client, db
from app.models.roles import Role, can_assign_role, normalize_role
from app.deps.authz import get_current_user, require_min_role, CurrentUser
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(tags=["Administración y Control de Accesos"])
security = HTTPBearer()

# Configurar rate limiter
limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class UserLoginRequest(BaseModel):
    id_token: str


class UserRegistrationRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    cellphone: str
    grupo: str
    role: Optional[str] = None  # ignorado; siempre se fuerza operador


class AssignRolesRequest(BaseModel):
    roles: List[str]


class GrantTemporaryPermissionRequest(BaseModel):
    permission: str
    expires_at: str


class ChangeRoleRequest(BaseModel):
    role: str


class ChangeGrupoRequest(BaseModel):
    grupo: str


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_user_firestore_data(uid: str) -> dict:
    doc = db.collection("users").document(uid).get()
    if doc.exists:
        return doc.to_dict() or {}
    return {}


def _sync_claims_if_needed(uid: str, decoded_token: dict, firestore_data: dict) -> bool:
    token_role = decoded_token.get("role")
    token_grupo = decoded_token.get("grupo")
    db_role = firestore_data.get("role") or normalize_role(firestore_data.get("rol", ""))
    db_grupo = firestore_data.get("grupo")
    if token_role != db_role or token_grupo != db_grupo:
        try:
            auth_client.set_custom_user_claims(
                uid, {"role": db_role or Role.OPERADOR, "grupo": db_grupo}
            )
            return True
        except Exception as e:
            logger.warning(f"No se pudieron sincronizar claims para {uid}: {e}")
    return False


# ---------------------------------------------------------------------------
# Endpoints de sesion / autenticacion
# ---------------------------------------------------------------------------

@router.post("/auth/validate-session")
async def validate_session(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Valida el ID token de Firebase y devuelve datos del usuario con role y grupo.
    """
    try:
        token = credentials.credentials
        decoded_token = auth_client.verify_id_token(token, check_revoked=True)
        uid = decoded_token["uid"]
        user = auth_client.get_user(uid)
        fs_data = _get_user_firestore_data(uid)
        role = fs_data.get("role") or normalize_role(fs_data.get("rol", "")) or Role.OPERADOR
        grupo = fs_data.get("grupo")
        claims_refreshed = _sync_claims_if_needed(uid, decoded_token, fs_data)
        return {
            "valid": True,
            "user": {
                "uid": user.uid,
                "email": user.email,
                "full_name": user.display_name or fs_data.get("full_name", ""),
                "email_verified": user.email_verified,
                "disabled": user.disabled,
                "role": role,
                "grupo": grupo,
            },
            "claims_refreshed": claims_refreshed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token invalido o revocado")


@router.post("/auth/login")
@limiter.limit("5/minute")
async def login_user(credentials: UserLoginRequest, request: Request):
    """
    Valida el ID token del frontend, devuelve datos del usuario con role y grupo.
    """
    try:
        decoded_token = auth_client.verify_id_token(credentials.id_token, check_revoked=True)
        uid = decoded_token["uid"]
        user = auth_client.get_user(uid)
        fs_data = _get_user_firestore_data(uid)
        if not fs_data:
            logger.warning(f"Usuario {uid} no encontrado en Firestore. Creando doc con role=operador.")
            db.collection("users").document(uid).set({
                "uid": uid,
                "email": user.email or "",
                "full_name": user.display_name or "",
                "role": Role.OPERADOR,
                "grupo": None,
                "needs_review": True,
                "created_at": datetime.now(timezone.utc),
            })
            fs_data = {"role": Role.OPERADOR, "grupo": None}
        role = fs_data.get("role") or normalize_role(fs_data.get("rol", "")) or Role.OPERADOR
        grupo = fs_data.get("grupo")
        claims_refreshed = _sync_claims_if_needed(uid, decoded_token, fs_data)
        logging.info(f"Usuario {user.email} inicio sesion (role={role})")
        return {
            "success": True,
            "user": {
                "email": user.email,
                "uid": user.uid,
                "full_name": user.display_name or fs_data.get("full_name", ""),
                "email_verified": user.email_verified,
                "role": role,
                "grupo": grupo,
            },
            "claims_refreshed": claims_refreshed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.warning(f"Intento de login fallido: {str(e)}")
        raise HTTPException(status_code=401, detail="Token invalido o revocado")


@router.get("/auth/register/health-check")
async def register_health_check():
    return {
        "firebase_auth": "available",
        "firestore": "available",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/auth/register")
@limiter.limit("3/minute")
async def register_user(user_data: UserRegistrationRequest, request: Request):
    """
    Registra un nuevo usuario. El rol siempre se fuerza a operador.
    """
    try:
        user = auth_client.create_user(
            email=user_data.email,
            password=user_data.password,
            display_name=user_data.full_name,
        )
        db.collection("users").document(user.uid).set({
            "email": user_data.email,
            "full_name": user_data.full_name,
            "cellphone": user_data.cellphone,
            "grupo": user_data.grupo,
            "role": Role.OPERADOR,
            "created_at": datetime.now(timezone.utc),
            "uid": user.uid,
        })
        auth_client.set_custom_user_claims(user.uid, {"role": Role.OPERADOR, "grupo": user_data.grupo})
        logging.info(f"Usuario registrado: {user.email} (UID: {user.uid})")
        return {
            "success": True,
            "message": "Usuario registrado exitosamente",
            "uid": user.uid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/change-password")
async def change_password(
    new_password: str = Form(...),
    current_user: CurrentUser = Depends(get_current_user),
    target_uid: Optional[str] = Form(None),
):
    """
    Cambia la contrasena. Un usuario solo puede cambiar la suya;
    administrador+ puede cambiar la de cualquiera.
    """
    try:
        uid_to_change = target_uid or current_user.uid
        if uid_to_change != current_user.uid and not current_user.at_least(Role.ADMINISTRADOR):
            raise HTTPException(status_code=403, detail="Solo un administrador puede cambiar la contrasena de otro usuario.")
        auth_client.update_user(uid_to_change, password=new_password)
        return {
            "success": True,
            "message": "Contrasena actualizada exitosamente",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/auth/workload-identity/status")
async def get_workload_identity_status():
    return {"workload_identity": "configured", "status": "active", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.post("/auth/google")
async def google_auth_unified(google_token: str = Form(...)):
    """
    Autenticacion con Google Sign-In. Devuelve role y grupo.
    """
    try:
        decoded_token = auth_client.verify_id_token(google_token)
        uid = decoded_token["uid"]
        user = auth_client.get_user(uid)
        fs_data = _get_user_firestore_data(uid)
        if not fs_data:
            db.collection("users").document(uid).set({
                "uid": uid,
                "email": user.email or "",
                "full_name": user.display_name or "",
                "role": Role.OPERADOR,
                "grupo": None,
                "needs_review": True,
                "created_at": datetime.now(timezone.utc),
            })
            fs_data = {"role": Role.OPERADOR, "grupo": None}
            auth_client.set_custom_user_claims(uid, {"role": Role.OPERADOR, "grupo": None})
        role = fs_data.get("role") or normalize_role(fs_data.get("rol", "")) or Role.OPERADOR
        grupo = fs_data.get("grupo")
        custom_token = auth_client.create_custom_token(uid)
        return {
            "success": True,
            "token": custom_token.decode("utf-8"),
            "user": {
                "email": user.email,
                "uid": user.uid,
                "full_name": user.display_name,
                "role": role,
                "grupo": grupo,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.delete("/auth/user/{uid}", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def delete_user(uid: str, permanent: bool = False):
    """Eliminar usuario. Requiere nivel administrador o superior."""
    try:
        auth_client.delete_user(uid)
        db.collection("users").document(uid).delete()
        logging.warning(f"Usuario eliminado: {uid}")
        return {
            "success": True,
            "message": f"Usuario {uid} eliminado",
            "permanent": permanent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Gestion de roles
# ---------------------------------------------------------------------------

@router.patch("/admin/users/{uid}/role")
async def change_user_role(
    uid: str,
    body: ChangeRoleRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.ADMINISTRADOR)),
):
    """
    Cambia el rol de un usuario.
    - Nadie puede cambiar su propio rol.
    - Administrador puede asignar hasta administrador (no puede crear desarrollador).
    - Solo desarrollador puede asignar o quitar el rol desarrollador.
    - Revoca tokens activos del usuario afectado para forzar re-login.
    """
    if uid == current_user.uid:
        raise HTTPException(status_code=403, detail="No puedes cambiar tu propio rol.")
    new_role = normalize_role(body.role)
    if not new_role:
        raise HTTPException(status_code=400, detail=f"Rol invalido: '{body.role}'. Valores validos: {[r.value for r in Role]}")
    if not can_assign_role(current_user.role, new_role):
        raise HTTPException(status_code=403, detail=f"No tienes permiso para asignar el rol '{new_role}'.")
    target_doc = db.collection("users").document(uid).get()
    if not target_doc.exists:
        raise HTTPException(status_code=404, detail=f"Usuario {uid} no encontrado.")
    target_data = target_doc.to_dict() or {}
    old_role = target_data.get("role") or normalize_role(target_data.get("rol", "")) or "sin_rol"
    grupo = target_data.get("grupo")
    db.collection("users").document(uid).update({"role": new_role, "needs_review": False})
    auth_client.set_custom_user_claims(uid, {"role": new_role, "grupo": grupo})
    auth_client.revoke_refresh_tokens(uid)
    db.collection("audit_role_changes").add({
        "actor_uid": current_user.uid,
        "actor_role": current_user.role,
        "target_uid": uid,
        "target_email": target_data.get("email", ""),
        "old_role": old_role,
        "new_role": new_role,
        "timestamp": datetime.now(timezone.utc),
    })
    logger.info(f"Rol cambiado: uid={uid} {old_role}->{new_role} por actor={current_user.uid}")
    return {
        "success": True,
        "uid": uid,
        "old_role": old_role,
        "new_role": new_role,
        "requires_relogin": True,
        "message": "El usuario debe re-autenticarse para que el nuevo rol sea efectivo.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/admin/users/{uid}/grupo")
async def change_user_grupo(
    uid: str,
    body: ChangeGrupoRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.ADMINISTRADOR)),
):
    """Cambia el grupo de un usuario. Requiere nivel administrador o superior."""
    if uid == current_user.uid:
        raise HTTPException(status_code=403, detail="No puedes cambiar tu propio grupo.")
    target_doc = db.collection("users").document(uid).get()
    if not target_doc.exists:
        raise HTTPException(status_code=404, detail=f"Usuario {uid} no encontrado.")
    target_data = target_doc.to_dict() or {}
    old_grupo = target_data.get("grupo")
    role = target_data.get("role") or normalize_role(target_data.get("rol", "")) or Role.OPERADOR
    db.collection("users").document(uid).update({"grupo": body.grupo, "needs_review": False})
    auth_client.set_custom_user_claims(uid, {"role": role, "grupo": body.grupo})
    auth_client.revoke_refresh_tokens(uid)
    logger.info(f"Grupo cambiado: uid={uid} {old_grupo}->{body.grupo} por actor={current_user.uid}")
    return {
        "success": True,
        "uid": uid,
        "old_grupo": old_grupo,
        "new_grupo": body.grupo,
        "requires_relogin": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Listado de usuarios
# ---------------------------------------------------------------------------

@router.get("/admin/users")
async def list_system_users(
    request: Request,
    limit: Optional[int] = 50,
    offset: int = 0,
    full_name: Optional[str] = None,
    grupo: Optional[str] = None,
    email: Optional[str] = None,
    cellphone: Optional[str] = None,
    role: Optional[str] = None,
    uid: Optional[str] = None,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Lista usuarios de Firestore.
    Lider: solo ve su grupo. Administrador/Desarrollador: ve todos.
    """
    try:
        if limit is not None and limit < 1:
            raise HTTPException(status_code=400, detail="El parametro 'limit' debe ser mayor a 0")
        if offset < 0:
            raise HTTPException(status_code=400, detail="El parametro 'offset' no puede ser negativo")
        effective_grupo = grupo
        if not current_user.at_least(Role.ADMINISTRADOR):
            effective_grupo = current_user.grupo
        filters = {}
        explicit_filters = {"full_name": full_name, "grupo": effective_grupo, "email": email, "cellphone": cellphone, "role": role, "uid": uid}
        for key, value in explicit_filters.items():
            if value is not None and str(value).strip() != "":
                filters[key] = str(value).strip()
        reserved_params = {"limit", "offset", "full_name", "grupo", "email", "cellphone", "role", "uid"}
        extra_filters = {k: v.strip() for k, v in request.query_params.items() if k not in reserved_params and v and v.strip()}
        filters.update(extra_filters)
        docs = db.collection("users").stream()
        filtered_users = []
        for doc in docs:
            data = doc.to_dict() or {}
            if "uid" not in data or not data.get("uid"):
                data["uid"] = doc.id
            data["id"] = doc.id
            if "rol" in data and "role" not in data:
                data["role"] = normalize_role(data.get("rol"))
            matches = True
            for field_name, expected_value in filters.items():
                current_value = data.get(field_name)
                if current_value is None:
                    matches = False
                    break
                if str(current_value).strip().lower() != expected_value.lower():
                    matches = False
                    break
            if matches:
                filtered_users.append(data)
        total_items = len(filtered_users)
        if limit is None:
            paginated_users = filtered_users[offset:]
        else:
            paginated_users = filtered_users[offset:offset + limit]
        return {
            "success": True,
            "data": paginated_users,
            "count": len(paginated_users),
            "total_items": total_items,
            "limit": limit,
            "offset": offset,
            "filters": filters,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/admin/users", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def list_firebase_users(limit: int = 100):
    """Lista usuarios de Firebase Auth. Requiere nivel administrador o superior."""
    try:
        users = auth_client.list_users(max_results=limit).iterate_all()
        user_list = [{"uid": u.uid, "email": u.email, "display_name": u.display_name, "email_verified": u.email_verified, "disabled": u.disabled, "created_at": u.user_metadata.creation_timestamp} for u in users]
        return {"users": user_list, "total": len(user_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Endpoints TODO con proteccion minima
# ---------------------------------------------------------------------------

@router.get("/auth/admin/users/super-admins", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def list_super_admin_users():
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.get("/auth/admin/users/{uid}", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def get_user_details(uid: str):
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.put("/auth/admin/users/{uid}", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def update_user_info(uid: str):
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.post("/auth/admin/users/{uid}/roles", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def assign_roles_to_user(uid: str, roles: AssignRolesRequest):
    raise HTTPException(status_code=410, detail="Endpoint obsoleto. Usar PATCH /admin/users/{uid}/role.")

@router.post("/auth/admin/users/{uid}/temporary-permissions", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def grant_temporary_permission(uid: str, permission: GrantTemporaryPermissionRequest):
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.delete("/auth/admin/users/{uid}/temporary-permissions/{permission}", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def revoke_temporary_permission(uid: str, permission: str):
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.get("/auth/admin/roles", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def list_roles():
    return {"roles": [{"name": r.value, "level": lv} for r, lv in [(Role.OPERADOR, 1), (Role.LIDER, 2), (Role.ADMINISTRADOR, 3), (Role.DESARROLLADOR, 4)]]}

@router.get("/auth/admin/roles/{role_id}", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def get_role_details(role_id: str):
    raise HTTPException(status_code=501, detail="No implementado aun.")

@router.get("/auth/admin/audit-logs", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def get_audit_logs(limit: int = 100, user_uid: Optional[str] = None, action: Optional[str] = None):
    """Logs de cambios de rol."""
    try:
        query = db.collection("audit_role_changes").order_by("timestamp", direction="DESCENDING").limit(limit)
        if user_uid:
            query = query.where("target_uid", "==", user_uid)
        logs = [{**doc.to_dict(), "id": doc.id} for doc in query.stream()]
        return {"logs": logs, "total": len(logs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/auth/admin/system/stats", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def get_system_stats():
    try:
        users_count = len(list(db.collection("users").stream()))
        return {"total_users": users_count, "total_roles": len(Role), "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/auth/config", dependencies=[Depends(security)])
async def get_firebase_config(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Configuracion de Firebase para el frontend (requiere token valido)."""
    try:
        auth_client.verify_id_token(credentials.credentials, check_revoked=True)
        return {
            "apiKey": "AIzaSyCQRFYX84gaSzWcOIsT6bGvMGNG1P0I0QI",
            "authDomain": "dagma-85aad.firebaseapp.com",
            "projectId": "dagma-85aad",
            "storageBucket": "dagma-85aad.appspot.com",
            "messagingSenderId": "your-messaging-sender-id",
            "appId": "your-app-id",
        }
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalido")
