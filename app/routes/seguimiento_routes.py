"""
Rutas para Sistema de Seguimiento de Reportes/Reconocimientos DAGMA
Gestión del ciclo de vida completo de reportes: estados, asignaciones, historial y estadísticas
"""
from fastapi import APIRouter, HTTPException, Query, Body
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field, validator
import uuid
from firebase_admin import firestore

# Importar configuración de Firebase
from app.firebase_config import db

router = APIRouter(
    prefix="/api/v1/reportes",
    tags=["Sistema de Seguimiento de Reportes"]
)


# ==================== MODELOS PYDANTIC ====================#

class EvidenciaInput(BaseModel):
    """Modelo para evidencias de avance"""
    tipo: str = Field(..., description="Tipo de evidencia: 'foto' o 'documento'")
    url: str = Field(..., description="URL de la evidencia en S3")
    descripcion: Optional[str] = Field(None, max_length=500, description="Descripción de la evidencia")
    
    @validator('tipo')
    def validate_tipo(cls, v):
        if v not in ['foto', 'documento']:
            raise ValueError("El tipo debe ser 'foto' o 'documento'")
        return v


class AvanceInput(BaseModel):
    """Modelo para registrar un nuevo avance"""
    estado_nuevo: str = Field(..., description="Nuevo estado del reporte")
    descripcion: str = Field(..., min_length=10, max_length=2000, description="Descripción del avance")
    autor: str = Field(..., description="Nombre del funcionario que registra el avance")
    porcentaje: int = Field(..., ge=0, le=100, description="Porcentaje de avance")
    evidencias: Optional[List[EvidenciaInput]] = Field(default=[], description="Lista de evidencias")
    
    @validator('estado_nuevo')
    def validate_estado(cls, v):
        estados_validos = ['notificado', 'radicado', 'en-gestion', 'asignado', 'en-proceso', 'resuelto', 'cerrado']
        if v not in estados_validos:
            raise ValueError(f"Estado inválido. Valores permitidos: {', '.join(estados_validos)}")
        return v


class EncargadoInput(BaseModel):
    """Modelo para asignar encargado"""
    encargado: str = Field(..., min_length=1, max_length=255, description="Nombre completo del encargado")
    centro_gestor: str = Field(..., min_length=1, max_length=255, description="Centro gestor responsable")


class PrioridadInput(BaseModel):
    """Modelo para cambiar prioridad"""
    prioridad: str = Field(..., description="Nivel de prioridad")
    
    @validator('prioridad')
    def validate_prioridad(cls, v):
        prioridades_validas = ['baja', 'media', 'alta', 'urgente']
        if v not in prioridades_validas:
            raise ValueError(f"Prioridad inválida. Valores permitidos: {', '.join(prioridades_validas)}")
        return v


# ==================== FUNCIONES AUXILIARES ====================#

def validar_transicion_estado(estado_actual: str, estado_nuevo: str) -> bool:
    """
    Valida que la transición entre estados sea permitida según las reglas de negocio
    """
    transiciones_permitidas = {
        'notificado': ['radicado', 'cerrado'],
        'radicado': ['en-gestion', 'asignado', 'cerrado'],
        'en-gestion': ['asignado', 'en-proceso', 'cerrado'],
        'asignado': ['en-proceso', 'cerrado'],
        'en-proceso': ['resuelto', 'cerrado'],
        'resuelto': ['cerrado'],
        'cerrado': []  # Estado final
    }
    
    return estado_nuevo in transiciones_permitidas.get(estado_actual, [])


def validar_porcentaje_estado(estado: str, porcentaje: int) -> bool:
    """
    Valida que el porcentaje sea coherente con el estado
    """
    if estado == 'resuelto' and porcentaje < 90:
        return False
    if estado == 'cerrado' and porcentaje != 100:
        return False
    return True


