"""
Script para generar 50 registros completos con fotos en POST /grupo-cuadrilla/reporte_intervencion

Características:
- TODOS los registros con fotos (2-5 fotos por registro)
- TODOS los campos poblados
- Gran variación en los datos
- Coordenadas dispersas por toda Cali
"""
import requests
import json
import io
import random
from PIL import Image, ImageDraw, ImageFont

# URL base de la API
BASE_URL = "http://localhost:8000"

# Datos variados para generar 50 registros únicos
TIPOS_INTERVENCION = [
    "Poda de árboles", "Remoción de residuos", "Mantenimiento de zonas verdes",
    "Plantación de árboles", "Control de plagas", "Riego de áreas verdes",
    "Limpieza de parques", "Recolección de hojas", "Fumigación",
    "Fertilización", "Tala controlada", "Desmalezamiento",
    "Limpieza de caños", "Poda de palmas", "Siembra de flores",
    "Mantenimiento de jardines", "Control de maleza", "Poda sanitaria",
    "Transplante de árboles", "Riego tecnificado", "Podas estéticas",
    "Reforestación", "Limpieza de hojarasca", "Control fitosanitario",
    "Mantenimiento preventivo", "Remoción de escombros", "Barrido de parques",
    "Limpieza de senderos", "Poda de formación", "Abonado orgánico"
]

DESCRIPCIONES = [
    "Trabajo ejecutado con éxito en toda el área designada",
    "Intervención completada satisfactoriamente según cronograma",
    "Operación finalizada cumpliendo todos los estándares de calidad",
    "Actividad realizada de manera eficiente y segura",
    "Labor completada con supervisión constante y buenos resultados",
    "Trabajo finalizado dentro del tiempo establecido",
    "Intervención ejecutada con todas las medidas de seguridad",
    "Operación completada con resultados óptimos",
    "Actividad realizada con personal calificado y equipos adecuados",
    "Labor ejecutada cumpliendo protocolos ambientales establecidos",
    "Trabajo completado con mínimo impacto en la comunidad",
    "Intervención finalizada con alta calidad y eficiencia",
    "Operación realizada siguiendo procedimientos técnicos",
    "Actividad completada con excelentes resultados visuales",
    "Labor finalizada con materiales de primera calidad",
    "Trabajo ejecutado con coordinación efectiva del equipo",
    "Intervención completada respetando el entorno natural",
    "Operación finalizada con beneficio para la comunidad",
    "Actividad realizada con herramientas especializadas",
    "Labor completada con éxito total y sin contratiempos",
    "Trabajo finalizado cumpliendo objetivos planificados",
    "Intervención ejecutada con criterios técnicos apropiados",
    "Operación completada con personal experimentado",
    "Actividad realizada con supervisión ambiental constante",
    "Labor finalizada generando impacto positivo en la zona"
]

TIPOS_ARBOL = [
    "Guayacán", "Ceiba", "Samán", "Oití", "Caucho", "Palma Real",
    "Acacia", "Chiminango", "Yarumo", "Carbonero", "Búcaro", "Guásimo",
    "Matarratón", "Nauno", "Pino", "Sauce", "Nogal", "Laurel",
    "Cedro", "Caoba", "Roble", "Bambú", "Guadua", "Eucalipto",
    "Ciprés", "Aguacate", "Mango", "Guayaba", "Papayo", "Tamarindo",
    "Cámbulo", "Gualanday", "Totumo", "Almendro", "Tulipán Africano"
]

