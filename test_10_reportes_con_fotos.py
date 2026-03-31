"""
Script para probar el endpoint POST /grupo-cuadrilla/reporte_intervencion con 10 registros variados

Este script genera 10 reportes de intervención con diferentes combinaciones de campos y fotos.
"""
import requests
import json
import io
import random
from PIL import Image

# URL base de la API
BASE_URL = "http://localhost:8000"

# Datos de ejemplo variados
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

REGISTRADORES = [
    "Luis González",
    "Sandra Ramírez",
    "Roberto Castro",
    "Patricia Díaz",
    "Fernando Vargas",
    "Alejandra Ruiz",
    "Carlos Mendoza",
    "Diana López"
]

GRUPOS = [
    "Cuadrilla Verde A",
    "Equipo de Mantenimiento 1",
    "Brigada Ambiental B",
    "Grupo Operativo Norte",
    "Cuadrilla de Poda C",
    "Equipo Verde Sur",
    "Brigada Este",
    "Cuadrilla Oeste"
]

# Coordenadas de ejemplo en Cali
COORDENADAS = [
    [-76.5225, 3.4516],  # Centro
    [-76.5320, 3.3950],  # Sur
    [-76.5410, 3.4320],  # Oeste
    [-76.5100, 3.4720],  # Norte
    [-76.4980, 3.4450],  # Este
    [-76.5280, 3.4180],  # Suroeste
    [-76.5150, 3.4610],  # Noreste
    [-76.5390, 3.4080],  # Occidente
    [-76.5050, 3.4380],  # Oriente
    [-76.5250, 3.4250]   # Centro-sur
]

def crear_imagen_prueba(numero, tamaño=(800, 600)):
    """Crea una imagen de prueba en memoria con diferentes colores"""
    colores = [
        (255, 0, 0),    # Rojo
        (0, 255, 0),    # Verde
        (0, 0, 255),    # Azul
        (255, 255, 0),  # Amarillo
        (255, 0, 255),  # Magenta
        (0, 255, 255),  # Cyan
        (128, 128, 0),  # Oliva
        (128, 0, 128),  # Púrpura
    ]
    color = colores[numero % len(colores)]
    
    img = Image.new('RGB', tamaño, color)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='JPEG', quality=85)
    img_bytes.seek(0)
    
    return img_bytes

def generar_reporte(numero):
    """Genera un reporte con datos variados"""
    print(f"\n{'='*70}")
    print(f"REPORTE #{numero}")
    print(f"{'='*70}")
    
    # Datos base del formulario
    data = {}
    
    # 85% probabilidad de incluir cada campo opcional
    if random.random() > 0.15:
        data['tipo_intervencion'] = random.choice(TIPOS_INTERVENCION)
        print(f"Tipo intervención: {data['tipo_intervencion']}")
    
    if random.random() > 0.15:
        data['descripcion_intervencion'] = f"Intervención #{numero}: {random.choice(['Trabajo completado exitosamente', 'Operación ejecutada correctamente', 'Actividad finalizada', 'Labor realizada con éxito'])}"
        print(f"Descripción: {data['descripcion_intervencion']}")
    
    if random.random() > 0.25:
        data['tipo_arbol'] = random.choice(TIPOS_ARBOL)
        print(f"Tipo árbol: {data['tipo_arbol']}")
    
    if random.random() > 0.25:
        data['numero_individuos_intervenidos'] = str(random.randint(1, 50))
        print(f"Individuos intervenidos: {data['numero_individuos_intervenidos']}")
    
    if random.random() > 0.15:
        data['registrado_por'] = random.choice(REGISTRADORES)
        print(f"Registrado por: {data['registrado_por']}")
    
    if random.random() > 0.15:
        data['grupo'] = random.choice(GRUPOS)
        print(f"Grupo: {data['grupo']}")
    
    if random.random() > 0.35:
        data['id_actividad'] = f"ACT-2026-{random.randint(1000, 9999)}"
        print(f"ID Actividad: {data['id_actividad']}")
    
    # Coordenadas (90% probabilidad)
    if random.random() > 0.10:
        coord = random.choice(COORDENADAS)
        data['coordinates_type'] = 'Point'
        data['coordinates_data'] = json.dumps(coord)
        print(f"Coordenadas: {coord}")
    
    if random.random() > 0.30:
        data['observaciones'] = f"Observación #{numero}: {random.choice(['Sin novedades', 'Completado satisfactoriamente', 'Requiere seguimiento posterior', 'Área en óptimas condiciones', 'Intervención efectiva'])}"
        print(f"Observaciones: {data['observaciones']}")
    
    return data

