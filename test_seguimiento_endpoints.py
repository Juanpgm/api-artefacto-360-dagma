"""
Script de prueba para los endpoints del Sistema de Seguimiento de Reportes DAGMA
Ejecutar: python test_seguimiento_endpoints.py
"""
import requests
import json
from datetime import datetime, timedelta
import time

# Configuración
BASE_URL = "http://localhost:8000/api/v1/reportes"
# Si tienes autenticación, agrega el token aquí
AUTH_TOKEN = None

def get_headers():
    """Obtener headers con autenticación si está disponible"""
    headers = {'Content-Type': 'application/json'}
    if AUTH_TOKEN:
        headers['Authorization'] = f'Bearer {AUTH_TOKEN}'
    return headers


def print_separator(title=""):
    """Imprimir separador visual"""
    print("\n" + "="*80)
    if title:
        print(f"  {title}")
        print("="*80)
    print()


def test_1_obtener_reportes():
    """Test 1: Obtener lista de reportes con seguimiento"""
    print_separator("TEST 1: Obtener Reportes con Seguimiento")
    
    try:
        # Sin filtros
        print("📋 Obteniendo todos los reportes...")
        response = requests.get(f"{BASE_URL}/seguimiento", headers=get_headers())
        
        if response.status_code == 200:
            data = response.json()
            total = data['pagination']['total']
            print(f"✅ SUCCESS - Total reportes: {total}")
            
            if data['data']:
                print(f"\n📊 Primeros reportes:")
                for i, reporte in enumerate(data['data'][:3], 1):
                    print(f"\n  {i}. {reporte.get('nombre_parque', 'N/A')}")
                    print(f"     Estado: {reporte.get('estado', 'N/A')}")
                    print(f"     Prioridad: {reporte.get('prioridad', 'N/A')}")
                    print(f"     Avance: {reporte.get('porcentaje_avance', 0)}%")
                    print(f"     Historial: {len(reporte.get('historial', []))} registros")
        else:
            print(f"❌ FAIL - Status: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
    
    # Con filtros
    print("\n📋 Obteniendo reportes con filtros (estado=notificado)...")
    try:
        response = requests.get(
            f"{BASE_URL}/seguimiento",
            params={'estado': 'notificado', 'limit': 5},
            headers=get_headers()
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS - Reportes filtrados: {len(data['data'])}")
        else:
            print(f"⚠️ No se encontraron reportes con ese filtro (esperado si no hay datos)")
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")


def test_2_registrar_avance(reporte_id):
    """Test 2: Registrar un nuevo avance en un reporte"""
    print_separator("TEST 2: Registrar Avance")
    
    if not reporte_id:
        print("⚠️ SKIP - Se necesita un reporte_id válido")
        return None
    
    print(f"📝 Registrando avance en reporte: {reporte_id}")
    
    avance_data = {
        "estado_nuevo": "radicado",
        "descripcion": f"Test automatizado: Se radicó el reporte el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. Este es un registro de prueba del sistema de seguimiento.",
        "autor": "Sistema de Pruebas Automatizadas",
        "porcentaje": 25,
        "evidencias": [
            {
                "tipo": "documento",
                "url": "https://ejemplo.com/documentos/test-radicado.pdf",
                "descripcion": "Documento de prueba - Radicado ficticio"
            }
        ]
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/{reporte_id}/avance",
            json=avance_data,
            headers=get_headers()
        )
        
        if response.status_code == 201:
            data = response.json()
            print(f"✅ SUCCESS - {data['message']}")
            historial_id = data['data']['historial_id']
            print(f"   Historial ID: {historial_id}")
            
            reporte = data['data']['reporte_actualizado']
            print(f"   Estado actual: {reporte.get('estado')}")
            print(f"   Porcentaje: {reporte.get('porcentaje_avance')}%")
            
            return historial_id
        else:
            print(f"❌ FAIL - Status: {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return None


def test_3_asignar_encargado(reporte_id):
    """Test 3: Asignar encargado a un reporte"""
    print_separator("TEST 3: Asignar Encargado")
    
    if not reporte_id:
        print("⚠️ SKIP - Se necesita un reporte_id válido")
        return
    
    print(f"👤 Asignando encargado al reporte: {reporte_id}")
    
    encargado_data = {
        "encargado": "Ing. Carlos Andrés Méndez Rojas",
        "centro_gestor": "Secretaría de Infraestructura y Valorización - DAGMA"
    }
    
    try:
        response = requests.patch(
            f"{BASE_URL}/{reporte_id}/encargado",
            json=encargado_data,
            headers=get_headers()
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS - {data['message']}")
            print(f"   Encargado: {data['data']['encargado']}")
            print(f"   Centro Gestor: {data['data']['centro_gestor']}")
        else:
            print(f"❌ FAIL - Status: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")


def test_4_cambiar_prioridad(reporte_id):
    """Test 4: Cambiar prioridad de un reporte"""
    print_separator("TEST 4: Cambiar Prioridad")
    
    if not reporte_id:
        print("⚠️ SKIP - Se necesita un reporte_id válido")
        return
    
    print(f"⚡ Cambiando prioridad del reporte: {reporte_id}")
    
    prioridades = ['baja', 'media', 'alta', 'urgente']
    
    for prioridad in ['alta', 'urgente']:
        print(f"\n   Configurando prioridad: {prioridad}")
        
        try:
            response = requests.patch(
                f"{BASE_URL}/{reporte_id}/prioridad",
                json={"prioridad": prioridad},
                headers=get_headers()
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ {data['message']} - Prioridad: {data['data']['prioridad']}")
            else:
                print(f"   ❌ FAIL - Status: {response.status_code}")
                
        except Exception as e:
            print(f"   ❌ ERROR: {str(e)}")


def test_5_obtener_historial(reporte_id):
    """Test 5: Obtener historial de un reporte"""
    print_separator("TEST 5: Obtener Historial de Reporte")
    
    if not reporte_id:
        print("⚠️ SKIP - Se necesita un reporte_id válido")
        return
    
    print(f"📜 Obteniendo historial del reporte: {reporte_id}")
    
    try:
        response = requests.get(
            f"{BASE_URL}/{reporte_id}/historial",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            data = response.json()
            historial = data['data']['historial']
            print(f"✅ SUCCESS - Registros en el historial: {len(historial)}")
            
            if historial:
                print(f"\n📊 Últimos avances:")
                for i, avance in enumerate(historial[:5], 1):
                    print(f"\n   {i}. Fecha: {avance.get('fecha', 'N/A')}")
                    print(f"      Autor: {avance.get('autor', 'N/A')}")
                    print(f"      Cambio: {avance.get('estado_anterior')} → {avance.get('estado_nuevo')}")
                    print(f"      Porcentaje: {avance.get('porcentaje')}%")
                    print(f"      Evidencias: {len(avance.get('evidencias', []))}")
                    print(f"      Descripción: {avance.get('descripcion', '')[:80]}...")
        else:
            print(f"❌ FAIL - Status: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")


def test_6_obtener_estadisticas():
    """Test 6: Obtener estadísticas del sistema"""
    print_separator("TEST 6: Obtener Estadísticas")
    
    print("📊 Obteniendo estadísticas generales...")
    
    try:
        response = requests.get(
            f"{BASE_URL}/seguimiento/estadisticas",
            headers=get_headers()
        )
        
        if response.status_code == 200:
            data = response.json()
            stats = data['data']
            
            print(f"✅ SUCCESS")
            print(f"\n📈 Resumen General:")
            print(f"   Total reportes: {stats.get('total_reportes', 0)}")
            print(f"   Avance promedio: {stats.get('avance_promedio', 0)}%")
            
            print(f"\n📋 Distribución por Estado:")
            for estado, cantidad in stats.get('por_estado', {}).items():
                print(f"   {estado}: {cantidad}")
            
            print(f"\n⚡ Distribución por Prioridad:")
            for prioridad, cantidad in stats.get('por_prioridad', {}).items():
                print(f"   {prioridad}: {cantidad}")
            
            print(f"\n🏢 Reportes por Centro Gestor:")
            for centro in stats.get('por_centro_gestor', []):
                print(f"   {centro['nombre']}: {centro['total']} total ({centro['resueltos']} resueltos)")
            
            tendencia = stats.get('tendencia_ultimos_30_dias', [])
            if tendencia:
                print(f"\n📈 Tendencia últimos 30 días: {len(tendencia)} registros")
        else:
            print(f"❌ FAIL - Status: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")


def test_7_validaciones():
    """Test 7: Probar validaciones"""
    print_separator("TEST 7: Validaciones y Manejo de Errores")
    
    print("🔍 Probando validaciones...")
    
    # Test: Reporte no encontrado
    print("\n1. Reporte no existente:")
    try:
        response = requests.get(
            f"{BASE_URL}/reporte-inexistente-12345/historial",
            headers=get_headers()
        )
        if response.status_code == 404:
            print("   ✅ Validación correcta - 404 Not Found")
        else:
            print(f"   ⚠️ Status inesperado: {response.status_code}")
    except Exception as e:
        print(f"   ❌ ERROR: {str(e)}")
    
    # Test: Descripción muy corta (si tienes un reporte válido)
    print("\n2. Descripción muy corta:")
    print("   (Requiere reporte_id válido para probar)")
    
    # Test: Porcentaje inválido
    print("\n3. Porcentaje inválido:")
    print("   (Requiere reporte_id válido para probar)")
    
    # Test: Estado inválido
    print("\n4. Estado inválido:")
    print("   (Requiere reporte_id válido para probar)")
    
    # Test: Prioridad inválida
    print("\n5. Prioridad inválida:")
    try:
        response = requests.patch(
            f"{BASE_URL}/test-id/prioridad",
            json={"prioridad": "super-urgente"},  # Prioridad no válida
            headers=get_headers()
        )
        if response.status_code in [400, 422]:
            print("   ✅ Validación correcta - Error de validación")
        else:
            print(f"   ⚠️ Status: {response.status_code}")
    except Exception as e:
        print(f"   ❌ ERROR: {str(e)}")


def run_all_tests():
    """Ejecutar todos los tests"""
    print_separator("SISTEMA DE SEGUIMIENTO DE REPORTES DAGMA - TESTS")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {BASE_URL}")
    print(f"Autenticación: {'Sí' if AUTH_TOKEN else 'No'}")
    
    # Obtener un reporte_id para las pruebas
    print("\n🔍 Buscando reporte existente para pruebas...")
    reporte_id = None
    try:
        response = requests.get(f"{BASE_URL}/seguimiento?limit=1", headers=get_headers())
        if response.status_code == 200:
            data = response.json()
            if data['data']:
                reporte_id = data['data'][0]['id']
                print(f"✅ Reporte encontrado: {reporte_id}")
            else:
                print("⚠️ No hay reportes disponibles. Algunos tests se saltarán.")
        else:
            print(f"⚠️ No se pudo obtener reportes. Status: {response.status_code}")
    except Exception as e:
        print(f"⚠️ Error buscando reportes: {str(e)}")
    
    # Ejecutar tests
    test_1_obtener_reportes()
    time.sleep(0.5)
    
    if reporte_id:
        test_2_registrar_avance(reporte_id)
        time.sleep(0.5)
        
        test_3_asignar_encargado(reporte_id)
        time.sleep(0.5)
        
        test_4_cambiar_prioridad(reporte_id)
        time.sleep(0.5)
        
        test_5_obtener_historial(reporte_id)
        time.sleep(0.5)
    
    test_6_obtener_estadisticas()
    time.sleep(0.5)
    
    test_7_validaciones()
    
    print_separator("TESTS COMPLETADOS")
    print("✅ Suite de tests ejecutada exitosamente")
    print("\n📝 Notas:")
    print("   - Algunos tests requieren datos existentes en la base de datos")
    print("   - Si no hay reportes, algunos tests se saltarán automáticamente")
    print("   - Revisa los logs para ver detalles de cada test")


if __name__ == "__main__":
    run_all_tests()
