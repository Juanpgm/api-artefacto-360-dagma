"""Script rápido para verificar estadísticas de los reportes"""
import requests

r = requests.get('http://localhost:8000/grupo-cuadrilla/reportes_intervenciones')
data = r.json()

total = data['total']
reportes = data['data']

con_fotos = [rep for rep in reportes if rep.get('photos_uploaded', 0) > 0]
sin_fotos = [rep for rep in reportes if rep.get('photos_uploaded', 0) == 0]

total_fotos = sum(rep.get('photos_uploaded', 0) for rep in reportes)
promedio_fotos = total_fotos / len(con_fotos) if con_fotos else 0

# Estadísticas de coordenadas
coords = [rep.get('coordinates', {}).get('coordinates', []) for rep in reportes if rep.get('coordinates')]
lons = [c[0] for c in coords if len(c) >= 2]
lats = [c[1] for c in coords if len(c) >= 2]

print("=" * 70)
print("📊 ESTADÍSTICAS DE REPORTES DE INTERVENCIÓN")
print("=" * 70)
print(f"Total de reportes: {total}")
print(f"Reportes con fotos: {len(con_fotos)}")
print(f"Reportes sin fotos: {len(sin_fotos)}")
print(f"Total de fotos subidas: {total_fotos}")
print(f"Promedio de fotos por reporte (con fotos): {promedio_fotos:.1f}")
print()
print("📍 Dispersión de coordenadas:")
print(f"  Longitud - Min: {min(lons):.6f} | Max: {max(lons):.6f}")
print(f"  Latitud - Min: {min(lats):.6f} | Max: {max(lats):.6f}")
print(f"  Rango Lon: {max(lons) - min(lons):.6f}°")
print(f"  Rango Lat: {max(lats) - min(lats):.6f}°")
print("=" * 70)

# Contar campos completos en los últimos 50 reportes
ultimos_50 = reportes[:50]
campos_verificar = ['tipo_intervencion', 'descripcion_intervencion', 'tipo_arbol', 
                    'numero_individuos_intervenidos', 'registrado_por', 'grupo', 
                    'id_actividad', 'observaciones']

reportes_completos = []
for rep in ultimos_50:
    campos_llenos = sum(1 for campo in campos_verificar if rep.get(campo))
    if campos_llenos == len(campos_verificar) and rep.get('photos_uploaded', 0) > 0:
        reportes_completos.append(rep)

print(f"\n✅ Últimos 50 reportes con TODOS los campos + fotos: {len(reportes_completos)}")
print("=" * 70)
