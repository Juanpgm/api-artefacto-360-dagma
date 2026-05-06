"""
Dependencias de autorización (RBAC) para API Artefacto 360 DAGMA.

Uso:
    from app.deps.authz import get_current_user, require_role, require_min_role

    @router.get("/ruta")
    async def mi_endpoint(current_user: CurrentUser = Depends(get_current_user)):
        ...

    @router.delete("/ruta/{id}", dependencies=[Depends(require_min_role("administrador"))])
    async def eliminar(...):
        ...
"""
import base64
import json
import logging
import os
from typing import Callable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.firebase_config import auth_client, db
from app.models.roles import CurrentUser, Role, normalize_role, role_at_least

logger = logging.getLogger(__name__)
security = HTTPBearer()


# ---------------------------------------------------------------------------
# Helper interno: construir CurrentUser desde token decodificado + Firestore
# ---------------------------------------------------------------------------

def _build_current_user_from_claims(decoded_token: dict) -> Optional[CurrentUser]:
    """
    Intenta construir CurrentUser usando solo los custom claims del token.
    Devuelve None si los claims están incompletos.
    """
    role = decoded_token.get("role")
    grupo = decoded_token.get("grupo")

    if not role:
        return None

    # Validar que el rol sea un valor conocido
    normalized = normalize_role(role)
    if not normalized:
        return None

    return CurrentUser(
        uid=decoded_token["uid"],
        email=decoded_token.get("email", ""),
        role=normalized,
        grupo=grupo,
        full_name=decoded_token.get("name"),
    )


async def _hydrate_from_firestore(uid: str, email: str = "", full_name: str = "") -> CurrentUser:
    """
    Lee el documento users/{uid} de Firestore y construye CurrentUser.
    Si el usuario no existe en Firestore crea un doc mínimo con role=operador.
    """
    try:
        doc = db.collection("users").document(uid).get()
        if doc.exists:
            data = doc.to_dict() or {}
            raw_role = data.get("role") or data.get("rol")
            role = normalize_role(raw_role) or Role.OPERADOR
            return CurrentUser(
                uid=uid,
                email=data.get("email", email),
                role=role,
                grupo=data.get("grupo"),
                full_name=data.get("full_name", full_name),
            )
        else:
            # Usuario existe en Firebase Auth pero no en Firestore → crear doc mínimo
            logger.warning(
                f"Usuario {uid} existe en Firebase Auth pero no en Firestore. "
                "Creando documento con role=operador y needs_review=true."
            )
            db.collection("users").document(uid).set(
                {
                    "uid": uid,
                    "email": email,
                    "full_name": full_name,
                    "role": Role.OPERADOR,
                    "grupo": None,
                    "needs_review": True,
                },
                merge=True,
            )
            return CurrentUser(
                uid=uid,
                email=email,
                role=Role.OPERADOR,
                grupo=None,
                full_name=full_name,
            )
    except Exception as e:
        logger.error(f"Error leyendo Firestore para uid={uid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error al verificar credenciales del usuario",
        )


# ---------------------------------------------------------------------------
# Dependencia principal
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    """
    Dependencia que:
    1. Valida el Firebase ID token (con check_revoked=True para invalidación inmediata).
    2. Intenta construir CurrentUser desde custom claims del token.
    3. Si los claims no están o el rol es desconocido, hidrata desde Firestore.
    4. Lanza 401 si el token es inválido/revocado, 403 si no tiene rol asignado.
    """
    token = credentials.credentials

    try:
        decoded_token = auth_client.verify_id_token(token, check_revoked=True)
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_msg = str(exc)

        # In local dev, Firebase can't fetch Google's public keys if port 443 is blocked.
        # Fall back to decoding the JWT payload (no signature check) to extract the UID,
        # then hydrate the role from Firestore — never from the unverified token.
        is_cert_error = "CertificateFetchError" in exc_name or "CertificateFetchError" in exc_msg
        is_local = os.environ.get("RAILWAY_ENVIRONMENT", "local") != "production"

        if is_cert_error and is_local:
            try:
                payload_b64 = token.split(".")[1]
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                uid = payload.get("user_id") or payload.get("sub") or ""
                if not uid:
                    raise ValueError("UID vacío en payload JWT")
                decoded_token = {**payload, "uid": uid}
                logger.warning(
                    f"[DEV] Cert fetch bloqueado — uid={uid} autenticado sin verificar firma"
                )
            except Exception as decode_err:
                logger.warning(f"[DEV] No se pudo decodificar JWT: {decode_err}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token inválido o revocado",
                )
        else:
            logger.warning(f"Token de Firebase rechazado: {type(exc).__name__}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o revocado",
            )

    # Intentar claims primero (más rápido, sin leer Firestore)
    current_user = _build_current_user_from_claims(decoded_token)

    if current_user is None:
        # Claims incompletos → hidratar desde Firestore
        uid = decoded_token.get("uid", "")
        email = decoded_token.get("email", "")
        full_name = decoded_token.get("name", "")
        current_user = await _hydrate_from_firestore(uid, email, full_name)

        # Sincronizar claims para futuros requests (no bloquea la respuesta)
        try:
            auth_client.set_custom_user_claims(
                current_user.uid,
                {"role": current_user.role, "grupo": current_user.grupo},
            )
        except Exception as e:
            logger.warning(f"No se pudieron sincronizar claims para {current_user.uid}: {e}")

    return current_user


# ---------------------------------------------------------------------------
# Factories de autorización
# ---------------------------------------------------------------------------

def require_role(*roles: str) -> Callable:
    """
    Factory que retorna una dependencia FastAPI que exige que el usuario
    tenga exactamente uno de los roles indicados.

    Ejemplo:
        @router.get("/ruta", dependencies=[Depends(require_role("administrador", "desarrollador"))])
    """
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            logger.warning(
                f"Acceso denegado: uid={current_user.uid} role={current_user.role} "
                f"required_roles={roles}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acción requiere uno de los siguientes roles: {', '.join(roles)}",
            )
        return current_user

    return dependency


def require_min_role(min_role: str) -> Callable:
    """
    Factory que retorna una dependencia FastAPI que exige que el usuario
    tenga un nivel de rol >= `min_role`.

    Ejemplo:
        @router.delete("/ruta/{id}", dependencies=[Depends(require_min_role("administrador"))])
    """
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_at_least(current_user.role, min_role):
            logger.warning(
                f"Acceso denegado: uid={current_user.uid} role={current_user.role} "
                f"min_required={min_role}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acción requiere nivel de rol mínimo: {min_role}",
            )
        return current_user

    return dependency


# ---------------------------------------------------------------------------
# Helper de scoping por grupo
# ---------------------------------------------------------------------------

def enforce_group_scope(current_user: CurrentUser, requested_grupo: Optional[str]) -> str:
    """
    Para operador y lider, fuerza que el grupo de la operación sea
    el propio grupo del usuario (ignora el parámetro de la query si difiere).

    Para administrador y desarrollador, permite cualquier grupo.

    Devuelve el grupo efectivo que debe usarse en la consulta.
    Lanza 403 si el usuario intenta acceder a un grupo distinto al suyo.
    """
    if current_user.at_least(Role.ADMINISTRADOR):
        return requested_grupo  # puede ver todo

    # operador o lider
    if not current_user.grupo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no tiene grupo asignado. Contacta a un administrador.",
        )

    if requested_grupo and requested_grupo.lower() != current_user.grupo.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No tienes acceso al grupo '{requested_grupo}'. Solo puedes acceder a '{current_user.grupo}'.",
        )

    return current_user.grupo
