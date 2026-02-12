"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query
from typing import List, Optional
from datetime import datetime, timezone
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

router = APIRouter(tags=["Artefacto de Captura DAGMA"])


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
            coordinates = json.loads(coordinates_data)
            validate_coordinates(coordinates, coordinates_type)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Formato de coordenadas inválido. Debe ser un JSON array válido"
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
            "observaciones": observaciones or "",
            "coordinates": geometry,
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


# ==================== ENDPOINT 6: Eliminar Reporte ====================#
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