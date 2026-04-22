"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query, Response, Depends, Body
from typing import List, Optional
from app.models.roles import Role, CurrentUser
from app.deps.authz import get_current_user, require_min_role, enforce_group_scope
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
                s3_client.upload_fileobj(
                    io.BytesIO(photo_content),
                    bucket_name,
                    s3_key,
                    ExtraArgs={'ContentType': photo.content_type}
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

    # Fin función


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

GRUPOS_CONFIG = {
    "cuadrilla": {
        "collection": "reportes_intervenciones_grupo_cuadrilla",
        "display_name": "Cuadrilla",
        "s3_prefix": "cuadrilla",
    },
    "vivero": {
        "collection": "reportes_intervenciones_grupo_vivero",
        "display_name": "Vivero",
        "s3_prefix": "vivero",
    },
    "gobernanza": {
        "collection": "reportes_intervenciones_grupo_gobernanza",
        "display_name": "Gobernanza",
        "s3_prefix": "gobernanza",
    },
    "ecosistemas": {
        "collection": "reportes_intervenciones_grupo_ecosistemas",
        "display_name": "Ecosistemas",
        "s3_prefix": "ecosistemas",
    },
    "umata": {
        "collection": "reportes_intervenciones_grupo_umata",
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
    registrado_por: Optional[str],
    grupo: Optional[str],
    id_actividad: Optional[str],
    observaciones: Optional[str],
    coordinates_type: Optional[str],
    coordinates_data: Optional[str],
    photos: Optional[List[UploadFile]],
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
    collection_name = config["collection"]
    s3_prefix = config["s3_prefix"]
    display_name = config["display_name"]

    # Validar scope de grupo: operador/lider solo puede registrar en su propio grupo
    if current_user is not None:
        grupo_effectivo = enforce_group_scope(current_user, grupo_key)
        if grupo_effectivo and grupo_effectivo != grupo_key:
            raise HTTPException(
                status_code=403,
                detail=f"No tienes acceso al grupo '{grupo_key}'. Solo puedes registrar en '{current_user.grupo}'.",
            )
        # Forzar registrado_por desde el token (no confiamos en el form)
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
            "grupo": grupo,
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

        # Guardar en Firebase
        try:
            db.collection(collection_name).document(reporte_id).set(reporte_data)
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
    collection_name = config["collection"]
    display_name = config["display_name"]

    # Operador y lider solo pueden ver su propio grupo
    if current_user is not None and not current_user.at_least(Role.ADMINISTRADOR):
        if current_user.grupo and current_user.grupo.lower() != grupo_key.lower():
            raise HTTPException(
                status_code=403,
                detail=f"No tienes acceso al grupo '{grupo_key}'. Solo puedes consultar '{current_user.grupo}'.",
            )
        # Forzar filtro de grupo a su propio grupo
        grupo = current_user.grupo

    try:
        reportes_ref = db.collection(collection_name)
        bucket_name = os.getenv('S3_BUCKET_NAME', '360-dagma-photos')
        s3_client = None
        try:
            s3_client = get_s3_client()
        except Exception:
            print("⚠️ No se pudo inicializar S3 client para presigned URLs")

        # Si se proporciona un ID específico, buscar directamente
        if id:
            doc = reportes_ref.document(id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                reportes = [data]
                if s3_client:
                    enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
                return {
                    "success": True,
                    "total": 1,
                    "data": reportes,
                    "filters": {
                        "id": id,
                        "id_actividad": id_actividad,
                        "grupo": grupo
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            # Fallback: buscar por campo interno 'id'
            docs = reportes_ref.where("id", "==", id).stream()
            reportes = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                reportes.append(data)

            if not reportes:
                return {
                    "success": True,
                    "total": 0,
                    "data": [],
                    "filters": {
                        "id": id,
                        "id_actividad": id_actividad,
                        "grupo": grupo
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

            if s3_client:
                enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
            return {
                "success": True,
                "total": len(reportes),
                "data": reportes,
                "filters": {
                    "id": id,
                    "id_actividad": id_actividad,
                    "grupo": grupo
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        # Aplicar filtros opcionales
        query = reportes_ref

        if id_actividad:
            query = query.where('id_actividad', '==', id_actividad.strip())

        if grupo:
            query = query.where('grupo', '==', grupo.strip())

        # Obtener documentos
        docs = query.stream()

        reportes = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            reportes.append(data)

        if s3_client:
            enriquecer_reportes_con_enlaces(reportes, s3_client, bucket_name)
        return {
            "success": True,
            "total": len(reportes),
            "data": reportes,
            "filters": {
                "id": id,
                "id_actividad": id_actividad.strip() if id_actividad else None,
                "grupo": grupo.strip() if grupo else None
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

**Propósito**: Registrar un reporte de intervención para cualquier grupo operativo DAGMA.

### 🏷️ Grupos válidos (usar en la URL):
`cuadrilla`, `vivero`, `gobernanza`, `ecosistemas`, `umata`

**Ejemplo**: `POST /grupos/cuadrilla/reporte_intervencion`

### ✅ Campos comunes (todos los grupos):
- **tipo_intervencion**: Tipo de intervención realizada
- **descripcion_intervencion**: Descripción detallada
- **direccion**: Dirección de la intervención
- **registrado_por**: Persona que registra
- **grupo**: Grupo operativo
- **id_actividad**: ID de la actividad asociada
- **observaciones**: Observaciones adicionales
- **coordinates_type**: Tipo de geometría (Point, LineString, Polygon)
- **coordinates_data**: Coordenadas GPS en formato JSON array
- **photos**: Archivos de fotos (máximo 10)

### 🔧 Campos específicos por grupo:
- **Cuadrilla** → `arboles_data`: JSON array de árboles `[{"especie": "Ceiba", "cantidad": 5}]`
- **Vivero** → `tipos_plantas`: JSON dict de plantas `{"Guayacán": 10, "Ceiba": 5}`
- **Gobernanza** → `unidades_impactadas`: Número entero
- **Ecosistemas** → `unidad_medida` + `unidades_impactadas`
- **UMATA** → `unidades_impactadas`: Número entero

### 📝 Ejemplo:
```javascript
const formData = new FormData();
formData.append('tipo_intervencion', 'Mantenimiento');
formData.append('descripcion_intervencion', 'Poda de árboles');
formData.append('coordinates_type', 'Point');
formData.append('coordinates_data', '[-76.5225, 3.4516]');
formData.append('photos', file1);

fetch('/grupos/cuadrilla/reporte_intervencion', { method: 'POST', body: formData });
```
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

**Propósito**: Consultar reportes de intervención de cualquier grupo operativo DAGMA.

### 🏷️ Grupos válidos (usar en la URL):
`cuadrilla`, `vivero`, `gobernanza`, `ecosistemas`, `umata`

**Ejemplo**: `GET /grupos/vivero/reportes_intervenciones`

### 📥 Parámetros de Filtrado (opcionales):
- **id**: Filtrar por ID específico del reporte
- **id_actividad**: Filtrar por ID de actividad asociada
- **grupo**: Filtrar por nombre del grupo operativo

### 📝 Ejemplos:
```javascript
fetch('/grupos/cuadrilla/reportes_intervenciones');
fetch('/grupos/vivero/reportes_intervenciones?id_actividad=ACT-2026-1234');
fetch('/grupos/ecosistemas/reportes_intervenciones?id=abc-123-xyz');
```
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
            query = query.where("grupo", "==", grupo.strip())

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
    "descripcion_intervencion", "photosUrl", "registrado_por", "direccion",
    "photos_uploaded", "documentos_con_enlaces",
})


async def _fetch_grupo_reportes(grupo_key: str, id_actividad: Optional[str], grupo_filter: Optional[str]) -> tuple[str, list[dict]]:
    """Fetches reports for a single group collection, returns (grupo_key, list_of_docs)."""
    config = get_grupo_config(grupo_key)
    collection_name = config["collection"]
    try:
        ref = db.collection(collection_name)
        query = ref
        if id_actividad:
            query = query.where("id_actividad", "==", id_actividad.strip())
        if grupo_filter:
            query = query.where("grupo", "==", grupo_filter.strip())
        docs = query.stream()
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data["_grupo_key"] = grupo_key
            results.append(data)
        return grupo_key, results
    except Exception as e:
        logger.warning(f"[reportes_intervenciones] Error leyendo {collection_name}: {e}")
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

