"""
Roles y jerarquía de autorización para API Artefacto 360 DAGMA.

Jerarquía (menor a mayor privilegio):
    operador < lider < administrador < desarrollador
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Role(str, Enum):
    """Roles disponibles en el sistema (valores normalizados en minúscula)."""
    OPERADOR = "operador"
    LIDER = "lider"
    ADMINISTRADOR = "administrador"
    DIRECTOR = "director"
    DESARROLLADOR = "desarrollador"


# Nivel numérico por rol (mayor = más privilegio)
ROLE_HIERARCHY: dict[str, int] = {
    Role.OPERADOR: 1,
    Role.LIDER: 2,
    Role.ADMINISTRADOR: 3,
    Role.DIRECTOR: 3,
    Role.DESARROLLADOR: 4,
}

# Mapa de normalización para datos legacy en Firestore
LEGACY_ROLE_MAP: dict[str, str] = {
    "admin": Role.ADMINISTRADOR,
    "administrator": Role.ADMINISTRADOR,
    "administrador": Role.ADMINISTRADOR,
    "director": Role.DIRECTOR,
    "directora": Role.DIRECTOR,
    "super_admin": Role.DESARROLLADOR,
    "superadmin": Role.DESARROLLADOR,
    "desarrollador": Role.DESARROLLADOR,
    "developer": Role.DESARROLLADOR,
    "lider": Role.LIDER,
    "líder": Role.LIDER,
    "leader": Role.LIDER,
    "coordinador": Role.LIDER,
    "operador": Role.OPERADOR,
    "operator": Role.OPERADOR,
    "operario": Role.OPERADOR,
}


def normalize_role(raw_role: Optional[str]) -> Optional[str]:
    """
    Normaliza un valor de rol legacy al valor canónico del enum Role.
    Devuelve None si el valor no puede mapearse.
    """
    if not raw_role:
        return None
    cleaned = raw_role.strip().lower()
    return LEGACY_ROLE_MAP.get(cleaned)


def role_at_least(user_role: str, min_role: str) -> bool:
    """
    Devuelve True si `user_role` tiene nivel >= `min_role`.
    Ej: role_at_least("administrador", "lider") → True
    """
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    min_level = ROLE_HIERARCHY.get(min_role, 999)
    return user_level >= min_level


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """
    Devuelve True si `actor_role` tiene permiso para asignar `target_role`.

    Reglas:
    - Solo puede asignar roles con nivel <= el propio.
    - Excepción: administrador puede asignar hasta administrador (no desarrollador).
    - desarrollador puede asignar cualquier rol (incluido desarrollador).
    """
    actor_level = ROLE_HIERARCHY.get(actor_role, 0)
    target_level = ROLE_HIERARCHY.get(target_role, 0)

    if actor_role == Role.DESARROLLADOR:
        return True
    if actor_role in (Role.ADMINISTRADOR, Role.DIRECTOR):
        # administrador/director no puede crear/asignar desarrollador
        return target_level <= ROLE_HIERARCHY[Role.ADMINISTRADOR]
    # lider y operador no pueden asignar roles
    return False


class CurrentUser(BaseModel):
    """Modelo que representa el usuario autenticado en el contexto de cada request."""
    uid: str
    email: str
    role: str
    grupo: Optional[str] = None
    full_name: Optional[str] = None

    @property
    def level(self) -> int:
        return ROLE_HIERARCHY.get(self.role, 0)

    def has_role(self, *roles: str) -> bool:
        """True si el usuario tiene alguno de los roles indicados."""
        return self.role in roles

    def at_least(self, min_role: str) -> bool:
        """True si el usuario tiene nivel >= min_role."""
        return role_at_least(self.role, min_role)
