"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query, Response, Depends, Body, BackgroundTasks
from typing import List, Optional
from enum import Enum
from app.models.roles import Role, CurrentUser, normalize_role
from app.deps.authz import get_current_user, require_min_role
import asyncio
from datetime import datetime, timezone
import pytz
import json
import uuid

# Conjunto para mantener referencias fuertes a background tasks (evita garbage collection)
_background_tasks = set()


def _resolver_destinatarios_actividad(actividad_data: dict) -> dict:
    """Retorna {email_lower: nombre} para el líder (si tiene email guardado) y el personal
    asignado operativo de una actividad.

    NO incluye al coordinador que programó la actividad ni a líderes de grupos
    (estos últimos requieren consulta a Firestore; se resuelven por separado).
    """
    result: dict[str, str] = {}

    def _add(email: str, nombre: str) -> None:
        e = (email or "").strip().lower()
        if e and "@" in e:
            result.setdefault(e, (nombre or "").strip() or e)

    # Líder de actividad (solo si el campo email está guardado explícitamente en Firestore)
    _add(actividad_data.get("lider_actividad_email", ""), actividad_data.get("lider_actividad", ""))
    # Personal operativo asignado
    for p in (actividad_data.get("personal_asignado") or []):
        _add(p.get("email", ""), p.get("nombre_completo", ""))
    return result


async def _resolver_lider_actividad_async(actividad_data: dict) -> tuple[str, str]:
    """Resuelve (email, nombre) del líder de la actividad.

    Primero intenta `lider_actividad_email` (campo directo en Firestore).
    Si está vacío, busca por nombre completo en la colección `users` filtrando
    por rol=lider. Retorna ("", nombre) si no se puede resolver el email.
    """
    lider_email = (actividad_data.get("lider_actividad_email") or "").strip().lower()
    lider_nombre = (actividad_data.get("lider_actividad") or "").strip()
    if lider_email and "@" in lider_email:
        return lider_email, lider_nombre
    if not lider_nombre:
        return "", ""
    target = strip_accents(lider_nombre).strip().lower()
    try:
        seen: set[str] = set()
        for rol_field in ("role", "rol"):
            try:
                query = (
                    db.collection("users")
                    .where(rol_field, "in", ["lider", "líder", "LIDER", "LÍDER"])
                    .limit(500)
                )
                docs = await stream_to_list(query)
            except Exception:
                continue
            for udoc in docs:
                if udoc.id in seen:
                    continue
                seen.add(udoc.id)
                ud = udoc.to_dict() or {}
                for campo_nombre in ("full_name", "nombre_completo", "displayName"):
                    nombre_db = (ud.get(campo_nombre) or "").strip()
                    if nombre_db and strip_accents(nombre_db).strip().lower() == target:
                        email_found = (ud.get("email") or "").strip().lower()
                        if email_found and "@" in email_found:
                            return email_found, nombre_db or lider_nombre
    except Exception as e:
        logger.warning(f"[NOTIFY] Error resolviendo email del líder '{lider_nombre}': {e}")
    return "", lider_nombre


async def _resolver_lider_telefono_async(lider_email: str, lider_nombre: str) -> "str | None":
    """Resuelve el teléfono del líder de actividad.

    Orden de búsqueda:
    1. ``users.cellphone`` / ``users.telefono`` (por email)
    2. ``personal_operativo.numero_contacto`` (por email)
    3. ``personal_operativo.numero_contacto`` (por nombre_completo exacto)

    Retorna el teléfono como string o None si no se encuentra.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)
    # 1. Buscar en users por email
    if lider_email and "@" in lider_email:
        try:
            docs = await stream_to_list(
                db.collection("users").where("email", "==", lider_email).limit(1)
            )
            for udoc in docs:
                ud = udoc.to_dict() or {}
                tel_raw = ud.get("cellphone") or ud.get("telefono")
                if tel_raw not in (None, ""):
                    return str(tel_raw).strip()
        except Exception:
            pass
        # 2. Buscar en personal_operativo por email
        try:
            docs = await stream_to_list(
                db.collection("personal_operativo").where("email", "==", lider_email).limit(1)
            )
            for udoc in docs:
                ud = udoc.to_dict() or {}
                tel_raw = ud.get("numero_contacto")
                if tel_raw not in (None, ""):
                    return str(tel_raw).strip()
        except Exception:
            pass
    # 3. Buscar en personal_operativo por nombre_completo
    if lider_nombre:
        try:
            docs = await stream_to_list(
                db.collection("personal_operativo")
                .where("nombre_completo", "==", lider_nombre.strip())
                .limit(1)
            )
            for udoc in docs:
                ud = udoc.to_dict() or {}
                tel_raw = ud.get("numero_contacto")
                if tel_raw not in (None, ""):
                    return str(tel_raw).strip()
        except Exception:
            pass
    _logger.debug(
        f"[LIDER-TEL] No se pudo resolver teléfono del líder "
        f"email='{lider_email}' nombre='{lider_nombre}'"
    )
    return None


async def _recuperar_email_persona(nombre: str, email_actual: str) -> "str | None":
    """Intenta recuperar un email válido para una persona del personal_asignado.

    Si `email_actual` ya es válido, lo devuelve de inmediato.
    Si no, busca por nombre_completo en `users` (insensible a tildes/mayúsculas)
    y luego en `personal_operativo`. Devuelve None si no encuentra nada.
    """
    if email_actual and "@" in email_actual:
        return email_actual.strip().lower()
    if not nombre or not nombre.strip():
        return None
    target = strip_accents(nombre.strip()).lower()
    # 1. Buscar en users por nombre
    try:
        docs = await stream_to_list(db.collection("users").limit(500))
        for udoc in docs:
            ud = udoc.to_dict() or {}
            for campo in ("full_name", "nombre_completo", "displayName"):
                nombre_db = (ud.get(campo) or "").strip()
                if nombre_db and strip_accents(nombre_db).lower() == target:
                    email_found = (ud.get("email") or "").strip().lower()
                    if email_found and "@" in email_found:
                        return email_found
    except Exception as e:
        logger.warning(f"[NOTIFY] Error buscando email de '{nombre}' en users: {e}")
    # 2. Buscar en personal_operativo por nombre_completo
    try:
        docs = await stream_to_list(
            db.collection("personal_operativo")
            .where("nombre_completo", "==", nombre.strip())
            .limit(5)
        )
        for udoc in docs:
            ud = udoc.to_dict() or {}
            email_found = (ud.get("email") or "").strip().lower()
            if email_found and "@" in email_found:
                return email_found
    except Exception as e:
        logger.warning(f"[NOTIFY] Error buscando email de '{nombre}' en personal_operativo: {e}")
    return None


async def _resolver_destinatarios_actividad_async(actividad_data: dict) -> dict:
    """Versión async de _resolver_destinatarios_actividad que recupera emails faltantes.

    Para cada persona en personal_asignado sin email embebido, intenta recuperar
    su dirección consultando primero `users` y luego `personal_operativo`.
    El setdefault por email_lower garantiza deduplicación.
    """
    # Partir del resultado sync (líder + personal con email embebido)
    result: dict[str, str] = _resolver_destinatarios_actividad(actividad_data)

    # Recuperar emails faltantes del personal_asignado
    personal = actividad_data.get("personal_asignado") or []
    nombres_sin_email = [
        (p.get("nombre_completo", ""), p.get("email", ""))
        for p in personal
        if not (p.get("email", "") and "@" in p.get("email", ""))
    ]
    for nombre, email_actual in nombres_sin_email:
        if not nombre:
            continue
        recovered = await _recuperar_email_persona(nombre, email_actual)
        if recovered:
            result.setdefault(recovered, nombre.strip() or recovered)
    return result


import os
import io
import logging

from pydantic import BaseModel, Field
from app.models.validation import CoordinatesModel, ArbolesDataModel
from app.utils.firestore_async import run_blocking, stream_to_list
from app.utils.text_utils import normalize_grupo, grupos_match, strip_accents, canonical_grupo_key

# Importar configuración de Firebase y S3/Storage
from app.firebase_config import db
try:
    from google.cloud.firestore_v1.transaction import transactional as _fs_transactional
except ImportError:
    _fs_transactional = None
import boto3
from botocore.exceptions import ClientError

# Servicios de notificación Google (Gmail + Calendar)
from app.services.gmail_service import (
    send_activity_confirmation_email,
    send_assignment_notification_email,
    send_removal_notification_email,
    send_leaders_notification_email,
    send_activity_leader_assigned_email,
    send_assignment_summary_leader_email,
    send_activity_cancellation_email,
    send_activity_modification_email,
)
from app.services.calendar_service import create_activity_event, sync_event_personnel

logger = logging.getLogger(__name__)

# Importar librerías para intersecciones geográficas

from app.utils.spatial_index import SpatialIndex

router = APIRouter(tags=["Artefacto de Captura DAGMA"])

# ==================== CARGAR GEOJSONS ====================#
# Cargar los archivos GeoJSON al iniciar la aplicación
_BASEMAPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'basemaps', 'cartografia_base')
_COMUNAS_FILE = os.path.join(_BASEMAPS_DIR, 'comunas_corregimientos.geojson')
_BARRIOS_FILE = os.path.join(_BASEMAPS_DIR, 'barrios_veredas.geojson')

def _load_geojson_features(filepath: str) -> dict:
    """Carga un archivo GeoJSON y retorna un diccionario con las características"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            geojson_data = json.load(f)
        
        features_dict = {}
        if 'features' in geojson_data:
            for feature in geojson_data['features']:
                if 'properties' in feature and 'geometry' in feature:
                    features_dict[len(features_dict)] = feature
        
        return features_dict
    except Exception as e:
        logger.warning(f"Error cargando GeoJSON {filepath}: {str(e)}")
        return {}

# Cargar los datos al iniciar

_COMUNAS_FEATURES = _load_geojson_features(_COMUNAS_FILE)
_BARRIOS_FEATURES = _load_geojson_features(_BARRIOS_FILE)

# Crear índices espaciales para búsquedas eficientes
_COMUNAS_INDEX = SpatialIndex(_COMUNAS_FEATURES, 'comuna_corregimiento')
_BARRIOS_INDEX = SpatialIndex(_BARRIOS_FEATURES, 'barrio_vereda')

logger.info("Cargadas %d comunas/corregimientos", len(_COMUNAS_FEATURES))
logger.info("Cargados %d barrios/veredas", len(_BARRIOS_FEATURES))



def get_location_from_coordinates(coordinates: List) -> tuple:
    """
    Realiza intersecciones geográficas para encontrar la comuna/corregimiento y barrio/vereda
    usando índices espaciales STRtree para mayor eficiencia.
    Args:
        coordinates: Array de coordenadas [lon, lat] para Point
    Returns:
        Tupla con (comuna_corregimiento, barrio_vereda) o (None, None) si no encuentra
    """
    if not coordinates or len(coordinates) != 2:
        return None, None
    try:
        comuna_corregimiento = _COMUNAS_INDEX.query(coordinates)
        barrio_vereda = _BARRIOS_INDEX.query(coordinates)
        return comuna_corregimiento, barrio_vereda
    except Exception as e:
        logger.error(f"Error en intersección geográfica (índice): {str(e)}")
        return None, None


# ==================== FUNCIONES AUXILIARES ====================#



def validate_coordinates(coordinates: list, geometry_type: str) -> bool:
    """
    Valida coordenadas usando modelo Pydantic
    """
    CoordinatesModel(coordinates=coordinates, geometry_type=geometry_type)
    return True



def validate_arboles_data(arboles_data: str) -> list:
    """
    Valida y parsea el campo arboles_data usando modelo Pydantic
    """
    model = ArbolesDataModel(arboles=arboles_data)
    return [a.dict() for a in model.arboles]


def validate_photo_file(file: UploadFile) -> bool:
    """
    Valida que el archivo sea una imagen válida
    """
    # Validar tipo MIME
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic"]
    if file.content_type not in allowed_types:
        raise ValueError(f"Tipo de archivo no permitido: {file.content_type}. Permitidos: {', '.join(allowed_types)}")
    
    # Validar extensión
    allowed_extensions = [".jpg", ".jpeg", ".png", ".webp", ".heic"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise ValueError(f"Extensión no permitida: {file_ext}")
    
    return True


def get_s3_client():
    """
    Crear cliente de S3 con las credenciales del entorno
    """
    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_region = os.getenv('AWS_REGION', 'us-east-1')
    
    if not aws_access_key or not aws_secret_key:
        raise ValueError("Credenciales de AWS no configuradas. Verifica AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY")
    
    return boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region
    )


# ==================== HELPERS S3: Upload y Presigned URLs ====================#

# Límite de tamaño por foto (defensa contra OOM/DoS). Coincide con el límite del
# frontend para conversión a canvas (15 MB).
MAX_PHOTO_BYTES = 15 * 1024 * 1024


async def upload_photos_to_s3(photos: List[UploadFile], grupo: str, reporte_id: str, s3_client, bucket_name: str) -> list:
    """
    Sube fotos a S3 y retorna lista de dicts con metadata rica de cada archivo.
    Estructura S3 key: reportes/{grupo}/{reporte_id}/{timestamp}_{i}_{filename}
    Ahora usa cargas concurrentes con asyncio.gather.
    """
    documentos = []

    async def upload_single_photo(i, photo):
        ts_photo = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_filename = "".join(c for c in photo.filename if c.isalnum() or c in "._-")
        photo_filename = f"{ts_photo}_{i}_{safe_filename}"
        s3_key = f"reportes/{grupo}/{reporte_id}/{photo_filename}"
        if s3_client:
            try:
                # Rechazar fotos demasiado grandes antes de cargarlas en memoria
                # cuando el tamaño del part está disponible (Starlette UploadFile.size).
                if getattr(photo, "size", None) and photo.size > MAX_PHOTO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"La foto '{photo.filename}' supera el límite de {MAX_PHOTO_BYTES // (1024 * 1024)} MB",
                    )
                photo_content = await photo.read()
                if len(photo_content) > MAX_PHOTO_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"La foto '{photo.filename}' supera el límite de {MAX_PHOTO_BYTES // (1024 * 1024)} MB",
                    )
                content_type = photo.content_type or "application/octet-stream"
                # Ejecutar upload S3 (síncrono) en thread para no bloquear el event loop
                await asyncio.to_thread(
                    s3_client.upload_fileobj,
                    io.BytesIO(photo_content),
                    bucket_name,
                    s3_key,
                    {"ContentType": content_type},
                )
                doc_meta = {
                    "filename": photo.filename,
                    "s3_key": s3_key,
                    "s3_url": f"https://{bucket_name}.s3.amazonaws.com/{s3_key}",
                    "content_type": photo.content_type or "application/octet-stream",
                    "size": len(photo_content),
                    "upload_date": datetime.now(timezone.utc).isoformat()
                }
                await photo.seek(0)
                return doc_meta
            except ClientError as e:
                logger.error(f"Error subiendo foto a S3: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error subiendo foto '{photo.filename}' a S3: {str(e)}"
                )
        else:
            doc_meta = {
                "filename": photo.filename,
                "s3_key": s3_key,
                "s3_url": f"https://{bucket_name}.s3.amazonaws.com/{s3_key}",
                "content_type": photo.content_type or "application/octet-stream",
                "size": 0,
                "upload_date": datetime.now(timezone.utc).isoformat()
            }
            logger.debug(f"Modo desarrollo: URL ficticia generada para {photo.filename}")
            return doc_meta

    # Deduplica: el frontend envía la misma foto dos veces cuando hay una sola
    # (workaround para la coerción de FastAPI de UploadFile → List[UploadFile]).
    seen_names: set = set()
    unique_photos = []
    for photo in photos:
        if photo.filename not in seen_names:
            seen_names.add(photo.filename)
            unique_photos.append(photo)
    photos = unique_photos

    # Ejecutar todas las cargas concurrentemente. Con return_exceptions=True
    # ninguna excepción cancela las demás tareas: recolectamos éxitos y errores,
    # limpiamos las fotos que sí se subieron y propagamos el primer error.
    tasks = [upload_single_photo(i, photo) for i, photo in enumerate(photos)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    documentos = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]

    if errors:
        if s3_client:
            for doc in documentos:
                try:
                    s3_client.delete_object(Bucket=bucket_name, Key=doc["s3_key"])
                except Exception as cleanup_err:
                    logger.warning(
                        f"No se pudo limpiar foto huérfana en S3 (key={doc.get('s3_key')}): {cleanup_err}"
                    )
        first = errors[0]
        if isinstance(first, HTTPException):
            raise first
        raise HTTPException(status_code=500, detail=f"Error subiendo fotos: {first}")

    return list(documentos)


def generar_documentos_con_enlaces(documentos: list, s3_client, bucket_name: str) -> list:
    """
    Enriquece lista de documentos con presigned URLs para descarga y visualización.
    Input: lista de dicts {filename, s3_key, s3_url, content_type, size, upload_date}
    Output: lista enriquecida con url_descarga, url_visualizar, url_presigned, url_expiration_seconds
    """
    EXPIRATION = 3600
    resultado = []
    for doc in documentos:
        enriched = {
            "filename": doc.get("filename", os.path.basename(doc.get("s3_key", ""))),
            "s3_key": doc.get("s3_key", ""),
            "s3_url": doc.get("s3_url", ""),
            "content_type": doc.get("content_type", "application/octet-stream"),
            "size": doc.get("size", 0),
            "upload_date": doc.get("upload_date", ""),
        }
        s3_key = doc.get("s3_key", "")
        filename = enriched["filename"]
        try:
            url_descarga = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': s3_key,
                    'ResponseContentDisposition': f'attachment; filename="{filename}"'
                },
                ExpiresIn=EXPIRATION
            )
            url_visualizar = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': s3_key,
                    'ResponseContentDisposition': 'inline'
                },
                ExpiresIn=EXPIRATION
            )
            enriched["url_descarga"] = url_descarga
            enriched["url_visualizar"] = url_visualizar
            enriched["url_presigned"] = url_visualizar
            enriched["url_expiration_seconds"] = EXPIRATION
        except Exception as e:
            logger.warning(f"Error generando presigned URL para {s3_key}: {str(e)}")
            enriched["url_descarga"] = enriched["s3_url"]
            enriched["url_visualizar"] = enriched["s3_url"]
            enriched["url_presigned"] = enriched["s3_url"]
            enriched["url_expiration_seconds"] = 0
        resultado.append(enriched)
    return resultado


