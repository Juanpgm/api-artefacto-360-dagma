"""
Script para probar el endpoint GET /grupo-cuadrilla/reportes_intervenciones

Prueba los diferentes filtros: id, id_actividad, grupo
"""
import requests
import json

# URL base de la API
BASE_URL = "http://localhost:8000"

def test_endpoint(descripcion, params=None):
    """Prueba el endpoint con los parámetros dados"""
    print(f"\n{'='*70}")
    print(f"TEST: {descripcion}")
    print(f"{'='*70}")
    
    url = f"{BASE_URL}/grupo-cuadrilla/reportes_intervenciones"
    
    try:
        response = requests.get(url, params=params)
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS")
            print(f"Total reportes: {data.get('total', 0)}")
            print(f"Filtros aplicados: {json.dumps(data.get('filters', {}), indent=2)}")
            
            # Mostrar primeros 3 reportes (si existen)
            reportes = data.get('data', [])
            if reportes:
                print(f"\nPrimeros {min(3, len(reportes))} reportes:")
                for i, reporte in enumerate(reportes[:3], 1):
                    print(f"\n  {i}. ID: {reporte.get('id', 'N/A')}")
                    print(f"     Tipo: {reporte.get('tipo_intervencion', 'N/A')}")
                    print(f"     Grupo: {reporte.get('grupo', 'N/A')}")
                    print(f"     ID Actividad: {reporte.get('id_actividad', 'N/A')}")
                    print(f"     Registrado por: {reporte.get('registrado_por', 'N/A')}")
                    print(f"     Timestamp: {reporte.get('timestamp', 'N/A')}")
            else:
                print("\n⚠️ No se encontraron reportes con estos filtros")
        else:
            print(f"❌ ERROR: {response.status_code}")
            print(f"Detalle: {response.text}")
            
    except Exception as e:
        print(f"❌ EXCEPCIÓN: {str(e)}")

def main():
    """Función principal"""
    print("=" * 70)
    print("TEST: Endpoint GET /grupo-cuadrilla/reportes_intervenciones")
    print("=" * 70)
    
    # Test 1: Obtener todos los reportes (sin filtros)
    test_endpoint(
        "Obtener todos los reportes (sin filtros)"
    )
    
    # Test 2: Filtrar por ID de actividad (usando uno de los registros de prueba)
    test_endpoint(
        "Filtrar por ID de actividad",
        params={"id_actividad": "ACT-2026-9045"}
    )
    
    # Test 3: Filtrar por grupo
    test_endpoint(
        "Filtrar por grupo",
        params={"grupo": "Equipo de Mantenimiento 1"}
    )
    
    # Test 4: Filtrar por ID de actividad inexistente
    test_endpoint(
        "Filtrar por ID de actividad inexistente",
        params={"id_actividad": "ACT-INEXISTENTE"}
    )
    
    # Test 5: Combinar filtros (id_actividad + grupo)
    test_endpoint(
        "Combinar filtros: id_actividad + grupo",
        params={
            "id_actividad": "ACT-2026-9045",
            "grupo": "Equipo de Mantenimiento 1"
        }
    )
    
    # Test 6: Filtrar por ID específico (necesitarás un ID real de tu base de datos)
    # Para obtener un ID, primero ejecuta el test sin filtros y copia uno de los IDs
    print("\n" + "=" * 70)
    print("Para probar el filtro por ID específico:")
    print("1. Ejecuta este script primero sin filtros")
    print("2. Copia un ID de los reportes mostrados")
    print("3. Descomenta y modifica el siguiente test con ese ID")
    print("=" * 70)
    
    # test_endpoint(
    #     "Filtrar por ID específico",
    #     params={"id": "REEMPLAZA-CON-UN-ID-REAL"}
    # )
    
    print("\n" + "=" * 70)
    print("RESUMEN: Tests completados")
    print("=" * 70)

if __name__ == "__main__":
    main()
