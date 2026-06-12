"""
Rutas de Administración y Control de Accesos
"""
from fastapi import APIRouter, HTTPException, Depends, Form, Request, UploadFile, File, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timezone
import logging
import os
import io
import asyncio
import base64
import json
from app.firebase_config import auth_client, db
from app.models.roles import Role, can_assign_role, normalize_role
from app.deps.authz import get_current_user, require_min_role, CurrentUser
from app.utils.firestore_async import run_blocking, stream_to_list
from app.utils.text_utils import normalize_grupo
from app.services.gmail_service import (
    send_role_change_email,
    send_grupo_change_email,
)
from app.limiter import limiter

router = APIRouter(tags=["Administración y Control de Accesos"])
security = HTTPBearer()

# Rate limiter — uses the shared instance registered in app.state,
# so SlowAPIMiddleware enforces these limits correctly at runtime.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dev token fallback helper
# ---------------------------------------------------------------------------

def _verify_token_with_fallback(token: str) -> dict:
    """Verifies a Firebase ID token. In local dev, falls back to unsigned JWT decode
    when Google's certificate endpoint (port 443) is blocked.

    The fallback activates when ALLOW_UNVERIFIED_JWT=true or in local development environments
    (when RAILWAY_ENVIRONMENT is not production).
    """
    try:
        return auth_client.verify_id_token(token, check_revoked=True)
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_msg = str(exc)
        is_cert_error = "CertificateFetchError" in exc_name or "CertificateFetchError" in exc_msg
        allow_unverified = os.environ.get("ALLOW_UNVERIFIED_JWT", "").lower() == "true"
        is_local = os.environ.get("RAILWAY_ENVIRONMENT", "local") != "production"
        if is_cert_error and (allow_unverified or is_local):
            try:
                payload_b64 = token.split(".")[1]
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                uid = payload.get("user_id") or payload.get("sub") or ""
                if not uid:
                    raise ValueError("UID vacío en payload JWT")
                logger.warning(f"[DEV] Cert fetch bloqueado — uid={uid} autenticado sin verificar firma")
                return {**payload, "uid": uid}
            except Exception as decode_err:
                logger.warning(f"[DEV] No se pudo decodificar JWT: {decode_err}")
        raise


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


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    cellphone: Optional[str] = None


class SelfUpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    cellphone: Optional[str] = None


class CompleteGoogleProfileRequest(BaseModel):
    grupo: str
    full_name: Optional[str] = None
    cellphone: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

# Cliente S3 reutilizado a nivel de modulo para evitar coste de creacion en cada request.
_S3_CLIENT = None
_S3_INIT_FAILED = False


def _get_s3_client():
    global _S3_CLIENT, _S3_INIT_FAILED
    if _S3_CLIENT is not None or _S3_INIT_FAILED:
        return _S3_CLIENT
    try:
        import boto3
        aws_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        if not aws_key or not aws_secret:
            _S3_INIT_FAILED = True
            return None
        _S3_CLIENT = boto3.client(
            "s3",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region,
        )
        return _S3_CLIENT
    except Exception as e:
        logger.warning(f"No se pudo inicializar cliente S3: {e}")
        _S3_INIT_FAILED = True
        return None


def _get_s3_photo_url(s3_key: str) -> Optional[str]:
    """
    Genera una URL pre-firmada de S3 para la foto de perfil (válida 7 días).
    Retorna None si la configuración de S3 no está disponible.
    """
    try:
        bucket = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET_NAME")
        if not bucket:
            return None
        s3 = _get_s3_client()
        if s3 is None:
            return None
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=604800,  # 7 días
        )
    except Exception as e:
        logger.warning(f"No se pudo generar presigned URL para {s3_key}: {e}")
        return None


def _get_user_firestore_data(uid: str) -> dict:
    doc = db.collection("users").document(uid).get()
    if doc.exists:
        return doc.to_dict() or {}
    return {}


