"""
Script de prueba para los nuevos endpoints del Dashboard
- GET /grupo-operativo/stats
- GET /grupo-operativo/reportes/recent
- GET /grupo-operativo/reportes (con filtros)
"""
import requests
import json

# Configuración
API_URL = "http://localhost:8000"

def test_stats_endpoint():
    """
    Prueba del endpoint GET /grupo-operativo/stats
    """
    print("\n" + "="*60)
    print("PRUEBA 1: Estadísticas del Dashboard")
    print("="*60)
    
    url = f"{API_URL}/grupo-operativo/stats"
    
    try:
        response = requests.get(url)
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Respuesta exitosa:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # Validar estructura de la respuesta
            assert "success" in data, "Falta campo 'success'"
            assert "data" in data, "Falta campo 'data'"
            assert "total_visitas_mes" in data["data"], "Falta 'total_visitas_mes'"
            assert "total_pendientes" in data["data"], "Falta 'total_pendientes'"
            assert "parques_visitados" in data["data"], "Falta 'parques_visitados'"
            
            print("\n✅ Todas las validaciones pasaron")
        else:
            print(f"\n❌ Error: {response.text}")
    except Exception as e:
        print(f"\n❌ Error en la prueba: {str(e)}")


def test_recent_reports_endpoint():
    """
    Prueba del endpoint GET /grupo-operativo/reportes/recent
    """
    print("\n" + "="*60)
    print("PRUEBA 2: Actividad Reciente")
    print("="*60)
    
    # Probar con diferentes límites
    test_cases = [
        {"limit": 3, "descripcion": "Últimos 3 reportes (default)"},
        {"limit": 5, "descripcion": "Últimos 5 reportes"},
        {"limit": 1, "descripcion": "Último reporte"},
    ]
    
    for test_case in test_cases:
        print(f"\n📋 {test_case['descripcion']}")
        url = f"{API_URL}/grupo-operativo/reportes/recent?limit={test_case['limit']}"
        
        try:
            response = requests.get(url)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Reportes obtenidos: {data['count']}")
                print(f"Timestamp: {data['timestamp']}")
                
                # Validar estructura
                assert data["count"] <= test_case["limit"], f"Se retornaron más reportes que el límite especificado"
                print("✅ Validación pasada")
            else:
                print(f"❌ Error: {response.text}")
        except Exception as e:
            print(f"❌ Error en la prueba: {str(e)}")


def test_filtered_reports_endpoint():
    """
    Prueba del endpoint GET /grupo-operativo/reportes con filtros
    """
    print("\n" + "="*60)
    print("PRUEBA 3: Reportes con Filtros y Paginación")
    print("="*60)
    
    test_cases = [
        {
            "params": {},
            "descripcion": "Sin filtros - Todos los reportes"
        },
        {
            "params": {"year": 2024, "month": 2},
            "descripcion": "Filtro por fecha: Febrero 2024"
        },
        {
            "params": {"search": "parque"},
            "descripcion": "Búsqueda: 'parque'"
        },
        {
            "params": {"type": "Mantenimiento"},
            "descripcion": "Filtro por tipo: Mantenimiento"
        },
        {
            "params": {"page": 1, "limit": 10},
            "descripcion": "Paginación: Página 1, 10 resultados"
        },
        {
            "params": {"year": 2024, "month": 1, "page": 1, "limit": 5},
            "descripcion": "Filtros combinados: Enero 2024 + Paginación"
        }
    ]
    
    for test_case in test_cases:
        print(f"\n📋 {test_case['descripcion']}")
        
        # Construir URL con parámetros
        url = f"{API_URL}/grupo-operativo/reportes"
        
        try:
            response = requests.get(url, params=test_case["params"])
            print(f"Status Code: {response.status_code}")
            print(f"URL: {response.url}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Reportes obtenidos: {data['pagination']['total_items']}")
                print(f"Página actual: {data['pagination']['page']}/{data['pagination']['total_pages']}")
                print(f"Filtros aplicados: {json.dumps(data['filters'], ensure_ascii=False)}")
                
                # Validar estructura
                assert "success" in data
                assert "data" in data
                assert "pagination" in data
                assert "filters" in data
                assert isinstance(data["data"], list)
                
                print("✅ Validación pasada")
            else:
                print(f"❌ Error: {response.text}")
        except Exception as e:
            print(f"❌ Error en la prueba: {str(e)}")


def test_pagination_logic():
    """
    Prueba específica de la lógica de paginación
    """
    print("\n" + "="*60)
    print("PRUEBA 4: Validación de Paginación")
    print("="*60)
    
    url = f"{API_URL}/grupo-operativo/reportes"
    
    try:
        # Primera página
        response_page1 = requests.get(url, params={"page": 1, "limit": 5})
        data_page1 = response_page1.json()
        
        print(f"\nPágina 1:")
        print(f"  - Total items: {data_page1['pagination']['total_items']}")
        print(f"  - Total páginas: {data_page1['pagination']['total_pages']}")
        print(f"  - Has next: {data_page1['pagination']['has_next']}")
        print(f"  - Has prev: {data_page1['pagination']['has_prev']}")
        
        # Validaciones
        assert data_page1['pagination']['has_prev'] == False, "Primera página no debería tener 'prev'"
        
        # Si hay más de una página, probar la segunda
        if data_page1['pagination']['has_next']:
            response_page2 = requests.get(url, params={"page": 2, "limit": 5})
            data_page2 = response_page2.json()
            
            print(f"\nPágina 2:")
            print(f"  - Has next: {data_page2['pagination']['has_next']}")
            print(f"  - Has prev: {data_page2['pagination']['has_prev']}")
            
            assert data_page2['pagination']['has_prev'] == True, "Segunda página debería tener 'prev'"
            print("\n✅ Lógica de paginación funciona correctamente")
        else:
            print("\n⚠️  Solo hay una página de datos. No se puede probar paginación completa.")
            
    except Exception as e:
        print(f"\n❌ Error en la prueba: {str(e)}")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("🧪 PRUEBAS DE NUEVOS ENDPOINTS DEL DASHBOARD")
    print("="*60)
    print(f"\nAPI Base URL: {API_URL}")
    print("Asegúrate de que el servidor esté corriendo en el puerto 8000")
    
    input("\nPresiona ENTER para comenzar las pruebas...")
    
    # Ejecutar pruebas
    test_stats_endpoint()
    test_recent_reports_endpoint()
    test_filtered_reports_endpoint()
    test_pagination_logic()
    
    print("\n" + "="*60)
    print("✅ TODAS LAS PRUEBAS COMPLETADAS")
    print("="*60)