def convertir_photosUrl_a_documentos(photos_urls: list, s3_client, bucket_name: str) -> list:
    """
    Backward compatibility: convierte lista de URLs públicas S3 (formato antiguo photosUrl)
    a estructura de documentos rica para poder generar presigned URLs.
    """
    documentos = []
    for url in photos_urls:
        if not url or not isinstance(url, str):
            continue
        s3_key = url.split('.com/')[-1] if '.com/' in url else url
        filename = os.path.basename(s3_key)
        doc_meta = {
            "filename": filename,
            "s3_key": s3_key,
            "s3_url": url,
            "content_type": "application/octet-stream",
            "size": 0,
            "upload_date": ""
        }
        if s3_client:
            try:
                head = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
                doc_meta["content_type"] = head.get("ContentType", "application/octet-stream")
                doc_meta["size"] = head.get("ContentLength", 0)
                last_modified = head.get("LastModified")
                if last_modified:
                    doc_meta["upload_date"] = last_modified.isoformat()
            except Exception as e:
                logger.warning(f"No se pudo obtener metadata de S3 para {s3_key}: {str(e)}")
        documentos.append(doc_meta)
    return documentos


def enriquecer_reportes_con_enlaces(reportes: list, s3_client, bucket_name: str) -> list:
    """
    Enriquece lista de reportes con documentos_con_enlaces y photosUrl (presigned).
    Maneja tanto formato nuevo (documentos) como legacy (photosUrl).
    """
    for reporte in reportes:
        # Si no existe 'documentos' pero sí 'photosUrl', convertir y asignar
        if not reporte.get("documentos") and reporte.get("photosUrl"):
            reporte["documentos"] = convertir_photosUrl_a_documentos(
                reporte["photosUrl"], s3_client, bucket_name
            )
        if reporte.get("documentos"):
            docs_enriquecidos = generar_documentos_con_enlaces(
                reporte["documentos"], s3_client, bucket_name
            )
            reporte["documentos_con_enlaces"] = docs_enriquecidos
            reporte["total_documentos"] = len(reporte["documentos"])
            # Poblar photosUrl con URLs presigned para compatibilidad con slim mode
            reporte["photosUrl"] = [
                d.get("url_visualizar") or d.get("url_presigned") or d.get("s3_url") or ""
                for d in docs_enriquecidos
                if d.get("url_visualizar") or d.get("url_presigned") or d.get("s3_url")
            ]
        else:
            reporte["documentos_con_enlaces"] = []
            reporte["photosUrl"] = []
            reporte["total_documentos"] = 0
    return reportes


# ==================== MODELOS ====================#
class ReconocimientoResponse(BaseModel):
    """Modelo de respuesta para reconocimientos"""
    success: bool
    id: Optional[str] = None
    message: str
    nombre_parque: Optional[str] = None
    coordinates: Optional[dict] = None
    photosUrl: Optional[List[str]] = None
    photos_uploaded: Optional[int] = None
    documentos_con_enlaces: Optional[List[dict]] = None
    timestamp: str
    numero_registro: Optional[int] = None


# ==================== CONFIGURACIÓN DE GRUPOS OPERATIVOS ====================#

# Colección única para todos los reportes de intervención de todos los grupos.
# El campo "grupo" dentro de cada documento es el discriminador.
COLLECTION_REPORTES_INTERVENCIONES = "reportes_intervenciones"
COLLECTION_COUNTERS = "counters"
_COUNTER_DOC_REPORTES = "reportes_intervenciones"


def _get_next_numero_registro() -> int:
    """Lee e incrementa el contador global de reportes de intervención de forma síncrona."""
    counter_ref = db.collection(COLLECTION_COUNTERS).document(_COUNTER_DOC_REPORTES)
    try:
        if _fs_transactional is not None:
            @_fs_transactional
            def _increment(transaction, ref):
                snap = ref.get(transaction=transaction)
                n = ((snap.to_dict() or {}).get("total", 0)) + 1
                transaction.set(ref, {"total": n}, merge=True)
                return n
            return _increment(db.transaction(), counter_ref)
        else:
            # Fallback sin transacción (baja probabilidad de colisión en este sistema)
            snap = counter_ref.get()
            n = ((snap.to_dict() or {}).get("total", 0)) + 1
            counter_ref.set({"total": n}, merge=True)
            return n
    except Exception as e:
        logger.warning(f"[COUNTER] Error incrementing numero_registro: {e}")
        return None

GRUPOS_CONFIG = {
    "flora_urbana": {
        "display_name": "Flora urbana",
        # s3_prefix stays "cuadrilla" — changing it would orphan existing photos in S3
        "s3_prefix": "cuadrilla",
    },
    "vivero": {
        "display_name": "Vivero",
        "s3_prefix": "vivero",
    },
    "gobernanza": {
        "display_name": "Gobernanza",
        "s3_prefix": "gobernanza",
    },
    "ecosistemas": {
        "display_name": "Ecosistemas",
        "s3_prefix": "ecosistemas",
    },
    "umata": {
        "display_name": "UMATA",
        "s3_prefix": "umata",
    },
}

GRUPOS_VALIDOS = list(GRUPOS_CONFIG.keys())


def get_grupo_config(grupo: str) -> dict:
    """Obtiene la configuración de un grupo operativo o lanza 404."""
    config = GRUPOS_CONFIG.get(canonical_grupo_key(grupo))
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Grupo '{grupo}' no encontrado. Grupos válidos: {', '.join(GRUPOS_VALIDOS)}"
        )
    return config


def _limpiar_reporte(data: dict) -> dict:
    """
    Elimina claves con valor None del documento antes de enviarlo al cliente.
    Evita enviar campos vacíos de otros grupos (ej: 'arboles: null' en vivero).
    """
    return {k: v for k, v in data.items() if v is not None}


def validate_grupo_specific_fields(
    grupo_key: str,
    arboles_data: Optional[str],
    tipos_plantas: Optional[str],
    unidades_impactadas: Optional[int],
    unidad_medida: Optional[str],
) -> dict:
    """
    Valida y procesa los campos específicos de cada grupo.
    Retorna un dict con los campos específicos para incluir en reporte_data.
    """
    extra = {}

    if grupo_key == "flora_urbana":
        arboles = None
        if arboles_data:
            try:
                arboles = validate_arboles_data(arboles_data)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error en arboles_data: {str(e)}"
                )
        extra["arboles"] = arboles

    elif grupo_key == "vivero":
        tipos_plantas_dict = None
        cantidad_total_plantas = 0
        if tipos_plantas:
            try:
                tipos_plantas_dict = json.loads(tipos_plantas)
                if not isinstance(tipos_plantas_dict, dict):
                    raise HTTPException(
                        status_code=400,
                        detail="tipos_plantas debe ser un objeto JSON (diccionario). Ej: {\"Guayacán\": 10, \"Ceiba\": 5}"
                    )
                for planta, cantidad in tipos_plantas_dict.items():
                    if not isinstance(cantidad, (int, float)) or cantidad < 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"La cantidad para '{planta}' debe ser un número positivo. Recibido: {cantidad}"
                        )
                    tipos_plantas_dict[planta] = int(cantidad)
                cantidad_total_plantas = sum(tipos_plantas_dict.values())
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Formato JSON inválido en tipos_plantas. Envíe como '{{\"Guayacán\": 10, \"Ceiba\": 5}}'. Recibido: '{tipos_plantas}'"
                )
        extra["tipos_plantas"] = tipos_plantas_dict
        extra["cantidad_total_plantas"] = cantidad_total_plantas

    elif grupo_key == "gobernanza":
        extra["unidades_impactadas"] = unidades_impactadas

    elif grupo_key == "ecosistemas":
        extra["unidad_medida"] = unidad_medida
        extra["unidades_impactadas"] = unidades_impactadas

    elif grupo_key == "umata":
        extra["unidades_impactadas"] = unidades_impactadas

    return extra


# ==================== ENDPOINTS UNIFICADOS DE REPORTES ====================#

async def _post_reporte_intervencion(
    grupo_key: str,
    tipo_intervencion: Optional[str],
    descripcion_intervencion: Optional[str],
    direccion: Optional[str],
    registrado_por: Optional[str] = None,
    grupo: Optional[str] = None,
    id_actividad: Optional[str] = None,
    observaciones: Optional[str] = None,
    coordinates_type: Optional[str] = None,
    coordinates_data: Optional[str] = None,
    coordenadas_origen: Optional[str] = None,
    photos: Optional[List[UploadFile]] = None,
    # Campos específicos por grupo (todos opcionales)
    arboles_data: Optional[str] = None,
    tipos_plantas: Optional[str] = None,
    unidades_impactadas: Optional[int] = None,
    unidad_medida: Optional[str] = None,
    current_user: Optional[CurrentUser] = None,
) -> ReconocimientoResponse:
    """
    Handler unificado para POST de reportes de intervención.
    Maneja todos los grupos operativos con un solo flujo de lógica.
    """
    config = get_grupo_config(grupo_key)
    s3_prefix = config["s3_prefix"]
    display_name = config["display_name"]

    # Cualquier usuario autenticado puede registrar intervenciones en cualquier grupo.
    # Se usa el nombre completo (o email) del usuario autenticado para registrado_por.
    if current_user is not None:
        registrado_por = current_user.full_name or current_user.email or current_user.uid

    try:
        # Validar tipo de geometría
        valid_geometry_types = ["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"]
        if coordinates_type and coordinates_type not in valid_geometry_types:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de geometría inválido. Permitidos: {', '.join(valid_geometry_types)}"
            )

        # Validar y procesar campos específicos del grupo
        grupo_fields = validate_grupo_specific_fields(
            grupo_key, arboles_data, tipos_plantas, unidades_impactadas, unidad_medida
        )

        # Validar cantidad de fotos
        if photos is not None and len(photos) > 10:
            raise HTTPException(
                status_code=400,
                detail="Máximo 10 fotos por reporte de intervención"
            )

        # Validar cada foto
        if photos:
            for photo in photos:
                try:
                    validate_photo_file(photo)
                except ValueError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Error en archivo '{photo.filename}': {str(e)}"
                    )

        # Generar ID único para el reporte
        reporte_id = str(uuid.uuid4())

        # Obtener número de registro secuencial y permanente
        try:
            numero_registro = await asyncio.to_thread(_get_next_numero_registro)
            if numero_registro is None:
                logger.warning("[COUNTER] _get_next_numero_registro() returned None")
        except Exception as e:
            logger.warning(f"[COUNTER] Error getting numero_registro: {e}", exc_info=True)
            numero_registro = None

        # Timestamp con zona horaria de Colombia
        tz_col = pytz.timezone("America/Bogota")
        timestamp = datetime.now(tz_col).isoformat()

        # Parsear y validar coordenadas
        geometry = None
        coordinates = None
        comuna_corregimiento = None
        barrio_vereda = None

        if coordinates_data and coordinates_type:
            try:
                logger.debug(f"Recibido coordinates_data: {repr(coordinates_data)}")
                logger.debug(f"Tipo: {type(coordinates_data)}, Long: {len(coordinates_data) if coordinates_data else 0}")

                coordinates_str = coordinates_data.strip()

                if not coordinates_str.startswith('['):
                    parts = coordinates_str.split(',')
                    if len(parts) == 2:
                        try:
                            lon = float(parts[0].strip())
                            lat = float(parts[1].strip())
                            coordinates = [lon, lat]
                            logger.debug(f"Coordenadas parseadas como lon,lat: {coordinates}")
                        except ValueError:
                            raise json.JSONDecodeError("Formato inválido", coordinates_str, 0)
                    else:
                        raise json.JSONDecodeError("Debe tener formato [lon,lat]", coordinates_str, 0)
                else:
                    coordinates = json.loads(coordinates_str)

                validate_coordinates(coordinates, coordinates_type)

                geometry = {
                    "type": coordinates_type,
                    "coordinates": coordinates
                }

                if coordinates_type == "Point":
                    try:
                        comuna_corregimiento, barrio_vereda = get_location_from_coordinates(coordinates)
                        if comuna_corregimiento:
                            logger.debug(f"Comuna/Corregimiento encontrada: {comuna_corregimiento}")
                        if barrio_vereda:
                            logger.debug(f"Barrio/Vereda encontrado: {barrio_vereda}")
                    except Exception as e:
                        logger.warning(f"Error obteniendo ubicación: {str(e)}")
                else:
                    logger.debug(f"Geolocalización no aplica para geometría {coordinates_type} (solo Point)")

            except json.JSONDecodeError as e:
                logger.warning(f"Error JSON en coordinates_data: {str(e)}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Formato de coordenadas inválido. Envíe como '[lon,lat]' (ej: '[-76.5225,3.4516]') o 'lon,lat' (ej: '-76.5225,3.4516'). Recibido: '{coordinates_data}'"
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error en coordenadas: {str(e)}"
                )

        # Obtener cliente S3 y bucket name
        bucket_name = os.getenv('S3_BUCKET_NAME', '360-dagma-photos')

        # Subir fotos a S3
        documentos = []
        s3_client = None

        if photos:
            try:
                s3_client = get_s3_client()
            except ValueError as e:
                logger.error(f"[S3-FAIL] Credenciales S3 no configuradas: {e}")
                raise HTTPException(
                    status_code=500,
                    detail="Configuración S3 incompleta. Las fotos no pueden guardarse en este momento."
                )
            documentos = await upload_photos_to_s3(photos, s3_prefix, reporte_id, s3_client, bucket_name)

        # Preparar datos comunes para guardar en Firebase
        reporte_data = {
            "id": reporte_id,
            "numero_registro": numero_registro,
            "tipo_intervencion": tipo_intervencion,
            "descripcion_intervencion": descripcion_intervencion,
            "direccion": direccion,
            "registrado_por": registrado_por,
            "grupo": canonical_grupo_key(grupo_key),  # Canonical key para queries consistentes
            "id_actividad": id_actividad,
            "observaciones": observaciones or "",
            "coordinates": geometry,
            "coordenadas_origen": (coordenadas_origen or "gps").strip().lower() or "gps",
            "comuna_corregimiento": comuna_corregimiento,
            "barrio_vereda": barrio_vereda,
            "documentos": documentos,
            "photos_uploaded": len(documentos),
            "timestamp": timestamp
        }

        # Merge campos específicos del grupo
        reporte_data.update(grupo_fields)

        # Eliminar campos None antes de guardar: evita guardar claves vacías de otros grupos
        reporte_data = {k: v for k, v in reporte_data.items() if v is not None}

        # Guardar en Firebase (colección unificada)
        try:
            db.collection(COLLECTION_REPORTES_INTERVENCIONES).document(reporte_id).set(reporte_data)
            logger.info(f"Reporte de intervención grupo {display_name} {reporte_id} guardado en Firebase")
        except Exception as e:
            logger.error(f"Error guardando en Firebase: {str(e)}")
            if s3_client:
                for doc in documentos:
                    try:
                        s3_client.delete_object(Bucket=bucket_name, Key=doc["s3_key"])
                    except Exception as cleanup_err:
                        logger.warning(
                            f"No se pudo limpiar foto huérfana en S3 (key={doc.get('s3_key')}): {cleanup_err}"
                        )
            raise HTTPException(
                status_code=500,
                detail=f"Error guardando en Firebase: {str(e)}"
            )

        photos_urls = [d["s3_url"] for d in documentos]
        docs_enriquecidos = generar_documentos_con_enlaces(documentos, s3_client, bucket_name) if s3_client and documentos else []
        return ReconocimientoResponse(
            success=True,
            id=reporte_id,
            message=f"Reporte de intervención del grupo {display_name} registrado exitosamente",
            nombre_parque=None,
            coordinates=geometry,
            photosUrl=photos_urls,
            photos_uploaded=len(documentos),
            documentos_con_enlaces=docs_enriquecidos,
            timestamp=timestamp,
            numero_registro=numero_registro,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error registrando reporte de intervención del grupo {display_name}: {str(e)}"
        )


