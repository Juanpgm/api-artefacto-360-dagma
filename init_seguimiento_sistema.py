"""
Script de inicialización para el Sistema de Seguimiento de Reportes DAGMA
Crea colecciones e índices en Firestore y genera datos de ejemplo (opcional)
"""
import os
import sys
from datetime import datetime, timezone, timedelta
import uuid
import random

# Agregar el directorio padre al path para importar módulos
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.firebase_config import db


def crear_indices():
    """
    Información sobre índices necesarios en Firestore
    Los índices se deben crear desde Firebase Console
    """
    print("📋 ÍNDICES NECESARIOS EN FIRESTORE")
    print("=" * 80)
    print("\nNOTA: Los índices compuestos deben crearse manualmente desde Firebase Console")
    print("      Ve a: Firestore Database > Indexes > Create Index\n")
    
    indices = [
        {
            "collection": "reportes_seguimiento",
            "fields": [
                {"field": "estado", "order": "ASCENDING"},
                {"field": "updated_at", "order": "DESCENDING"}
            ]
        },
        {
            "collection": "reportes_seguimiento",
            "fields": [
                {"field": "prioridad", "order": "ASCENDING"},
                {"field": "updated_at", "order": "DESCENDING"}
            ]
        },
        {
            "collection": "reportes_seguimiento",
            "fields": [
                {"field": "encargado", "order": "ASCENDING"},
                {"field": "updated_at", "order": "DESCENDING"}
            ]
        },
        {
            "collection": "historial_avance_reportes",
            "fields": [
                {"field": "reporte_id", "order": "ASCENDING"},
                {"field": "fecha", "order": "DESCENDING"}
            ]
        },
        {
            "collection": "historial_avance_reportes",
            "fields": [
                {"field": "fecha", "order": "ASCENDING"}
            ]
        },
        {
            "collection": "evidencias_avance_reportes",
            "fields": [
                {"field": "historial_avance_id", "order": "ASCENDING"}
            ]
        }
    ]
    
    for i, indice in enumerate(indices, 1):
        print(f"\n{i}. Índice para colección '{indice['collection']}':")
        print(f"   Campos:")
        for field in indice["fields"]:
            print(f"   - {field['field']}: {field['order']}")
    
    print("\n" + "=" * 80)
    print("✅ Configuración de índices completa\n")


def verificar_colecciones():
    """
    Verifica que las colecciones principales existan
    """
    print("🔍 VERIFICANDO COLECCIONES")
    print("=" * 80)
    
    colecciones_requeridas = [
        'reconocimientos',  # Colección base de reportes
        'reportes_seguimiento',
        'historial_avance_reportes',
        'evidencias_avance_reportes'
    ]
    
    for coleccion in colecciones_requeridas:
        try:
            # Intentar obtener 1 documento
            docs = db.collection(coleccion).limit(1).stream()
            count = len(list(docs))
            print(f"✅ Colección '{coleccion}': OK (documentos: {count if count > 0 else 'vacía'})")
        except Exception as e:
            print(f"⚠️  Colección '{coleccion}': No accesible - {str(e)}")
    
    print("\n" + "=" * 80 + "\n")