async def obtener_reporte_completo(reporte_id: str) -> Dict[str, Any]:
    """
    Obtiene un reporte con toda su información de seguimiento e historial
    """
    # Obtener reporte base (reconocimiento)
    reporte_ref = db.collection('reconocimientos').document(reporte_id)
    reporte_doc = reporte_ref.get()
    
    if not reporte_doc.exists:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró el reporte con ID: {reporte_id}"
        )
    
    reporte_data = reporte_doc.to_dict()
    reporte_data['id'] = reporte_id
    
    # Obtener información de seguimiento
    seguimiento_ref = db.collection('reportes_seguimiento').document(reporte_id)
    seguimiento_doc = seguimiento_ref.get()
    
    if seguimiento_doc.exists:
        seguimiento_data = seguimiento_doc.to_dict()
        reporte_data.update({
            'estado': seguimiento_data.get('estado', 'notificado'),
            'prioridad': seguimiento_data.get('prioridad', 'media'),
            'porcentaje_avance': seguimiento_data.get('porcentaje_avance', 0),
            'encargado': seguimiento_data.get('encargado'),
            'centro_gestor': seguimiento_data.get('centro_gestor'),
        })
    else:
        # Si no existe seguimiento, usar valores por defecto
        reporte_data.update({
            'estado': 'notificado',
            'prioridad': 'media',
            'porcentaje_avance': 0,
            'encargado': None,
            'centro_gestor': None,
        })
    
    # Obtener historial de avances
    historial_ref = db.collection('historial_avance_reportes') \
        .where('reporte_id', '==', reporte_id) \
        .order_by('fecha', direction=firestore.Query.DESCENDING)
    
    historial_docs = historial_ref.stream()
    historial = []
    
    for hist_doc in historial_docs:
        hist_data = hist_doc.to_dict()
        hist_data['id'] = hist_doc.id
        
        # Obtener evidencias de este avance
        evidencias_ref = db.collection('evidencias_avance_reportes') \
            .where('historial_avance_id', '==', hist_doc.id)
        evidencias_docs = evidencias_ref.stream()
        
        evidencias = []
        for ev_doc in evidencias_docs:
            ev_data = ev_doc.to_dict()
            evidencias.append({
                'tipo': ev_data.get('tipo'),
                'url': ev_data.get('url'),
                'descripcion': ev_data.get('descripcion')
            })
        
        hist_data['evidencias'] = evidencias
        historial.append(hist_data)
    
    reporte_data['historial'] = historial
    
    return reporte_data


# ==================== ENDPOINTS ====================#

