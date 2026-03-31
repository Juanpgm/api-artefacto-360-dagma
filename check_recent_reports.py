"""Ver los reportes más recientes"""
import requests

r = requests.get('http://localhost:8000/grupo-cuadrilla/reportes_intervenciones')
reportes = r.json()['data'][:10]

print("\n" + "=" * 75)
print("📋 10 REPORTES MÁS RECIENTES")
print("=" * 75)

for i, rep in enumerate(reportes, 1):
    print(f"\n{i}. ID: {rep['id'][:20]}...")
    print(f"   Tipo: {rep.get('tipo_intervencion', 'N/A')}")
    print(f"   Fotos: {rep.get('photos_uploaded', 0)}")
    print(f"   Grupo: {rep.get('grupo', 'N/A')}")
    print(f"   Registrado por: {rep.get('registrado_por', 'N/A')}")
    
print("\n" + "=" * 75)