REGISTRADORES = [
    "Luis González Pérez", "Sandra Ramírez Castro", "Roberto Castro Díaz",
    "Patricia Díaz Muñoz", "Fernando Vargas López", "Alejandra Ruiz Torres",
    "Carlos Mendoza Silva", "Diana López García", "Jorge Herrera Soto",
    "María Fernández Cruz", "Pedro Martínez Rojas", "Ana Gómez Vega",
    "Juan Rodríguez Mora", "Carmen Sánchez Ortiz", "Miguel Torres Ríos",
    "Laura Morales Paz", "Andrés Castro Mejía", "Claudia Jiménez Ramos",
    "Ricardo Ospina León", "Beatriz Parra Vélez", "Daniel Reyes Acosta",
    "Sofia Varela Núñez", "Gabriel Molina Cortés", "Valentina Cruz Marín",
    "Sebastián Torres Gómez", "Isabella Moreno Suárez", "Mateo Ruiz Cardona"
]

GRUPOS = [
    "Cuadrilla Verde A", "Equipo de Mantenimiento 1", "Brigada Ambiental B",
    "Grupo Operativo Norte", "Cuadrilla de Poda C", "Equipo Verde Sur",
    "Brigada Este", "Cuadrilla Oeste", "Grupo Operativo Centro",
    "Brigada Ambiental A", "Equipo de Mantenimiento 2", "Cuadrilla Verde B",
    "Grupo Operativo Sur", "Brigada Este 2", "Equipo Verde Norte",
    "Cuadrilla de Poda A", "Grupo Operativo Occidente", "Brigada Central",
    "Equipo de Limpieza 1", "Cuadrilla Ecológica A", "Brigada Verde B",
    "Grupo de Reforestación", "Equipo de Jardinería 1", "Cuadrilla Mixta A",
    "Brigada de Intervención Rápida", "Equipo Especializado 1"
]

ACTIVIDADES_BASE = [
    "Mantenimiento quincenal del distrito verde",
    "Intervención de emergencia por caída de ramas",
    "Programa de embellecimiento urbano 2026",
    "Proyecto de reforestación comunitaria",
    "Plan de recuperación de zonas verdes",
    "Campaña de limpieza sectorial",
    "Programa de control fitosanitario preventivo",
    "Proyecto de mejoramiento paisajístico",
    "Plan de mantenimiento de parques zonales",
    "Intervención de áreas de alto tráfico",
    "Programa de renovación vegetal urbana",
    "Proyecto de jardines temáticos",
    "Plan de conservación de especies nativas",
    "Campaña de prevención de riesgos arbóreos",
    "Programa de jardinería sostenible"
]

OBSERVACIONES = [
    "Sin novedades durante la ejecución del trabajo",
    "Área requiere seguimiento en 15 días",
    "Trabajo completado con excelente respuesta de la comunidad",
    "Se recomienda nueva intervención en 30 días",
    "Zona en óptimas condiciones posterior al trabajo",
    "Se identificaron áreas adicionales que requieren atención",
    "Comunidad muy satisfecha con los resultados",
    "Trabajo coordinado exitosamente con entes locales",
    "Se requiere mantenimiento continuo en el sector",
    "Resultados superaron las expectativas iniciales",
    "Área presenta buena receptividad del material plantado",
    "Se detectaron plagas en árboles cercanos",
    "Zona con alto potencial para futuras intervenciones",
    "Trabajo realizado con apoyo de líderes comunitarios",
    "Se recomienda ampliar cobertura a zonas aledañas",
    "Excelentes condiciones climáticas durante la intervención",
    "Área liberada completamente de residuos",
    "Se realizó sensibilización ambiental a residentes",
    "Trabajo requirió coordinación con tránsito y movilidad",
    "Zona presenta mejora significativa en aspecto visual",
    "Se instaló señalización de conservación ambiental",
    "Comunidad participó activamente en las labores",
    "Área con potencial para establecer jardín demostrativo",
    "Se identificaron especies vulnerables que requieren protección",
    "Trabajo culminado anticipadamente por eficiencia del equipo"
]

