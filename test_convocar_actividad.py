"""
Prueba para el endpoint POST /convocar_actividad
"""
import requests
import json

API_URL = "http://localhost:8000"
ENDPOINT = f"{API_URL}/convocar_actividad"

def test_convocar_actividad():
    """
    Prueba del endpoint POST /convocar_actividad
    """
    payload = {
        "fecha_actividad": "20/02/2026",
        "hora_encuentro": "14:30",
        "grupos_requeridos": ["Grupo A", "Grupo B"],
        "lider_actividad": "Juan Pérez",
        "punto_encuentro": {
            "geometry": {"type": "Point", "coordinates": [-76.5225, 3.4516]},
            "direccion": "Calle 5 #10-20"
        },
        "observaciones": "Llevar herramientas",
        "telefono": "3001234567",
        "personas_requeridas_grupo": 5,
        "objetivo_actividad": "Limpieza de parque",
        "email": "juan.perez@email.com"
    }
    print("\n📤 Enviando petición al endpoint /convocar_actividad...")
    print(f"   URL: {ENDPOINT}")
    print(f"   Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")
    response = requests.post(ENDPOINT, json=payload)
    print(f"\n📥 Respuesta recibida:")
    print(f"   Status Code: {response.status_code}")
    try:
        result = response.json()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception:
        print(response.text)
    assert response.status_code == 200, "El endpoint no respondió con éxito"
    assert result["success"] is True, "El campo 'success' debe ser True"
    assert "id" in result, "Debe retornar un id"
    assert "marca_temporal" in result, "Debe retornar marca_temporal"
    assert "data" in result, "Debe retornar los datos registrados"

if __name__ == "__main__":
    test_convocar_actividad()
