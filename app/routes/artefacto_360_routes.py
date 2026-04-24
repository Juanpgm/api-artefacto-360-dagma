"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query, Response, Depends, Body
from typing import List, Optional
from enum import Enum
from app.models.roles import Role, CurrentUser
from app.deps.authz import get_current_user, require_min_role
import asyncio
from datetime import datetime, timedelta, timezone
import pytz
import json
import uuid

# Conjunto para mantener referencias fuertes a background tasks (evita garbage collection)
_background_tasks = set()
import math
import os
import io
import logging

from pydantic import BaseModel, Field
from app.models.validation import CoordinatesModel, ArbolesDataModel

# Importar configuración de Firebase y S3/Storage
from app.firebase_config import db
import boto3
from botocore.exceptions import ClientError

# Servicios de notificación Google (Gmail + Calendar)
from app.services.gmail_service import (
    send_activity_confirmation_email,
    send_assignment_notification_email,
    send_removal_notification_email,
)
from app.services.calendar_service import create_activity_event, sync_event_personnel

logger = logging.getLogger(__name__)

# Importar librerías para intersecciones geográficas

from shapely.geometry import Point, shape
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
        print(f"⚠️ Error cargando GeoJSON {filepath}: {str(e)}")
        return {}

# Cargar los datos al iniciar

_COMUNAS_FEATURES = _load_geojson_features(_COMUNAS_FILE)
_BARRIOS_FEATURES = _load_geojson_features(_BARRIOS_FILE)

# Crear índices espaciales para búsquedas eficientes
_COMUNAS_INDEX = SpatialIndex(_COMUNAS_FEATURES, 'comuna_corregimiento')
_BARRIOS_INDEX = SpatialIndex(_BARRIOS_FEATURES, 'barrio_vereda')

print(f"✅ Cargadas {len(_COMUNAS_FEATURES)} comunas/corregimientos")
print(f"✅ Cargados {len(_BARRIOS_FEATURES)} barrios/veredas")



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
        print(f"❌ Error en intersección geográfica (índice): {str(e)}")
        return None, None


# ==================== FUNCIONES AUXILIARES ====================#
from app.utils.clean_json import clean_json



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
                photo_content = await photo.read()
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
                print(f"❌ Error subiendo foto a S3: {str(e)}")
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
            print(f"⚠️ Modo desarrollo: URL ficticia generada para {photo.filename}")
            return doc_meta

    # Ejecutar todas las cargas concurrentemente
    tasks = [upload_single_photo(i, photo) for i, photo in enumerate(photos)]
    documentos = await asyncio.gather(*tasks)
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
            print(f"⚠️ Error generando presigned URL para {s3_key}: {str(e)}")
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
                print(f"⚠️ No se pudo obtener metadata de S3 para {s3_key}: {str(e)}")
        documentos.append(doc_meta)
    return documentos


def enriquecer_reportes_con_enlaces(reportes: list, s3_client, bucket_name: str) -> list:
    """
    Enriquece lista de reportes con documentos_con_enlaces.
    Maneja tanto formato nuevo (documentos) como legacy (photosUrl).
    """
    for reporte in reportes:
        # Si no existe 'documentos' pero sí 'photosUrl', convertir y asignar
        if not reporte.get("documentos") and reporte.get("photosUrl"):
            reporte["documentos"] = convertir_photosUrl_a_documentos(
                reporte["photosUrl"], s3_client, bucket_name
            )
        if reporte.get("documentos"):
            reporte["documentos_con_enlaces"] = generar_documentos_con_enlaces(
                reporte["documentos"], s3_client, bucket_name
            )
            reporte["total_documentos"] = len(reporte["documentos"])
        else:
            reporte["documentos_con_enlaces"] = []
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


# ==================== CONFIGURACIÓN DE GRUPOS OPERATIVOS ====================#

# Colección única para todos los reportes de intervención de todos los grupos.
# El campo "grupo" dentro de cada documento es el discriminador.
COLLECTION_REPORTES_INTERVENCIONES = "reportes_intervenciones"

