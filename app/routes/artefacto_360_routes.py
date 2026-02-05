"""
Rutas para gestión de Artefacto de Captura DAGMA
"""
from fastapi import APIRouter, HTTPException, Form, UploadFile, File, Query
from typing import List, Optional
from datetime import datetime
import json
import uuid
import math
from pydantic import BaseModel, Field

# Importar configuración de Firebase y S3/Storage
from app.firebase_config import db
# import boto3

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
            "timestamp": datetime.utcnow().isoformat()
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
        # Generar ID único para el reconocimiento
        reconocimiento_id = str(uuid.uuid4())
        
        # Parsear coordenadas
        try:
            coordinates = json.loads(coordinates_data)
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise ValueError("Las coordenadas deben ser un array con al menos 2 números")
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Formato de coordenadas inválido. Debe ser un JSON array válido"
            )
        
        # Crear objeto de geometría
        geometry = {
            "type": coordinates_type,
            "coordinates": coordinates
        }
        
        # TODO: Subir fotos a S3/Firebase Storage
        photos_urls = []
        for photo in photos:
            # Generar nombre único para la foto
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            photo_filename = f"{timestamp}_{photo.filename}"
            
            # TODO: Implementar subida a S3
            # s3_key = f"reconocimientos/{reconocimiento_id}/{photo_filename}"
            # s3_client.upload_fileobj(photo.file, "360-dagma-photos", s3_key)
            # photo_url = f"https://360-dagma-photos.s3.amazonaws.com/{s3_key}"
            
            # URL temporal de ejemplo
            photo_url = f"https://360-dagma-photos.s3.amazonaws.com/reconocimientos/{reconocimiento_id}/{photo_filename}"
            photos_urls.append(photo_url)
        
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
            "created_at": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # TODO: Guardar en Firebase
        # db.collection('reconocimientos_dagma').document(reconocimiento_id).set(reconocimiento_data)
        
        return ReconocimientoResponse(
            success=True,
            id=reconocimiento_id,
            message="Reconocimiento registrado exitosamente",
            coordinates=geometry,
            photosUrl=photos_urls,
            photos_uploaded=len(photos_urls),
            timestamp=datetime.utcnow().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error registrando reconocimiento: {str(e)}"
        )


# ==================== ENDPOINT 3: Obtener Reportes ====================#
@router.get(
    "/grupo-operativo/reportes",
    summary="🔵 GET | Obtener Reportes",
    description="""
## 🔵 GET | Obtener Reportes del Grupo Operativo

**Propósito**: Consultar todos los reportes registrados por el grupo operativo.

### ✅ Respuesta
Retorna lista de reportes con sus detalles.

### 📝 Ejemplo de uso:
```javascript
const response = await fetch('/grupo-operativo/reportes');
const reportes = await response.json();
```
    """
)
async def get_reportes():
    """
    Obtener todos los reportes del grupo operativo
    """
    try:
        # TODO: Implementar conexión a Firebase
        # reportes_ref = db.collection('reconocimientos_dagma')
        # docs = reportes_ref.order_by('created_at', direction='DESCENDING').stream()
        
        reportes = []
        # for doc in docs:
        #     data = doc.to_dict()
        #     data['id'] = doc.id
        #     reportes.append(data)
        
        return {
            "success": True,
            "data": reportes,
            "count": len(reportes),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes: {str(e)}"
        )


# ==================== ENDPOINT 4: Eliminar Reporte ====================#
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
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error eliminando reporte: {str(e)}"
        )