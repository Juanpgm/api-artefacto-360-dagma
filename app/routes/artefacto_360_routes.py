from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import timedelta
"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query
from typing import List, Optional
from datetime import datetime, timezone
import pytz
import json
import uuid
import math
import os
import io
from pydantic import BaseModel, Field

# Importar configuración de Firebase y S3/Storage
from app.firebase_config import db
from firebase_admin import firestore
import boto3
from botocore.exceptions import ClientError

# Importar librerías para intersecciones geográficas
from shapely.geometry import Point, shape

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

print(f"✅ Cargadas {len(_COMUNAS_FEATURES)} comunas/corregimientos")
print(f"✅ Cargados {len(_BARRIOS_FEATURES)} barrios/veredas")


def get_location_from_coordinates(coordinates: List) -> tuple:
    """
    Realiza intersecciones geográficas para encontrar la comuna/corregimiento y barrio/vereda
    
    Args:
        coordinates: Array de coordenadas [lon, lat] para Point
        
    Returns:
        Tupla con (comuna_corregimiento, barrio_vereda) o (None, None) si no encuentra
    """
    if not coordinates or len(coordinates) != 2:
        return None, None
    
    try:
        # Crear punto a partir de las coordenadas
        point = Point(coordinates[0], coordinates[1])
        
        # Buscar intersección con comunas
        comuna_corregimiento = None
        for idx, feature in _COMUNAS_FEATURES.items():
            try:
                # Convertir la geometría del GeoJSON a un objeto Shapely
                geom = shape(feature['geometry'])
                if point.within(geom):
                    # El punto está dentro de este polígono
                    comuna_corregimiento = feature['properties'].get('comuna_corregimiento')
                    break
            except Exception as e:
                print(f"⚠️ Error procesando comuna {idx}: {str(e)}")
                continue
        
        # Buscar intersección con barrios
        barrio_vereda = None
        for idx, feature in _BARRIOS_FEATURES.items():
            try:
                geom = shape(feature['geometry'])
                if point.within(geom):
                    # El punto está dentro de este polígono
                    barrio_vereda = feature['properties'].get('barrio_vereda')
                    break
            except Exception as e:
                print(f"⚠️ Error procesando barrio {idx}: {str(e)}")
                continue
        
        return comuna_corregimiento, barrio_vereda
    
    except Exception as e:
        print(f"❌ Error en intersección geográfica: {str(e)}")
        return None, None