def generar_coordenadas_cali():
    """
    Genera coordenadas aleatorias dispersas dentro del área urbana de Cali
    Cali: Longitud [-76.6, -76.4], Latitud [3.35, 3.55]
    """
    # Generar coordenadas con mayor dispersión
    lon = round(random.uniform(-76.60, -76.40), 6)
    lat = round(random.uniform(3.35, 3.55), 6)
    return [lon, lat]

def crear_imagen_prueba_avanzada(numero, texto="DAGMA"):
    """Crea una imagen más elaborada con texto y colores variados"""
    # Colores variados
    colores_fondo = [
        (34, 139, 34),   # Verde bosque
        (46, 125, 50),   # Verde oscuro
        (76, 175, 80),   # Verde medio
        (139, 195, 74),  # Verde lima
        (205, 220, 57),  # Lima
        (255, 235, 59),  # Amarillo
        (255, 152, 0),   # Naranja
        (244, 67, 54),   # Rojo
        (33, 150, 243),  # Azul
        (103, 58, 183),  # Púrpura
    ]
    
    color_fondo = colores_fondo[numero % len(colores_fondo)]
    
    # Crear imagen con degradado simple
    img = Image.new('RGB', (1024, 768), color_fondo)
    draw = ImageDraw.Draw(img)
    
    # Dibujar algunos elementos decorativos
    for i in range(5):
        x = random.randint(50, 974)
        y = random.randint(50, 718)
        r = random.randint(30, 100)
        color_circulo = tuple(max(0, min(255, c + random.randint(-50, 50))) for c in color_fondo)
        draw.ellipse([x-r, y-r, x+r, y+r], fill=color_circulo)
    
    # Agregar texto
    try:
        # Intentar con fuente por defecto
        draw.text((50, 350), f"{texto} #{numero}", fill=(255, 255, 255))
    except:
        pass
    
    # Guardar en BytesIO
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='JPEG', quality=90)
    img_bytes.seek(0)
    
    return img_bytes

def generar_reporte_completo(numero):
    """Genera un reporte completo con TODOS los campos poblados"""
    print(f"\n{'='*75}")
    print(f"REPORTE #{numero}/50")
    print(f"{'='*75}")
    
    # Todos los campos obligatorios y opcionales
    tipo_intervencion = random.choice(TIPOS_INTERVENCION)
    descripcion = random.choice(DESCRIPCIONES)
    tipo_arbol = random.choice(TIPOS_ARBOL)
    num_individuos = random.randint(1, 75)
    registrado_por = random.choice(REGISTRADORES)
    grupo = random.choice(GRUPOS)
    id_actividad = f"ACT-2026-{random.randint(1000, 9999)}"
    coordenadas = generar_coordenadas_cali()
    observaciones = random.choice(OBSERVACIONES)
    
    data = {
        'tipo_intervencion': tipo_intervencion,
        'descripcion_intervencion': descripcion,
        'tipo_arbol': tipo_arbol,
        'numero_individuos_intervenidos': str(num_individuos),
        'registrado_por': registrado_por,
        'grupo': grupo,
        'id_actividad': id_actividad,
        'coordinates_type': 'Point',
        'coordinates_data': json.dumps(coordenadas),
        'observaciones': observaciones
    }
    
    # Mostrar resumen
    print(f"📋 Tipo: {tipo_intervencion}")
    print(f"🌳 Árbol: {tipo_arbol} | Individuos: {num_individuos}")
    print(f"👤 Registrado por: {registrado_por}")
    print(f"👥 Grupo: {grupo}")
    print(f"📍 ID Actividad: {id_actividad}")
    print(f"🗺️  Coordenadas: [{coordenadas[0]}, {coordenadas[1]}]")
    print(f"📝 Observaciones: {observaciones[:60]}...")
    
    return data