async def _get_reportes_intervenciones(
    grupo_key: str,
    id: Optional[str] = None,
    id_actividad: Optional[str] = None,
    grupo: Optional[str] = None,
    current_user: Optional[CurrentUser] = None,
    limit: Optional[int] = 200,
) -> dict:
    """
    Handler unificado para GET de reportes de intervención.
    Maneja todos los grupos operativos con un solo flujo de lógica.
    """
    config = get_grupo_config(grupo_key)
    display_name = config["display_name"]

    # Operador y lider solo pueden ver su propio grupo
    if current_user is not None and not current_user.at_least(Role.ADMINISTRADOR):
        if current_user.grupo and not grupos_match(current_user.grupo, grupo_key):
            raise HTTPException(
                status_code=403,
                detail=f"No tienes acceso al grupo '{grupo_key}'. Solo puedes consultar '{current_user.grupo}'.",
            )

    try:
        reportes_ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
        bucket_name = os.getenv('S3_BUCKET_NAME', '360-dagma-photos')
        s3_client = None
        try:
            s3_client = get_s3_client()
        except Exception:
            logger.warning("No se pudo inicializar S3 client para presigned URLs")

        # Si se proporciona un ID específico, buscar directamente por document ID
        if id:
            doc = await asyncio.to_thread(reportes_ref.document(id).get)
            if doc.exists:
                data = doc.to_dict()
                # Verificar que el reporte pertenece al grupo solicitado
                if canonical_grupo_key(data.get("grupo") or "") != canonical_grupo_key(grupo_key):
                    return {
                        "success": True,
                        "total": 0,
                        "data": [],
                        "filters": {"id": id, "id_actividad": id_actividad, "grupo": grupo_key},
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                data['id'] = doc.id
                reportes = [_limpiar_reporte(data)]
                if s3_client:
                    enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
                return {
                    "success": True,
                    "total": 1,
                    "data": reportes,
                    "filters": {"id": id, "id_actividad": id_actividad, "grupo": grupo_key},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            # Fallback: buscar por campo interno 'id' dentro del grupo
            fallback_q = reportes_ref.where("grupo", "==", grupo_key).where("id", "==", id).limit(50)
            docs = await stream_to_list(fallback_q)
            reportes = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                reportes.append(_limpiar_reporte(data))

            if not reportes:
                return {
                    "success": True,
                    "total": 0,
                    "data": [],
                    "filters": {"id": id, "id_actividad": id_actividad, "grupo": grupo_key},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            if s3_client:
                enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
            return {
                "success": True,
                "total": len(reportes),
                "data": reportes,
                "filters": {"id": id, "id_actividad": id_actividad, "grupo": grupo_key},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        # El filtro de grupo_key es siempre obligatorio en la colección unificada
        query = reportes_ref.where('grupo', '==', grupo_key)

        if id_actividad:
            query = query.where('id_actividad', '==', id_actividad.strip())

        # Cap defensivo: limita la cantidad de documentos traidos para evitar OOM
        # y latencias altas. Por defecto 200; el cliente puede pedir menos via Query.
        effective_limit = limit if (limit is not None and limit > 0) else 200
        query = query.limit(effective_limit)

        # Obtener documentos sin bloquear el event loop
        docs = await stream_to_list(query)

        reportes = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            reportes.append(_limpiar_reporte(data))

        if s3_client:
            enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
        # Enriquecer con datos de actividad asociada
        await _enriquecer_con_actividad(reportes)
        return {
            "success": True,
            "total": len(reportes),
            "data": reportes,
            "filters": {
                "id": id,
                "id_actividad": id_actividad.strip() if id_actividad else None,
                "grupo": grupo_key,
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo reportes de intervención grupo {display_name}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes de intervención del grupo {display_name}: {str(e)}"
        )


# ==================== ENRICH: datos de actividad asociada ====================#


async def _enriquecer_con_actividad(reportes: list) -> None:
    """
    Para cada reporte que tenga id_actividad, consulta Firestore y agrega
    los campos actividad_* directamente en el dict del reporte (in-place).
    Usa asyncio.gather para hacer todas las consultas en paralelo.
    """
    # Recolectar IDs únicos de actividad presentes en el lote
    actividad_ids = list({r.get("id_actividad") for r in reportes if r.get("id_actividad")})
    if not actividad_ids:
        return

    async def _fetch_one(act_id: str) -> tuple:
        try:
            doc = await asyncio.to_thread(
                db.collection("plan_distrito_verde").document(act_id).get
            )
            if doc.exists:
                return act_id, doc.to_dict() or {}
            # fallback: buscar en colección alternativa 'actividades'
            doc2 = await asyncio.to_thread(
                db.collection("actividades").document(act_id).get
            )
            if doc2.exists:
                return act_id, doc2.to_dict() or {}
            return act_id, {}
        except Exception as e:
            logger.debug(f"[ENRICH] Error fetching actividad {act_id}: {e}")
            return act_id, {}

    results = await asyncio.gather(*[_fetch_one(aid) for aid in actividad_ids])
    actividades_map: dict[str, dict] = dict(results)

    for reporte in reportes:
        aid = reporte.get("id_actividad")
        if not aid or aid not in actividades_map:
            continue
        act = actividades_map[aid]
        if not act:
            continue
        # Inyectar campos de actividad como prefijo actividad_*
        reporte["actividad_codigo"] = aid
        reporte["actividad_tipo_jornada"] = act.get("tipo_jornada")
        reporte["actividad_lider"] = act.get("lider_actividad")
        reporte["actividad_estado"] = act.get("estado_actividad")
        reporte["actividad_objetivo"] = act.get("objetivo_actividad")
        reporte["actividad_fecha"] = act.get("fecha_actividad")




# ==================== RUTAS UNIFICADAS: /grupos/{grupo}/... ====================#


class UpdateCoordenadasRequest(BaseModel):
    coordinates_data: str = Field(..., description="Coordenadas JSON array. Point: [-76.5225, 3.4516]")
    coordinates_type: Optional[str] = Field("Point", description="Tipo de geometría GeoJSON")


@router.patch(
    "/grupos/{grupo_key}/reporte_intervencion/{reporte_id}/coordenadas",
    summary="🟡 PATCH | Actualizar Coordenadas de Reporte",
    description="""
## 🟡 PATCH | Actualizar Coordenadas de Reporte

**Propósito**: Actualiza las coordenadas geoespaciales de un reporte de intervención ya guardado.
Recalcula automáticamente `comuna_corregimiento` y `barrio_vereda` con las nuevas coords.

### 📥 Body
```json
{
  "coordinates_data": "[-76.5225, 3.4516]",
  "coordinates_type": "Point"
}
```

### ✅ Respuesta
```json
{
  "success": true,
  "id": "REP-2026-001",
  "message": "Coordenadas actualizadas",
  "coordinates": { "type": "Point", "coordinates": [-76.5225, 3.4516] }
}
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def patch_coordenadas_reporte(
    grupo_key: str,
    reporte_id: str,
    body: UpdateCoordenadasRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.OPERADOR)),
):
    """
    Actualiza las coordenadas de un reporte existente.
    Solo el autor del reporte, su lider de grupo o un administrador puede editar.
    """
    try:
        config = get_grupo_config(grupo_key)
        tz_col = pytz.timezone("America/Bogota")

        # Validar y parsear coordenadas
        try:
            coords_list = json.loads(body.coordinates_data)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status_code=422, detail="coordinates_data debe ser un JSON array válido")

        coordinates_type = (body.coordinates_type or "Point").strip()
        if not validate_coordinates(coords_list, coordinates_type):
            raise HTTPException(status_code=422, detail="Coordenadas inválidas para el tipo de geometría indicado")

        # Obtener documento
        doc_ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES).document(reporte_id.strip())
        doc = await asyncio.to_thread(doc_ref.get)
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Reporte '{reporte_id}' no encontrado")

        data = doc.to_dict() or {}

        # Verificar grupo (canonicalizado para tolerar datos stale como "cuadrilla" → "flora_urbana")
        if canonical_grupo_key(data.get("grupo", "")) != canonical_grupo_key(grupo_key):
            raise HTTPException(status_code=403, detail="El reporte no pertenece al grupo indicado")

        # Verificar autoría: operador solo puede editar sus propios reportes
        if not current_user.at_least(Role.LIDER):
            registrado_por = data.get("registrado_por", "")
            user_identifiers = {
                getattr(current_user, "email", None),
                getattr(current_user, "full_name", None),
                getattr(current_user, "uid", None),
            } - {None}
            if registrado_por not in user_identifiers:
                raise HTTPException(status_code=403, detail="Solo puedes editar tus propios reportes")

        # Construir geometría GeoJSON
        geometry = {"type": coordinates_type, "coordinates": coords_list}

        # Recalcular ubicación administrativa
        comuna_corregimiento, barrio_vereda = get_location_from_coordinates(coords_list)

        update_fields: dict = {
            "coordinates": geometry,
            "coordenadas_origen": "manual",
            "coordenadas_editadas": True,
            "ultima_edicion_coords": datetime.now(tz_col).isoformat(),
        }
        if comuna_corregimiento:
            update_fields["comuna_corregimiento"] = comuna_corregimiento
        if barrio_vereda:
            update_fields["barrio_vereda"] = barrio_vereda

        await asyncio.to_thread(doc_ref.update, update_fields)

        logger.info(
            f"[COORDS] Reporte {reporte_id} coords actualizadas por {current_user.email}: {coords_list}"
        )

        return {
            "success": True,
            "id": reporte_id,
            "message": "Coordenadas actualizadas",
            "coordinates": geometry,
            "comuna_corregimiento": comuna_corregimiento,
            "barrio_vereda": barrio_vereda,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error actualizando coordenadas reporte {reporte_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error actualizando coordenadas: {str(e)}")


# ── Modelo para PATCH campos de texto ──
class UpdateCamposReporteRequest(BaseModel):
    tipo_intervencion: Optional[str] = Field(None, max_length=200, description="Nuevo tipo de intervención")
    descripcion_intervencion: Optional[str] = Field(None, max_length=3000, description="Nueva descripción")
    observaciones: Optional[str] = Field(None, max_length=2000, description="Nuevas observaciones")
    direccion: Optional[str] = Field(None, max_length=500, description="Nueva dirección")


@router.patch(
    "/grupos/{grupo_key}/reporte_intervencion/{reporte_id}",
    summary="🟡 PATCH | Editar Campos de Reporte",
    description="""
## 🟡 PATCH | Editar Campos de Reporte

Actualiza campos de texto editables de un reporte de intervención ya guardado.
Solo actualiza los campos enviados (patch parcial).

### Campos editables
- `tipo_intervencion`: Tipo de intervención
- `descripcion_intervencion`: Descripción detallada
- `observaciones`: Observaciones adicionales
- `direccion`: Dirección física

### 📥 Body
```json
{
  "tipo_intervencion": "Poda correctiva",
  "descripcion_intervencion": "Descripción actualizada...",
  "observaciones": "Sin novedad",
  "direccion": "Calle 5 # 10-20"
}
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def patch_campos_reporte(
    grupo_key: str,
    reporte_id: str,
    body: UpdateCamposReporteRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.OPERADOR)),
):
    """
    Actualiza campos de texto editables de un reporte.
    El autor del reporte, su líder de grupo o un administrador puede editar.
    """
    try:
        get_grupo_config(grupo_key)
        tz_col = pytz.timezone("America/Bogota")

        doc_ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES).document(reporte_id.strip())
        doc = await asyncio.to_thread(doc_ref.get)
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Reporte '{reporte_id}' no encontrado")

        data = doc.to_dict() or {}

        # Verificar grupo (canonicalizado para tolerar datos stale como "cuadrilla" → "flora_urbana")
        if canonical_grupo_key(data.get("grupo", "")) != canonical_grupo_key(grupo_key):
            raise HTTPException(status_code=403, detail="El reporte no pertenece al grupo indicado")

        # Verificar autoría: operador solo puede editar sus propios reportes
        if not current_user.at_least(Role.LIDER):
            registrado_por = data.get("registrado_por", "")
            user_identifiers = {
                getattr(current_user, "email", None),
                getattr(current_user, "full_name", None),
                getattr(current_user, "uid", None),
            } - {None}
            if registrado_por not in user_identifiers:
                raise HTTPException(status_code=403, detail="Solo puedes editar tus propios reportes")

        # Construir dict de campos a actualizar (solo los enviados y no None)
        update_fields: dict = {
            "ultima_edicion_campos": datetime.now(tz_col).isoformat(),
            "editado": True,
        }
        if body.tipo_intervencion is not None:
            update_fields["tipo_intervencion"] = body.tipo_intervencion.strip()
        if body.descripcion_intervencion is not None:
            update_fields["descripcion_intervencion"] = body.descripcion_intervencion.strip()
        if body.observaciones is not None:
            update_fields["observaciones"] = body.observaciones.strip()
        if body.direccion is not None:
            update_fields["direccion"] = body.direccion.strip()

        if len(update_fields) <= 2:
            raise HTTPException(status_code=400, detail="Se debe enviar al menos un campo a actualizar")

        await asyncio.to_thread(doc_ref.update, update_fields)

        logger.info(
            f"[PATCH-CAMPOS] Reporte {reporte_id} actualizado por {current_user.email}: {list(update_fields.keys())}"
        )

        # Retornar el reporte actualizado (datos originales + cambios)
        data.update(update_fields)
        data["id"] = reporte_id

        return {
            "success": True,
            "id": reporte_id,
            "message": "Reporte actualizado correctamente",
            "campos_actualizados": [k for k in update_fields if k not in ("ultima_edicion_campos", "editado")],
            "data": _limpiar_reporte(data),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error actualizando campos reporte {reporte_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error actualizando reporte: {str(e)}")


@router.post(
    "/grupos/{grupo_key}/reporte_intervencion",
    summary="🟢 POST | Registrar Reporte de Intervención (Unificado)",
    description="""
## 🟢 POST | Registrar Reporte de Intervención — Endpoint Unificado

Registra un reporte de intervención en la colección unificada `reportes_intervenciones` de Firestore.
El campo `grupo` se asigna automáticamente desde el parámetro de ruta `grupo_key`; no es necesario enviarlo en el body.
El campo `registrado_por` se toma del token de autenticación (`current_user.email`).

**Autenticación**: Requiere `Authorization: Bearer <firebase_id_token>` en el header.

---

### 🏷️ Grupos válidos (`grupo_key` en la URL)
| grupo_key | Grupo operativo |
|---|---|
| `cuadrilla` | Cuadrilla de intervención |
| `vivero` | Vivero municipal |
| `gobernanza` | Gobernanza ambiental |
| `ecosistemas` | Ecosistemas |
| `umata` | UMATA |

---

### 📋 Campos del formulario (`multipart/form-data`)

#### Comunes a todos los grupos
| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `tipo_intervencion` | string | ✅ | Tipo de intervención (ej: "Poda", "Siembra") |
| `descripcion_intervencion` | string | — | Descripción detallada de la actividad |
| `direccion` | string | — | Dirección física donde se realizó |
| `id_actividad` | string | — | ID de la actividad asociada (ej: `ACT-2026-001`) |
| `observaciones` | string | — | Observaciones adicionales |
| `coordinates_type` | string | — | Tipo de geometría GeoJSON: `Point`, `LineString`, `Polygon` |
| `coordinates_data` | string | — | Coordenadas en formato JSON array. Point: `[-76.5225, 3.4516]`; Polygon: `[[-76.5,3.4],[-76.6,3.4],[-76.6,3.5],[-76.5,3.4]]` |
| `photos` | archivo(s) | — | Imágenes JPG/PNG/WEBP/HEIC. Máximo 10. Subidas a AWS S3. |

#### Específicos por grupo (ignorados si el grupo no los usa)
| Campo | Grupo | Tipo | Descripción |
|---|---|---|---|
| `arboles_data` | `cuadrilla` | JSON array | Lista de árboles: `[{"especie": "Ceiba", "cantidad": 5}]` |
| `tipos_plantas` | `vivero` | JSON dict | Plantas por especie: `{"Guayacán": 10, "Ceiba": 5}` |
| `unidades_impactadas` | `gobernanza`, `ecosistemas`, `umata` | integer | Número de unidades impactadas |
| `unidad_medida` | `ecosistemas` | string | Unidad de medida (ej: `m²`, `hectáreas`, `individuos`) |

---

### 📤 Respuesta exitosa (`200 OK`)
```json
{
  "success": true,
  "id": "uuid-del-reporte",
  "message": "Reporte de intervención del grupo Cuadrilla registrado exitosamente",
  "coordinates": { "type": "Point", "coordinates": [-76.5225, 3.4516] },
  "photosUrl": ["https://bucket.s3.amazonaws.com/reportes/cuadrilla/.../foto.jpg"],
  "photos_uploaded": 1,
  "documentos_con_enlaces": [...],
  "timestamp": "2026-04-22T10:30:00-05:00"
}
```

---

### 🔑 Ejemplos cURL

**Cuadrilla — con árboles y foto:**
```bash
curl -X POST "https://web-production-2d737.up.railway.app/grupos/cuadrilla/reporte_intervencion" \\
  -H "Authorization: Bearer <firebase_id_token>" \\
  -F "tipo_intervencion=Poda de emergencia" \\
  -F "descripcion_intervencion=Poda preventiva por tormenta eléctrica" \\
  -F "direccion=Calle 5 con Carrera 10, Barrio El Lido" \\
  -F "id_actividad=ACT-2026-001" \\
  -F "coordinates_type=Point" \\
  -F "coordinates_data=[-76.5225, 3.4516]" \\
  -F 'arboles_data=[{"especie":"Ceiba pentandra","cantidad":3},{"especie":"Saman","cantidad":2}]' \\
  -F "photos=@/ruta/local/foto.jpg;type=image/jpeg"
```

**Vivero — con tipos de plantas (sin foto):**
```bash
curl -X POST "https://web-production-2d737.up.railway.app/grupos/vivero/reporte_intervencion" \\
  -H "Authorization: Bearer <firebase_id_token>" \\
  -F "tipo_intervencion=Siembra de compensacion" \\
  -F "id_actividad=ACT-2026-002" \\
  -F 'tipos_plantas={"Guayacan amarillo":10,"Ceiba tolua":5}'
```

**Gobernanza — con unidades impactadas:**
```bash
curl -X POST "https://web-production-2d737.up.railway.app/grupos/gobernanza/reporte_intervencion" \\
  -H "Authorization: Bearer <firebase_id_token>" \\
  -F "tipo_intervencion=Taller comunitario" \\
  -F "descripcion_intervencion=Educacion ambiental barrio El Lido" \\
  -F "id_actividad=ACT-2026-003" \\
  -F "unidades_impactadas=45"
```

**Ecosistemas — con unidad de medida:**
```bash
curl -X POST "https://web-production-2d737.up.railway.app/grupos/ecosistemas/reporte_intervencion" \\
  -H "Authorization: Bearer <firebase_id_token>" \\
  -F "tipo_intervencion=Monitoreo de fauna" \\
  -F "id_actividad=ACT-2026-004" \\
  -F "unidad_medida=individuos" \\
  -F "unidades_impactadas=120"
```

**UMATA — básico:**
```bash
curl -X POST "https://web-production-2d737.up.railway.app/grupos/umata/reporte_intervencion" \\
  -H "Authorization: Bearer <firebase_id_token>" \\
  -F "tipo_intervencion=Asistencia tecnica agricola" \\
  -F "id_actividad=ACT-2026-005" \\
  -F "unidades_impactadas=30"
```

---

### ⚠️ Errores comunes
| Código | Causa |
|---|---|
| `401` | Token ausente o inválido |
| `403` | El usuario no tiene permiso para registrar en este grupo |
| `404` | `grupo_key` no existe |
| `400` | JSON inválido en `arboles_data` o `tipos_plantas`, tipo de geometría inválido, o foto con formato no permitido |
| `500` | Error en Firebase o S3 |
    """,
    response_model=ReconocimientoResponse
)
async def post_reporte_intervencion_unificado(
    grupo_key: str,
    tipo_intervencion: Optional[str] = Form(None, description="Tipo de intervención"),
    descripcion_intervencion: Optional[str] = Form(None, description="Descripción de la intervención"),
    direccion: Optional[str] = Form(None, description="Dirección de la intervención"),
    # registrado_por ya no se expone como parámetro externo, solo se fuerza desde el token
    grupo: Optional[str] = Form(None, description="Grupo operativo"),
    current_user: CurrentUser = Depends(get_current_user),
    id_actividad: Optional[str] = Form(None, description="ID de la actividad asociada"),
    observaciones: Optional[str] = Form(None, description="Observaciones adicionales"),
    coordinates_type: Optional[str] = Form(None, description="Tipo de geometría (Point, LineString, Polygon, etc.)"),
    coordinates_data: Optional[str] = Form(None, description="Coordenadas en formato JSON array. Ejemplo: [-76.5225, 3.4516]"),
    coordenadas_origen: Optional[str] = Form(None, description="Origen de las coordenadas: 'gps' o 'manual' (bug #5)"),
    photos: Optional[List[UploadFile]] = File(None, description="Lista de archivos de fotos a subir a S3"),
    arboles_data: Optional[str] = Form(None, description='[Cuadrilla] Lista de árboles JSON. Ej: [{"especie": "Ceiba", "cantidad": 5}]'),
    tipos_plantas: Optional[str] = Form(None, description='[Vivero] Dict JSON de plantas. Ej: {"Guayacán": 10, "Ceiba": 5}'),
    unidades_impactadas: Optional[int] = Form(None, description="[Gobernanza/Ecosistemas/UMATA] Número de unidades impactadas"),
    unidad_medida: Optional[str] = Form(None, description="[Ecosistemas] Unidad de medida (ej: m², hectáreas, individuos)"),
):
    return await _post_reporte_intervencion(
        grupo_key=grupo_key,
        tipo_intervencion=tipo_intervencion,
        descripcion_intervencion=descripcion_intervencion,
        direccion=direccion,
        # registrado_por se asigna internamente desde el token
        grupo=grupo,
        id_actividad=id_actividad,
        observaciones=observaciones,
        coordinates_type=coordinates_type,
        coordinates_data=coordinates_data,
        coordenadas_origen=coordenadas_origen,
        photos=photos,
        arboles_data=arboles_data,
        tipos_plantas=tipos_plantas,
        unidades_impactadas=unidades_impactadas,
        unidad_medida=unidad_medida,
        current_user=current_user,
    )


@router.get(
    "/grupos/{grupo_key}/reportes_intervenciones",
    summary="🔵 GET | Obtener Reportes de Intervención (Unificado)",
    description="""
## 🔵 GET | Obtener Reportes de Intervención — Endpoint Unificado

Consulta reportes de intervención de un grupo operativo desde la colección unificada `reportes_intervenciones`.
Cada documento retornado contiene **solo los campos relevantes para ese grupo** (sin claves vacías de otros grupos).

**Autenticación**: Requiere `Authorization: Bearer <firebase_id_token>` en el header.
Los roles `lider` y `operador` solo pueden consultar su propio grupo. `administrador` puede consultar cualquiera.

---

### 🏷️ Grupos válidos (`grupo_key` en la URL)
`cuadrilla` · `vivero` · `gobernanza` · `ecosistemas` · `umata`

---

### 📥 Query parameters (todos opcionales)
| Parámetro | Descripción |
|---|---|
| `id` | ID exacto del documento en Firestore. Retorna `total: 1` o `total: 0`. |
| `id_actividad` | Filtra todos los reportes de esa actividad para el grupo. |

> El filtro por `grupo` está implícito en la ruta — siempre se aplica `grupo == grupo_key`.

---

### 📤 Estructura de respuesta (`200 OK`)
```json
{
  "success": true,
  "total": 2,
  "filters": {
    "grupo": "cuadrilla",
    "id_actividad": "ACT-2026-001",
    "id": null
  },
  "data": [
    {
      "id": "uuid-del-reporte",
      "grupo": "cuadrilla",
      "tipo_intervencion": "Poda",
      "descripcion_intervencion": "...",
      "direccion": "Calle 5 con Carrera 10",
      "registrado_por": "usuario@dagma.gov.co",
      "id_actividad": "ACT-2026-001",
      "observaciones": "",
      "coordinates": { "type": "Point", "coordinates": [-76.5225, 3.4516] },
      "comuna_corregimiento": "COMUNA 2",
      "barrio_vereda": "El Lido",
      "arboles": [{ "especie": "Ceiba", "cantidad": 3 }],
      "documentos": [...],
      "documentos_con_enlaces": [...],
      "total_documentos": 1,
      "photos_uploaded": 1,
      "timestamp": "2026-04-22T10:30:00-05:00"
    }
  ],
  "timestamp": "2026-04-22T15:00:00+00:00"
}
```

> Los campos específicos de grupo (`arboles`, `tipos_plantas`, `cantidad_total_plantas`, `unidades_impactadas`, `unidad_medida`) **solo aparecen si fueron guardados**. No se envían con valor `null` si no aplican al grupo.

---

### 🔑 Ejemplos cURL

**Todos los reportes de cuadrilla:**
```bash
curl -X GET "https://web-production-2d737.up.railway.app/grupos/cuadrilla/reportes_intervenciones" \\
  -H "Authorization: Bearer <firebase_id_token>"
```

**Filtrar por actividad:**
```bash
curl -X GET "https://web-production-2d737.up.railway.app/grupos/vivero/reportes_intervenciones?id_actividad=ACT-2026-001" \\
  -H "Authorization: Bearer <firebase_id_token>"
```

**Buscar un reporte por su ID:**
```bash
curl -X GET "https://web-production-2d737.up.railway.app/grupos/gobernanza/reportes_intervenciones?id=abc123-uuid" \\
  -H "Authorization: Bearer <firebase_id_token>"
```

---

### ⚠️ Errores comunes
| Código | Causa |
|---|---|
| `401` | Token ausente o inválido |
| `403` | El usuario no tiene permiso para consultar este grupo |
| `404` | `grupo_key` no existe |
| `500` | Error en Firebase o S3 |
    """
)
async def get_reportes_intervenciones_unificado(
    grupo_key: str,
    id: Optional[str] = Query(None, min_length=1, description="Filtrar por ID del reporte"),
    id_actividad: Optional[str] = Query(None, min_length=1, description="Filtrar por ID de actividad"),
    grupo: Optional[str] = Query(None, min_length=1, description="Filtrar por nombre del grupo"),
    limit: Optional[int] = Query(200, ge=1, le=1000, description="Maximo de reportes a devolver"),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await _get_reportes_intervenciones(
        grupo_key=grupo_key, id=id, id_actividad=id_actividad, grupo=grupo,
        current_user=current_user, limit=limit,
    )


# ==================== RUTAS LEGACY (backward compatibility) ====================#
# Las rutas originales /grupo-{name}/... se mantienen como aliases

@router.post("/grupo-cuadrilla/reporte_intervencion", summary="🟢 POST | Reporte Intervención Cuadrilla", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_cuadrilla_legacy(
    tipo_intervencion: Optional[str] = Form(None), descripcion_intervencion: Optional[str] = Form(None),
    arboles_data: Optional[str] = Form(None),
    grupo: Optional[str] = Form(None), id_actividad: Optional[str] = Form(None),
    observaciones: Optional[str] = Form(None), coordinates_type: Optional[str] = Form(None),
    coordinates_data: Optional[str] = Form(None), photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="flora_urbana", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=None, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, arboles_data=arboles_data,
    )

@router.post("/grupo-vivero/reporte_intervencion", summary="🟢 POST | Reporte Intervención Vivero", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_vivero_legacy(
    tipo_intervencion: Optional[str] = Form(None), tipos_plantas: Optional[str] = Form(None),
    descripcion_intervencion: Optional[str] = Form(None), direccion: Optional[str] = Form(None),
    grupo: Optional[str] = Form(None),
    id_actividad: Optional[str] = Form(None), observaciones: Optional[str] = Form(None),
    coordinates_type: Optional[str] = Form(None), coordinates_data: Optional[str] = Form(None),
    photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="vivero", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=direccion, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, tipos_plantas=tipos_plantas,
    )

@router.post("/grupo-gobernanza/reporte_intervencion", summary="🟢 POST | Reporte Intervención Gobernanza", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_gobernanza_legacy(
    tipo_intervencion: Optional[str] = Form(None), unidades_impactadas: Optional[int] = Form(None),
    descripcion_intervencion: Optional[str] = Form(None), direccion: Optional[str] = Form(None),
    registrado_por: Optional[str] = Form(None), grupo: Optional[str] = Form(None),
    id_actividad: Optional[str] = Form(None), observaciones: Optional[str] = Form(None),
    coordinates_type: Optional[str] = Form(None), coordinates_data: Optional[str] = Form(None),
    photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="gobernanza", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=direccion, registrado_por=registrado_por, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, unidades_impactadas=unidades_impactadas,
    )

@router.post("/grupo-ecosistemas/reporte_intervencion", summary="🟢 POST | Reporte Intervención Ecosistemas", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_ecosistemas_legacy(
    tipo_intervencion: Optional[str] = Form(None), unidad_medida: Optional[str] = Form(None),
    unidades_impactadas: Optional[int] = Form(None), descripcion_intervencion: Optional[str] = Form(None),
    direccion: Optional[str] = Form(None),
    grupo: Optional[str] = Form(None), id_actividad: Optional[str] = Form(None),
    observaciones: Optional[str] = Form(None), coordinates_type: Optional[str] = Form(None),
    coordinates_data: Optional[str] = Form(None), photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="ecosistemas", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=direccion, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, unidad_medida=unidad_medida, unidades_impactadas=unidades_impactadas,
    )

@router.post("/grupo-umata/reporte_intervencion", summary="🟢 POST | Reporte Intervención UMATA", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_umata_legacy(
    tipo_intervencion: Optional[str] = Form(None), unidades_impactadas: Optional[int] = Form(None),
    descripcion_intervencion: Optional[str] = Form(None), direccion: Optional[str] = Form(None),
    registrado_por: Optional[str] = Form(None), grupo: Optional[str] = Form(None),
    id_actividad: Optional[str] = Form(None), observaciones: Optional[str] = Form(None),
    coordinates_type: Optional[str] = Form(None), coordinates_data: Optional[str] = Form(None),
    photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="umata", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=direccion, registrado_por=registrado_por, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, unidades_impactadas=unidades_impactadas,
    )


@router.get("/grupo-cuadrilla/reportes_intervenciones", summary="🔵 GET | Reportes Cuadrilla", include_in_schema=False)
async def get_reportes_cuadrilla_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="flora_urbana", id=id, id_actividad=id_actividad, grupo=grupo)

@router.post("/grupo-flora-urbana/reporte_intervencion", summary="🟢 POST | Reporte Intervención Flora Urbana", response_model=ReconocimientoResponse, include_in_schema=False)
async def post_reporte_flora_urbana_legacy(
    tipo_intervencion: Optional[str] = Form(None), descripcion_intervencion: Optional[str] = Form(None),
    arboles_data: Optional[str] = Form(None),
    grupo: Optional[str] = Form(None), id_actividad: Optional[str] = Form(None),
    observaciones: Optional[str] = Form(None), coordinates_type: Optional[str] = Form(None),
    coordinates_data: Optional[str] = Form(None), photos: Optional[List[UploadFile]] = File(None),
):
    return await _post_reporte_intervencion(
        grupo_key="flora_urbana", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
        direccion=None, grupo=grupo, id_actividad=id_actividad,
        observaciones=observaciones, coordinates_type=coordinates_type, coordinates_data=coordinates_data,
        photos=photos, arboles_data=arboles_data,
    )

@router.get("/grupo-flora-urbana/reportes_intervenciones", summary="🔵 GET | Reportes Flora Urbana", include_in_schema=False)
async def get_reportes_flora_urbana_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="flora_urbana", id=id, id_actividad=id_actividad, grupo=grupo)

@router.get("/grupo-vivero/reportes_intervenciones", summary="🔵 GET | Reportes Vivero", include_in_schema=False)
async def get_reportes_vivero_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="vivero", id=id, id_actividad=id_actividad, grupo=grupo)

@router.get("/grupo-gobernanza/reportes_intervenciones", summary="🔵 GET | Reportes Gobernanza", include_in_schema=False)
async def get_reportes_gobernanza_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="gobernanza", id=id, id_actividad=id_actividad, grupo=grupo)

@router.get("/grupo-ecosistemas/reportes_intervenciones", summary="🔵 GET | Reportes Ecosistemas", include_in_schema=False)
async def get_reportes_ecosistemas_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="ecosistemas", id=id, id_actividad=id_actividad, grupo=grupo)

@router.get("/grupo-umata/reportes_intervenciones", summary="🔵 GET | Reportes UMATA", include_in_schema=False)
async def get_reportes_umata_legacy(
    id: Optional[str] = Query(None, min_length=1), id_actividad: Optional[str] = Query(None, min_length=1),
    grupo: Optional[str] = Query(None, min_length=1),
):
    return await _get_reportes_intervenciones(grupo_key="umata", id=id, id_actividad=id_actividad, grupo=grupo)



# ==================== ENDPOINT 4: Obtener Líderes por Grupo ======================================#
@router.get(
    "/grupos",
    summary="🔵 GET | Obtener Grupos",
    description="""
## 🔵 GET | Obtener Grupos

**Propósito**: Consultar grupos desde la colección `grupos` en Firebase.
Cada grupo incluye su nombre, correo institucional y líder asignado.

### 📥 Parámetros
- **grupo** (opcional): Filtrar por nombre de grupo (coincidencia exacta)

### 📝 Ejemplos de uso:
```javascript
// Obtener todos los grupos
fetch('/grupos');

// Filtrar por grupo
fetch('/grupos?grupo=Vivero');
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def get_grupos(
    response: Response,
    grupo: Optional[str] = Query(None, min_length=1, description="Filtrar por nombre de grupo"),
):
    """
    Obtener grupos de la colección `grupos` con filtro opcional por nombre.
    """
    try:
        ref = db.collection("grupos")
        query = ref

        if grupo:
            query = query.where("nombre", "==", grupo.strip())

        docs = await stream_to_list(query)

        grupos = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            grupos.append(data)

        response.headers["Cache-Control"] = "public, max-age=60"
        return {
            "status": "success",
            "data": grupos,
            "count": len(grupos),
            "filters": {
                "grupo": grupo.strip() if grupo else None
            },
            "timestamp": datetime.now(pytz.timezone("America/Bogota")).isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo grupos: {str(e)}"
        )


# ==================== ENDPOINT: Crear Grupo ======================================#
@router.post(
    "/grupos",
    summary="🟢 POST | Crear Grupo",
    tags=["Artefacto de Captura DAGMA"],
)
async def crear_grupo(
    body: dict = Body(...),
    current_user: CurrentUser = Depends(require_min_role(Role.ADMINISTRADOR)),
):
    """
    Crear un nuevo grupo operativo en la colección `grupos`.
    Requiere rol mínimo ADMINISTRADOR.
    """
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="El campo 'nombre' es requerido")

    # Doc ID normalizado (igual que init_grupos_collection.py)
    doc_id = nombre.lower().replace(" ", "_")
    ahora = datetime.now(pytz.timezone("America/Bogota")).isoformat()

    lider_nombre = (body.get("lider") or "").strip() or None
    lider_telefono = (body.get("telefono_contacto") or "").strip() or None

    doc_data = {
        "nombre": nombre,
        "email": (body.get("email") or "").strip() or None,
        "lider": {
            "nombre": lider_nombre,
            "email": None,
            "numero_contacto": lider_telefono,
        },
        "creado_por": current_user.email,
        "timestamp": ahora,
    }

    db.collection("grupos").document(doc_id).set(doc_data)

    return {
        "status": "success",
        "data": doc_data,
        "timestamp": ahora,
    }


# ==================== ENDPOINT 5: Obtener Actividades ======================================#
@router.get(
    "/actividades",
    summary="🔵 GET | Obtener Actividades",
    description="""
## 🔵 GET | Obtener Actividades

**Propósito**: Consultar actividades registradas en el plan de intervención "Distrito Verde" desde Firebase.

### 📥 Parámetros
- **id** (opcional): Filtrar por ID específico de la actividad
- **grupo** (opcional): Filtrar por grupo operativo (ej. `cuadrilla`)
- **limit** (opcional): Número máximo de resultados por página (1-200, default 50)
- **start_after** (opcional): ID del último documento recibido para paginación cursor

### 📝 Ejemplos de uso:
```javascript
// Primera página
fetch('/actividades?grupo=cuadrilla&limit=50');

// Página siguiente (cursor del último id recibido)
fetch('/actividades?grupo=cuadrilla&limit=50&start_after=LAST_DOC_ID');
```

### 📊 Respuesta paginada:
```json
{
  "success": true,
  "total": 50,
  "data": [...],
  "next_cursor": "last-doc-id-or-null",
  "timestamp": "..."
}
```
    """
)
async def get_actividades(
    response: Response,
    id: Optional[str] = Query(None, description="Filtrar por ID de actividad"),
    grupo: Optional[str] = Query(None, description="Filtrar por grupo operativo"),
    limit: int = Query(50, ge=1, le=200, description="Resultados por página"),
    start_after: Optional[str] = Query(None, description="ID del último doc recibido (cursor de paginación)"),
):
    """
    Obtener actividades con filtro por grupo y paginación cursor.
    """
    try:
        def obtener_personal_asignado(
            actividad_document_id: str,
            actividad_id_interno: Optional[str] = None,
            doc_data: Optional[dict] = None,
            fallback_index: Optional[dict[str, list[dict]]] = None,
        ) -> list[dict]:
            """
            Lee personal_asignado del campo del documento (fuente principal, escrita por PATCH/PUT).
            Si el campo no existe o está vacío, hace fallback a la subcolección
            personal_asignado_actividad (datos legacy del POST /asignar_personal_actividad).
            """
            # 1) Fuente principal: campo del documento
            if doc_data and isinstance(doc_data.get("personal_asignado"), list) and len(doc_data["personal_asignado"]) > 0:
                return doc_data["personal_asignado"]

            # 2) Fallback indexado en batch (si está disponible)
            if fallback_index is not None:
                personal_encontrado = []
                ids_vistos = set()
                posibles_claves = [
                    f"actividad_document_id::{actividad_document_id}",
                    f"actividad_id::{actividad_document_id}",
                ]
                if actividad_id_interno and actividad_id_interno != actividad_document_id:
                    posibles_claves.append(f"actividad_id::{actividad_id_interno}")

                for clave in posibles_claves:
                    for item in fallback_index.get(clave, []):
                        item_id = item.get("id")
                        if item_id in ids_vistos:
                            continue
                        ids_vistos.add(item_id)
                        personal_encontrado.append(item)
                return personal_encontrado

            # 3) Fallback legacy por consulta directa (single item)
            personal_encontrado = []
            ids_vistos = set()

            consultas = [
                ("actividad_document_id", actividad_document_id),
                ("actividad_id", actividad_document_id),
            ]

            if actividad_id_interno and actividad_id_interno != actividad_document_id:
                consultas.append(("actividad_id", actividad_id_interno))

            for campo, valor in consultas:
                docs_personal = db.collection("personal_asignado_actividad").where(campo, "==", valor).stream()
                for doc_personal in docs_personal:
                    if doc_personal.id in ids_vistos:
                        continue
                    personal_data = doc_personal.to_dict() or {}
                    personal_data["id"] = doc_personal.id
                    personal_encontrado.append(personal_data)
                    ids_vistos.add(doc_personal.id)

            return personal_encontrado

        plan_ref = db.collection('plan_distrito_verde')

        # Búsqueda por ID único — sin paginación
        if id:
            doc = await run_blocking(plan_ref.document(id).get)
            if doc.exists:
                data = doc.to_dict()
                actividad_id_interno = data.get("id") if isinstance(data, dict) else None
                personal = obtener_personal_asignado(doc.id, actividad_id_interno, doc_data=data)
                data['id'] = doc.id
                data['grupo'] = personal
                data['personal_asignado'] = personal
                response.headers["Cache-Control"] = "public, max-age=60"
                return {
                    "success": True,
                    "total": 1,
                    "data": [data],
                    "next_cursor": None,
                    "filters": {"id": id},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            # Fallback: buscar por campo interno 'id'
            docs = await stream_to_list(plan_ref.where("id", "==", id).limit(50))
            actividades = []
            for doc in docs:
                data = doc.to_dict()
                actividad_id_interno = data.get("id") if isinstance(data, dict) else None
                personal = obtener_personal_asignado(doc.id, actividad_id_interno, doc_data=data)
                data['id'] = doc.id
                data['grupo'] = personal
                data['personal_asignado'] = personal
                actividades.append(data)

            response.headers["Cache-Control"] = "public, max-age=60"
            return {
                "success": True,
                "total": len(actividades),
                "data": actividades,
                "next_cursor": None,
                "filters": {"id": id},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        # Construir query con filtros opcionales
        query = plan_ref
        canonical_grupo: Optional[str] = None

        if grupo:
            # Filter by grupos_requeridos array.
            # `grupos_requeridos` guarda nombres canónicos como en la colección `grupos`
            # (p.ej. "Acústica", "Cuadrilla"). El cliente puede enviarlo con tildes
            # diferentes o en minúscula; resolvemos el nombre canónico contra `grupos`
            # antes de filtrar (Firestore no soporta comparaciones case/accent insensitive).
            grupo_input = grupo.strip()
            # Normalizar siempre: tras la migración 2026-05-22 todos los valores
            # en grupos_requeridos son lowercase+sin tildes (normalize_grupo).
            # El catálogo `grupos` no fue migrado (sigue con mayúsculas display),
            # por lo que usarlo como canonical provocaba array_contains fallidos.
            canonical_grupo = normalize_grupo(grupo_input)

            query = query.where("grupos_requeridos", "array_contains", canonical_grupo)

        # Paginación cursor-based (Firestore no soporta OFFSET)
        cursor_snap = None
        if start_after:
            cursor_snap = await asyncio.to_thread(plan_ref.document(start_after).get)
            if cursor_snap.exists:
                query = query.start_after(cursor_snap)
            else:
                cursor_snap = None

        query = query.limit(limit)

        def _materialize_actividades():
            """Itera el stream y construye la lista en un thread worker.
            Mantiene las llamadas anidadas a Firestore (obtener_personal_asignado)
            fuera del event loop.
            """
            docs = list(query.stream())

            # Defense-in-depth: si se filtró por grupo y no hubo resultados,
            # puede haber actividades con grupos_requeridos no canónicos
            # (datos previos a la migración 2026-05-22 o creados por fuera del
            # POST normalizado). Hacemos un segundo barrido SIN el where y
            # filtramos en memoria usando normalize_grupo. Sólo se activa
            # cuando el fast path falla, así que no impacta la latencia normal.
            if not docs and canonical_grupo:
                fallback_query = plan_ref
                if cursor_snap is not None and getattr(cursor_snap, "exists", False):
                    fallback_query = fallback_query.start_after(cursor_snap)
                # Margen amplio para compensar el filtrado en memoria.
                fallback_query = fallback_query.limit(max(limit * 5, 200))
                raw_docs = list(fallback_query.stream())
                matched = []
                anomalous: set[str] = set()
                for d in raw_docs:
                    gr_list = (d.to_dict() or {}).get("grupos_requeridos") or []
                    if not isinstance(gr_list, list):
                        continue
                    hit = False
                    for g in gr_list:
                        if not g or not isinstance(g, str):
                            continue
                        ng = normalize_grupo(g)
                        if ng == canonical_grupo:
                            hit = True
                        if ng != g:
                            anomalous.add(g)
                    if hit:
                        matched.append(d)
                        if len(matched) >= limit:
                            break
                if anomalous:
                    logger.warning(
                        f"[ACTIVIDADES] grupos_requeridos no canónicos detectados "
                        f"(grupo='{canonical_grupo}'): {sorted(anomalous)}"
                    )
                if matched:
                    logger.info(
                        f"[ACTIVIDADES] fallback in-memory grupo='{canonical_grupo}' "
                        f"recuperó {len(matched)} doc(s) tras 0 hits del fast path"
                    )
                docs = matched

            out = []
            last_id = docs[-1].id if docs else None

            # Indexar fallback de personal_asignado en batch para evitar N+1.
            fallback_candidates = []
            for doc in docs:
                data = doc.to_dict() or {}
                if not (isinstance(data.get("personal_asignado"), list) and len(data["personal_asignado"]) > 0):
                    fallback_candidates.append((doc.id, data.get("id")))

            fallback_index: dict[str, list[dict]] = {}

            def _chunks(items: list[str], size: int = 30):
                for i in range(0, len(items), size):
                    yield items[i:i + size]

            if fallback_candidates:
                doc_ids = list({doc_id for doc_id, _ in fallback_candidates if doc_id})
                internal_ids = list({str(int_id) for _, int_id in fallback_candidates if int_id})

                def _accumulate(campo: str, valores: list[str]):
                    if not valores:
                        return
                    for batch in _chunks(valores, 30):
                        q = db.collection("personal_asignado_actividad").where(campo, "in", batch)
                        for pdoc in q.stream():
                            pdata = pdoc.to_dict() or {}
                            pdata["id"] = pdoc.id
                            k = f"{campo}::{pdata.get(campo)}"
                            fallback_index.setdefault(k, []).append(pdata)

                _accumulate("actividad_document_id", doc_ids)
                _accumulate("actividad_id", doc_ids)
                _accumulate("actividad_id", internal_ids)

            for doc in docs:
                data = doc.to_dict() or {}
                actividad_id_interno = data.get("id") if isinstance(data, dict) else None
                personal = obtener_personal_asignado(
                    doc.id,
                    actividad_id_interno,
                    doc_data=data,
                    fallback_index=fallback_index,
                )
                data['id'] = doc.id
                data['grupo'] = personal
                data['personal_asignado'] = personal
                out.append(data)
            return out, last_id

        actividades, last_doc_id = await asyncio.to_thread(_materialize_actividades)

        # Ordenar por marca_temporal descendente (más reciente primero)
        actividades.sort(key=lambda a: a.get("marca_temporal", ""), reverse=True)

        # next_cursor solo si hubo resultados iguales al limit (puede haber más)
        next_cursor = last_doc_id if len(actividades) == limit else None

        response.headers["Cache-Control"] = "public, max-age=60"
        return {
            "success": True,
            "total": len(actividades),
            "data": actividades,
            "next_cursor": next_cursor,
            "filters": {
                "grupo": grupo.strip() if grupo else None,
                "limit": limit,
                "start_after": start_after,
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo actividades: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo actividades: {str(e)}"
        )


# ==================== ENDPOINT 6: Convocar Actividad ====================#

from fastapi import Body

class PuntoEncuentroModel(BaseModel):
    geometry: dict = Field(..., description="Coordenadas tipo punto en formato RFC 7946")
    direccion: str = Field(..., description="Dirección del punto de encuentro")
    comunas_corregimiento: str = Field(None, description="Comuna o corregimiento (autocompletado por intersección)")
    barrio_vereda: str = Field(None, description="Barrio o vereda (autocompletado por intersección)")

class ConvocarActividadRequest(BaseModel):
    fecha_actividad: str = Field(..., description="Fecha en formato dd/mm/aaaa")
    hora_encuentro: str = Field(..., description="Hora en formato hh:mm")
    tipo_jornada: str = Field(..., description="Tipo de jornada")
    duracion_actividad: float = Field(..., gt=0, description="Duración de la actividad en horas")
    grupos_requeridos: list[str] = Field(..., description="Lista de grupos requeridos")
    lider_actividad: str = Field(..., description="Líder de la actividad")
    lider_actividad_email: str = Field(None, description="Email del líder de la actividad (opcional). Si se provee, se le enviará una notificación específica como líder.")
    lider_actividad_telefono: str = Field(None, description="Teléfono del líder de la actividad (opcional). Sobreescribe el campo telefono del coordinador en los correos.")
    punto_encuentro: dict = Field(..., description="Punto de encuentro (geometry, direccion)")
    observaciones: str = Field(None, description="Observaciones")
    telefono: str = Field(..., description="Teléfono de contacto")
    objetivo_actividad: str = Field(..., description="Objetivo de la actividad")
    email: str = Field(..., description="Email de contacto")

class ConvocarActividadResponse(BaseModel):
    success: bool
    id: str
    message: str
    marca_temporal: str
    data: dict


@router.post(
    "/programar_actividad",
    summary="🟢 POST | Programar Actividad",
    description="""
## 🟢 POST | Programar Actividad

Registra una convocatoria de actividad con georreferenciación automática del punto de encuentro.

**Tag:** Artefacto de Captura DAGMA
""",
    tags=["Artefacto de Captura DAGMA"],
    response_model=ConvocarActividadResponse
)
async def convocar_actividad(
    body: ConvocarActividadRequest = Body(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Convoca una actividad y la registra en la base de datos, calculando comuna/corregimiento y barrio/vereda.

    Requiere autenticación con nivel mínimo 'lider'.
    Para asignar un usuario con rol 'Administrador' como lider_actividad, el solicitante
    debe tener rol 'Administrador', 'Director' o 'Desarrollador'.
    """
    # Validar que si el lider_actividad_email corresponde a un Administrador,
    # solo Administrador/Director/Desarrollador puede hacer esa asignación.
    if body.lider_actividad_email:
        target_email = body.lider_actividad_email.strip().lower()
        target_docs = await stream_to_list(
            db.collection("users").where("email", "==", target_email).limit(1)
        )
        if target_docs:
            target_data = target_docs[0].to_dict() or {}
            target_role = normalize_role(target_data.get("role") or target_data.get("rol"))
            if target_role == Role.ADMINISTRADOR and not current_user.at_least(Role.ADMINISTRADOR):
                raise HTTPException(
                    status_code=403,
                    detail="Solo Administrador, Director o Desarrollador pueden asignar un Administrador como líder de actividad.",
                )
    try:
        # Validar y extraer geometry
        punto = body.punto_encuentro
        geometry = punto.get("geometry")
        direccion = punto.get("direccion")
        if not geometry or geometry.get("type") != "Point" or not geometry.get("coordinates"):
            raise HTTPException(status_code=400, detail="geometry debe ser tipo Point y tener coordinates")
        coordinates = geometry["coordinates"]
        validate_coordinates(coordinates, "Point")
        # Intersección geográfica
        comuna_corregimiento, barrio_vereda = get_location_from_coordinates(coordinates)
        # Actualizar punto_encuentro con resultados
        punto["comunas_corregimiento"] = comuna_corregimiento
        punto["barrio_vereda"] = barrio_vereda
        # Timestamp en hora de Colombia
        tz_col = pytz.timezone("America/Bogota")
        marca_temporal = datetime.now(tz_col).isoformat()
        # Generar ID único
        actividad_id = str(uuid.uuid4())
        # Preparar datos para guardar
        actividad_data = {
            "id": actividad_id,
            "marca_temporal": marca_temporal,
            "fecha_actividad": body.fecha_actividad,
            "hora_encuentro": body.hora_encuentro,
            "tipo_jornada": body.tipo_jornada,
            "duracion_actividad": body.duracion_actividad,
            "grupos_requeridos": [normalize_grupo(g) for g in (body.grupos_requeridos or []) if g],
            "lider_actividad": body.lider_actividad,
            "lider_actividad_email": (body.lider_actividad_email or "").strip(),
            "lider_actividad_telefono": (body.lider_actividad_telefono or "").strip(),
            "punto_encuentro": punto,
            "observaciones": body.observaciones or "",
            "telefono": body.telefono,
            "objetivo_actividad": body.objetivo_actividad,
            "email": body.email,
            "estado_actividad": "Programada"
        }
        # Crear evento en Google Calendar con el coordinador como asistente
        # (sigue siendo inline porque necesitamos event_id para persistirlo)
        try:
            created_event = await asyncio.to_thread(
                create_activity_event,
                actividad_data=actividad_data,
                attendee_emails=[body.email],
            )
            if created_event:
                actividad_data['calendar_event_id'] = created_event.get('id')
                actividad_data['calendar_event_link'] = created_event.get('htmlLink')
        except Exception as e:
            logger.warning(f"[CALENDAR] Error creando evento: {e}")
            actividad_data['calendar_event_error'] = str(e)

        # Guardar en Firebase (sin bloquear el event loop)
        await asyncio.to_thread(
            db.collection("plan_distrito_verde").document(actividad_id).set,
            actividad_data,
        )

        # Notificaciones por email: se envian en background para no demorar la respuesta.
        def _enviar_notificaciones_actividad():
            # Helper de comparación de nombres insensible a tildes/espacios/case.
            def _name_key(s: str | None) -> str:
                return strip_accents(str(s or "")).strip().lower()

            # Variables compartidas entre secciones B y C: nombre y teléfono del
            # LÍDER de la actividad (para sobreescribir `telefono` del coordinador
            # en los correos enviados a los líderes de grupo).
            _lider_nombre_res = (body.lider_actividad or "").strip()
            # Prefer phone sent from frontend (already resolved from personal_operativo)
            _lider_telefono_res = (body.lider_actividad_telefono or "").strip() or None

            # ---- A) Confirmación al usuario que programó ----
            try:
                send_activity_confirmation_email(body.email, actividad_data)
            except Exception as e:
                logger.warning(f"[GMAIL] Error enviando confirmación: {e}")

            # ---- B) Email al LÍDER de la actividad ----
            # Prioridad: email explícito > resolución por nombre en `users`.
            try:
                lider_email = (body.lider_actividad_email or "").strip()
                lider_nombre = (body.lider_actividad or "").strip()
                if not lider_email and lider_nombre:
                    target = _name_key(lider_nombre)
                    try:
                        for rol_field in ("role", "rol"):
                            q = db.collection("users").where(
                                rol_field, "in", ["lider", "líder", "LIDER", "LÍDER"]
                            )
                            for udoc in q.stream():
                                ud = udoc.to_dict() or {}
                                nombre_db = (
                                    ud.get("full_name")
                                    or ud.get("nombre_completo")
                                    or ud.get("displayName")
                                    or ud.get("nombre")
                                    or ""
                                )
                                if nombre_db and _name_key(nombre_db) == target:
                                    lider_email = (ud.get("email") or "").strip()
                                    break
                            if lider_email:
                                break
                    except Exception as e:
                        logger.warning(
                            f"[NOTIFY] No se pudo resolver email del líder por nombre '{lider_nombre}': {e}"
                        )
                if lider_email and "@" in lider_email:
                    # Resolver teléfono del líder: 1) users, 2) personal_operativo por email.
                    try:
                        for udoc in db.collection("users").where(
                            "email", "==", lider_email
                        ).limit(1).stream():
                            ud = udoc.to_dict() or {}
                            tel_raw = ud.get("cellphone") or ud.get("telefono")
                            if tel_raw not in (None, ""):
                                _lider_telefono_res = str(tel_raw).strip()
                            _lider_nombre_res = (
                                ud.get("full_name")
                                or ud.get("nombre_completo")
                                or ud.get("displayName")
                                or lider_nombre
                            ) or lider_nombre
                            break
                    except Exception:
                        pass
                    # Fallback: buscar en personal_operativo por email
                    if not _lider_telefono_res:
                        try:
                            for udoc in db.collection("personal_operativo").where(
                                "email", "==", lider_email
                            ).limit(1).stream():
                                ud = udoc.to_dict() or {}
                                tel_raw = ud.get("numero_contacto")
                                if tel_raw not in (None, ""):
                                    _lider_telefono_res = str(tel_raw).strip()
                                break
                        except Exception:
                            pass
                # Fallback final: buscar en personal_operativo por nombre_completo
                if not _lider_telefono_res and lider_nombre:
                    try:
                        for udoc in db.collection("personal_operativo").where(
                            "nombre_completo", "==", lider_nombre.strip()
                        ).limit(1).stream():
                            ud = udoc.to_dict() or {}
                            tel_raw = ud.get("numero_contacto")
                            if tel_raw not in (None, ""):
                                _lider_telefono_res = str(tel_raw).strip()
                            break
                    except Exception:
                        pass
                    try:
                        send_activity_leader_assigned_email(
                            lider_email, lider_nombre, actividad_data
                        )
                        logger.info(
                            f"[NOTIFY] Líder de actividad notificado: {lider_email}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[GMAIL] Error notificando líder de actividad {lider_email}: {e}"
                        )
                else:
                    logger.warning(
                        f"[NOTIFY] No se encontró email para líder de actividad '{lider_nombre}'"
                    )
            except Exception as e:
                logger.warning(f"[GMAIL] Error en flujo líder-de-actividad: {e}")

            # ---- C) Email a los líderes de los GRUPOS REQUERIDOS ----
            # NOTA (bug #4, 2026-05-28): por defecto SE OMITE este aviso a líderes de grupo.
            # Solo el líder de la actividad debe recibir notificación al programarse una
            # actividad; los líderes de grupo pueden consultar la app. Reintroducir
            # vía opt-in con NOTIFY_GROUP_LEADERS_ON_CREATE=1 si en el futuro se requiere.
            if os.getenv("NOTIFY_GROUP_LEADERS_ON_CREATE", "false").strip().lower() not in ("1", "true", "yes"):
                logger.info(
                    "[NOTIFY] Aviso a líderes de grupos OMITIDO "
                    "(NOTIFY_GROUP_LEADERS_ON_CREATE no activo)"
                )
                return
            try:
                app_url = os.getenv(
                    "FRONTEND_URL", "https://dagma-360-capture-frontend.vercel.app"
                )
                notify_institutional = os.getenv(
                    "NOTIFY_GROUP_INSTITUTIONAL_EMAIL", "false"
                ).strip().lower() in ("1", "true", "yes")

                grupos_requeridos_norm = {
                    normalize_grupo(g) for g in (body.grupos_requeridos or []) if g
                }
                if not grupos_requeridos_norm:
                    logger.info(
                        "[NOTIFY] No hay grupos_requeridos; no se notifica a líderes de grupo"
                    )
                    return

                logger.info(
                    f"[NOTIFY] grupos_requeridos_norm={sorted(grupos_requeridos_norm)}"
                )

                # Índice destinatarios por clave de grupo normalizada.
                lideres_por_grupo: dict[str, list[dict]] = {
                    k: [] for k in grupos_requeridos_norm
                }
                fuentes_count: dict[str, dict[str, int]] = {
                    k: {"users": 0, "lideres_grupos": 0, "grupos": 0, "institucional": 0}
                    for k in grupos_requeridos_norm
                }

                def _add(clave: str, email: str, nombre: str, fuente: str) -> None:
                    if not clave or clave not in lideres_por_grupo:
                        return
                    e = (email or "").strip()
                    if not e or "@" not in e:
                        return
                    lideres_por_grupo[clave].append(
                        {"email": e, "nombre": (nombre or "").strip() or e}
                    )
                    fuentes_count[clave][fuente] = fuentes_count[clave].get(fuente, 0) + 1

                # Fuente PRIMARIA: users con role/rol == lider
                try:
                    seen_uids: set[str] = set()
                    for rol_field in ("role", "rol"):
                        try:
                            qs = (
                                db.collection("users")
                                .where(
                                    rol_field,
                                    "in",
                                    ["lider", "líder", "LIDER", "LÍDER"],
                                )
                                .limit(1000)
                                .stream()
                            )
                        except Exception as e:
                            logger.debug(f"[NOTIFY] users.where({rol_field}) falló: {e}")
                            continue
                        for udoc in qs:
                            if udoc.id in seen_uids:
                                continue
                            seen_uids.add(udoc.id)
                            ud = udoc.to_dict() or {}
                            clave = normalize_grupo(ud.get("grupo"))
                            if clave not in grupos_requeridos_norm:
                                continue
                            email = (ud.get("email") or "").strip()
                            nombre = (
                                ud.get("full_name")
                                or ud.get("nombre_completo")
                                or ud.get("displayName")
                                or ud.get("nombre")
                                or email
                            )
                            _add(clave, email, nombre, "users")
                except Exception as e:
                    logger.warning(f"[NOTIFY] Error consultando users (líderes): {e}")

                # Fuente SECUNDARIA: colección `lideres_grupos` (xlsx)
                try:
                    for ldoc in db.collection("lideres_grupos").stream():
                        ld = ldoc.to_dict() or {}
                        clave = normalize_grupo(
                            ld.get("grupo") or ld.get("Grupo") or ""
                        )
                        if clave not in grupos_requeridos_norm:
                            continue
                        email = (
                            ld.get("email")
                            or ld.get("Email")
                            or ld.get("correo")
                            or ld.get("Correo")
                            or ""
                        ).strip()
                        nombre = (
                            ld.get("nombre_completo")
                            or ld.get("Nombre completo")
                            or ld.get("nombre")
                            or ld.get("Nombre")
                            or ld.get("lider")
                            or ld.get("Lider")
                            or ""
                        )
                        _add(clave, email, nombre, "lideres_grupos")
                except Exception as e:
                    logger.debug(f"[NOTIFY] Error leyendo lideres_grupos: {e}")

                # Fuente TERCIARIA: colección `grupos` (lider.email + institucional opcional)
                try:
                    for gdoc in db.collection("grupos").stream():
                        gdata = gdoc.to_dict() or {}
                        nombre_grupo = (gdata.get("nombre") or gdoc.id or "").strip()
                        clave = normalize_grupo(nombre_grupo)
                        if clave not in grupos_requeridos_norm:
                            continue
                        lider_raw = gdata.get("lider")
                        l_email, l_nombre = "", ""
                        if isinstance(lider_raw, dict):
                            l_email = (lider_raw.get("email") or "").strip()
                            l_nombre = (lider_raw.get("nombre") or "").strip()
                        elif isinstance(lider_raw, str):
                            s = lider_raw.strip()
                            if "@" in s:
                                l_email = s
                            else:
                                l_nombre = s
                        if l_email and "@" in l_email:
                            _add(clave, l_email, l_nombre or nombre_grupo, "grupos")
                        # Correo institucional del grupo: opt-in vía env.
                        if notify_institutional:
                            inst = (gdata.get("email") or "").strip()
                            if inst and "@" in inst:
                                _add(clave, inst, nombre_grupo, "institucional")
                except Exception as e:
                    logger.debug(f"[NOTIFY] Error leyendo grupos: {e}")

                # Despachar correos deduplicando por email global.
                ya_enviados: set[str] = set()
                envios_ok = 0
                for clave, lideres in lideres_por_grupo.items():
                    fc = fuentes_count.get(clave, {})
                    total = len(lideres)
                    logger.info(
                        f"[NOTIFY] grupo={clave} fuentes=users:{fc.get('users',0)} "
                        f"lideres_grupos:{fc.get('lideres_grupos',0)} "
                        f"grupos:{fc.get('grupos',0)} "
                        f"institucional:{fc.get('institucional',0)} total={total}"
                    )
                    if total == 0:
                        logger.warning(
                            f"[NOTIFY] grupo SIN destinatarios: {clave} — "
                            f"no se enviará correo de aviso"
                        )
                        continue
                    for lider in lideres:
                        email_to = lider["email"].lower()
                        if email_to in ya_enviados:
                            continue
                        ya_enviados.add(email_to)
                        try:
                            send_leaders_notification_email(
                                lider["email"], lider["nombre"], actividad_data, app_url,
                                lider_nombre=_lider_nombre_res or None,
                                lider_telefono=_lider_telefono_res,
                            )
                            envios_ok += 1
                            logger.info(
                                f"[NOTIFY] envío OK a {lider['email']} (grupo={clave})"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[GMAIL] Error notificando lider {lider['email']} "
                                f"(grupo={clave}): {e}"
                            )
                logger.info(
                    f"[NOTIFY] Resumen: grupos={len(grupos_requeridos_norm)} "
                    f"destinatarios_unicos={len(ya_enviados)} envios_ok={envios_ok}"
                )
            except Exception as e:
                logger.warning(
                    f"[GMAIL] Error notificando líderes de grupos requeridos: {e}"
                )

        if background_tasks is not None:
            background_tasks.add_task(_enviar_notificaciones_actividad)
        else:
            # Fallback (tests / llamada directa): mantener comportamiento previo de best-effort
            try:
                _enviar_notificaciones_actividad()
            except Exception:
                pass

        return ConvocarActividadResponse(
            success=True,
            id=actividad_id,
            message="Actividad programada exitosamente",
            marca_temporal=marca_temporal,
            data=actividad_data
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error programando actividad: {str(e)}")


@router.delete(
    "/actividades/{actividad_id}",
    summary="🔴 DELETE | Eliminar Actividad",
    description="""
## 🔴 DELETE | Eliminar Actividad

**Propósito**: Eliminar un registro de la colección `actividades` a partir de su `id`.

### 📥 Parámetros
- **actividad_id**: ID del registro a eliminar

### ✅ Respuesta exitosa
```json
{
  "success": true,
  "id": "abc-123",
  "message": "Actividad eliminada exitosamente",
  "timestamp": "2026-02-19T..."
}
```
    """
)
async def delete_actividad(actividad_id: str, background_tasks: BackgroundTasks = None):
    """
    Eliminar actividad por ID. Envía correo de cancelación a coordinador, líder y personal asignado.
    """
    try:
        collection_ref = db.collection("plan_distrito_verde")

        # Intentar primero por ID de documento
        doc_ref = collection_ref.document(actividad_id)
        doc_snapshot = await run_blocking(doc_ref.get)

        if doc_snapshot.exists:
            actividad_data_cancel = doc_snapshot.to_dict() or {}
            actividad_data_cancel['id'] = actividad_id
            await run_blocking(doc_ref.delete)
        else:
            # Fallback: buscar por campo interno 'id'
            docs = await stream_to_list(collection_ref.where("id", "==", actividad_id).limit(1))
            matching_doc = docs[0] if docs else None

            if not matching_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"No se encontró actividad con id: {actividad_id}"
                )

            actividad_data_cancel = matching_doc.to_dict() or {}
            actividad_data_cancel['id'] = actividad_id
            await run_blocking(collection_ref.document(matching_doc.id).delete)

        # Enviar correos de cancelación en background (no bloquea la respuesta)
        async def _enviar_cancelaciones():
            destinatarios = await _resolver_destinatarios_actividad_async(actividad_data_cancel)
            # Resolver líder de actividad por nombre si no hay email directo en Firestore
            lider_email_canc, lider_nombre_canc = await _resolver_lider_actividad_async(actividad_data_cancel)
            if lider_email_canc:
                destinatarios.setdefault(lider_email_canc, lider_nombre_canc or lider_email_canc)
            # Resolver teléfono del líder (users → personal_operativo por email → por nombre)
            lider_nombre_canc_fb = lider_nombre_canc or (actividad_data_cancel.get("lider_actividad") or "")
            lider_telefono_canc = await _resolver_lider_telefono_async(lider_email_canc, lider_nombre_canc_fb)
            # Incluir también líderes de los grupos requeridos
            grupos_req = actividad_data_cancel.get("grupos_requeridos") or []
            if grupos_req:
                try:
                    grupos_norm = {normalize_grupo(g) for g in grupos_req if g}
                    seen_uids: set = set()
                    for rol_field in ("role", "rol"):
                        try:
                            lider_query = (
                                db.collection("users")
                                .where(rol_field, "in", ["lider", "líder", "LIDER", "LÍDER"])
                                .limit(200)
                            )
                            lider_docs = await stream_to_list(lider_query)
                        except Exception:
                            continue
                        for udoc in lider_docs:
                            if udoc.id in seen_uids:
                                continue
                            seen_uids.add(udoc.id)
                            ud = udoc.to_dict() or {}
                            if normalize_grupo(ud.get("grupo")) not in grupos_norm:
                                continue
                            email = (ud.get("email") or "").strip().lower()
                            if email and "@" in email:
                                destinatarios.setdefault(
                                    email,
                                    ud.get("full_name") or ud.get("nombre_completo") or email,
                                )
                except Exception as e:
                    logger.warning(f"[CANCEL] Error resolviendo líderes de grupo: {e}")

            logger.info(f"[CANCEL] Enviando cancelación a {len(destinatarios)} destinatario(s)")
            for email_addr, nombre_addr in destinatarios.items():
                try:
                    await asyncio.to_thread(
                        send_activity_cancellation_email,
                        to_email=email_addr,
                        nombre=nombre_addr,
                        actividad_data=actividad_data_cancel,
                        lider_telefono=lider_telefono_canc,
                    )
                    logger.info(f"[CANCEL] Correo enviado a {email_addr}")
                except Exception as e:
                    logger.warning(f"[CANCEL] Error enviando a {email_addr}: {e}")

        task = asyncio.create_task(_enviar_cancelaciones())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return {
            "success": True,
            "id": actividad_id,
            "message": "Actividad eliminada exitosamente",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando actividad del plan Distrito Verde: {str(e)}"
        )


@router.put(
    "/actividades/{actividad_id}",
    summary="🟡 PUT | Actualizar Actividad",
    description="""
## 🟡 PUT | Actualizar Actividad

**Propósito**: Modificar cualquier campo de un registro en la colección `actividades`.

### 📥 Parámetros
- **actividad_id**: ID del registro a actualizar
- **body**: JSON con los campos a modificar (puede incluir cualquier campo)

### ✅ Respuesta exitosa
```json
{
  "success": true,
  "id": "abc-123",
  "message": "Actividad actualizada exitosamente",
  "data": {
    "id": "abc-123",
    "fecha_actividad": "20/02/2026",
    ...otros campos
  },
  "timestamp": "2026-02-19T..."
}
```

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/actividades/abc-123', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        "estado_actividad": "En Ejecución",
        "observaciones": "Cambio de observación"
    })
});
```
    """
)
async def update_actividad(
    actividad_id: str,
    body: dict,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Actualizar una actividad. Requiere nivel lider o superior.
    Si body incluye personal_asignado, dispara notificaciones (email + Calendar).
    """
    # Lider solo puede modificar personal_asignado; cualquier otro campo requiere administrador+.
    # Administrador+ puede modificar cualquier campo de cualquier actividad.
    if not current_user.at_least(Role.ADMINISTRADOR):
        allowed_fields = {"personal_asignado"}
        disallowed = set(body.keys()) - allowed_fields
        if disallowed:
            raise HTTPException(
                status_code=403,
                detail=f"Lider solo puede modificar personal_asignado. Campos no permitidos: {disallowed}",
            )
    try:
        if not body:
            raise HTTPException(
                status_code=400,
                detail="El cuerpo de la solicitud no puede estar vacío"
            )

        collection_ref = db.collection("plan_distrito_verde")

        # Resolver documento
        doc_ref = collection_ref.document(actividad_id)
        doc_snapshot = await run_blocking(doc_ref.get)

        if not doc_snapshot.exists:
            docs = await stream_to_list(collection_ref.where("id", "==", actividad_id).limit(1))
            matching_doc = docs[0] if docs else None
            if not matching_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"No se encontró actividad con id: {actividad_id}"
                )
            doc_ref = collection_ref.document(matching_doc.id)
            doc_snapshot = await run_blocking(doc_ref.get)

        # Datos anteriores (para detectar cambios en personal)
        data_anterior = doc_snapshot.to_dict() or {}

        # Normalizar grupos_requeridos si viene en el cuerpo (consistencia con migración 2026-05-22)
        if "grupos_requeridos" in body and isinstance(body.get("grupos_requeridos"), list):
            body["grupos_requeridos"] = [normalize_grupo(g) for g in body["grupos_requeridos"] if g]

        # Actualizar en Firestore
        await run_blocking(doc_ref.update, body)
        updated_doc = await run_blocking(doc_ref.get)
        updated_data = updated_doc.to_dict() or {}
        updated_data['id'] = actividad_id

        # --- Si personal_asignado cambió, disparar notificaciones ---
        if "personal_asignado" in body:
            personal_nuevo = body["personal_asignado"] or []
            personal_anterior = data_anterior.get("personal_asignado", [])
            actividad_data = updated_data

            # Detectar asignados y desasignados comparando emails
            emails_anteriores = {(p.get("email") or "").strip().lower() for p in personal_anterior if p.get("email")}
            emails_nuevos = {(p.get("email") or "").strip().lower() for p in personal_nuevo if p.get("email")}
            emails_agregados = emails_nuevos - emails_anteriores
            emails_eliminados = emails_anteriores - emails_nuevos

            logger.info(f"[EMAIL] personal_asignado en body. "
                        f"Anteriores({len(emails_anteriores)}): {emails_anteriores}, "
                        f"Nuevos({len(emails_nuevos)}): {emails_nuevos}, "
                        f"Agregados: {emails_agregados}, Eliminados: {emails_eliminados}")

            # Calendar: actualizar evento si cambió personal
            calendar_event_id = actividad_data.get("calendar_event_id")
            if calendar_event_id and (emails_agregados or emails_eliminados):
                try:
                    await asyncio.to_thread(sync_event_personnel, calendar_event_id, personal_nuevo)
                except Exception as e:
                    logger.warning(f"[CALENDAR] Error sincronizando personal en evento: {e}")

            # Emails de asignación/desasignación (background, no bloquea la respuesta)
            # Construir mapas iniciales con emails embebidos válidos
            mapa_nuevos = {(v.get("email") or "").strip().lower(): v for v in personal_nuevo if (v.get("email") or "").strip() and "@" in (v.get("email") or "")}
            mapa_anteriores = {(p.get("email") or "").strip().lower(): p for p in personal_anterior if (p.get("email") or "").strip() and "@" in (p.get("email") or "")}
            # Recuperar emails faltantes (personal sin email embebido)
            _sin_email_nuevos = [v for v in personal_nuevo if not ((v.get("email") or "").strip() and "@" in (v.get("email") or ""))]
            _sin_email_anteriores = [p for p in personal_anterior if not ((p.get("email") or "").strip() and "@" in (p.get("email") or ""))]

            # Bug #3 (2026-05-28): capturar email del actor para suprimir auto-notificaciones.
            actor_email_lower = (getattr(current_user, "email", "") or "").strip().lower()

            async def _enviar_emails():
                # Recuperar emails de personal sin email embebido
                for _p in _sin_email_nuevos:
                    _recovered = await _recuperar_email_persona(_p.get("nombre_completo", ""), _p.get("email", ""))
                    if _recovered:
                        mapa_nuevos.setdefault(_recovered, _p)
                for _p in _sin_email_anteriores:
                    _recovered = await _recuperar_email_persona(_p.get("nombre_completo", ""), _p.get("email", ""))
                    if _recovered:
                        mapa_anteriores.setdefault(_recovered, _p)
                # Resolver datos del LÍDER de la actividad (nombre + teléfono)
                # para inyectarlos en las plantillas. El campo `telefono` en
                # `actividad_data` corresponde al COORDINADOR que programó la
                # actividad, no al líder; usar ese teléfono confunde al personal.
                lider_email_act = (actividad_data.get("lider_actividad_email") or "").strip()
                lider_nombre_act = (actividad_data.get("lider_actividad") or "").strip()
                lider_telefono_act = None

                # Si no hay email directo (el frontend no lo envía), resolver por nombre
                if not lider_email_act and lider_nombre_act:
                    lider_email_act, lider_nombre_act = await _resolver_lider_actividad_async(actividad_data)

                # Resolver teléfono: users.cellphone → personal_operativo por email → por nombre
                lider_telefono_act = await _resolver_lider_telefono_async(lider_email_act, lider_nombre_act)

                for email_addr in emails_agregados:
                    persona = mapa_nuevos.get(email_addr, {})
                    logger.info(f"[EMAIL] Enviando email de ASIGNACION a: {email_addr}")
                    try:
                        result = await asyncio.to_thread(
                            send_assignment_notification_email,
                            person_email=email_addr,
                            nombre=persona.get("nombre_completo", ""),
                            grupo=persona.get("grupo", ""),
                            actividad_data=actividad_data,
                            lider_nombre=lider_nombre_act or None,
                            lider_telefono=lider_telefono_act,
                        )
                        logger.info(f"[EMAIL] Resultado asignacion {email_addr}: {result}")
                    except Exception as e:
                        logger.error(f"[EMAIL] Error enviando asignacion a {email_addr}: {e}", exc_info=True)

                for email_addr in emails_eliminados:
                    persona = mapa_anteriores.get(email_addr, {})
                    logger.info(f"[EMAIL] Enviando email de DESASIGNACION a: {email_addr}")
                    try:
                        result = await asyncio.to_thread(
                            send_removal_notification_email,
                            person_email=email_addr,
                            nombre=persona.get("nombre_completo", ""),
                            actividad_data=actividad_data,
                            lider_nombre=lider_nombre_act or None,
                            lider_telefono=lider_telefono_act,
                        )
                        logger.info(f"[EMAIL] Resultado desasignacion {email_addr}: {result}")
                    except Exception as e:
                        logger.error(f"[EMAIL] Error enviando desasignacion a {email_addr}: {e}", exc_info=True)

                # CC al líder de la actividad con el resumen del delta (agregados/removidos).
                # Bug #3 (2026-05-28): si el ACTOR que asigna ES el líder, NO autonotificar.
                actor_is_leader = bool(
                    actor_email_lower
                    and lider_email_act
                    and actor_email_lower == lider_email_act.strip().lower()
                )
                if actor_is_leader:
                    logger.info(
                        f"[POLICY] Resumen-líder OMITIDO: actor={actor_email_lower} ES el líder de la actividad"
                    )
                elif lider_email_act and "@" in lider_email_act and (emails_agregados or emails_eliminados):
                    try:
                        agregados_list = [mapa_nuevos.get(e, {}) for e in emails_agregados]
                        removidos_list = [mapa_anteriores.get(e, {}) for e in emails_eliminados]
                        await asyncio.to_thread(
                            send_assignment_summary_leader_email,
                            leader_email=lider_email_act,
                            leader_name=lider_nombre_act,
                            actividad_data=actividad_data,
                            agregados=agregados_list,
                            removidos=removidos_list,
                        )
                    except Exception as e:
                        logger.warning(f"[EMAIL] Error enviando resumen a líder {lider_email_act}: {e}")

            if emails_agregados or emails_eliminados:
                task = asyncio.create_task(_enviar_emails())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            else:
                logger.info("[EMAIL] Sin cambios en personal_asignado, no se envian emails")

        # --- Detectar cambios en campos principales de la actividad (no personal) ---
        _CAMPOS_MODIFICACION = {
            "fecha_actividad": "Fecha",
            "hora_encuentro": "Hora de encuentro",
            "tipo_jornada": "Tipo de jornada",
            "duracion_actividad": "Duración",
            "objetivo_actividad": "Objetivo",
            "lider_actividad": "Líder",
            "grupos_requeridos": "Grupos requeridos",
            "punto_encuentro": "Punto de encuentro",
            "observaciones": "Observaciones",
        }
        campos_en_body = {k for k in _CAMPOS_MODIFICACION if k in body}
        cambios_detectados = []
        for campo in campos_en_body:
            antes = data_anterior.get(campo)
            despues = updated_data.get(campo)
            if antes != despues:
                if campo == "punto_encuentro":
                    antes_str = (antes or {}).get("direccion", str(antes or "")) if isinstance(antes, dict) else str(antes or "")
                    despues_str = (despues or {}).get("direccion", str(despues or "")) if isinstance(despues, dict) else str(despues or "")
                elif campo == "grupos_requeridos":
                    antes_str = ", ".join(sorted(antes or []))
                    despues_str = ", ".join(sorted(despues or []))
                    # Ignore order-only differences
                    if sorted(antes or []) == sorted(despues or []):
                        continue
                else:
                    antes_str = str(antes or "")
                    despues_str = str(despues or "")
                cambios_detectados.append({
                    "campo": _CAMPOS_MODIFICACION[campo],
                    "antes": antes_str,
                    "despues": despues_str,
                })

        if not cambios_detectados:
            logger.info(
                f"[MODIF] Sin cambios en campos rastreados para actividad {actividad_id}. "
                f"Campos en body: {list(campos_en_body)}"
            )

        if cambios_detectados:
            app_url_mod = os.getenv("FRONTEND_URL", "https://dagma-360-capture-frontend.vercel.app")
            # Capturar copias inmutables para la closure async (evita race conditions)
            _cambios_snapshot = list(cambios_detectados)
            _updated_snapshot = dict(updated_data)

            async def _enviar_modificacion():
                destinatarios = await _resolver_destinatarios_actividad_async(_updated_snapshot)
                # Resolver líder de actividad por nombre si no hay email directo en Firestore
                lider_email_mod, lider_nombre_mod = await _resolver_lider_actividad_async(_updated_snapshot)
                if lider_email_mod:
                    destinatarios.setdefault(lider_email_mod, lider_nombre_mod or lider_email_mod)
                # Resolver teléfono: users.cellphone → personal_operativo por email → por nombre
                lider_nombre_mod_fb = lider_nombre_mod or (_updated_snapshot.get("lider_actividad") or "")
                lider_telefono_mod = await _resolver_lider_telefono_async(lider_email_mod, lider_nombre_mod_fb)
                logger.info(
                    f"[MODIF] Cambios detectados: {[c['campo'] for c in _cambios_snapshot]}. "
                    f"Enviando a {len(destinatarios)} destinatario(s): {list(destinatarios.keys())}"
                )
                for email_addr, nombre_addr in destinatarios.items():
                    try:
                        await asyncio.to_thread(
                            send_activity_modification_email,
                            to_email=email_addr,
                            nombre=nombre_addr,
                            actividad_data=_updated_snapshot,
                            cambios=_cambios_snapshot,
                            app_url=app_url_mod,
                            lider_telefono=lider_telefono_mod,
                        )
                        logger.info(f"[MODIF] Correo enviado a {email_addr}")
                    except Exception as e:
                        logger.warning(f"[MODIF] Error enviando a {email_addr}: {e}")

            task = asyncio.create_task(_enviar_modificacion())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return {
            "success": True,
            "id": actividad_id,
            "message": "Actividad actualizada exitosamente",
            "data": updated_data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error actualizando actividad: {str(e)}"
        )


# ==================== ENDPOINT: Personal Operativo ======================================#

class PersonalOperativoRequest(BaseModel):
    nombre_completo: str = Field(..., min_length=1, description="Nombre completo del personal operativo")
    email: str = Field(..., min_length=1, description="Correo electrónico")
    numero_contacto: int = Field(..., description="Número de contacto")
    grupo: str = Field(..., min_length=1, description="Grupo operativo al que pertenece")


@router.post(
    "/personal_operativo",
    summary="🟢 POST | Crear Personal Operativo",
    description="""
## 🟢 POST | Crear Personal Operativo

**Propósito**: Registrar un nuevo integrante de personal operativo en la colección `personal_operativo` de Firebase.

### 📥 Body (JSON)
- **nombre_completo**: Nombre completo del personal
- **email**: Correo electrónico
- **numero_contacto**: Número de contacto (entero)
- **grupo**: Nombre del grupo operativo

### ✅ Respuesta exitosa
```json
{
  "status": "success",
  "message": "Personal operativo creado exitosamente",
  "data": { "id": "...", "nombre_completo": "...", ... },
  "timestamp": "..."
}
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def crear_personal_operativo(
    body: PersonalOperativoRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Crear un registro de personal operativo en Firebase.
    Requiere nivel lider o superior. Lider solo puede crear personal de su grupo.
    """
    try:
        # Lider solo puede crear personal de su propio grupo
        if not current_user.at_least(Role.ADMINISTRADOR):
            if current_user.grupo and not grupos_match(body.grupo, current_user.grupo):
                raise HTTPException(
                    status_code=403,
                    detail=f"Solo puedes crear personal del grupo '{current_user.grupo}'.",
                )

        colombia_tz = pytz.timezone("America/Bogota")
        nuevo_id = str(uuid.uuid4())
        ahora = datetime.now(colombia_tz).isoformat()

        doc_data = {
            "id": nuevo_id,
            "nombre_completo": body.nombre_completo.strip(),
            "email": body.email.strip(),
            "numero_contacto": body.numero_contacto,
            "grupo": normalize_grupo(body.grupo),
            "fecha_creacion": ahora,
        }

        db.collection("personal_operativo").document(nuevo_id).set(doc_data)

        return {
            "status": "success",
            "message": "Personal operativo creado exitosamente",
            "data": doc_data,
            "timestamp": ahora,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando personal operativo: {str(e)}")


@router.get(
    "/personal_operativo",
    summary="🔵 GET | Obtener Personal Operativo",
    description="""
## 🔵 GET | Obtener Personal Operativo

**Propósito**: Consultar todos los registros de la colección `personal_operativo` en Firebase.

### 📥 Parámetros
- **grupo** (opcional): Filtrar por nombre de grupo (coincidencia exacta)

### 📝 Ejemplos de uso:
```javascript
// Todos los integrantes
fetch('/personal_operativo');

// Filtrar por grupo
fetch('/personal_operativo?grupo=Cuadrilla');
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def get_personal_operativo(
    response: Response,
    grupo: Optional[str] = Query(None, min_length=1, description="Filtrar por nombre de grupo"),
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Obtener personal operativo. Lider solo ve su grupo.
    """
    try:
        # Lider solo ve su propio grupo (filtro forzado)
        if not current_user.at_least(Role.ADMINISTRADOR) and current_user.grupo:
            grupo = current_user.grupo

        ref = db.collection("personal_operativo")
        query = ref

        if grupo:
            query = query.where("grupo", "==", normalize_grupo(grupo))

        docs = await stream_to_list(query)

        personal = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            personal.append(data)

        response.headers["Cache-Control"] = "public, max-age=60"
        return {
            "status": "success",
            "data": personal,
            "count": len(personal),
            "filters": {"grupo": normalize_grupo(grupo) if grupo else None},
            "timestamp": datetime.now(pytz.timezone("America/Bogota")).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo personal operativo: {str(e)}")


@router.patch(
    "/personal_operativo/verificar-registro",
    summary="🔄 PATCH | Verificar Registro en Users de Personal Operativo",
    description="""
## 🔄 PATCH | Verificar Registro en Users de Personal Operativo

**Propósito**: Recorre la colección `personal_operativo`, compara el `email` de cada persona
contra la colección `users` y actualiza el campo `estado_registro` de cada documento:

- **"Usuario registrado"** — si el email existe en `users`
- **"Usuario no registrado"** — si el email no existe en `users`

### 📥 Parámetros (query)
- **grupo** (opcional): limitar la verificación a un grupo específico

### ✅ Respuesta exitosa
```json
{
  "status": "success",
  "total_procesados": 12,
  "registrados": 8,
  "no_registrados": 4,
  "detalle": [
    { "id": "...", "nombre_completo": "...", "email": "...", "estado_registro": "Usuario registrado" },
    ...
  ]
}
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def verificar_registro_personal(
    grupo: Optional[str] = Query(None, description="Filtrar por grupo operativo"),
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Verifica si cada persona en personal_operativo está registrada en users (por email)
    y actualiza el campo estado_registro en cada documento.
    Lider solo puede verificar su propio grupo.
    """
    try:
        # Lider solo puede verificar su propio grupo
        if not current_user.at_least(Role.ADMINISTRADOR) and current_user.grupo:
            grupo = current_user.grupo

        # 1) Obtener emails registrados y personal en thread worker para no bloquear loop.
        def _load_users_and_personal():
            users_docs = list(db.collection("users").stream())
            emails: set[str] = set()
            for u in users_docs:
                u_data = u.to_dict() or {}
                email_u = (u_data.get("email") or "").strip().lower()
                if email_u:
                    emails.add(email_u)

            ref = db.collection("personal_operativo")
            q = ref.where("grupo", "==", normalize_grupo(grupo)) if grupo else ref
            personal_docs = list(q.stream())
            return emails, personal_docs

        emails_registrados, docs = await run_blocking(_load_users_and_personal)

        colombia_tz = pytz.timezone("America/Bogota")
        ahora = datetime.now(colombia_tz).isoformat()

        detalle = []
        registrados = 0
        no_registrados = 0

        for doc in docs:
            data = doc.to_dict() or {}
            email_p = (data.get("email") or "").strip().lower()
            estado = "Usuario registrado" if email_p in emails_registrados else "Usuario no registrado"

            # Actualizar campo en Firestore
            await run_blocking(
                db.collection("personal_operativo").document(doc.id).update,
                {
                    "estado_registro": estado,
                    "estado_registro_verificado_en": ahora,
                },
            )

            if estado == "Usuario registrado":
                registrados += 1
            else:
                no_registrados += 1

            detalle.append({
                "id": doc.id,
                "nombre_completo": data.get("nombre_completo"),
                "email": data.get("email"),
                "grupo": data.get("grupo"),
                "estado_registro": estado,
            })

        return {
            "status": "success",
            "total_procesados": len(detalle),
            "registrados": registrados,
            "no_registrados": no_registrados,
            "filtro_grupo": normalize_grupo(grupo) if grupo else None,
            "verificado_en": ahora,
            "detalle": detalle,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verificando registro: {str(e)}")


# ==================== ENDPOINT: Reportes Intervenciones Unificado (todos los grupos) ============#

# "cuadrilla" kept for dual-read during migration window — remove after migrate_flora_urbana_reportes.py runs
_GRUPOS_KEYS = ["flora_urbana", "cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"]

_SLIM_FIELDS = frozenset({
    "id", "timestamp", "grupo", "tipo_intervencion", "barrio_vereda",
    "comuna_corregimiento", "descripcion_intervencion", "photosUrl",
    "registrado_por", "direccion", "photos_uploaded", "documentos_con_enlaces",
    "numero_registro",
})


async def _fetch_grupo_reportes(grupo_key: str, id_actividad: Optional[str], grupo_filter: Optional[str]) -> tuple[str, list[dict]]:
    """Fetches reports for a single group from the unified collection, returns (grupo_key, list_of_docs)."""
    get_grupo_config(grupo_key)  # Valida que el grupo es válido
    try:
        ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
        # Dual-read: during migration window, flora_urbana reports may still be stored as "cuadrilla"
        # Remove the extra query once migrate_flora_urbana_reportes.py has been applied to production
        if grupo_key == "flora_urbana":
            query_canonical = ref.where("grupo", "==", "flora_urbana")
            query_legacy = ref.where("grupo", "==", "cuadrilla")
            if id_actividad:
                query_canonical = query_canonical.where("id_actividad", "==", id_actividad.strip())
                query_legacy = query_legacy.where("id_actividad", "==", id_actividad.strip())
            docs_canonical = await stream_to_list(query_canonical)
            docs_legacy = await stream_to_list(query_legacy)
            docs = docs_canonical + docs_legacy
        else:
            query = ref.where("grupo", "==", grupo_key)
            if id_actividad:
                query = query.where("id_actividad", "==", id_actividad.strip())
            docs = await stream_to_list(query)
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data["_grupo_key"] = grupo_key
            results.append(data)
        return grupo_key, results
    except Exception as e:
        logger.warning(f"[reportes_intervenciones] Error leyendo grupo {grupo_key}: {e}")
        return grupo_key, []


@router.get(
    "/reportes_intervenciones",
    summary="🔵 GET | Reportes de Intervención — Todos los Grupos",
    description="""
## 🔵 GET | Reportes de Intervención — Todos los Grupos

**Propósito**: Devuelve reportes de intervención de los 5 grupos operativos en una sola llamada,
usando consultas paralelas (`asyncio.gather`) a cada colección.

### 📥 Parámetros
- **grupo** (opcional): Filtrar a un solo grupo (`cuadrilla`, `vivero`, `gobernanza`, `ecosistemas`, `umata`)
- **id_actividad** (opcional): Filtrar por ID de actividad en todos los grupos

### ✅ Respuesta
```json
{
  "status": "success",
  "data": {
    "cuadrilla": [...],
    "vivero": [...],
    "gobernanza": [...],
    "ecosistemas": [...],
    "umata": [...]
  },
  "totals": { "cuadrilla": 12, "vivero": 5, ... },
  "total_general": 40,
  "timestamp": "..."
}
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def get_reportes_intervenciones_todos(
    response: Response,
    grupo: Optional[str] = Query(None, description="Filtrar por grupo operativo (cuadrilla, vivero, gobernanza, ecosistemas, umata)"),
    id_actividad: Optional[str] = Query(None, description="Filtrar por ID de actividad"),
    sin_actividad: bool = Query(False, description="Si True, retorna solo reportes sin id_actividad (huérfanos)"),
    page: int = Query(1, ge=1, description="Página a retornar (empieza en 1)"),
    per_page: int = Query(30, ge=1, le=100, description="Registros por página (máx. 100)"),
    slim: bool = Query(False, description="Retornar solo campos esenciales para listado (omite coordinates y detalle)"),
):
    """
    Retorna reportes de todos los grupos en paralelo con asyncio.gather.
    Soporta paginación (?page=1&per_page=30) y proyección de campos (?slim=true).
    Las presigned URLs de S3 se generan solo para los registros de la página actual.
    """
    if grupo and normalize_grupo(grupo) not in _GRUPOS_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"grupo debe ser uno de: {', '.join(_GRUPOS_KEYS)}"
        )

    totals: dict[str, int] = {}
    all_reportes: list[dict] = []

    if grupo:
        # Filtro específico: query indexada por grupo
        normalized_grupo = normalize_grupo(grupo)
        _, group_docs = await _fetch_grupo_reportes(normalized_grupo, id_actividad, None)
        totals[normalized_grupo] = len(group_docs)
        all_reportes = group_docs
    else:
        # Sin filtro: scan completo de la colección para no perder documentos
        # con valores de 'grupo' que no coincidan exactamente con _GRUPOS_KEYS
        try:
            ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
            query = ref
            if id_actividad:
                query = query.where("id_actividad", "==", id_actividad.strip())
            docs = await stream_to_list(query)
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                all_reportes.append(data)
                g = data.get("grupo", "")
                totals[g] = totals.get(g, 0) + 1
        except Exception as e:
            logger.warning(f"[reportes_intervenciones] Error leyendo coleccion completa: {e}")

    # Filtro huérfanos: reportes sin actividad asociada
    if sin_actividad:
        all_reportes = [r for r in all_reportes if not r.get("id_actividad")]

    # Ordenar por timestamp descendente (ISO 8601 es lexicográficamente comparable)
    all_reportes.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    total_general = len(all_reportes)
    total_pages = max(1, (total_general + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    page_items = all_reportes[offset: offset + per_page]

    # Enriquecer con presigned URLs solo los registros de la página actual
    bucket_name = os.getenv('S3_BUCKET_NAME', '360-dagma-photos')
    s3_client = None
    try:
        s3_client = get_s3_client()
    except Exception:
        logger.warning("No se pudo inicializar S3 client para presigned URLs en reportes unificados")

    if s3_client and page_items:
        enriquecer_reportes_con_enlaces(page_items, s3_client, bucket_name)

    # Enriquecer con datos de actividad asociada (solo en modo no-slim para no inflar el payload)
    if not slim and page_items:
        await _enriquecer_con_actividad(page_items)

    # Proyección slim: omitir coordinates y campos de detalle
    if slim:
        page_items = [{k: v for k, v in r.items() if k in _SLIM_FIELDS} for r in page_items]

    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "status": "success",
        "data": page_items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total_general,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "totals": totals,
        "total_general": total_general,
        "filters": {
            "grupo": normalize_grupo(grupo) if grupo else None,
            "id_actividad": id_actividad.strip() if id_actividad else None,
            "sin_actividad": sin_actividad,
            "slim": slim,
        },
        "timestamp": datetime.now(pytz.timezone("America/Bogota")).isoformat(),
    }


# ==================== ASISTENCIA ACTIVIDADES ====================#

class AlertaTipo(str, Enum):
    """Tipos predefinidos de alerta para ausencias o novedades en actividades"""
    accidente_laboral        = "accidente_laboral"
    fuerza_mayor             = "fuerza_mayor"
    llamado_atencion         = "llamado_atencion"
    abandono_sin_justif      = "abandono_sin_justificacion"
    llegada_tarde            = "llegada_tarde"
    retiro_voluntario        = "retiro_voluntario"
    otro                     = "otro"

ALERTA_TIPO_LABELS: dict = {
    "accidente_laboral":        "Accidente Laboral",
    "fuerza_mayor":             "Retiro por Fuerza Mayor",
    "llamado_atencion":         "Llamado de Atención",
    "abandono_sin_justificacion": "Abandono sin Justificación",
    "llegada_tarde":            "Llegada Tarde",
    "retiro_voluntario":        "Retiro Voluntario",
    "otro":                     "Otro",
}


class AsistenciaPersonaItem(BaseModel):
    """Registro de asistencia por persona en una actividad"""
    nombre_completo: str = Field(..., min_length=1, description="Nombre completo del integrante")
    email: Optional[str] = Field(None, description="Email del integrante")
    uid: Optional[str] = Field(None, description="UID Firebase del integrante")
    grupo: Optional[str] = Field(None, description="Grupo operativo al que pertenece")
    validacion: bool = Field(..., description="true = asistio, false = no asistio")
    observacion: Optional[str] = Field(None, description="Observacion libre sobre la asistencia")
    alerta: Optional[AlertaTipo] = Field(None, description="Tipo de alerta o novedad (solo para ausentes)")


class AsistenciaActividadRequest(BaseModel):
    """Body del POST /asistencia_actividades"""
    actividad_id: str = Field(..., min_length=1, description="ID de la actividad en plan_distrito_verde")
    personal_asignado: List[AsistenciaPersonaItem] = Field(
        ..., min_length=1, description="Lista de personal con su validacion de asistencia"
    )


class AsistenciaPatchPersonaItem(BaseModel):
    """Campos actualizables de una persona en el PATCH de asistencia.
    email es la clave de busqueda dentro del array personal_asignado."""
    email: str = Field(..., min_length=1, description="Email del integrante (clave de busqueda)")
    validacion: Optional[bool] = Field(None, description="true = asistio, false = no asistio")
    observacion: Optional[str] = Field(None, description="Observacion libre sobre la asistencia")
    alerta: Optional[AlertaTipo] = Field(None, description="Tipo de alerta o novedad (solo para ausentes)")


class AsistenciaActividadPatchRequest(BaseModel):
    """Body del PATCH /asistencia_actividades/{actividad_id}"""
    personal: List[AsistenciaPatchPersonaItem] = Field(
        ..., min_length=1, description="Lista de personas con los campos a actualizar (solo los campos enviados se modifican)"
    )


@router.get(
    "/alertas_tipos",
    summary="📋 GET | Catálogo de tipos de alerta para asistencia",
    tags=["Artefacto de Captura DAGMA"],
)
async def get_alertas_tipos():
    """
    Retorna el catálogo de tipos de alerta predefinidos para usar en el registro de asistencia.
    No requiere autenticación.
    """
    return [
        {"value": tipo.value, "label": ALERTA_TIPO_LABELS[tipo.value]}
        for tipo in AlertaTipo
    ]


@router.post(
    "/asistencia_actividades",
    summary="🟢 POST | Registrar Asistencia de Actividad",
    description="""
## 🟢 POST | Registrar Asistencia de Actividad

**Propósito**: Crea o actualiza el registro de asistencia de una actividad en la coleccion `asistencia_actividades`.
Si ya existe un documento para el `actividad_id` dado, lo reemplaza con los nuevos datos.

### 📥 Body
```json
{
  "actividad_id": "uuid-de-la-actividad",
  "personal_asignado": [
    {
      "nombre_completo": "Juan Perez",
      "email": "juan@dagma.gov.co",
      "uid": "firebase-uid",
      "grupo": "Cuadrilla",
      "validacion": true,
      "observacion": "Llego a tiempo",
      "alerta": null
    },
    {
      "nombre_completo": "Maria Lopez",
      "email": "maria@dagma.gov.co",
      "uid": null,
      "grupo": "Vivero",
      "validacion": false,
      "observacion": null,
      "alerta": "No se presento sin justificacion"
    }
  ]
}
```

### 📤 Respuesta exitosa
- **status**: `success`
- **id**: ID del documento en `asistencia_actividades` (igual al `actividad_id`)
- **total_personal**: cantidad de registros guardados
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def post_asistencia_actividad(
    body: AsistenciaActividadRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Crea o reemplaza el registro de asistencia para una actividad.
    Usa actividad_id como ID del documento en Firestore.
    """
    try:
        tz_col = pytz.timezone("America/Bogota")
        ahora = datetime.now(tz_col).isoformat()

        personal_data = [p.model_dump() for p in body.personal_asignado]

        total_personal = len(personal_data)
        asistentes = sum(1 for p in personal_data if p["validacion"] is True)
        ausentes = total_personal - asistentes
        asistencia_general = round((asistentes / total_personal) * 100, 2) if total_personal > 0 else 0.0

        doc_data = {
            "actividad_id": body.actividad_id.strip(),
            "personal_asignado": personal_data,
            "total_personal": total_personal,
            "asistentes": asistentes,
            "ausentes": ausentes,
            "asistencia_general": asistencia_general,
            "marca_temporal": ahora,
            "registrado_por": current_user.uid,
        }

        db.collection("asistencia_actividades").document(body.actividad_id.strip()).set(doc_data)

        return {
            "status": "success",
            "message": "Asistencia registrada exitosamente",
            "id": body.actividad_id.strip(),
            "data": doc_data,
            "timestamp": ahora,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error registrando asistencia: {str(e)}")


@router.get(
    "/asistencia_actividades",
    summary="🔵 GET | Obtener Asistencia de Actividad",
    description="""
## 🔵 GET | Obtener Asistencia de Actividad

**Propósito**: Consulta el registro de asistencia de una actividad especifica en la coleccion `asistencia_actividades`.

### 📥 Parametros
- **actividad_id** (requerido): ID de la actividad en `plan_distrito_verde`
- **solo_alertas** (opcional): Si `true`, retorna solo los integrantes que tienen `alerta` no nula

### 📝 Ejemplos
```javascript
// Asistencia de una actividad especifica
fetch('/asistencia_actividades?actividad_id=uuid-actividad');

// Solo integrantes con alertas
fetch('/asistencia_actividades?actividad_id=uuid-actividad&solo_alertas=true');
```
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def get_asistencia_actividades(
    response: Response,
    actividad_id: str = Query(..., min_length=1, description="ID de la actividad a consultar"),
    solo_alertas: bool = Query(False, description="Retornar solo integrantes con alerta no nula"),
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Obtiene el registro de asistencia de una actividad. actividad_id es requerido.
    """
    try:
        tz_col = pytz.timezone("America/Bogota")
        col_ref = db.collection("asistencia_actividades")

        doc = col_ref.document(actividad_id.strip()).get()
        if not doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontro asistencia para actividad_id '{actividad_id}'"
            )

        data = doc.to_dict()

        # Recalcular metrica en vuelo (garantiza precision aunque el doc sea legacy)
        personal_full = data.get("personal_asignado", [])
        total_personal = len(personal_full)
        asistentes = sum(1 for p in personal_full if p.get("validacion") is True)
        ausentes = total_personal - asistentes
        asistencia_general = round((asistentes / total_personal) * 100, 2) if total_personal > 0 else 0.0

        if solo_alertas:
            data["personal_asignado"] = [p for p in personal_full if p.get("alerta")]

        response.headers["Cache-Control"] = "no-cache"
        return {
            "status": "success",
            "data": data,
            "metricas": {
                "total_personal": total_personal,
                "asistentes": asistentes,
                "ausentes": ausentes,
                "asistencia_general": asistencia_general,
            },
            "filters": {
                "actividad_id": actividad_id.strip(),
                "solo_alertas": solo_alertas,
            },
            "timestamp": datetime.now(tz_col).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error consultando asistencia: {str(e)}")


@router.get(
    "/asistencias_resumen",
    summary="🔵 GET | Listar Resúmenes de Asistencia",
    tags=["Artefacto de Captura DAGMA"],
)
async def get_asistencias_resumen(
    grupo: Optional[str] = Query(None, description="Filtrar por nombre de grupo"),
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Retorna el resumen de todos los registros de asistencia.
    Cada item incluye métricas calculadas, lista de grupos participantes y conteo de alertas.
    Opcionalmente filtra por grupo.
    """
    try:
        tz_col = pytz.timezone("America/Bogota")
        docs = await stream_to_list(db.collection("asistencia_actividades"))
        actividad_ids = [doc.id for doc in docs]

        def _fetch_planes_map(ids: list[str]) -> dict[str, dict]:
            if not ids:
                return {}
            refs = [db.collection("plan_distrito_verde").document(doc_id) for doc_id in ids]
            out: dict[str, dict] = {}
            for snap in db.get_all(refs):
                if snap.exists:
                    out[snap.id] = snap.to_dict() or {}
            return out

        planes_map = await run_blocking(_fetch_planes_map, actividad_ids)

        resultado = []
        for doc in docs:
            data = doc.to_dict()
            if not data:
                continue
            personal_list = data.get("personal_asignado", [])

            # Filtro por grupo (si se especifica)
            if grupo:
                tiene_grupo = any(
                    grupos_match(p.get("grupo"), grupo)
                    for p in personal_list
                )
                if not tiene_grupo:
                    continue

            total = len(personal_list)
            asistentes = sum(1 for p in personal_list if p.get("validacion") is True)
            ausentes = total - asistentes
            alertas = sum(1 for p in personal_list if p.get("alerta"))
            asistencia_general = round((asistentes / total) * 100, 2) if total > 0 else 0.0
            grupos_participantes = sorted({p.get("grupo", "") for p in personal_list if p.get("grupo")})

            # Personal sin datos personales (sin email, uid)
            personal_publico = [
                {
                    "nombre_completo": p.get("nombre_completo", "—"),
                    "grupo": p.get("grupo"),
                    "validacion": p.get("validacion"),
                    "observacion": p.get("observacion"),
                    "alerta": p.get("alerta"),
                }
                for p in personal_list
            ]

            # Datos de la actividad desde plan_distrito_verde
            actividad_info: dict = {}
            act = planes_map.get(doc.id)
            if act:
                punto = act.get("punto_encuentro") or {}
                actividad_info = {
                    "fecha_actividad": act.get("fecha_actividad"),
                    "hora_encuentro": act.get("hora_encuentro"),
                    "tipo_jornada": act.get("tipo_jornada"),
                    "objetivo_actividad": act.get("objetivo_actividad"),
                    "estado_actividad": act.get("estado_actividad"),
                    "direccion": punto.get("direccion"),
                    "comunas_corregimiento": punto.get("comunas_corregimiento"),
                    "barrio_vereda": punto.get("barrio_vereda"),
                }

            resultado.append({
                "actividad_id": doc.id,
                "fecha_registro": data.get("ultima_modificacion") or data.get("marca_temporal"),
                "total_personal": total,
                "asistentes": asistentes,
                "ausentes": ausentes,
                "alertas": alertas,
                "asistencia_general": asistencia_general,
                "grupos_participantes": grupos_participantes,
                "personal_asignado": personal_publico,
                **actividad_info,
            })

        # Ordenar por fecha_actividad descendente (o fecha_registro si no hay)
        resultado.sort(
            key=lambda x: x.get("fecha_actividad") or x.get("fecha_registro") or "",
            reverse=True,
        )

        return {
            "status": "success",
            "total": len(resultado),
            "data": resultado,
            "timestamp": datetime.now(tz_col).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando asistencias: {str(e)}")


@router.patch(
    "/asistencia_actividades/{actividad_id}",
    summary="🟡 PATCH | Actualizar Asistencia de Integrante",
    description="""
## 🟡 PATCH | Actualizar Asistencia de Integrante

**Proposito**: Actualiza parcialmente la asistencia de uno o varios integrantes de una actividad.
Ideal para auto-save en UI: envia solo los campos que cambiaron, sin reenviar la lista completa.

**El documento debe existir** (crearlo primero con `POST /asistencia_actividades`).

### 📥 Path
- **actividad_id**: ID de la actividad (igual al usado en el POST)

### 📥 Body
Enviar solo los campos que cambiaron. `email` es requerido como clave de busqueda.
```json
{
  "personal": [
    {
      "email": "juan@dagma.gov.co",
      "validacion": false,
      "alerta": "No se presento sin justificacion"
    }
  ]
}
```

### 📤 Respuesta
- **data**: documento completo actualizado
- **metricas**: asistencia_general recalculada
- **actualizados**: lista de emails que fueron modificados

### ⚠️ Errores
- **404**: no existe registro de asistencia para ese `actividad_id` (usar POST primero)
- **422**: algun email del body no existe en el `personal_asignado` del documento
    """,
    tags=["Artefacto de Captura DAGMA"],
)
async def patch_asistencia_actividad(
    actividad_id: str,
    body: AsistenciaActividadPatchRequest,
    current_user: CurrentUser = Depends(require_min_role(Role.LIDER)),
):
    """
    Actualiza parcialmente la asistencia de uno o varios integrantes.
    Solo modifica los campos enviados (None = sin cambio).
    Recalcula metricas tras cada actualizacion.
    """
    try:
        tz_col = pytz.timezone("America/Bogota")
        ahora = datetime.now(tz_col).isoformat()

        doc_ref = db.collection("asistencia_actividades").document(actividad_id.strip())
        doc = doc_ref.get()

        if not doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No existe registro de asistencia para actividad_id '{actividad_id}'. Crealo primero con POST /asistencia_actividades"
            )

        data = doc.to_dict()
        personal_actual = data.get("personal_asignado", [])

        # Construir indice email -> posicion para busqueda O(1)
        indice_email = {
            p.get("email", "").strip().lower(): i
            for i, p in enumerate(personal_actual)
        }

        actualizados = []
        for patch_persona in body.personal:
            clave = patch_persona.email.strip().lower()
            if clave not in indice_email:
                raise HTTPException(
                    status_code=422,
                    detail=f"El email '{patch_persona.email}' no existe en el personal_asignado de esta actividad"
                )
            idx = indice_email[clave]
            persona = dict(personal_actual[idx])  # copia para no mutar el original

            # Merge: solo sobreescribir campos explicitamente enviados (no-None)
            if patch_persona.validacion is not None:
                persona["validacion"] = patch_persona.validacion
            if patch_persona.observacion is not None:
                persona["observacion"] = patch_persona.observacion
            if patch_persona.alerta is not None:
                persona["alerta"] = patch_persona.alerta

            personal_actual[idx] = persona
            actualizados.append(patch_persona.email)

        # Recalcular metricas
        total_personal = len(personal_actual)
        asistentes = sum(1 for p in personal_actual if p.get("validacion") is True)
        ausentes = total_personal - asistentes
        asistencia_general = round((asistentes / total_personal) * 100, 2) if total_personal > 0 else 0.0

        doc_ref.update({
            "personal_asignado": personal_actual,
            "asistentes": asistentes,
            "ausentes": ausentes,
            "asistencia_general": asistencia_general,
            "ultima_modificacion": ahora,
        })

        data["personal_asignado"] = personal_actual
        data["asistentes"] = asistentes
        data["ausentes"] = ausentes
        data["asistencia_general"] = asistencia_general
        data["ultima_modificacion"] = ahora

        return {
            "status": "success",
            "message": f"{len(actualizados)} integrante(s) actualizados",
            "actualizados": actualizados,
            "data": data,
            "metricas": {
                "total_personal": total_personal,
                "asistentes": asistentes,
                "ausentes": ausentes,
                "asistencia_general": asistencia_general,
            },
            "timestamp": ahora,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error actualizando asistencia: {str(e)}")


# ==================== ADMIN: Backfill Geo Data ====================#

@router.post(
    "/admin/backfill_geo_intervenciones",
    summary="🔧 ADMIN | Recalcular barrio_vereda / comuna_corregimiento en intervenciones",
    tags=["Artefacto de Captura DAGMA"],
    include_in_schema=True,
)
async def backfill_geo_intervenciones(
    dry_run: bool = False,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Recorre todos los documentos de `reportes_intervenciones` que tienen coordenadas Point
    pero carecen de `barrio_vereda` y/o `comuna_corregimiento` y los actualiza.

    - **dry_run=true**: solo reporta cuántos se actualizarían, sin escribir en Firestore.
    - Requiere rol **administrador**.
    """
    if not current_user.at_least(Role.ADMINISTRADOR):
        raise HTTPException(
            status_code=403,
            detail="Solo un administrador puede ejecutar el backfill de datos geográficos.",
        )

    col = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
    docs = await stream_to_list(col)

    actualizados: list[dict] = []
    omitidos = 0
    errores = 0

    for doc in docs:
        data = doc.to_dict()
        coords_field = data.get("coordinates")

        # Solo procesamos puntos
        if not coords_field or coords_field.get("type") != "Point":
            omitidos += 1
            continue

        # Si ya tiene ambos campos poblados, omitir
        if data.get("barrio_vereda") and data.get("comuna_corregimiento"):
            omitidos += 1
            continue

        raw_coords = coords_field.get("coordinates")
        if not raw_coords or len(raw_coords) < 2:
            omitidos += 1
            continue

        try:
            comuna, barrio = get_location_from_coordinates(raw_coords)
        except Exception as exc:
            logger.warning(f"Error geo para doc {doc.id}: {exc}")
            errores += 1
            continue

        if not comuna and not barrio:
            # Coordenadas fuera del área cartografiada
            omitidos += 1
            continue

        update_payload: dict = {}
        if not data.get("barrio_vereda") and barrio:
            update_payload["barrio_vereda"] = barrio
        if not data.get("comuna_corregimiento") and comuna:
            update_payload["comuna_corregimiento"] = comuna

        if not update_payload:
            omitidos += 1
            continue

        if not dry_run:
            col.document(doc.id).update(update_payload)

        actualizados.append({
            "id": doc.id,
            "grupo": data.get("grupo"),
            **update_payload,
        })

    return {
        "status": "success",
        "dry_run": dry_run,
        "actualizados": len(actualizados),
        "omitidos": omitidos,
        "errores": errores,
        "detalle": actualizados,
    }