@router.get(
    "/seguimiento",
    summary="📋 Obtener Reportes con Seguimiento",
    description="""
## Obtiene la lista completa de reportes con información de seguimiento e historial

### Filtros disponibles:
- **estado**: Filtrar por estado del reporte
- **prioridad**: Filtrar por nivel de prioridad
- **encargado**: Filtrar por nombre del encargado
- **fecha_desde / fecha_hasta**: Rango de fechas
- **page / limit**: Paginación

### Ejemplo de uso:
```bash
GET /api/v1/reportes/seguimiento?estado=en-gestion&prioridad=alta&page=1&limit=20
```
    """
)
async def get_reportes_seguimiento(
    estado: Optional[str] = Query(None, description="Filtrar por estado"),
    prioridad: Optional[str] = Query(None, description="Filtrar por prioridad"),
    encargado: Optional[str] = Query(None, description="Filtrar por encargado"),
    fecha_desde: Optional[str] = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    fecha_hasta: Optional[str] = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="Número de página"),
    limit: int = Query(50, ge=1, le=100, description="Resultados por página")
):
    """
    Obtener lista de reportes con seguimiento
    """
    try:
        # Construir query base
        query = db.collection('reportes_seguimiento')
        
        # Aplicar filtros
        if estado:
            query = query.where('estado', '==', estado)
        
        if prioridad:
            query = query.where('prioridad', '==', prioridad)
        
        if encargado:
            query = query.where('encargado', '==', encargado)
        
        # Obtener documentos
        docs = query.stream()
        
        # Procesar cada documento
        reportes = []
        for doc in docs:
            seguimiento_data = doc.to_dict()
            reporte_id = doc.id
            
            try:
                # Obtener reporte completo
                reporte_completo = await obtener_reporte_completo(reporte_id)
                
                # Aplicar filtro de fechas si está especificado
                if fecha_desde or fecha_hasta:
                    fecha_registro = reporte_completo.get('timestamp')
                    if fecha_registro:
                        if isinstance(fecha_registro, str):
                            fecha_registro = datetime.fromisoformat(fecha_registro.replace('Z', '+00:00'))
                        
                        if fecha_desde:
                            fecha_desde_dt = datetime.fromisoformat(fecha_desde)
                            if fecha_registro < fecha_desde_dt:
                                continue
                        
                        if fecha_hasta:
                            fecha_hasta_dt = datetime.fromisoformat(fecha_hasta) + timedelta(days=1)
                            if fecha_registro >= fecha_hasta_dt:
                                continue
                
                reportes.append(reporte_completo)
            except Exception as e:
                # Si hay error obteniendo un reporte individual, continuar con los demás
                print(f"Error obteniendo reporte {reporte_id}: {str(e)}")
                continue
        
        # Calcular paginación
        total = len(reportes)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        reportes_paginados = reportes[start_idx:end_idx]
        
        total_pages = (total + limit - 1) // limit
        
        return {
            "success": True,
            "data": reportes_paginados,
            "pagination": {
                "total": total,
                "page": page,
                "limit": limit,
                "totalPages": total_pages
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo reportes: {str(e)}"
        )


@router.post(
    "/{reporteId}/avance",
    summary="✍️ Registrar Avance / Cambiar Estado",
    description="""
## Registra un nuevo avance en el reporte y cambia su estado

### Validaciones automáticas:
- Transición de estado válida según reglas de negocio
- Porcentaje coherente con el estado
- Porcentaje no puede retroceder
- Estado 'resuelto' requiere porcentaje >= 90%
- Estado 'cerrado' requiere porcentaje = 100%

### Ejemplo de uso:
```json
{
  "estado_nuevo": "en-gestion",
  "descripcion": "Se coordinó visita técnica con el ingeniero...",
  "autor": "María López García",
  "porcentaje": 45,
  "evidencias": [
    {
      "tipo": "foto",
      "url": "https://storage.example.com/evidencia1.jpg",
      "descripcion": "Reunión de coordinación"
    }
  ]
}
```
    """
)
async def registrar_avance(
    reporteId: str,
    avance: AvanceInput = Body(...)
):
    """
    Registrar un nuevo avance en el reporte
    """
    try:
        # Verificar que el reporte existe
        reporte_ref = db.collection('reconocimientos').document(reporteId)
        reporte_doc = reporte_ref.get()
        
        if not reporte_doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró el reporte con ID: {reporteId}"
            )
        
        # Obtener o crear seguimiento
        seguimiento_ref = db.collection('reportes_seguimiento').document(reporteId)
        seguimiento_doc = seguimiento_ref.get()
        
        if seguimiento_doc.exists:
            seguimiento_data = seguimiento_doc.to_dict()
            estado_actual = seguimiento_data.get('estado', 'notificado')
            porcentaje_actual = seguimiento_data.get('porcentaje_avance', 0)
        else:
            estado_actual = 'notificado'
            porcentaje_actual = 0
        
        # Validar transición de estado
        if not validar_transicion_estado(estado_actual, avance.estado_nuevo):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_STATE_TRANSITION",
                    "message": f"No se puede cambiar de '{estado_actual}' a '{avance.estado_nuevo}'"
                }
            )
        
        # Validar que el porcentaje no retroceda
        if avance.porcentaje < porcentaje_actual:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "VALIDATION_ERROR",
                    "message": f"El porcentaje no puede retroceder. Actual: {porcentaje_actual}%, Nuevo: {avance.porcentaje}%",
                    "field": "porcentaje"
                }
            )
        
        # Validar porcentaje según estado
        if not validar_porcentaje_estado(avance.estado_nuevo, avance.porcentaje):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "VALIDATION_ERROR",
                    "message": f"Porcentaje inválido para el estado '{avance.estado_nuevo}'",
                    "field": "porcentaje"
                }
            )
        
        # Crear registro de historial
        historial_id = str(uuid.uuid4())
        fecha_actual = datetime.now(timezone.utc)
        
        historial_data = {
            'reporte_id': reporteId,
            'fecha': fecha_actual,
            'autor': avance.autor,
            'descripcion': avance.descripcion,
            'estado_anterior': estado_actual,
            'estado_nuevo': avance.estado_nuevo,
            'porcentaje': avance.porcentaje,
            'created_at': fecha_actual
        }
        
        db.collection('historial_avance_reportes').document(historial_id).set(historial_data)
        
        # Guardar evidencias si existen
        for evidencia in avance.evidencias:
            evidencia_id = str(uuid.uuid4())
            evidencia_data = {
                'historial_avance_id': historial_id,
                'tipo': evidencia.tipo,
                'url': evidencia.url,
                'descripcion': evidencia.descripcion,
                'created_at': fecha_actual
            }
            db.collection('evidencias_avance_reportes').document(evidencia_id).set(evidencia_data)
        
        # Actualizar seguimiento
        seguimiento_update = {
            'estado': avance.estado_nuevo,
            'porcentaje_avance': avance.porcentaje,
            'updated_at': fecha_actual
        }
        
        if not seguimiento_doc.exists:
            # Crear nuevo registro de seguimiento
            seguimiento_update.update({
                'reporte_id': reporteId,
                'prioridad': 'media',
                'encargado': None,
                'centro_gestor': None,
                'created_at': fecha_actual
            })
        
        seguimiento_ref.set(seguimiento_update, merge=True)
        
        # Obtener reporte actualizado completo
        reporte_actualizado = await obtener_reporte_completo(reporteId)
        
        return {
            "success": True,
            "message": "Avance registrado exitosamente",
            "data": {
                "historial_id": historial_id,
                "reporte_actualizado": reporte_actualizado
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error registrando avance: {str(e)}"
        )


@router.patch(
    "/{reporteId}/encargado",
    summary="👤 Asignar Encargado",
    description="""
## Asigna un encargado responsable al reporte

### Ejemplo de uso:
```json
{
  "encargado": "Ing. Carlos Andrés Méndez Rojas",
  "centro_gestor": "Secretaría de Infraestructura y Valorización"
}
```
    """
)
async def asignar_encargado(
    reporteId: str,
    data: EncargadoInput = Body(...)
):
    """
    Asignar encargado a un reporte
    """
    try:
        # Verificar que el reporte existe
        reporte_ref = db.collection('reconocimientos').document(reporteId)
        reporte_doc = reporte_ref.get()
        
        if not reporte_doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró el reporte con ID: {reporteId}"
            )
        
        # Actualizar seguimiento
        seguimiento_ref = db.collection('reportes_seguimiento').document(reporteId)
        fecha_actual = datetime.now(timezone.utc)
        
        seguimiento_doc = seguimiento_ref.get()
        
        update_data = {
            'encargado': data.encargado,
            'centro_gestor': data.centro_gestor,
            'updated_at': fecha_actual
        }
        
        if not seguimiento_doc.exists:
            # Crear nuevo registro si no existe
            update_data.update({
                'reporte_id': reporteId,
                'estado': 'notificado',
                'prioridad': 'media',
                'porcentaje_avance': 0,
                'created_at': fecha_actual
            })
        
        seguimiento_ref.set(update_data, merge=True)
        
        return {
            "success": True,
            "message": "Encargado asignado exitosamente",
            "data": {
                "id": reporteId,
                "encargado": data.encargado,
                "centro_gestor": data.centro_gestor,
                "updated_at": fecha_actual.isoformat()
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error asignando encargado: {str(e)}"
        )


@router.patch(
    "/{reporteId}/prioridad",
    summary="⚡ Cambiar Prioridad",
    description="""
## Cambia la prioridad de un reporte

### Valores válidos:
- `baja`
- `media`
- `alta`
- `urgente`

### Ejemplo de uso:
```json
{
  "prioridad": "urgente"
}
```
    """
)
async def cambiar_prioridad(
    reporteId: str,
    data: PrioridadInput = Body(...)
):
    """
    Cambiar prioridad de un reporte
    """
    try:
        # Verificar que el reporte existe
        reporte_ref = db.collection('reconocimientos').document(reporteId)
        reporte_doc = reporte_ref.get()
        
        if not reporte_doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró el reporte con ID: {reporteId}"
            )
        
        # Actualizar seguimiento
        seguimiento_ref = db.collection('reportes_seguimiento').document(reporteId)
        fecha_actual = datetime.now(timezone.utc)
        
        seguimiento_doc = seguimiento_ref.get()
        
        update_data = {
            'prioridad': data.prioridad,
            'updated_at': fecha_actual
        }
        
        if not seguimiento_doc.exists:
            # Crear nuevo registro si no existe
            update_data.update({
                'reporte_id': reporteId,
                'estado': 'notificado',
                'porcentaje_avance': 0,
                'encargado': None,
                'centro_gestor': None,
                'created_at': fecha_actual
            })
        
        seguimiento_ref.set(update_data, merge=True)
        
        return {
            "success": True,
            "message": "Prioridad actualizada exitosamente",
            "data": {
                "id": reporteId,
                "prioridad": data.prioridad,
                "updated_at": fecha_actual.isoformat()
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error cambiando prioridad: {str(e)}"
        )


@router.get(
    "/{reporteId}/historial",
    summary="📜 Obtener Historial de Avances",
    description="""
## Obtiene el historial completo de avances de un reporte específico

### Retorna:
- Lista de avances ordenados por fecha descendente
- Evidencias asociadas a cada avance
- Cambios de estado y porcentaje

### Ejemplo de uso:
```bash
GET /api/v1/reportes/rep-123abc/historial
```
    """
)
async def get_historial_reporte(reporteId: str):
    """
    Obtener historial de avances de un reporte
    """
    try:
        # Verificar que el reporte existe
        reporte_ref = db.collection('reconocimientos').document(reporteId)
        reporte_doc = reporte_ref.get()
        
        if not reporte_doc.exists:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró el reporte con ID: {reporteId}"
            )
        
        reporte_data = reporte_doc.to_dict()
        
        # Obtener historial
        historial_ref = db.collection('historial_avance_reportes') \
            .where('reporte_id', '==', reporteId) \
            .order_by('fecha', direction=firestore.Query.DESCENDING)
        
        historial_docs = historial_ref.stream()
        historial = []
        
        for hist_doc in historial_docs:
            hist_data = hist_doc.to_dict()
            hist_data['id'] = hist_doc.id
            
            # Convertir fecha a ISO string
            if 'fecha' in hist_data and hist_data['fecha']:
                hist_data['fecha'] = hist_data['fecha'].isoformat()
            
            # Obtener evidencias
            evidencias_ref = db.collection('evidencias_avance_reportes') \
                .where('historial_avance_id', '==', hist_doc.id)
            evidencias_docs = evidencias_ref.stream()
            
            evidencias = []
            for ev_doc in evidencias_docs:
                ev_data = ev_doc.to_dict()
                evidencias.append({
                    'tipo': ev_data.get('tipo'),
                    'url': ev_data.get('url'),
                    'descripcion': ev_data.get('descripcion')
                })
            
            hist_data['evidencias'] = evidencias
            historial.append(hist_data)
        
        return {
            "success": True,
            "data": {
                "reporte_id": reporteId,
                "nombre_parque": reporte_data.get('nombre_parque'),
                "historial": historial
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo historial: {str(e)}"
        )


@router.get(
    "/seguimiento/estadisticas",
    summary="📊 Obtener Estadísticas",
    description="""
## Obtiene estadísticas agregadas del sistema de seguimiento

### Información incluida:
- Total de reportes
- Avance promedio
- Distribución por estado
- Distribución por prioridad
- Reportes por centro gestor
- Tendencia de los últimos 30 días

### Filtros opcionales:
- **fecha_desde / fecha_hasta**: Rango de fechas para las estadísticas

### Ejemplo de uso:
```bash
GET /api/v1/reportes/seguimiento/estadisticas?fecha_desde=2026-01-01&fecha_hasta=2026-02-12
```
    """
)
async def get_estadisticas(
    fecha_desde: Optional[str] = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    fecha_hasta: Optional[str] = Query(None, description="Fecha hasta (YYYY-MM-DD)")
):
    """
    Obtener estadísticas del sistema de seguimiento
    """
    try:
        # Obtener todos los seguimientos
        seguimientos_ref = db.collection('reportes_seguimiento')
        seguimientos_docs = seguimientos_ref.stream()
        
        # Inicializar contadores
        total_reportes = 0
        suma_porcentajes = 0
        por_estado = {
            'notificado': 0,
            'radicado': 0,
            'en-gestion': 0,
            'asignado': 0,
            'en-proceso': 0,
            'resuelto': 0,
            'cerrado': 0
        }
        por_prioridad = {
            'baja': 0,
            'media': 0,
            'alta': 0,
            'urgente': 0
        }
        centros_gestores = {}
        
        for doc in seguimientos_docs:
            data = doc.to_dict()
            
            # Aplicar filtro de fechas si está especificado
            if fecha_desde or fecha_hasta:
                created_at = data.get('created_at')
                if created_at:
                    if fecha_desde:
                        fecha_desde_dt = datetime.fromisoformat(fecha_desde)
                        if created_at < fecha_desde_dt:
                            continue
                    
                    if fecha_hasta:
                        fecha_hasta_dt = datetime.fromisoformat(fecha_hasta) + timedelta(days=1)
                        if created_at >= fecha_hasta_dt:
                            continue
            
            total_reportes += 1
            
            # Contadores de estado
            estado = data.get('estado', 'notificado')
            por_estado[estado] = por_estado.get(estado, 0) + 1
            
            # Contadores de prioridad
            prioridad = data.get('prioridad', 'media')
            por_prioridad[prioridad] = por_prioridad.get(prioridad, 0) + 1
            
            # Porcentaje promedio
            porcentaje = data.get('porcentaje_avance', 0)
            suma_porcentajes += porcentaje
            
            # Contadores por centro gestor
            centro_gestor = data.get('centro_gestor')
            if centro_gestor:
                if centro_gestor not in centros_gestores:
                    centros_gestores[centro_gestor] = {
                        'nombre': centro_gestor,
                        'total': 0,
                        'resueltos': 0,
                        'en_proceso': 0
                    }
                
                centros_gestores[centro_gestor]['total'] += 1
                
                if estado == 'resuelto' or estado == 'cerrado':
                    centros_gestores[centro_gestor]['resueltos'] += 1
                else:
                    centros_gestores[centro_gestor]['en_proceso'] += 1
        
        # Calcular promedio
        avance_promedio = (suma_porcentajes // total_reportes) if total_reportes > 0 else 0
        
        # Obtener tendencia de últimos 30 días
        fecha_hace_30_dias = datetime.now(timezone.utc) - timedelta(days=30)
        
        historial_ref = db.collection('historial_avance_reportes') \
            .where('fecha', '>=', fecha_hace_30_dias) \
            .order_by('fecha', direction=firestore.Query.ASCENDING)
        
        historial_docs = historial_ref.stream()
        
        tendencia_dict = {}
        for hist_doc in historial_docs:
            hist_data = hist_doc.to_dict()
            fecha = hist_data.get('fecha')
            
            if fecha:
                fecha_str = fecha.strftime('%Y-%m-%d')
                
                if fecha_str not in tendencia_dict:
                    tendencia_dict[fecha_str] = {
                        'fecha': fecha_str,
                        'nuevos': 0,
                        'resueltos': 0,
                        'en_proceso': 0
                    }
                
                estado_nuevo = hist_data.get('estado_nuevo')
                estado_anterior = hist_data.get('estado_anterior')
                
                # Contar nuevos (primera entrada)
                if estado_anterior == 'notificado' and estado_nuevo != 'notificado':
                    tendencia_dict[fecha_str]['nuevos'] += 1
                
                # Contar resueltos
                if estado_nuevo in ['resuelto', 'cerrado']:
                    tendencia_dict[fecha_str]['resueltos'] += 1
        
        tendencia = list(tendencia_dict.values())
        
        return {
            "success": True,
            "data": {
                "total_reportes": total_reportes,
                "avance_promedio": avance_promedio,
                "por_estado": por_estado,
                "por_prioridad": por_prioridad,
                "por_centro_gestor": list(centros_gestores.values()),
                "tendencia_ultimos_30_dias": tendencia
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo estadísticas: {str(e)}"
        )
