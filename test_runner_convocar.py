from fastapi.testclient import TestClient
from app.main import app
import json
import os
import sys

# Add the project root to sys.path to ensure imports work
sys.path.append(os.getcwd())

client = TestClient(app)

def test_convocar_actividad():
    print("Testing /programar_actividad endpoint...")
    
    payload = {
        "fecha_actividad": "20/02/2026",
        "hora_encuentro": "14:30",
        "tipo_jornada": "Jornada de limpieza",
        "duracion_actividad": 3.5,
        "grupos_requeridos": ["Grupo A", "Grupo B"],
        "lider_actividad": "Juan Pérez",
        "punto_encuentro": {
            "geometry": {"type": "Point", "coordinates": [-76.5225, 3.4516]},
            "direccion": "Calle 5 #10-20"
        },
        "observaciones": "Llevar herramientas",
        "telefono": "3001234567",
        "objetivo_actividad": "Limpieza de parque",
        "email": "juan.perez@email.com"
    }
    
    response = client.post("/programar_actividad", json=payload)
    
    print(f"Status Code: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Response: {response.text}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "id" in data
    assert "marca_temporal" in data
    
    print("Test Validated Successfully!")
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    test_convocar_actividad()
