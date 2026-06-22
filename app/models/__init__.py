"""
Models package for API Artefacto 360 DAGMA.
"""

from app.models.roles import Role, CurrentUser, ROLE_HIERARCHY, LEGACY_ROLE_MAP, normalize_role, role_at_least, can_assign_role
from app.models.validation import CoordinatesModel, ArbolModel, ArbolesDataModel
from app.models.schemas import (
    PersonalAsignado,
    ActividadBase,
    ActividadCreate,
    ReporteBase,
    ReporteCreate,
    LiderGrupo,
    GrupoBase,
    UserBase,
    UserProfile,
)