def crear_datos_ejemplo(crear_ejemplos=False):
    """
    Crea datos de ejemplo para pruebas (opcional)
    """
    if not crear_ejemplos:
        print("⏭️  Omitiendo creación de datos de ejemplo")
        return
    
    print("📝 CREANDO DATOS DE EJEMPLO")
    print("=" * 80)
    
    try:
        # 1. Crear un reconocimiento de ejemplo (si no existe ninguno)
        reconocimientos_ref = db.collection('reconocimientos')
        reconocimientos_count = len(list(reconocimientos_ref.limit(1).stream()))
        
        reporte_id = None
        
        if reconocimientos_count == 0:
            print("\n1. Creando reconocimiento de ejemplo...")
            reporte_id = str(uuid.uuid4())
            
            reconocimiento_data = {
                'nombre_parque': 'Parque de los Poetas',
                'tipo_intervencion': 'Mantenimiento',
                'descripcion_intervencion': 'Poda de césped y limpieza general',
                'direccion': 'Carrera 50 #13-10, Cali',
                'timestamp': datetime.now(timezone.utc),
                'observaciones': 'Reconocimiento de ejemplo para pruebas',
                'coordinates': {
                    'type': 'Point',
                    'coordinates': [-76.5225, 3.4516]
                },
                'photos_uploaded': 2,
                'photosUrl': [
                    'https://ejemplo.com/foto1.jpg',
                    'https://ejemplo.com/foto2.jpg'
                ]
            }
            
            db.collection('reconocimientos').document(reporte_id).set(reconocimiento_data)
            print(f"   ✅ Reconocimiento creado: {reporte_id}")
        else:
            # Usar un reconocimiento existente
            doc = next(reconocimientos_ref.limit(1).stream())
            reporte_id = doc.id
            print(f"\n1. Usando reconocimiento existente: {reporte_id}")
        
        # 2. Crear seguimiento
        print("\n2. Creando registro de seguimiento...")
        seguimiento_data = {
            'reporte_id': reporte_id,
            'estado': 'en-gestion',
            'prioridad': 'media',
            'porcentaje_avance': 35,
            'encargado': 'Ing. Carlos Andrés Méndez Rojas',
            'centro_gestor': 'Secretaría de Infraestructura y Valorización',
            'created_at': datetime.now(timezone.utc) - timedelta(days=5),
            'updated_at': datetime.now(timezone.utc)
        }
        
        db.collection('reportes_seguimiento').document(reporte_id).set(seguimiento_data)
        print(f"   ✅ Seguimiento creado")
        
        # 3. Crear historial de avances
        print("\n3. Creando historial de avances...")
        
        avances = [
            {
                'fecha': datetime.now(timezone.utc) - timedelta(days=5),
                'autor': 'Sistema',
                'descripcion': 'Reporte creado y notificado automáticamente por el sistema',
                'estado_anterior': 'notificado',
                'estado_nuevo': 'notificado',
                'porcentaje': 0
            },
            {
                'fecha': datetime.now(timezone.utc) - timedelta(days=4),
                'autor': 'María López García',
                'descripcion': 'Se radicó ante la Secretaría de Infraestructura con radicado No. RAD-2026-001234. Se asignó número de seguimiento interno.',
                'estado_anterior': 'notificado',
                'estado_nuevo': 'radicado',
                'porcentaje': 25
            },
            {
                'fecha': datetime.now(timezone.utc) - timedelta(days=2),
                'autor': 'María López García',
                'descripcion': 'Se coordinó visita técnica con el ingeniero Carlos Méndez de la Secretaría. La inspección está programada para revisar el estado del parque y determinar alcance de las intervenciones necesarias.',
                'estado_anterior': 'radicado',
                'estado_nuevo': 'en-gestion',
                'porcentaje': 35
            }
        ]
        
        historial_ids = []
        for i, avance in enumerate(avances, 1):
            historial_id = str(uuid.uuid4())
            historial_ids.append(historial_id)
            
            avance_data = {
                'reporte_id': reporte_id,
                'fecha': avance['fecha'],
                'autor': avance['autor'],
                'descripcion': avance['descripcion'],
                'estado_anterior': avance['estado_anterior'],
                'estado_nuevo': avance['estado_nuevo'],
                'porcentaje': avance['porcentaje'],
                'created_at': avance['fecha']
            }
            
            db.collection('historial_avance_reportes').document(historial_id).set(avance_data)
            print(f"   ✅ Avance {i}/3 creado")
        
        # 4. Crear evidencias de ejemplo
        print("\n4. Creando evidencias de ejemplo...")
        
        # Evidencia para el segundo avance (radicado)
        evidencia_data = {
            'historial_avance_id': historial_ids[1],
            'tipo': 'documento',
            'url': 'https://docs.ejemplo.com/radicado-RAD-2026-001234.pdf',
            'descripcion': 'Radicado oficial No. RAD-2026-001234',
            'created_at': datetime.now(timezone.utc) - timedelta(days=4)
        }
        
        evidencia_id = str(uuid.uuid4())
        db.collection('evidencias_avance_reportes').document(evidencia_id).set(evidencia_data)
        print(f"   ✅ Evidencia creada")
        
        print("\n" + "=" * 80)
        print(f"✅ Datos de ejemplo creados exitosamente")
        print(f"\n📌 ID del reporte de ejemplo: {reporte_id}")
        print(f"   Puedes usar este ID para probar los endpoints:")
        print(f"   GET  /api/v1/reportes/{reporte_id}/historial")
        print(f"   POST /api/v1/reportes/{reporte_id}/avance")
        
    except Exception as e:
        print(f"\n❌ Error creando datos de ejemplo: {str(e)}")
        import traceback
        traceback.print_exc()


def mostrar_informacion_uso():
    """
    Muestra información sobre cómo usar el sistema
    """
    print("\n" + "=" * 80)
    print("📚 INFORMACIÓN DE USO")
    print("=" * 80)
    print("""
El Sistema de Seguimiento de Reportes está listo para usar.

🚀 PRÓXIMOS PASOS:

1. Inicia el servidor de desarrollo:
   python app/run.py
   
   O con uvicorn:
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

2. Accede a la documentación interactiva:
   http://localhost:8000/docs

3. Prueba los endpoints:
   python test_seguimiento_endpoints.py

4. Lee la documentación completa:
   - README_SISTEMA_SEGUIMIENTO.md
   - DOCUMENTACION_ENDPOINTS_SEGUIMIENTO.md

📋 ENDPOINTS DISPONIBLES:

   GET    /api/v1/reportes/seguimiento
   POST   /api/v1/reportes/{id}/avance
   PATCH  /api/v1/reportes/{id}/encargado
   PATCH  /api/v1/reportes/{id}/prioridad
   GET    /api/v1/reportes/{id}/historial
   GET    /api/v1/reportes/seguimiento/estadisticas

🔍 RECURSOS:

   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc
   - Firebase Console: https://console.firebase.google.com/
   
""")
    print("=" * 80 + "\n")


def main():
    """
    Función principal de inicialización
    """
    print("\n")
    print("=" * 80)
    print("  INICIALIZACIÓN DEL SISTEMA DE SEGUIMIENTO DE REPORTES DAGMA")
    print("=" * 80)
    print(f"\nFecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    try:
        # 1. Verificar colecciones
        verificar_colecciones()
        
        # 2. Mostrar información de índices
        crear_indices()
        
        # 3. Preguntar si crear datos de ejemplo
        print("💡 ¿Deseas crear datos de ejemplo para pruebas?")
        print("   Esto creará:")
        print("   - 1 reconocimiento de ejemplo")
        print("   - 1 registro de seguimiento")
        print("   - 3 avances en el historial")
        print("   - 1 evidencia de ejemplo")
        print()
        respuesta = input("   Crear datos de ejemplo? (s/N): ").strip().lower()
        
        crear_ejemplos = respuesta in ['s', 'si', 'sí', 'y', 'yes']
        
        if crear_ejemplos:
            crear_datos_ejemplo(True)
        else:
            print("\n⏭️  Omitiendo creación de datos de ejemplo")
        
        # 4. Mostrar información de uso
        mostrar_informacion_uso()
        
        print("✅ Inicialización completada exitosamente\n")
        
    except Exception as e:
        print(f"\n❌ Error durante la inicialización: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