def generar_fotos(numero):
    """Genera entre 1 y 4 fotos de prueba para el reporte"""
    # 80% de probabilidad de incluir fotos
    if random.random() < 0.20:
        print("📸 Sin fotos")
        return []
    
    # Generar entre 1 y 4 fotos
    num_fotos = random.randint(1, 4)
    files = []
    
    print(f"📸 Generando {num_fotos} foto(s)...")
    for i in range(num_fotos):
        img_bytes = crear_imagen_prueba(numero * 10 + i)
        files.append(('photos', (f'reporte_{numero}_foto_{i+1}.jpg', img_bytes, 'image/jpeg')))
        print(f"   ✓ Foto {i+1}: reporte_{numero}_foto_{i+1}.jpg")
    
    return files

def enviar_reporte(numero, data, files):
    """Envía un reporte al endpoint"""
    try:
        url = f"{BASE_URL}/grupo-cuadrilla/reporte_intervencion"
        
        print(f"\n🚀 Enviando al servidor...")
        
        # Hacer la petición POST
        if files:
            response = requests.post(url, data=data, files=files)
        else:
            response = requests.post(url, data=data)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ ÉXITO")
            print(f"   ID: {result.get('id', 'N/A')}")
            print(f"   Fotos subidas: {result.get('photos_uploaded', 0)}")
            
            # Mostrar URLs de fotos si existen
            photos_urls = result.get('photosUrl', [])
            if photos_urls:
                print(f"   URLs generadas:")
                for idx, url in enumerate(photos_urls[:2], 1):  # Mostrar solo las primeras 2
                    print(f"      {idx}. {url[:80]}...")
                if len(photos_urls) > 2:
                    print(f"      ... y {len(photos_urls) - 2} más")
            
            return True
        else:
            print(f"❌ ERROR {response.status_code}")
            print(f"   Detalle: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ EXCEPCIÓN: {str(e)}")
        return False

def main():
    """Función principal"""
    print("=" * 70)
    print("🧪 TEST: 10 REPORTES DE INTERVENCIÓN CON FOTOS")
    print(f"Endpoint: POST {BASE_URL}/grupo-cuadrilla/reporte_intervencion")
    print("=" * 70)
    
    exitos = 0
    fallos = 0
    total_fotos = 0
    
    for i in range(1, 11):
        # Generar datos del reporte
        data = generar_reporte(i)
        
        # Generar fotos
        files = generar_fotos(i)
        total_fotos += len(files)
        
        # Enviar reporte
        if enviar_reporte(i, data, files):
            exitos += 1
        else:
            fallos += 1
    
    # Resumen final
    print("\n" + "=" * 70)
    print("📊 RESUMEN FINAL")
    print("=" * 70)
    print(f"✅ Reportes exitosos: {exitos}")
    print(f"❌ Reportes fallidos: {fallos}")
    print(f"📊 Total procesados: {exitos + fallos}")
    print(f"📈 Tasa de éxito: {(exitos/(exitos+fallos)*100):.1f}%")
    print(f"📸 Total de fotos enviadas: {total_fotos}")
    print(f"📷 Promedio de fotos por reporte: {(total_fotos/(exitos+fallos)):.1f}")
    print("=" * 70)
    
    if exitos == 10:
        print("\n🎉 ¡Todos los reportes se crearon exitosamente!")
        print("✓ El endpoint está funcionando correctamente")
        print("✓ La subida de fotos a S3 está operativa")
    elif exitos > 0:
        print(f"\n⚠️ Se completaron {exitos} de 10 reportes")
        print("   Revisar los logs de errores arriba")
    else:
        print("\n❌ Ningún reporte se completó exitosamente")
        print("   Verificar que la API esté corriendo y sea accesible")

if __name__ == "__main__":
    main()