GRUPOS_CONFIG = {
    "cuadrilla": {
        "display_name": "Cuadrilla",
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
    config = GRUPOS_CONFIG.get(grupo.lower())
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

    if grupo_key == "cuadrilla":
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
    # Solo se fuerza el registrado_por desde el token para no confiar en el form.
    if current_user is not None:
        registrado_por = current_user.uid

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
                print(f"📍 Recibido coordinates_data: {repr(coordinates_data)}")
                print(f"📍 Tipo: {type(coordinates_data)}, Long: {len(coordinates_data) if coordinates_data else 0}")

                coordinates_str = coordinates_data.strip()

                if not coordinates_str.startswith('['):
                    parts = coordinates_str.split(',')
                    if len(parts) == 2:
                        try:
                            lon = float(parts[0].strip())
                            lat = float(parts[1].strip())
                            coordinates = [lon, lat]
                            print(f"✅ Coordenadas parseadas como lon,lat: {coordinates}")
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
                            print(f"✅ Comuna/Corregimiento encontrada: {comuna_corregimiento}")
                        if barrio_vereda:
                            print(f"✅ Barrio/Vereda encontrado: {barrio_vereda}")
                    except Exception as e:
                        print(f"⚠️ Error obteniendo ubicación: {str(e)}")
                else:
                    print(f"ℹ️ La geolocalización solo es disponible para geometría Point, se capturó {coordinates_type}")

            except json.JSONDecodeError as e:
                print(f"❌ Error JSON: {str(e)}")
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
                print(f"⚠️ ADVERTENCIA: {str(e)}. Las fotos NO se subirán a S3.")

            documentos = await upload_photos_to_s3(photos, s3_prefix, reporte_id, s3_client, bucket_name)

        # Preparar datos comunes para guardar en Firebase
        reporte_data = {
            "id": reporte_id,
            "tipo_intervencion": tipo_intervencion,
            "descripcion_intervencion": descripcion_intervencion,
            "direccion": direccion,
            "registrado_por": registrado_por,
            "grupo": grupo_key,  # Campo canónico desde URL, no del form
            "id_actividad": id_actividad,
            "observaciones": observaciones or "",
            "coordinates": geometry,
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
            print(f"✅ Reporte de intervención grupo {display_name} {reporte_id} guardado en Firebase")
        except Exception as e:
            print(f"❌ Error guardando en Firebase: {str(e)}")
            if s3_client:
                for doc in documentos:
                    try:
                        s3_client.delete_object(Bucket=bucket_name, Key=doc["s3_key"])
                    except:
                        pass
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
            timestamp=timestamp
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
) -> dict:
    """
    Handler unificado para GET de reportes de intervención.
    Maneja todos los grupos operativos con un solo flujo de lógica.
    """
    config = get_grupo_config(grupo_key)
    display_name = config["display_name"]

    # Operador y lider solo pueden ver su propio grupo
    if current_user is not None and not current_user.at_least(Role.ADMINISTRADOR):
        if current_user.grupo and current_user.grupo.lower() != grupo_key.lower():
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
            print("⚠️ No se pudo inicializar S3 client para presigned URLs")

        # Si se proporciona un ID específico, buscar directamente por document ID
        if id:
            doc = reportes_ref.document(id).get()
            if doc.exists:
                data = doc.to_dict()
                # Verificar que el reporte pertenece al grupo solicitado
                if data.get("grupo") != grupo_key:
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
            docs = reportes_ref.where("grupo", "==", grupo_key).where("id", "==", id).stream()
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

        # Obtener documentos
        docs = query.stream()

        reportes = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            reportes.append(_limpiar_reporte(data))

        if s3_client:
            enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
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
        print(f"❌ Error obteniendo reportes de intervención grupo {display_name}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes de intervención del grupo {display_name}: {str(e)}"
        )


# ==================== RUTAS UNIFICADAS: /grupos/{grupo}/... ====================#

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
    current_user: CurrentUser = Depends(get_current_user),
):
    return await _get_reportes_intervenciones(
        grupo_key=grupo_key, id=id, id_actividad=id_actividad, grupo=grupo, current_user=current_user
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
        grupo_key="cuadrilla", tipo_intervencion=tipo_intervencion, descripcion_intervencion=descripcion_intervencion,
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
    return await _get_reportes_intervenciones(grupo_key="cuadrilla", id=id, id_actividad=id_actividad, grupo=grupo)

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

        docs = query.stream()

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
        def obtener_personal_asignado(actividad_document_id: str, actividad_id_interno: Optional[str] = None, doc_data: dict = None) -> list[dict]:
            """
            Lee personal_asignado del campo del documento (fuente principal, escrita por PATCH/PUT).
            Si el campo no existe o está vacío, hace fallback a la subcolección
            personal_asignado_actividad (datos legacy del POST /asignar_personal_actividad).
            """
            # 1) Fuente principal: campo del documento
            if doc_data and isinstance(doc_data.get("personal_asignado"), list) and len(doc_data["personal_asignado"]) > 0:
                return doc_data["personal_asignado"]

            # 2) Fallback: subcolección personal_asignado_actividad
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
            doc = plan_ref.document(id).get()
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
            docs = plan_ref.where("id", "==", id).stream()
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

        if grupo:
            # Filter by grupos_requeridos array (stores display names like "Cuadrilla", "Vivero", etc.)
            query = query.where("grupos_requeridos", "array_contains", grupo.strip())

        # Paginación cursor-based (Firestore no soporta OFFSET)
        if start_after:
            cursor_snap = plan_ref.document(start_after).get()
            if cursor_snap.exists:
                query = query.start_after(cursor_snap)

        query = query.limit(limit)
        docs = query.stream()

        actividades = []
        last_doc_id = None
        for doc in docs:
            data = doc.to_dict()
            actividad_id_interno = data.get("id") if isinstance(data, dict) else None
            personal = obtener_personal_asignado(doc.id, actividad_id_interno, doc_data=data)
            data['id'] = doc.id
            data['grupo'] = personal
            data['personal_asignado'] = personal
            actividades.append(data)
            last_doc_id = doc.id

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
        print(f"❌ Error obteniendo actividades: {str(e)}")
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
    body: ConvocarActividadRequest = Body(...)
):
    """
    Convoca una actividad y la registra en la base de datos, calculando comuna/corregimiento y barrio/vereda.
    """
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
            "grupos_requeridos": body.grupos_requeridos,
            "lider_actividad": body.lider_actividad,
            "punto_encuentro": punto,
            "observaciones": body.observaciones or "",
            "telefono": body.telefono,
            "objetivo_actividad": body.objetivo_actividad,
            "email": body.email,
            "estado_actividad": "Programada"
        }
        # Crear evento en Google Calendar con el coordinador como asistente
        try:
            created_event = create_activity_event(
                actividad_data=actividad_data,
                attendee_emails=[body.email]
            )
            if created_event:
                actividad_data['calendar_event_id'] = created_event.get('id')
                actividad_data['calendar_event_link'] = created_event.get('htmlLink')
        except Exception as e:
            logger.warning(f"[CALENDAR] Error creando evento: {e}")
            actividad_data['calendar_event_error'] = str(e)

        # Guardar en Firebase
        db.collection("plan_distrito_verde").document(actividad_id).set(actividad_data)

        # Email de confirmación al coordinador (no bloqueante)
        try:
            send_activity_confirmation_email(body.email, actividad_data)
        except Exception as e:
            logger.warning(f"[GMAIL] Error enviando confirmación: {e}")

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
async def delete_actividad(actividad_id: str):
    """
    Eliminar actividad por ID
    """
    try:
        collection_ref = db.collection("plan_distrito_verde")

        # Intentar primero por ID de documento
        doc_ref = collection_ref.document(actividad_id)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            doc_ref.delete()
            return {
                "success": True,
                "id": actividad_id,
                "message": "Actividad eliminada exitosamente",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        # Fallback: buscar por campo interno 'id'
        docs = collection_ref.where("id", "==", actividad_id).limit(1).stream()
        matching_doc = next(docs, None)

        if not matching_doc:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró actividad con id: {actividad_id}"
            )

        collection_ref.document(matching_doc.id).delete()

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
    # Solo lider de un grupo específico puede asignar personal en actividades que requieran su grupo
    # Administrador+ puede modificar cualquier campo de cualquier actividad
    if not current_user.at_least(Role.ADMINISTRADOR) and "estado_actividad" in body:
        # Solo administrador puede cambiar estado de actividad (no solo personal_asignado)
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
        doc_snapshot = doc_ref.get()

        if not doc_snapshot.exists:
            docs = collection_ref.where("id", "==", actividad_id).limit(1).stream()
            matching_doc = next(docs, None)
            if not matching_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"No se encontró actividad con id: {actividad_id}"
                )
            doc_ref = collection_ref.document(matching_doc.id)
            doc_snapshot = doc_ref.get()

        # Datos anteriores (para detectar cambios en personal)
        data_anterior = doc_snapshot.to_dict() or {}

        # Actualizar en Firestore
        doc_ref.update(body)
        updated_doc = doc_ref.get()
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
            mapa_nuevos = {(v.get("email") or "").strip().lower(): v for v in personal_nuevo if v.get("email")}
            mapa_anteriores = {(p.get("email") or "").strip().lower(): p for p in personal_anterior if p.get("email")}

            async def _enviar_emails():
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
                        )
                        logger.info(f"[EMAIL] Resultado desasignacion {email_addr}: {result}")
                    except Exception as e:
                        logger.error(f"[EMAIL] Error enviando desasignacion a {email_addr}: {e}", exc_info=True)

            if emails_agregados or emails_eliminados:
                task = asyncio.create_task(_enviar_emails())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            else:
                logger.info("[EMAIL] Sin cambios en personal_asignado, no se envian emails")

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
            if current_user.grupo and body.grupo.strip().lower() != current_user.grupo.lower():
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
            "grupo": body.grupo.strip(),
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
            query = query.where("grupo", "==", grupo.strip())

        docs = query.stream()

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
            "filters": {"grupo": grupo.strip() if grupo else None},
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

        # 1. Obtener todos los emails registrados en users (índice en memoria)
        users_query = db.collection("users").stream()
        emails_registrados: set[str] = set()
        for u in users_query:
            u_data = u.to_dict() or {}
            email_u = (u_data.get("email") or "").strip().lower()
            if email_u:
                emails_registrados.add(email_u)

        # 2. Obtener personal_operativo (con filtro de grupo si aplica)
        ref = db.collection("personal_operativo")
        query = ref.where("grupo", "==", grupo.strip()) if grupo else ref
        docs = list(query.stream())

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
            db.collection("personal_operativo").document(doc.id).update({
                "estado_registro": estado,
                "estado_registro_verificado_en": ahora,
            })

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
            "filtro_grupo": grupo.strip() if grupo else None,
            "verificado_en": ahora,
            "detalle": detalle,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verificando registro: {str(e)}")


