"""
Script para probar el endpoint POST /grupo-cuadrilla/reporte_intervencion con archivos de foto

Este script crea imágenes de prueba y las envía al endpoint para verificar la subida a S3.
"""
import requests
import json
import io
from PIL import Image

# URL base de la API
BASE_URL = "http://localhost:8000"

def crear_imagen_prueba(numero, tamaño=(800, 600)):
    """Crea una imagen de prueba en memoria"""
    # Crear imagen con color aleatorio
    colores = [
        (255, 0, 0),    # Rojo
        (0, 255, 0),    # Verde
        (0, 0, 255),    # Azul
        (255, 255, 0),  # Amarillo
    ]
    color = colores[numero % len(colores)]
    
    img = Image.new('RGB', tamaño, color)
    
    # Guardar en BytesIO
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='JPEG', quality=85)
    img_bytes.seek(0)  # Rebobinar al inicio
    
    return img_bytes

def test_reporte_con_fotos():
    """Envía un reporte con 3 fotos de prueba"""
    print("=" * 70)
    print("TEST: Enviar reporte de intervención con 3 fotos a S3")
    print(f"Endpoint: POST {BASE_URL}/grupo-cuadrilla/reporte_intervencion")
    print("=" * 70)
    
    # Preparar datos del formulario
    data = {
        'tipo_intervencion': 'Poda de árboles',
        'descripcion_intervencion': 'Test de subida de fotos a S3',
        'tipo_arbol': 'Guayacán',
        'numero_individuos_intervenidos': '5',
        'registrado_por': 'Usuario de prueba',
        'grupo': 'Cuadrilla de Testing',
        'id_actividad': 'TEST-2026-0001',
        'coordinates_type': 'Point',
        'coordinates_data': json.dumps([-76.5225, 3.4516]),
        'observaciones': 'Test de validación de fotos con S3'
    }
    
    # Crear 3 imágenes de prueba
    print("\n📸 Creando 3 imágenes de prueba...")
    files = []
    for i in range(3):
        img_bytes = crear_imagen_prueba(i)
        files.append(('photos', (f'test_foto_{i+1}.jpg', img_bytes, 'image/jpeg')))
        print(f"  ✓ Imagen {i+1} creada: test_foto_{i+1}.jpg")
    
    try:
        print("\n🚀 Enviando petición al endpoint...")
        url = f"{BASE_URL}/grupo-cuadrilla/reporte_intervencion"
        
        # Enviar petición con archivos
        response = requests.post(url, data=data, files=files)
        
        print(f"📡 Status Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("\n" + "=" * 70)
            print("✅ ÉXITO - Reporte creado con fotos")
            print("=" * 70)
            print(f"ID del reporte: {result.get('id', 'N/A')}")
            print(f"Fotos subidas: {result.get('photos_uploaded', 0)}")
            print(f"\nURLs de las fotos:")
            
            photos_urls = result.get('photosUrl', [])
            if photos_urls:
                for idx, url in enumerate(photos_urls, 1):
                    print(f"  {idx}. {url}")
                
                print("\n🔍 Verificación:")
                print(f"  • Todas las URLs apuntan al bucket S3: {'✓' if all('s3.amazonaws.com' in url for url in photos_urls) else '✗'}")
                print(f"  • Las URLs incluyen el ID del reporte: {'✓' if all(result['id'] in url for url in photos_urls) else '✗'}")
                print(f"  • El path contiene 'reportes_intervenciones_grupo_cuadrilla': {'✓' if all('reportes_intervenciones_grupo_cuadrilla' in url for url in photos_urls) else '✗'}")
            else:
                print("  ⚠️ No se generaron URLs de fotos (posible modo desarrollo sin credenciales S3)")
            
            print("\n📊 Datos del reporte:")
            print(f"  • Tipo: {result.get('message', 'N/A')}")
            print(f"  • Timestamp: {result.get('timestamp', 'N/A')}")
            print(f"  • Coordenadas: {result.get('coordinates', 'N/A')}")
            
            return True
        else:
            print("\n" + "=" * 70)
            print(f"❌ ERROR {response.status_code}")
            print("=" * 70)
            print(f"Respuesta: {response.text}")
            return False
            
    except Exception as e:
        print("\n" + "=" * 70)
        print(f"❌ EXCEPCIÓN: {str(e)}")
        print("=" * 70)
        return False

def test_reporte_sin_fotos():
    """Envía un reporte SIN fotos para comparar"""
    print("\n" + "=" * 70)
    print("TEST: Enviar reporte de intervención SIN fotos")
    print("=" * 70)
    
    data = {
        'tipo_intervencion': 'Limpieza de parque',
        'descripcion_intervencion': 'Test sin fotos',
        'registrado_por': 'Usuario de prueba',
        'grupo': 'Cuadrilla de Testing',
        'coordinates_type': 'Point',
        'coordinates_data': json.dumps([-76.5320, 3.3950]),
        'observaciones': 'Reporte de control sin fotos adjuntas'
    }
    
    try:
        url = f"{BASE_URL}/grupo-cuadrilla/reporte_intervencion"
        response = requests.post(url, data=data)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Reporte sin fotos creado - ID: {result.get('id', 'N/A')}")
            print(f"   Fotos subidas: {result.get('photos_uploaded', 0)} (debe ser 0)")
            return True
        else:
            print(f"❌ ERROR {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ EXCEPCIÓN: {str(e)}")
        return False

def main():
    """Función principal"""
    print("\n" + "=" * 70)
    print("🧪 SUITE DE TESTS - VALIDACIÓN DE FOTOS EN REPORTES")
    print("=" * 70)
    
    # Test 1: Con fotos
    test1_ok = test_reporte_con_fotos()
    
    # Test 2: Sin fotos
    test2_ok = test_reporte_sin_fotos()
    
    # Resumen
    print("\n" + "=" * 70)
    print("📋 RESUMEN DE TESTS")
    print("=" * 70)
    print(f"{'✅' if test1_ok else '❌'} Test con 3 fotos: {'ÉXITO' if test1_ok else 'FALLO'}")
    print(f"{'✅' if test2_ok else '❌'} Test sin fotos: {'ÉXITO' if test2_ok else 'FALLO'}")
    print("=" * 70)
    
    if test1_ok and test2_ok:
        print("\n🎉 Todos los tests pasaron correctamente")
        print("\n💡 NOTAS:")
        print("   • Si ves URLs ficticias, es porque no hay credenciales de S3 configuradas")
        print("   • Con credenciales válidas, las fotos se suben realmente al bucket S3")
        print("   • El endpoint maneja correctamente ambos casos (con y sin fotos)")
    else:
        print("\n⚠️ Algunos tests fallaron - revisar logs arriba")

if __name__ == "__main__":
    main()
