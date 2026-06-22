"""
Core Pydantic v2 domain models for API Artefacto 360 DAGMA.

These models reflect the shapes actually used in route handlers and Firestore
documents.  ConfigDict(extra='allow') is used throughout so that existing
Firestore documents with additional fields do not fail validation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------------------------------------------------------------------------
# PersonalAsignado — operative staff member attached to an activity
# Fields observed in: artefacto_360_routes.py (personal_asignado list items,
#   personal_operativo collection, personal_asignado_actividad collection)
# ---------------------------------------------------------------------------

class PersonalAsignado(BaseModel):
    """An operative staff member assigned to an activity."""

    model_config = ConfigDict(extra="allow")

    nombre_completo: Optional[str] = Field(None, description="Full name of the operative")
    email: Optional[str] = Field(None, description="Email address")
    grupo: Optional[str] = Field(None, description="Operative group (flora_urbana, vivero, etc.)")
    numero_contacto: Optional[str] = Field(None, description="Contact phone number")


# ---------------------------------------------------------------------------
# ActividadBase / ActividadCreate — activity in plan_distrito_verde
# Fields observed in: ConvocarActividadRequest, actividad_data dict,
#   GET /actividades response
# ---------------------------------------------------------------------------

class ActividadBase(BaseModel):
    """Shared fields for an activity record."""

    model_config = ConfigDict(extra="allow")

    fecha_actividad: Optional[str] = Field(None, description="Activity date (dd/mm/yyyy)")
    hora_encuentro: Optional[str] = Field(None, description="Meeting time (hh:mm)")
    tipo_jornada: Optional[str] = Field(None, description="Type of workday/shift")
    duracion_actividad: Optional[float] = Field(None, gt=0, description="Duration in hours")
    grupos_requeridos: Optional[List[str]] = Field(default=None, description="Required operative groups")
    lider_actividad: Optional[str] = Field(None, description="Activity leader name")
    lider_actividad_email: Optional[str] = Field(None, description="Activity leader email")
    lider_actividad_telefono: Optional[str] = Field(None, description="Activity leader phone")
    punto_encuentro: Optional[Dict[str, Any]] = Field(None, description="Meeting point with geometry and address")
    observaciones: Optional[str] = Field(None, description="Additional observations")
    telefono: Optional[str] = Field(None, description="Contact phone")
    objetivo_actividad: Optional[str] = Field(None, description="Activity objective")
    email: Optional[str] = Field(None, description="Contact email")
    estado_actividad: Optional[str] = Field(None, description="Activity status (Programada, En Ejecución, etc.)")
    personal_asignado: Optional[List[PersonalAsignado]] = Field(
        default=None, description="Operative staff assigned to this activity"
    )


class ActividadCreate(ActividadBase):
    """Fields required to create a new activity (POST /programar_actividad)."""

    model_config = ConfigDict(extra="allow")

    fecha_actividad: str = Field(..., description="Activity date (dd/mm/yyyy)")
    hora_encuentro: str = Field(..., description="Meeting time (hh:mm)")
    tipo_jornada: str = Field(..., description="Type of workday/shift")
    duracion_actividad: float = Field(..., gt=0, description="Duration in hours")
    grupos_requeridos: List[str] = Field(..., description="Required operative groups")
    lider_actividad: str = Field(..., description="Activity leader name")
    punto_encuentro: Dict[str, Any] = Field(..., description="Meeting point with geometry and address")
    telefono: str = Field(..., description="Contact phone")
    objetivo_actividad: str = Field(..., description="Activity objective")
    email: str = Field(..., description="Contact email")


# ---------------------------------------------------------------------------
# ReporteBase / ReporteCreate — intervention report
# Fields observed in: _post_reporte_intervencion handler, reporte_data dict,
#   GET /grupos/{grupo_key}/reportes_intervenciones
# ---------------------------------------------------------------------------

class ReporteBase(BaseModel):
    """Shared fields for an intervention report."""

    model_config = ConfigDict(extra="allow")

    tipo_intervencion: Optional[str] = Field(None, description="Type of intervention (e.g. Poda, Siembra)")
    descripcion_intervencion: Optional[str] = Field(None, description="Detailed description")
    direccion: Optional[str] = Field(None, description="Physical address where it took place")
    grupo: Optional[str] = Field(None, description="Operative group (canonical key)")
    id_actividad: Optional[str] = Field(None, description="Associated activity ID")
    observaciones: Optional[str] = Field(None, description="Additional observations")
    coordinates_type: Optional[str] = Field(None, description="GeoJSON geometry type (Point, LineString, Polygon, etc.)")
    coordinates_data: Optional[str] = Field(None, description="GeoJSON coordinates as JSON string")
    coordenadas_origen: Optional[str] = Field(None, description="Coordinate source: 'gps' or 'manual'")
    # Group-specific optional fields
    arboles_data: Optional[str] = Field(None, description="[Flora urbana] JSON array of trees")
    tipos_plantas: Optional[str] = Field(None, description="[Vivero] JSON dict of plant species and counts")
    unidades_impactadas: Optional[int] = Field(None, description="[Gobernanza/Ecosistemas/UMATA] Impacted units count")
    unidad_medida: Optional[str] = Field(None, description="[Ecosistemas] Unit of measure (m², hectáreas, individuos)")


class ReporteCreate(ReporteBase):
    """Fields required or expected when creating a new report.

    Note: grupo comes from the URL path in the unified endpoint; registrado_por
    is resolved from the auth token — neither is required in the body.
    """

    model_config = ConfigDict(extra="allow")

    tipo_intervencion: str = Field(..., description="Type of intervention")


# ---------------------------------------------------------------------------
# GrupoBase — operational group (grupos collection)
# Fields observed in: crear_grupo handler, grupos collection doc_data
# ---------------------------------------------------------------------------

class LiderGrupo(BaseModel):
    """Leader sub-document inside a group record."""

    model_config = ConfigDict(extra="allow")

    nombre: Optional[str] = Field(None, description="Leader name")
    email: Optional[str] = Field(None, description="Leader email")
    numero_contacto: Optional[str] = Field(None, description="Leader contact phone")


class GrupoBase(BaseModel):
    """Operational group (flora_urbana, vivero, gobernanza, ecosistemas, umata)."""

    model_config = ConfigDict(extra="allow")

    nombre: str = Field(..., description="Group name")
    email: Optional[str] = Field(None, description="Group contact email")
    lider: Optional[LiderGrupo] = Field(None, description="Group leader information")


# ---------------------------------------------------------------------------
# UserBase / UserProfile — user record (users collection)
# Fields observed in: auth_routes.py Firestore doc, validate-session response,
#   UserRegistrationRequest, UpdateProfileRequest, CurrentUser model
# ---------------------------------------------------------------------------

class UserBase(BaseModel):
    """Core user fields stored in Firestore users collection."""

    model_config = ConfigDict(extra="allow")

    uid: Optional[str] = Field(None, description="Firebase UID")
    email: Optional[str] = Field(None, description="User email")
    full_name: Optional[str] = Field(None, description="Full display name")
    role: Optional[str] = Field(None, description="Canonical role (operador, lider, administrador, director, desarrollador)")
    grupo: Optional[str] = Field(None, description="Operative group the user belongs to")
    cellphone: Optional[str] = Field(None, description="Contact phone number")


class UserProfile(UserBase):
    """Extended user profile as returned by /auth/validate-session and /auth/login."""

    model_config = ConfigDict(extra="allow")

    photoURL: Optional[str] = Field(None, description="Profile photo URL (S3 presigned or Google)")
    email_verified: Optional[bool] = Field(None, description="Whether email is verified in Firebase")
    disabled: Optional[bool] = Field(None, description="Whether the account is disabled")
    needs_review: Optional[bool] = Field(None, description="True if the account is pending admin review")
    photo_s3_key: Optional[str] = Field(None, description="S3 key for custom profile photo")