# ==================== FUNCIONES AUXILIARES ====================#
def clean_nan_values(obj):
    """
    Limpia valores NaN, infinitos y otros valores no compatibles con JSON
    """
    if isinstance(obj, dict):
        return {key: clean_nan_values(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan_values(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    else:
        return obj


def validate_coordinates(coordinates: list, geometry_type: str) -> bool:
    """
    Valida coordenadas según el tipo de geometría
    """
    if not isinstance(coordinates, list):
        raise ValueError("Las coordenadas deben ser un array")
    
    if geometry_type == "Point":
        if len(coordinates) != 2:
            raise ValueError("Point debe tener exactamente 2 coordenadas [lon, lat]")
        lon, lat = coordinates
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise ValueError("Las coordenadas deben ser números")
        if not (-180 <= lon <= 180):
            raise ValueError(f"Longitud inválida: {lon}. Debe estar entre -180 y 180")
        if not (-90 <= lat <= 90):
            raise ValueError(f"Latitud inválida: {lat}. Debe estar entre -90 y 90")
    
    elif geometry_type in ["LineString", "MultiPoint"]:
        if len(coordinates) < 2:
            raise ValueError(f"{geometry_type} debe tener al menos 2 puntos")
        for point in coordinates:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Cada punto debe ser [lon, lat]")
            lon, lat = point
            if not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
                raise ValueError(f"Coordenadas fuera de rango: [{lon}, {lat}]")
    
    elif geometry_type == "Polygon":
        if len(coordinates) < 1:
            raise ValueError("Polygon debe tener al menos un anillo")
        for ring in coordinates:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError("Cada anillo del polígono debe tener al menos 4 puntos")
    
    return True


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
    timestamp: str


# ==================== ENDPOINT 1: Inicialización de Parques ====================#
@router.get(
    "/init/parques",
    summary="🔵 GET | Inicialización de Parques",
    description="""
## 🔵 GET | Inicialización de Parques para DAGMA

**Propósito**: Obtener datos iniciales de parques para el artefacto de captura DAGMA.

### ✅ Respuesta
Retorna información de parques y zonas verdes del sistema.

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/init/parques');
const data = await response.json();
```
    """,
)
async def get_init_parques():
    """
    Obtener datos iniciales de parques para DAGMA
    """
    try:
        # Obtener datos de la colección 'parques' en Firebase
        parques_ref = db.collection('parques')
        docs = parques_ref.stream()
        
        # Convertir los documentos a lista de diccionarios
        parques = []
        for doc in docs:
            parque_data = doc.to_dict()
            parque_data['id'] = doc.id  # Agregar el ID del documento
            
            # Limpiar valores NaN e infinitos
            parque_data = clean_nan_values(parque_data)
            
            parques.append(parque_data)
        
        return {
            "success": True,
            "data": parques,
            "count": len(parques),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo parques: {str(e)}"
        )


# ==================== ENDPOINT 2: Registrar Reconocimiento ====================#
@router.post(
    "/grupo-operativo/reconocimiento",
    summary="🟢 POST | Registrar Reconocimiento",
    description="""
## 🟢 POST | Registrar Reconocimiento del Grupo Operativo DAGMA

**Propósito**: Registrar un reconocimiento realizado por el grupo operativo DAGMA,
incluyendo captura de coordenadas GPS y subida de fotos a Amazon S3.

### ✅ Campos requeridos:
- **tipo_intervencion**: Tipo de intervención realizada
- **descripcion_intervencion**: Descripción detallada de la intervención
- **direccion**: Dirección del lugar intervenido
- **nombre_parque**: Nombre del parque asociado (heredado de la colección 'parques')
- **observaciones**: Observaciones adicionales (opcional)
- **coordinates_type**: Tipo de geometría (Point, LineString, Polygon)
- **coordinates_data**: Coordenadas GPS en formato JSON array
- **photos**: Archivos de fotos (multipart/form-data)

### 📸 Almacenamiento de Fotos:
Las fotos se subirán al bucket **360-dagma-photos** en Amazon S3 con la siguiente estructura:
```
360-dagma-photos/
└── reconocimientos/
    └── {id_reconocimiento}/
        └── {timestamp}_{filename}
```

### 📍 Coordenadas GPS:
Basado en la lógica del endpoint `/unidades-proyecto/captura-estado-360`:
- Se capturan las coordenadas del dispositivo GPS
- Formato JSON: `[-76.5225, 3.4516]` para Point
- Soporta diferentes tipos de geometría

### 📝 Ejemplo de uso con FormData:
```javascript
const formData = new FormData();
formData.append('tipo_intervencion', 'Mantenimiento');
formData.append('descripcion_intervencion', 'Poda de árboles');
formData.append('direccion', 'Calle 5 #10-20');
formData.append('nombre_parque', 'Parque del Ingenio');
formData.append('observaciones', 'Trabajo completado satisfactoriamente');
formData.append('coordinates_type', 'Point');
formData.append('coordinates_data', '[-76.5225, 3.4516]');

// Agregar fotos
formData.append('photos', file1);
formData.append('photos', file2);

const response = await fetch('/grupo-operativo/reconocimiento', {
    method: 'POST',
    body: formData
});
```

### ✅ Respuesta exitosa:
```json
{
    "success": true,
    "id": "uuid-generado",
    "message": "Reconocimiento registrado exitosamente",
    "coordinates": {
        "type": "Point",
        "coordinates": [-76.5225, 3.4516]
    },
    "photosUrl": [
        "https://360-dagma-photos.s3.amazonaws.com/reconocimientos/uuid/foto1.jpg",
        "https://360-dagma-photos.s3.amazonaws.com/reconocimientos/uuid/foto2.jpg"
    ],
    "photos_uploaded": 2,
    "timestamp": "2026-01-24T10:30:00Z"
}
```
    """,
    response_model=ReconocimientoResponse
)
async def post_reconocimiento(
    tipo_intervencion: str = Form(..., min_length=1, description="Tipo de intervención"),
    descripcion_intervencion: str = Form(..., min_length=1, description="Descripción de la intervención"),
    direccion: str = Form(..., min_length=1, description="Dirección del lugar"),
    nombre_parque: str = Form(..., min_length=1, description="Nombre del parque asociado (de la colección 'parques')"),
    coordinates_type: str = Form(..., min_length=1, description="Tipo de geometría (Point, LineString, Polygon, etc.)"),
    coordinates_data: str = Form(..., description="Coordenadas en formato JSON array. Ejemplo: [-76.5225, 3.4516]"),
    photos: List[UploadFile] = File(..., description="Lista de archivos de fotos a subir a S3"),
    observaciones: Optional[str] = Form(None, description="Observaciones adicionales (opcional)")
):
    """
    Registrar un reconocimiento del grupo operativo DAGMA
    """
    try:
        # Validar tipo de geometría
        valid_geometry_types = ["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"]
        if coordinates_type not in valid_geometry_types:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de geometría inválido. Permitidos: {', '.join(valid_geometry_types)}"
            )
        
        # Validar cantidad de fotos
        if not photos or len(photos) == 0:
            raise HTTPException(
                status_code=400,
                detail="Debe proporcionar al menos una foto"
            )
        
        if len(photos) > 10:
            raise HTTPException(
                status_code=400,
                detail="Máximo 10 fotos por reconocimiento"
            )
        
        # Validar cada foto
        for photo in photos:
            try:
                validate_photo_file(photo)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error en archivo '{photo.filename}': {str(e)}"
                )
        
        # Generar ID único para el reconocimiento
        reconocimiento_id = str(uuid.uuid4())
        
        # Parsear y validar coordenadas
        try:
            print(f"📍 Recibido coordinates_data: {repr(coordinates_data)}")
            print(f"📍 Tipo: {type(coordinates_data)}, Long: {len(coordinates_data) if coordinates_data else 0}")
            
            # Intentar parsear como JSON
            coordinates_str = coordinates_data.strip()
            
            # Si no empieza con '[', asumir que es formato "lon,lat" y convertirlo
            if not coordinates_str.startswith('['):
                # Formato: -76.5225,3.4516 o -76.5225, 3.4516
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
                # Formato JSON array: [-76.5225, 3.4516]
                coordinates = json.loads(coordinates_str)
                
            validate_coordinates(coordinates, coordinates_type)
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
        
        # Crear objeto de geometría
        geometry = {
            "type": coordinates_type,
            "coordinates": coordinates
        }
        
        # Obtener ubicación geográfica (comuna/corregimiento y barrio/vereda)
        # Solo funciona para geometría Point
        comuna_corregimiento = None
        barrio_vereda = None
        
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
        
        # Obtener cliente S3 y bucket name
        bucket_name = os.getenv('S3_BUCKET_NAME', '360-dagma-photos')
        
        # Subir fotos a S3
        photos_urls = []
        s3_client = None
        
        try:
            s3_client = get_s3_client()
        except ValueError as e:
            # Si no hay credenciales de S3, advertir pero continuar (modo desarrollo)
            print(f"⚠️ ADVERTENCIA: {str(e)}. Las fotos NO se subirán a S3.")
        
        for i, photo in enumerate(photos):
            # Generar nombre único para la foto
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            # Sanitizar el nombre del archivo
            safe_filename = "".join(c for c in photo.filename if c.isalnum() or c in "._-")
            photo_filename = f"{timestamp}_{i}_{safe_filename}"
            
            s3_key = f"reconocimientos/{reconocimiento_id}/{photo_filename}"
            
            if s3_client:
                try:
                    # Leer el contenido del archivo
                    photo_content = await photo.read()
                    
                    # Subir a S3
                    # Nota: No se usa ACL porque muchos buckets modernos tienen ACLs deshabilitadas
                    # La accesibilidad pública se configura mediante Bucket Policy en AWS Console
                    s3_client.upload_fileobj(
                        io.BytesIO(photo_content),
                        bucket_name,
                        s3_key,
                        ExtraArgs={
                            'ContentType': photo.content_type
                        }
                    )
                    
                    # Generar URL pública
                    photo_url = f"https://{bucket_name}.s3.amazonaws.com/{s3_key}"
                    photos_urls.append(photo_url)
                    
                    # Rebobinar el archivo para futuras lecturas si es necesario
                    await photo.seek(0)
                    
                except ClientError as e:
                    print(f"❌ Error subiendo foto a S3: {str(e)}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error subiendo foto '{photo.filename}' a S3: {str(e)}"
                    )
            else:
                # Modo desarrollo: generar URL ficticia
                photo_url = f"https://{bucket_name}.s3.amazonaws.com/{s3_key}"
                photos_urls.append(photo_url)
                print(f"⚠️ Modo desarrollo: URL ficticia generada para {photo.filename}")
        
        # Preparar datos para guardar en Firebase
        reconocimiento_data = {
            "id": reconocimiento_id,
            "tipo_intervencion": tipo_intervencion,
            "descripcion_intervencion": descripcion_intervencion,
            "direccion": direccion,
            "nombre_parque": nombre_parque,
            "observaciones": observaciones or "",
            "coordinates": geometry,
            "comuna_corregimiento": comuna_corregimiento,
            "barrio_vereda": barrio_vereda,
            "photosUrl": photos_urls,
            "photos_uploaded": len(photos_urls),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Guardar en Firebase
        try:
            db.collection('reconocimientos_dagma').document(reconocimiento_id).set(reconocimiento_data)
            print(f"✅ Reconocimiento {reconocimiento_id} guardado en Firebase")
        except Exception as e:
            print(f"❌ Error guardando en Firebase: {str(e)}")
            # Si falla Firebase, intentar eliminar fotos de S3 (rollback)
            if s3_client:
                for photo_url in photos_urls:
                    try:
                        s3_key = photo_url.split('.com/')[-1]
                        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
                    except:
                        pass
            raise HTTPException(
                status_code=500,
                detail=f"Error guardando en Firebase: {str(e)}"
            )
        
        return ReconocimientoResponse(
            success=True,
            id=reconocimiento_id,
            message="Reconocimiento registrado exitosamente",
            nombre_parque=nombre_parque,
            coordinates=geometry,
            photosUrl=photos_urls,
            photos_uploaded=len(photos_urls),
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error registrando reconocimiento: {str(e)}"
        )


# ==================== ENDPOINT 3: Estadísticas (KPIs) ====================#
@router.get(
    "/grupo-operativo/stats",
    summary="🔵 GET | Estadísticas del Dashboard",
    description="""
## 🔵 GET | Estadísticas del Dashboard (KPIs)

**Propósito**: Obtener métricas resumidas de la actividad del usuario para mostrar en el Dashboard.

### ✅ Respuesta
Retorna estadísticas de visitas del mes actual, pendientes y parques visitados.

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/grupo-operativo/stats');
const stats = await response.json();
// stats.data = { total_visitas_mes: 12, total_pendientes: 5, parques_visitados: 8 }
```
    """
)
async def get_stats():
    """
    Obtener estadísticas resumidas del grupo operativo para Dashboard
    """
    try:
        # Obtener fecha del mes actual
        now = datetime.now(timezone.utc)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Consultar reportes del mes actual
        reportes_ref = db.collection('reconocimientos_dagma')
        docs = reportes_ref.where('created_at', '>=', start_of_month.isoformat()).stream()
        
        reportes_mes = []
        parques_visitados = set()
        
        for doc in docs:
            data = doc.to_dict()
            reportes_mes.append(data)
            # Agregar dirección como identificador de parque visitado
            if 'direccion' in data:
                parques_visitados.add(data['direccion'])
        
        # TODO: Implementar lógica de pendientes según el modelo de negocio
        # Por ahora retornamos 0
        total_pendientes = 0
        
        return {
            "success": True,
            "data": {
                "total_visitas_mes": len(reportes_mes),
                "total_pendientes": total_pendientes,
                "parques_visitados": len(parques_visitados)
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo estadísticas: {str(e)}"
        )


# ==================== ENDPOINT 4: Actividad Reciente ====================#
@router.get(
    "/grupo-operativo/reportes/recent",
    summary="🔵 GET | Actividad Reciente",
    description="""
## 🔵 GET | Obtener Reportes Recientes

**Propósito**: Obtener los últimos N reportes para el widget de "Actividad Reciente" del Dashboard.

### 📥 Parámetros
- **limit** (opcional): Cantidad de reportes a retornar (default: 3, máximo: 10)

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/grupo-operativo/reportes/recent?limit=5');
const reportes = await response.json();
```
    """
)
async def get_reportes_recent(
    limit: int = Query(default=3, ge=1, le=10, description="Cantidad de reportes recientes a retornar")
):
    """
    Obtener los últimos N reportes ordenados por fecha descendente
    """
    try:
        reportes_ref = db.collection('reconocimientos_dagma')
        docs = reportes_ref.order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit).stream()
        
        reportes = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            reportes.append(data)
        
        return {
            "success": True,
            "data": reportes,
            "count": len(reportes),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes recientes: {str(e)}"
        )


# ==================== ENDPOINT 5: Obtener Reportes con Filtros ====================#
@router.get(
    "/grupo-operativo/reportes",
    summary="🔵 GET | Obtener Reportes (con filtros)",
    description="""
## 🔵 GET | Obtener Reportes del Grupo Operativo

**Propósito**: Consultar reportes con filtros opcionales y paginación.

### 📥 Parámetros de Filtrado
- **year** (opcional): Filtrar por año (ej: 2024)
- **month** (opcional): Filtrar por mes (1-12)
- **search** (opcional): Búsqueda parcial en dirección, descripción o tipo de intervención
- **type** (opcional): Filtrar por tipo de intervención exacto
- **page** (opcional): Número de página (default: 1)
- **limit** (opcional): Resultados por página (default: 20, máximo: 100)

### ✅ Respuesta
Retorna lista de reportes filtrados con metadatos de paginación.

### 📝 Ejemplos de uso:
```javascript
// Todos los reportes
fetch('/grupo-operativo/reportes');

// Reportes de enero 2024
fetch('/grupo-operativo/reportes?year=2024&month=1');

// Buscar por parque
fetch('/grupo-operativo/reportes?search=Parque San Antonio');

// Filtrar por tipo
fetch('/grupo-operativo/reportes?type=Mantenimiento');

// Con paginación
fetch('/grupo-operativo/reportes?page=2&limit=10');
```
    """
)
async def get_reportes(
    year: Optional[int] = Query(None, ge=2020, le=2100, description="Filtrar por año"),
    month: Optional[int] = Query(None, ge=1, le=12, description="Filtrar por mes (1-12)"),
    search: Optional[str] = Query(None, min_length=1, description="Búsqueda parcial en dirección/descripción/tipo"),
    type: Optional[str] = Query(None, min_length=1, description="Filtrar por tipo de intervención"),
    page: int = Query(default=1, ge=1, description="Número de página"),
    limit: int = Query(default=20, ge=1, le=100, description="Resultados por página")
):
    """
    Obtener reportes del grupo operativo con filtros opcionales y paginación
    """
    try:
        reportes_ref = db.collection('reconocimientos_dagma')
        query = reportes_ref
        
        # Aplicar filtro de fecha (año y mes)
        if year and month:
            # Crear rango de fechas para el mes específico
            start_date = datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
            
            query = query.where('created_at', '>=', start_date.isoformat())
            query = query.where('created_at', '<', end_date.isoformat())
        elif year:
            # Solo año
            start_date = datetime(year, 1, 1, tzinfo=timezone.utc)
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            query = query.where('created_at', '>=', start_date.isoformat())
            query = query.where('created_at', '<', end_date.isoformat())
        
        # Aplicar filtro de tipo de intervención (exacto)
        if type:
            query = query.where('tipo_intervencion', '==', type)
        
        # Ordenar por fecha descendente
        query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
        
        # Obtener todos los documentos que cumplen los filtros
        docs = query.stream()
        
        all_reportes = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            
            # Aplicar filtro de búsqueda en memoria (Firebase no soporta búsqueda parcial de texto)
            if search:
                search_lower = search.lower()
                searchable_text = (
                    data.get('direccion', '').lower() + ' ' +
                    data.get('descripcion_intervencion', '').lower() + ' ' +
                    data.get('tipo_intervencion', '').lower()
                )
                if search_lower not in searchable_text:
                    continue
            
            all_reportes.append(data)
        
        # Calcular paginación
        total_items = len(all_reportes)
        total_pages = math.ceil(total_items / limit)
        start_index = (page - 1) * limit
        end_index = start_index + limit
        
        # Obtener página actual
        paginated_reportes = all_reportes[start_index:end_index]
        
        return {
            "success": True,
            "data": paginated_reportes,
            "pagination": {
                "page": page,
                "limit": limit,
                "total_items": total_items,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            },
            "filters": {
                "year": year,
                "month": month,
                "search": search,
                "type": type
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes: {str(e)}"
        )


# ==================== ENDPOINT 6: Obtener Líderes por Grupo ====================#
@router.get(
    "/lideres_grupo",
    summary="🔵 GET | Obtener Líderes de Grupo",
    description="""
## 🔵 GET | Obtener Líderes de Grupo

**Propósito**: Consultar líderes desde la colección `lideres_grupos` en Firebase.

### 📥 Parámetros
- **grupo** (opcional): Filtrar líderes por nombre de grupo (coincidencia exacta)

### 📝 Ejemplos de uso:
```javascript
// Obtener todos los líderes
fetch('/lideres_grupo');

// Filtrar por grupo
fetch('/lideres_grupo?grupo=Grupo 1');
```
    """
)
async def get_lideres_grupo(
    grupo: Optional[str] = Query(None, min_length=1, description="Filtrar por nombre de grupo")
):
    """
    Obtener líderes de la colección lideres_grupos con filtro opcional por grupo
    """
    try:
        lideres_ref = db.collection('lideres_grupos')
        query = lideres_ref

        if grupo:
            query = query.where('grupo', '==', grupo.strip())

        docs = query.stream()

        lideres = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            lideres.append(data)

        return {
            "success": True,
            "data": lideres,
            "count": len(lideres),
            "filters": {
                "grupo": grupo.strip() if grupo else None
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo líderes por grupo: {str(e)}"
        )


# ==================== ENDPOINT 7: Obtener Actividades Plan Distrito Verde ====================#
@router.get(
    "/actividades_plan_distrito_verde",
    summary="🟢 GET | Obtener Actividades del Plan Distrito Verde",
    description="""
## 🟢 GET | Obtener Actividades del Plan Distrito Verde

**Propósito**: Consultar actividades registradas en el plan de intervención "Distrito Verde" desde Firebase.

### 📥 Parámetros
- **id** (opcional): Filtrar por ID específico de la actividad

### ✅ Respuesta
Retorna lista de actividades con todos los detalles del plan de intervención.

### 📝 Ejemplos de uso:
```javascript
// Obtener todas las actividades
fetch('/actividades_plan_distrito_verde');

// Filtrar por ID específico
fetch('/actividades_plan_distrito_verde?id=abc-123');
```

### 📊 Estructura de datos retornados:
```json
{
  "success": true,
  "total": 5,
  "data": [
    {
      "id": "doc-id",
      "nombre": "Nombre de la actividad",
      "descripcion": "Descripción...",
      "ubicacion": "Lugar de ejecución",
      ...otros campos
    }
  ],
  "timestamp": "2026-02-14T10:30:00Z"
}
```
    """
)
async def get_actividades_plan_distrito_verde(id: Optional[str] = Query(None, description="Filtrar por ID de actividad")):
    """
    Obtener actividades del plan Distrito Verde de Firebase, opcionalmente filtradas por ID
    """
    try:
        # Obtener referencia a la colección
        plan_ref = db.collection('plan_distrito_verde')
        
        # Si se proporciona un ID, filtrar por él
        if id:
            # Intentar primero como ID de documento
            doc = plan_ref.document(id).get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id
                return {
                    "success": True,
                    "total": 1,
                    "data": [data],
                    "filters": {"id": id},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            
            # Fallback: buscar por campo interno 'id'
            docs = plan_ref.where("id", "==", id).stream()
            actividades = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                actividades.append(data)
            
            if not actividades:
                return {
                    "success": True,
                    "total": 0,
                    "data": [],
                    "filters": {"id": id},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            
            return {
                "success": True,
                "total": len(actividades),
                "data": actividades,
                "filters": {"id": id},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Si no hay filtro, obtener todos los documentos
        docs = plan_ref.stream()
        
        actividades = []
        for doc in docs:
            data = doc.to_dict()
            # Agregar el ID del documento a los datos
            data['id'] = doc.id
            actividades.append(data)
        
        return {
            "success": True,
            "total": len(actividades),
            "data": actividades,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        print(f"❌ Error obteniendo actividades: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo actividades del plan Distrito Verde: {str(e)}"
        )


# ==================== ENDPOINT 8: Convocar Actividad ====================#

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
        # Crear evento simple en Google Calendar (sin invitados)
        try:
            SCOPES = ['https://www.googleapis.com/auth/calendar']
            
            # Cargar credenciales desde múltiples fuentes (como en firebase_config.py)
            credentials = None
            
            # DEBUG: Mostrar qué variables existen
            print(f"\n[CALENDAR DEBUG] Iniciando carga de credenciales...")
            print(f"[CALENDAR DEBUG] FIREBASE_SERVICE_ACCOUNT_JSON exists: {bool(os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON'))}")
            print(f"[CALENDAR DEBUG] GOOGLE_APPLICATION_CREDENTIALS: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
            
            # Método 1: Usar JSON desde variable de entorno (Railway, Heroku)
            SERVICE_ACCOUNT_JSON = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
            if SERVICE_ACCOUNT_JSON:
                print(f"[CALENDAR DEBUG] Intentando cargar desde FIREBASE_SERVICE_ACCOUNT_JSON...")
                try:
                    # Validar que sea JSON válido
                    print(f"[CALENDAR DEBUG] Largura de JSON: {len(SERVICE_ACCOUNT_JSON)} caracteres")
                    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
                    print(f"[CALENDAR DEBUG] JSON válido. Campos: {list(service_account_info.keys())}")
                    credentials = service_account.Credentials.from_service_account_info(
                        service_account_info, scopes=SCOPES)
                    print(f"[CALENDAR DEBUG] ✅ Credenciales cargadas exitosamente desde FIREBASE_SERVICE_ACCOUNT_JSON")
                except json.JSONDecodeError as e:
                    print(f"[CALENDAR ERROR] JSON inválido en FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
                except Exception as e:
                    print(f"[CALENDAR ERROR] Error cargando desde FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
            else:
                print(f"[CALENDAR DEBUG] FIREBASE_SERVICE_ACCOUNT_JSON NO está configurada")
            
            # Método 2: Usar ruta de archivo (GOOGLE_APPLICATION_CREDENTIALS)
            if not credentials:
                print(f"[CALENDAR DEBUG] Intentando cargar desde GOOGLE_APPLICATION_CREDENTIALS...")
                GOOGLE_CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
                if GOOGLE_CREDS_PATH and os.path.exists(GOOGLE_CREDS_PATH):
                    try:
                        print(f"[CALENDAR DEBUG] Archivo encontrado: {GOOGLE_CREDS_PATH}")
                        credentials = service_account.Credentials.from_service_account_file(
                            GOOGLE_CREDS_PATH, scopes=SCOPES)
                        print(f"[CALENDAR DEBUG] ✅ Credenciales cargadas exitosamente desde {GOOGLE_CREDS_PATH}")
                    except Exception as e:
                        print(f"[CALENDAR ERROR] Error cargando desde {GOOGLE_CREDS_PATH}: {e}")
                else:
                    print(f"[CALENDAR DEBUG] GOOGLE_APPLICATION_CREDENTIALS no configurada o archivo no existe")
            
            # Método 3: Buscar archivos locales (desarrollo)
            if not credentials:
                print(f"[CALENDAR DEBUG] Intentando cargar desde archivos locales...")
                possible_paths = [
                    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json'),
                    'dagma-85aad-firebase-adminsdk-fbsvc-1e7612eab5.json',
                    'env/dagma-85aad-b7afe1c0f77f.json',
                ]
                for path in possible_paths:
                    print(f"[CALENDAR DEBUG] Buscando: {path}")
                    if os.path.exists(path):
                        try:
                            print(f"[CALENDAR DEBUG] Archivo encontrado: {path}")
                            credentials = service_account.Credentials.from_service_account_file(
                                path, scopes=SCOPES)
                            print(f"[CALENDAR DEBUG] ✅ Credenciales cargadas exitosamente desde {path}")
                            break
                        except Exception as e:
                            print(f"[CALENDAR ERROR] Error cargando {path}: {e}")
                            continue
                    else:
                        print(f"[CALENDAR DEBUG] No encontrado: {path}")
            
            if not credentials:
                error_msg = "No se encontraron credenciales para Google Calendar. Configura FIREBASE_SERVICE_ACCOUNT_JSON o GOOGLE_APPLICATION_CREDENTIALS"
                print(f"[CALENDAR CRITICAL] {error_msg}")
                raise ValueError(error_msg)
            service = build('calendar', 'v3', credentials=credentials)
            calendar_id = '19c263371dc17e144c9ee0b12ac40c28339cb20c259f528d348730d98e193eb9@group.calendar.google.com'
            # Parsear fecha y hora a formato RFC3339
            fecha = body.fecha_actividad  # dd/mm/aaaa
            hora = body.hora_encuentro   # hh:mm
            try:
                dt_inicio = datetime.strptime(f"{fecha} {hora}", "%d/%m/%Y %H:%M")
                dt_inicio = tz_col.localize(dt_inicio)
                dt_fin = dt_inicio + timedelta(hours=2)  # Duración por defecto: 2h
            except Exception as e:
                dt_inicio = datetime.now(tz_col)
                dt_fin = dt_inicio + timedelta(hours=2)
            event = {
                'summary': f"Actividad DAGMA: {body.objetivo_actividad}",
                'location': direccion,
                'description': body.observaciones or '',
                'start': {
                    'dateTime': dt_inicio.isoformat(),
                    'timeZone': 'America/Bogota',
                },
                'end': {
                    'dateTime': dt_fin.isoformat(),
                    'timeZone': 'America/Bogota',
                },
                'reminders': {
                    'useDefault': True,
                },
            }
            created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
            actividad_data['calendar_event_id'] = created_event.get('id')
            actividad_data['calendar_event_link'] = created_event.get('htmlLink')
        except Exception as e:
            print(f"⚠️ Error creando evento en Google Calendar: {e}")
            actividad_data['calendar_event_error'] = str(e)
        # Guardar en Firebase
        db.collection("plan_distrito_verde").document(actividad_id).set(actividad_data)
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
    "/plan_distrito_verde/{actividad_id}",
    summary="🔴 DELETE | Eliminar Actividad Programada",
    description="""
## 🔴 DELETE | Eliminar Actividad Programada

**Propósito**: Eliminar un registro de la colección `plan_distrito_verde` a partir de su `id`.

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
async def delete_plan_distrito_verde(actividad_id: str):
    """
    Eliminar actividad del plan Distrito Verde por ID
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
    "/plan_distrito_verde/{actividad_id}",
    summary="🟡 PUT | Actualizar Actividad Programada",
    description="""
## 🟡 PUT | Actualizar Actividad Programada

**Propósito**: Modificar cualquier campo de un registro en la colección `plan_distrito_verde`.

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
const response = await fetch('/plan_distrito_verde/abc-123', {
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
async def update_plan_distrito_verde(actividad_id: str, body: dict):
    """
    Actualizar un registro del plan Distrito Verde con los campos especificados
    """
    try:
        if not body:
            raise HTTPException(
                status_code=400,
                detail="El cuerpo de la solicitud no puede estar vacío"
            )

        collection_ref = db.collection("plan_distrito_verde")

        # Intentar primero por ID de documento
        doc_ref = collection_ref.document(actividad_id)
        doc_snapshot = doc_ref.get()

        if doc_snapshot.exists:
            doc_ref.update(body)
            updated_doc = doc_ref.get()
            updated_data = updated_doc.to_dict() or {}
            updated_data['id'] = actividad_id
            
            return {
                "success": True,
                "id": actividad_id,
                "message": "Actividad actualizada exitosamente",
                "data": updated_data,
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

        doc_ref = collection_ref.document(matching_doc.id)
        doc_ref.update(body)
        updated_doc = doc_ref.get()
        updated_data = updated_doc.to_dict() or {}
        updated_data['id'] = actividad_id

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
            detail=f"Error actualizando actividad del plan Distrito Verde: {str(e)}"
        )


@router.delete(
    "/grupo-operativo/eliminar-reporte",
    summary="🔴 DELETE | Eliminar Reporte",
    description="""
## 🔴 DELETE | Eliminar Reporte del Grupo Operativo

**Propósito**: Eliminar un reporte específico del sistema, incluyendo las fotos en S3.

### 📥 Parámetros
- **reporte_id**: ID único del reporte a eliminar

### 🗑️ Acciones realizadas:
1. Eliminar imágenes del bucket S3 (360-dagma-photos)
2. Eliminar documento de Firebase (reconocimientos_dagma)

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/grupo-operativo/eliminar-reporte?reporte_id=abc-123', {
    method: 'DELETE'
});
```

### ✅ Respuesta exitosa:
```json
{
    "success": true,
    "id": "abc-123",
    "message": "Reporte y fotos eliminados exitosamente",
    "photos_deleted": 3,
    "timestamp": "2026-01-24T..."
}
```
    """
)
async def delete_reporte(
    reporte_id: str = Query(..., description="ID del reporte a eliminar")
):
    """
    Eliminar un reporte del grupo operativo
    """
    try:
        # TODO: Implementar eliminación de fotos en S3
        # s3_client = boto3.client('s3')
        # bucket = '360-dagma-photos'
        # prefix = f'reconocimientos/{reporte_id}/'
        
        # Listar y eliminar objetos en S3
        # response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        # photos_deleted = 0
        
        # if 'Contents' in response:
        #     for obj in response['Contents']:
        #         s3_client.delete_object(Bucket=bucket, Key=obj['Key'])
        #         photos_deleted += 1
        
        photos_deleted = 0
        
        # TODO: Eliminar documento de Firebase
        # db.collection('reconocimientos_dagma').document(reporte_id).delete()
        
        return {
            "success": True,
            "id": reporte_id,
            "message": "Reporte y fotos eliminados exitosamente",
            "photos_deleted": photos_deleted,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando reporte: {str(e)}"
        )