def _sync_claims_if_needed(uid: str, decoded_token: dict, firestore_data: dict) -> bool:
    token_role = decoded_token.get("role")
    token_grupo = decoded_token.get("grupo")
    db_role = normalize_role(firestore_data.get("role") or firestore_data.get("rol")) or Role.OPERADOR
    db_grupo = firestore_data.get("grupo")
    if token_role != db_role or token_grupo != db_grupo:
        try:
            auth_client.set_custom_user_claims(
                uid, {"role": db_role, "grupo": db_grupo}
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
    Si el usuario no existe en Firestore (primer login con Google), crea el doc
    y devuelve needs_profile_completion=True para que el frontend recolecte los datos.
    """
    try:
        token = credentials.credentials
        decoded_token = _verify_token_with_fallback(token)
        uid = decoded_token["uid"]
        user = auth_client.get_user(uid)
        fs_data = _get_user_firestore_data(uid)
        is_new_user = not fs_data
        if is_new_user:
            # Primer login con Google: crear doc provisional
            fs_data = {
                "uid": uid,
                "email": user.email or "",
                "full_name": user.display_name or "",
                "role": Role.OPERADOR,
                "grupo": None,
                "needs_review": True,
                "created_at": datetime.now(timezone.utc),
            }
            db.collection("users").document(uid).set(fs_data)
            auth_client.set_custom_user_claims(uid, {"role": Role.OPERADOR, "grupo": None})
            logger.info(f"Nuevo usuario Google registrado provisionalmente: {user.email} ({uid})")
        role = normalize_role(fs_data.get("role") or fs_data.get("rol")) or Role.OPERADOR
        grupo = fs_data.get("grupo")
        needs_profile_completion = not grupo
        claims_refreshed = _sync_claims_if_needed(uid, decoded_token, fs_data)
        # Resolver photoURL: foto personalizada (S3) tiene prioridad sobre Google
        photo_url = user.photo_url
        photo_s3_key = fs_data.get("photo_s3_key") if fs_data else None
        if photo_s3_key:
            refreshed_url = _get_s3_photo_url(photo_s3_key)
            if refreshed_url:
                photo_url = refreshed_url
        return {
            "valid": True,
            "needs_profile_completion": needs_profile_completion,
            "user": {
                "uid": user.uid,
                "email": user.email,
                "full_name": user.display_name or fs_data.get("full_name", ""),
                "photoURL": photo_url,
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
        decoded_token = _verify_token_with_fallback(credentials.id_token)
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
        role = normalize_role(fs_data.get("role") or fs_data.get("rol")) or Role.OPERADOR
        grupo = fs_data.get("grupo")
        claims_refreshed = _sync_claims_if_needed(uid, decoded_token, fs_data)
        logger.info(f"Usuario {user.email} inicio sesion (role={role})")
        # Resolver photoURL igual que en validate-session
        photo_url = user.photo_url
        photo_s3_key = fs_data.get("photo_s3_key") if fs_data else None
        if photo_s3_key:
            refreshed_url = _get_s3_photo_url(photo_s3_key)
            if refreshed_url:
                photo_url = refreshed_url
        return {
            "success": True,
            "user": {
                "email": user.email,
                "uid": user.uid,
                "full_name": user.display_name or fs_data.get("full_name", ""),
                "photoURL": photo_url,
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
        logger.warning(f"Intento de login fallido: {str(e)}")
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
        grupo_canonico = normalize_grupo(user_data.grupo) or None
        db.collection("users").document(user.uid).set({
            "email": user_data.email,
            "full_name": user_data.full_name,
            "cellphone": user_data.cellphone,
            "grupo": grupo_canonico,
            "role": Role.OPERADOR,
            "created_at": datetime.now(timezone.utc),
            "uid": user.uid,
        })
        auth_client.set_custom_user_claims(user.uid, {"role": Role.OPERADOR, "grupo": grupo_canonico})
        logger.info(f"Usuario registrado: {user.email} (UID: {user.uid})")
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


@router.post("/auth/complete-google-profile")
async def complete_google_profile(
    body: CompleteGoogleProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Completa el perfil de un usuario que se registro con Google y no tiene grupo asignado.
    Solo se permite si grupo es actualmente nulo en Firestore.
    """
    uid = current_user.uid
    fs_data = _get_user_firestore_data(uid)
    if fs_data.get("grupo"):
        raise HTTPException(
            status_code=400,
            detail="El perfil ya esta completo. Usa /auth/me/profile para actualizar."
        )
    if not body.grupo or not body.grupo.strip():
        raise HTTPException(status_code=400, detail="El campo 'grupo' es obligatorio.")
    grupo_normalizado = normalize_grupo(body.grupo)
    if not grupo_normalizado:
        raise HTTPException(status_code=400, detail="El campo 'grupo' es obligatorio.")
    update_data: dict = {
        "grupo": grupo_normalizado,
        "needs_review": False,
    }
    if body.full_name and body.full_name.strip():
        update_data["full_name"] = body.full_name.strip()
        try:
            auth_client.update_user(uid, display_name=body.full_name.strip())
        except Exception as e:
            logger.warning(f"No se pudo actualizar display_name para {uid}: {e}")
    if body.cellphone and body.cellphone.strip():
        update_data["cellphone"] = body.cellphone.strip()
    db.collection("users").document(uid).update(update_data)
    auth_client.set_custom_user_claims(uid, {"role": current_user.role, "grupo": grupo_normalizado})
    logger.info(f"Perfil Google completado para {uid}: grupo={grupo_normalizado}")
    return {
        "success": True,
        "uid": uid,
        "grupo": grupo_normalizado,
        "updated_fields": list(update_data.keys()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/auth/me/profile")
async def self_update_profile(
    body: SelfUpdateProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Permite a cualquier usuario autenticado actualizar su propio perfil (full_name, cellphone).
    No permite cambiar email, role ni grupo.
    """
    uid = current_user.uid
    update_data: dict = {}
    if body.full_name is not None:
        update_data["full_name"] = body.full_name
        try:
            auth_client.update_user(uid, display_name=body.full_name)
        except Exception as e:
            logger.warning(f"No se pudo actualizar display_name en Firebase Auth para {uid}: {e}")
    if body.cellphone is not None:
        update_data["cellphone"] = body.cellphone
    if not update_data:
        raise HTTPException(status_code=400, detail="No se proporcionaron campos para actualizar.")
    db.collection("users").document(uid).update(update_data)
    return {
        "success": True,
        "uid": uid,
        "updated_fields": list(update_data.keys()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/auth/me/photo")
async def upload_profile_photo(
    photo: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Sube o reemplaza la foto de perfil del usuario autenticado.
    Almacena en S3 bajo avatars/{uid}/profile.{ext} y actualiza Firebase Auth + Firestore.
    """
    import boto3
    from botocore.exceptions import ClientError

    uid = current_user.uid
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    if photo.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de imagen no permitido: {photo.content_type}. Usa JPEG, PNG o WebP.",
        )
    ext_map = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    ext = ext_map.get(photo.content_type, ".jpg")
    s3_key = f"avatars/{uid}/profile{ext}"

    bucket = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET_NAME")
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    if not all([bucket, aws_key, aws_secret]):
        raise HTTPException(status_code=503, detail="Almacenamiento S3 no configurado en este entorno.")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=aws_key,
        aws_secret_access_key=aws_secret,
        region_name=aws_region,
    )
    try:
        content = await photo.read()
        if len(content) > 5 * 1024 * 1024:  # 5 MB límite
            raise HTTPException(status_code=413, detail="La imagen no puede superar 5 MB.")
        await asyncio.to_thread(
            s3.upload_fileobj,
            io.BytesIO(content),
            bucket,
            s3_key,
            ExtraArgs={"ContentType": photo.content_type},
        )
    except HTTPException:
        raise
    except ClientError as e:
        logger.error(f"Error S3 al subir foto de perfil para {uid}: {e}")
        raise HTTPException(status_code=500, detail="Error al subir la imagen al almacenamiento.")
    except Exception as e:
        logger.error(f"Error inesperado subiendo foto para {uid}: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la imagen.")

    # Generar presigned URL (7 días)
    photo_url = _get_s3_photo_url(s3_key)
    if not photo_url:
        raise HTTPException(status_code=500, detail="Error al generar URL de la imagen.")

    # Actualizar Firebase Auth
    try:
        auth_client.update_user(uid, photo_url=photo_url)
    except Exception as e:
        logger.warning(f"No se pudo actualizar photo_url en Firebase Auth para {uid}: {e}")

    # Actualizar Firestore (guardar key para regenerar URL en futuras sesiones)
    db.collection("users").document(uid).update({
        "photo_s3_key": s3_key,
        "photoURL": photo_url,
    })

    logger.info(f"Foto de perfil actualizada para {uid} → {s3_key}")
    return {"success": True, "photoURL": photo_url}


@router.post("/auth/google")
async def google_auth_unified(google_token: str = Form(...)):
    """
    Autenticacion con Google Sign-In. Devuelve role y grupo.
    """
    try:
        decoded_token = _verify_token_with_fallback(google_token)
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
        role = normalize_role(fs_data.get("role") or fs_data.get("rol")) or Role.OPERADOR
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
        logger.warning(f"Usuario eliminado: {uid}")
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
    background_tasks: BackgroundTasks = None,
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
    old_role = normalize_role(target_data.get("role") or target_data.get("rol")) or "sin_rol"
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
    # Notificar al usuario por correo (best-effort, background).
    target_email = (target_data.get("email") or "").strip()
    target_name = (target_data.get("full_name") or target_data.get("nombre_completo") or "").strip()
    actor_name = current_user.full_name if hasattr(current_user, "full_name") else current_user.uid
    if target_email and "@" in target_email:
        try:
            if background_tasks is not None:
                background_tasks.add_task(
                    send_role_change_email, target_email, target_name,
                    old_role, new_role, actor_name,
                )
            else:
                send_role_change_email(target_email, target_name, old_role, new_role, actor_name)
        except Exception as e:
            logger.warning(f"[EMAIL] Error agendando notificación cambio de rol a {target_email}: {e}")
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
    background_tasks: BackgroundTasks = None,
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
    role = normalize_role(target_data.get("role") or target_data.get("rol")) or Role.OPERADOR
    grupo_normalizado = normalize_grupo(body.grupo) if body.grupo else None
    db.collection("users").document(uid).update({"grupo": grupo_normalizado, "needs_review": False})
    auth_client.set_custom_user_claims(uid, {"role": role, "grupo": grupo_normalizado})
    auth_client.revoke_refresh_tokens(uid)
    logger.info(f"Grupo cambiado: uid={uid} {old_grupo}->{grupo_normalizado} por actor={current_user.uid}")
    # Notificar al usuario por correo (best-effort, background).
    target_email = (target_data.get("email") or "").strip()
    target_name = (target_data.get("full_name") or target_data.get("nombre_completo") or "").strip()
    actor_name = current_user.full_name if hasattr(current_user, "full_name") else current_user.uid
    if target_email and "@" in target_email:
        try:
            if background_tasks is not None:
                background_tasks.add_task(
                    send_grupo_change_email, target_email, target_name,
                    old_grupo or "", grupo_normalizado or "", actor_name,
                )
            else:
                send_grupo_change_email(target_email, target_name, old_grupo or "", grupo_normalizado or "", actor_name)
        except Exception as e:
            logger.warning(f"[EMAIL] Error agendando notificación cambio de grupo a {target_email}: {e}")
    return {
        "success": True,
        "uid": uid,
        "old_grupo": old_grupo,
        "new_grupo": grupo_normalizado,
        "requires_relogin": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/admin/users/{uid}/profile")
async def update_user_profile(
    uid: str,
    body: UpdateProfileRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.ADMINISTRADOR)),
):
    """Actualiza datos de perfil de un usuario (full_name, cellphone). Requiere administrador o superior."""
    target_doc = db.collection("users").document(uid).get()
    if not target_doc.exists:
        raise HTTPException(status_code=404, detail=f"Usuario {uid} no encontrado.")
    update_data: dict = {}
    if body.full_name is not None:
        update_data["full_name"] = body.full_name
        try:
            auth_client.update_user(uid, display_name=body.full_name)
        except Exception as e:
            logger.warning(f"No se pudo actualizar display_name en Firebase Auth para {uid}: {e}")
    if body.cellphone is not None:
        update_data["cellphone"] = body.cellphone
    if not update_data:
        raise HTTPException(status_code=400, detail="No se proporcionaron campos para actualizar.")
    db.collection("users").document(uid).update(update_data)
    logger.info(f"Perfil actualizado: uid={uid} campos={list(update_data.keys())} por actor={current_user.uid}")
    return {
        "success": True,
        "uid": uid,
        "updated_fields": list(update_data.keys()),
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
        
        # Normalizar el rol del filtro si se especifica
        normalized_filter_role = normalize_role(role) if role else None
        
        filters = {}
        explicit_filters = {
            "full_name": full_name,
            "grupo": effective_grupo,
            "email": email,
            "cellphone": cellphone,
            "role": normalized_filter_role or role,
            "uid": uid
        }
        for key, value in explicit_filters.items():
            if value is not None and str(value).strip() != "":
                filters[key] = str(value).strip()
        reserved_params = {"limit", "offset", "full_name", "grupo", "email", "cellphone", "role", "uid"}
        extra_filters = {k: v.strip() for k, v in request.query_params.items() if k not in reserved_params and v and v.strip()}
        filters.update(extra_filters)

        # Empuja filtros selectivos a Firestore para evitar cargar toda la coleccion.
        # Orden de preferencia: uid (1 doc), email (~1 doc), grupo (~decenas), role.
        # Para `grupo` se empuja la forma canonica (normalize_grupo) porque la
        # coleccion ya esta canonicalizada (ver scripts/normalize_grupos.py).
        # Si quedan docs legacy no canonicos, el filtro en memoria los rescata.
        query = db.collection("users")
        pushed_field = None
        for candidate in ("uid", "email", "grupo", "role"):
            if candidate in filters:
                value = filters[candidate]
                pushed_value = normalize_grupo(value) if candidate == "grupo" else value
                query = query.where(candidate, "==", pushed_value)
                pushed_field = candidate
                break
        # Cap defensivo: nunca traer mas de 1000 docs aunque no haya limit.
        hard_cap = (limit or 0) + offset + 500 if limit is not None else 1000
        query = query.limit(hard_cap)
        docs = await stream_to_list(query)
        filtered_users = []
        for doc in docs:
            data = doc.to_dict() or {}
            if "uid" not in data or not data.get("uid"):
                data["uid"] = doc.id
            data["id"] = doc.id
            data["role"] = normalize_role(data.get("role") or data.get("rol")) or Role.OPERADOR
            matches = True
            for field_name, expected_value in filters.items():
                if field_name == pushed_field:
                    continue  # ya filtrado en Firestore
                current_value = data.get(field_name)
                if current_value is None:
                    matches = False
                    break
                if field_name == "grupo":
                    # Comparacion tolerante: insensible a mayusculas/tildes y
                    # equivalencia entre "_" y espacio ("Central_Social" == "Central Social").
                    if normalize_grupo(current_value) != normalize_grupo(expected_value):
                        matches = False
                        break
                elif str(current_value).strip().lower() != expected_value.lower():
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

@router.get("/admin/users/lideres")
async def list_leader_users(
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Lista todos los usuarios elegibles como lider_actividad.

    - Lider: solo ve usuarios con rol 'lider'.
    - Administrador/Director/Desarrollador: ve también usuarios con rol 'administrador'.

    Este endpoint alimenta los selectores de programación, que necesitan
    ver el catálogo completo de líderes elegibles para los grupos requeridos.
    """
    try:
        # Roles elegibles según el nivel del usuario que consulta.
        caller_can_see_admins = current_user.at_least(Role.ADMINISTRADOR)

        # Filtro a nivel Firestore para no escanear toda la coleccion.
        # Algunos docs antiguos pueden tener el rol guardado en "rol" en lugar de "role";
        # se traen ambos y se deduplican por uid.
        queries = [
            db.collection("users").where("role", "==", Role.LIDER).limit(500),
            db.collection("users").where("rol", "==", Role.LIDER).limit(500),
        ]
        if caller_can_see_admins:
            queries += [
                db.collection("users").where("role", "==", Role.ADMINISTRADOR).limit(500),
                db.collection("users").where("rol", "==", Role.ADMINISTRADOR).limit(500),
            ]

        all_results = await asyncio.gather(*[stream_to_list(q) for q in queries])
        seen: set = set()
        lideres = []
        for doc in [d for batch in all_results for d in batch]:
            if doc.id in seen:
                continue
            seen.add(doc.id)
            data = doc.to_dict() or {}
            if "uid" not in data or not data.get("uid"):
                data["uid"] = doc.id

            role = normalize_role(data.get("role") or data.get("rol"))
            # Solo incluir lider (para todos) y administrador (solo para admin+).
            if role == Role.LIDER:
                pass  # siempre elegible
            elif role == Role.ADMINISTRADOR and caller_can_see_admins:
                pass  # elegible solo para admin+
            else:
                continue

            lideres.append(
                {
                    "uid": data.get("uid", doc.id),
                    "email": data.get("email", ""),
                    "full_name": data.get("full_name") or data.get("nombre_completo") or data.get("displayName") or data.get("email", ""),
                    "grupo": data.get("grupo"),
                    "role": role,
                    "photoURL": data.get("photoURL"),
                    "cellphone": data.get("cellphone"),
                }
            )

        lideres.sort(
            key=lambda item: (
                str(item.get("full_name") or "").strip().lower(),
                str(item.get("grupo") or "").strip().lower(),
            )
        )

        return {
            "success": True,
            "data": lideres,
            "count": len(lideres),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
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
        query = db.collection("audit_role_changes")
        if user_uid:
            query = query.where("target_uid", "==", user_uid)
        query = query.order_by("timestamp", direction="DESCENDING").limit(limit)
        docs = await stream_to_list(query)
        logs = [{**(doc.to_dict() or {}), "id": doc.id} for doc in docs]
        return {"logs": logs, "total": len(logs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/auth/admin/system/stats", dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def get_system_stats():
    try:
        # Mantiene conteo exacto, pero fuera del event loop.
        users_count = await run_blocking(lambda: len(list(db.collection("users").stream())))
        return {"total_users": users_count, "total_roles": len(Role), "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/auth/config", dependencies=[Depends(security)])
async def get_firebase_config(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Configuracion de Firebase para el frontend (requiere token valido)."""
    try:
        _verify_token_with_fallback(credentials.credentials)
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
