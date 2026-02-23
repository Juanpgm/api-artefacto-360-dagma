"""
Script para generar 20 registros de prueba en el endpoint POST /grupo-cuadrilla/reporte_intervencion

Este script genera datos variados de reportes de intervención con diferentes combinaciones de campos opcionales.
"""
import requests
import json
import random
from datetime import datetime, timedelta

# URL base de la API
BASE_URL = "http://localhost:8000"  # Ajustar según tu configuración

# Datos de ejemplo para generar registros variados
TIPOS_INTERVENCION = [
    "Poda de árboles",
    "Remoción de residuos",
    "Mantenimiento de zonas verdes",
    "Plantación de árboles",
    "Control de plagas",
    "Riego de áreas verdes",
    "Limpieza de parques",
    "Recolección de hojas",
    "Fumigación",
    "Fertilización"
]

TIPOS_ARBOL = [
    "Guayacán",
    "Ceiba",
    "Samán",
    "Oití",
    "Caucho",
    "Palma",
    "Acacia",
    "Chiminango",
    "Yarumo",
    "Carbonero"
]

OBJETIVOS_ACTIVIDAD = [
    "Mantenimiento rutinario del parque",
    "Mejoramiento estético de la zona verde",
    "Prevención de riesgos por caída de ramas",
    "Control fitosanitario",
    "Embellecimiento del sector",
    "Limpieza general del área",
    "Conservación de especies arbóreas",
    "Adecuación de espacios recreativos"
]

DIRECCIONES = [
    "Calle 5 con Carrera 50, Comuna 2",
    "Avenida 6N #28-10, Comuna 19",
    "Carrera 100 #15-25, Comuna 22",
    "Calle 70 #2N-45, Comuna 4",
    "Diagonal 24 #8-30, Comuna 10",
    "Transversal 36 #5B-20, Comuna 17",
    "Avenida Pasoancho, Comuna 3",
    "Calle 13 #100-50, Comuna 18",
    "Carrera 8 #40-15, Comuna 9",
    "Avenida Roosevelt #45-20, Comuna 14"
]

LIDERES = [
    "Carlos Rodríguez",
    "María Fernanda López",
    "Juan Pablo Martínez",
    "Ana Lucía García",
    "Pedro José Sánchez",
    "Diana Carolina Muñoz",
    "Jorge Andrés Pérez",
    "Claudia Patricia Torres"
]

REGISTRADORES = [
    "Luis González",
    "Sandra Ramírez",
    "Roberto Castro",
    "Patricia Díaz",
    "Fernando Vargas",
    "Alejandra Ruiz"
]

# Coordenadas de ejemplo en Cali (diferentes puntos de la ciudad)
COORDENADAS = [
    {"type": "Point", "coordinates": [-76.5225, 3.4516]},  # Centro
    {"type": "Point", "coordinates": [-76.5320, 3.3950]},  # Sur
    {"type": "Point", "coordinates": [-76.5410, 3.4320]},  # Oeste
    {"type": "Point", "coordinates": [-76.5100, 3.4720]},  # Norte
    {"type": "Point", "coordinates": [-76.4980, 3.4450]},  # Este
    {"type": "Point", "coordinates": [-76.5280, 3.4180]},
    {"type": "Point", "coordinates": [-76.5150, 3.4610]},
    {"type": "Point", "coordinates": [-76.5390, 3.4080]},
]

def generar_fecha_aleatoria():
    """Genera una fecha aleatoria en los próximos 30 días"""
    dias = random.randint(1, 30)
    fecha = datetime.now() + timedelta(days=dias)
    return fecha.strftime("%Y-%m-%d")

def generar_hora_aleatoria():
    """Genera una hora aleatoria entre 6:00 AM y 5:00 PM"""
    hora = random.randint(6, 17)
    minuto = random.choice([0, 15, 30, 45])
    return f"{hora:02d}:{minuto:02d}"