# ==================== ENDPOINT: Reportes Intervenciones Unificado (todos los grupos) ============#

_GRUPOS_KEYS = ["cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"]

_SLIM_FIELDS = frozenset({
    "id", "timestamp", "grupo", "tipo_intervencion", "barrio_vereda",
    "comuna_corregimiento", "descripcion_intervencion", "photosUrl",
    "registrado_por", "direccion", "photos_uploaded", "documentos_con_enlaces",
})


async def _fetch_grupo_reportes(grupo_key: str, id_actividad: Optional[str], grupo_filter: Optional[str]) -> tuple[str, list[dict]]:
    """Fetches reports for a single group from the unified collection, returns (grupo_key, list_of_docs)."""
    get_grupo_config(grupo_key)  # Valida que el grupo es válido
    try:
        ref = db.collection(COLLECTION_REPORTES_INTERVENCIONES)
        # Siempre filtramos por grupo (campo discriminador en la colección unificada)
        query = ref.where("grupo", "==", grupo_key)
        if id_actividad:
            query = query.where("id_actividad", "==", id_actividad.strip())
        docs = query.stream()
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
    page: int = Query(1, ge=1, description="Página a retornar (empieza en 1)"),
    per_page: int = Query(30, ge=1, le=100, description="Registros por página (máx. 100)"),
    slim: bool = Query(False, description="Retornar solo campos esenciales para listado (omite coordinates y detalle)"),
):
    """
    Retorna reportes de todos los grupos en paralelo con asyncio.gather.
    Soporta paginación (?page=1&per_page=30) y proyección de campos (?slim=true).
    Las presigned URLs de S3 se generan solo para los registros de la página actual.
    """
    if grupo and grupo.strip().lower() not in _GRUPOS_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"grupo debe ser uno de: {', '.join(_GRUPOS_KEYS)}"
        )

    grupos_a_consultar = [grupo.strip().lower()] if grupo else _GRUPOS_KEYS

    tasks = [
        _fetch_grupo_reportes(g, id_actividad, None)
        for g in grupos_a_consultar
    ]

    results = await asyncio.gather(*tasks)

    # Conteos totales por grupo (sobre el dataset completo, sin paginar)
    totals: dict[str, int] = {}
    all_reportes: list[dict] = []
    for grupo_key, reportes in results:
        totals[grupo_key] = len(reportes)
        all_reportes.extend(reportes)

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
        print("⚠️ No se pudo inicializar S3 client para presigned URLs en reportes unificados")

    if s3_client and page_items:
        enriquecer_reportes_con_enlaces(page_items, s3_client, bucket_name)

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
            "grupo": grupo.strip().lower() if grupo else None,
            "id_actividad": id_actividad.strip() if id_actividad else None,
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
        docs = db.collection("asistencia_actividades").stream()
        resultado = []
        for doc in docs:
            data = doc.to_dict()
            if not data:
                continue
            personal_list = data.get("personal_asignado", [])

            # Filtro por grupo (si se especifica)
            if grupo:
                tiene_grupo = any(
                    (p.get("grupo") or "").strip().lower() == grupo.strip().lower()
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
            try:
                act_doc = db.collection("plan_distrito_verde").document(doc.id).get()
                if act_doc.exists:
                    act = act_doc.to_dict() or {}
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
            except Exception:
                pass  # Si falla la búsqueda del plan, continuar con lo disponible

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
    docs = col.stream()

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
            print(f"⚠️ Error geo para doc {doc.id}: {exc}")
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

