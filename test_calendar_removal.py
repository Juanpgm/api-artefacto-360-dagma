"""Quick test: verify Calendar attendee removal works when PUT empties the list.
Calendar is now synchronous — no sleeps needed between operations."""
import requests

API = "http://localhost:8000"
EMAIL = "juanp.gzmz@gmail.com"
ACT_ID = "14ead36b-1e0e-4ec2-b10f-1961a8e454c6"

# 1. Limpiar
print("1. PUT vacío (limpiar)...")
r = requests.put(f"{API}/actividades/{ACT_ID}/personal_asignado", json={"personal_asignado": []})
print(f"   Status: {r.status_code}")
print(f"   calendar_actualizado: {r.json().get('calendar_actualizado')}")

# 2. Agregar persona via PATCH
print("\n2. PATCH agregar persona...")
r = requests.patch(f"{API}/actividades/{ACT_ID}/personal_asignado", json={
    "email": EMAIL,
    "nombre_completo": "Juan Pablo Guzmán",
    "numero_contacto": 3001234567,
    "grupo": "Cuadrilla",
})
body = r.json()
print(f"   Status: {r.status_code}")
print(f"   Total personal: {body.get('total_personal')}")
print(f"   calendar_actualizado: {body.get('calendar_actualizado')}")

# 3. Verificar en Calendar API
print("\n3. Verificando Calendar directo...")
from app.services.calendar_service import _build_calendar_service, CALENDAR_ID
service = _build_calendar_service()
event = service.events().get(calendarId=CALENDAR_ID, eventId="cl12l2n38fbvp9mgd54s4guth8").execute()
attendees = [a.get("email", "").lower() for a in event.get("attendees", [])]
if EMAIL.lower() in attendees:
    print(f"   OK: {EMAIL} esta en el evento")
else:
    print(f"   FALLO: {EMAIL} NO esta en el evento tras PATCH")

# 4. PUT vacío = debe ELIMINAR de Calendar
print("\n4. PUT vacío (eliminar de Calendar)...")
r = requests.put(f"{API}/actividades/{ACT_ID}/personal_asignado", json={"personal_asignado": []})
body = r.json()
print(f"   Status: {r.status_code}")
print(f"   eliminados: {body.get('eliminados')}")
print(f"   calendar_actualizado: {body.get('calendar_actualizado')}")

# 5. Verificar de nuevo
print("\n5. Verificando Calendar directo post-eliminación...")
event = service.events().get(calendarId=CALENDAR_ID, eventId="cl12l2n38fbvp9mgd54s4guth8").execute()
attendees = [a.get("email", "").lower() for a in event.get("attendees", [])]
if EMAIL.lower() in attendees:
    print(f"   FALLO: {EMAIL} SIGUE en el evento")
else:
    print(f"   OK: {EMAIL} fue eliminado del evento correctamente")