def generar_fotos_completas(numero):
    """Genera entre 2 y 5 fotos para cada reporte"""
    num_fotos = random.randint(2, 5)
    files = []
    
    print(f"📸 Generando {num_fotos} foto(s)...")
    for i in range(num_fotos):
        img_bytes = crear_imagen_prueba_avanzada(numero * 10 + i, f"DAGMA-R{numero}")
        files.append(('photos', (f'reporte_{numero}_foto_{i+1}.jpg', img_bytes, 'image/jpeg')))
    
    print(f"   ✓ {num_fotos} fotos creadas")
    return files

def enviar_reporte_completo(numero, data, files):
    """Envía un reporte completo al endpoint"""
    try:
        url = f"{BASE_URL}/grupo-cuadrilla/reporte_intervencion"
        
        # Hacer la petición POST con fotos
        response = requests.post(url, data=data, files=files)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ ÉXITO | ID: {result.get('id', 'N/A')[:36]}")
            print(f"   📷 Fotos subidas: {result.get('photos_uploaded', 0)}")
            return True, result.get('photos_uploaded', 0)
        else:
            print(f"❌ ERROR {response.status_code}")
            print(f"   {response.text[:150]}")
            return False, 0
            
    except Exception as e:
        print(f"❌ EXCEPCIÓN: {str(e)[:100]}")
        return False, 0

def main():
    """Función principal - genera 50 registros completos con fotos"""
    print("=" * 75)
    print("🧪 TEST: 50 REPORTES COMPLETOS CON FOTOS")
    print(f"Endpoint: POST {BASE_URL}/grupo-cuadrilla/reporte_intervencion")
    print("Características:")
    print("  • Todos los campos poblados")
    print("  • Todos con fotos (2-5 por reporte)")
    print("  • Coordenadas dispersas en Cali")
    print("  • Alta variación de datos")
    print("=" * 75)
    
    exitos = 0
    fallos = 0
    total_fotos = 0
    
    for i in range(1, 51):
        # Generar datos completos
        data = generar_reporte_completo(i)
        
        # Generar fotos (siempre 2-5 fotos)
        files = generar_fotos_completas(i)
        
        # Enviar reporte
        success, fotos_subidas = enviar_reporte_completo(i, data, files)
        
        if success:
            exitos += 1
            total_fotos += fotos_subidas
        else:
            fallos += 1
        
        # Pequeña pausa cada 10 reportes
        if i % 10 == 0:
            print(f"\n{'─'*75}")
            print(f"📊 Progreso: {i}/50 reportes procesados")
            print(f"✅ Éxitos: {exitos} | ❌ Fallos: {fallos} | 📷 Fotos: {total_fotos}")
            print(f"{'─'*75}")
    
    # Resumen final detallado
    print("\n" + "=" * 75)
    print("📊 RESUMEN FINAL - 50 REPORTES COMPLETOS")
    print("=" * 75)
    print(f"✅ Reportes exitosos: {exitos}/50")
    print(f"❌ Reportes fallidos: {fallos}/50")
    print(f"📈 Tasa de éxito: {(exitos/50*100):.1f}%")
    print(f"📸 Total de fotos subidas: {total_fotos}")
    print(f"📷 Promedio de fotos por reporte: {(total_fotos/exitos if exitos > 0 else 0):.1f}")
    print(f"🗺️  Coordenadas dispersas por toda Cali")
    print(f"📋 Todos los campos poblados en cada reporte")
    print("=" * 75)
    
    if exitos == 50:
        print("\n🎉 ¡PERFECTO! Todos los 50 reportes creados exitosamente")
        print("✓ Base de datos poblada con datos completos y variados")
        print("✓ Fotos subidas correctamente a S3")
        print("✓ Coordenadas dispersas para testing geoespacial")
    elif exitos >= 45:
        print(f"\n✅ Excelente resultado: {exitos}/50 reportes creados")
    elif exitos >= 40:
        print(f"\n✓ Buen resultado: {exitos}/50 reportes creados")
    else:
        print(f"\n⚠️ Se completaron {exitos}/50 reportes")
        print("   Revisar logs de errores arriba")

if __name__ == "__main__":
    main()