def generar_grupo_aleatorio():
    """Genera un nombre de grupo aleatorio"""
    grupos = [
        "Cuadrilla Verde A",
        "Equipo de Mantenimiento 1",
        "Brigada Ambiental B",
        "Grupo Operativo Norte",
        "Cuadrilla de Poda C",
        "Equipo Verde Sur"
    ]
    return random.choice(grupos)

def generar_reporte(numero):
    """Genera un reporte de intervención con datos aleatorios"""
    print(f"\n{'='*60}")
    print(f"REPORTE #{numero}")
    print(f"{'='*60}")
    
    # Campos base (algunos opcionales se incluyen aleatoriamente)
    data = {}
    
    # 80% de probabilidad de incluir cada campo opcional
    if random.random() > 0.2:
        data['tipo_intervencion'] = random.choice(TIPOS_INTERVENCION)
        print(f"Tipo intervención: {data['tipo_intervencion']}")
    
    if random.random() > 0.2:
        data['descripcion_intervencion'] = f"Intervención #{numero}: {random.choice(['Trabajo realizado con éxito', 'Operación completada', 'Actividad ejecutada correctamente', 'Labor finalizada'])}"
        print(f"Descripción: {data['descripcion_intervencion']}")
    
    if random.random() > 0.3:  # 70% de probabilidad
        data['tipo_arbol'] = random.choice(TIPOS_ARBOL)
        print(f"Tipo árbol: {data['tipo_arbol']}")
    
    if random.random() > 0.3:
        data['numero_individuos_intervenidos'] = str(random.randint(1, 50))
        print(f"Individuos intervenidos: {data['numero_individuos_intervenidos']}")
    
    if random.random() > 0.2:
        data['registrado_por'] = random.choice(REGISTRADORES)
        print(f"Registrado por: {data['registrado_por']}")
    
    if random.random() > 0.2:
        data['grupo'] = generar_grupo_aleatorio()
        print(f"Grupo: {data['grupo']}")
    
    if random.random() > 0.4:  # 60% de probabilidad
        data['id_actividad'] = f"ACT-2026-{random.randint(1000, 9999)}"
        print(f"ID Actividad: {data['id_actividad']}")
    
    # Coordenadas (90% de probabilidad)
    if random.random() > 0.1:
        coord = random.choice(COORDENADAS)
        data['coordinates_type'] = coord['type']
        data['coordinates_data'] = json.dumps(coord['coordinates'])
        print(f"Coordenadas: {coord['coordinates']}")
    
    if random.random() > 0.3:
        data['observaciones'] = f"Observación #{numero}: {random.choice(['Sin novedades', 'Trabajo completado satisfactoriamente', 'Requiere seguimiento', 'Área en buenas condiciones', 'Intervención exitosa'])}"
        print(f"Observaciones: {data['observaciones']}")
    
    return data

def enviar_reporte(numero, data):
    """Envía un reporte al endpoint"""
    try:
        url = f"{BASE_URL}/grupo-cuadrilla/reporte_intervencion"
        
        # Hacer la petición POST
        response = requests.post(url, data=data)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ ÉXITO - ID: {result.get('id', 'N/A')}")
            return True
        else:
            print(f"❌ ERROR {response.status_code}")
            print(f"Detalle: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ EXCEPCIÓN: {str(e)}")
        return False

def main():
    """Función principal"""
    print("=" * 60)
    print("TEST: Enviar 20 reportes de intervención al endpoint")
    print(f"Endpoint: POST {BASE_URL}/grupo-cuadrilla/reporte_intervencion")
    print("=" * 60)
    
    exitos = 0
    fallos = 0
    
    for i in range(1, 21):
        data = generar_reporte(i)
        if enviar_reporte(i, data):
            exitos += 1
        else:
            fallos += 1
    
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"✅ Reportes exitosos: {exitos}")
    print(f"❌ Reportes fallidos: {fallos}")
    print(f"📊 Total procesados: {exitos + fallos}")
    print(f"📈 Tasa de éxito: {(exitos/(exitos+fallos)*100):.1f}%")
    print("=" * 60)

if __name__ == "__main__":
    main()
