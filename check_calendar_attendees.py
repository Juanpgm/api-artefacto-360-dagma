"""Verify Calendar attendees directly via Google Calendar API."""
from app.services.calendar_service import _build_calendar_service, CALENDAR_ID

EVENT_ID = "cl12l2n38fbvp9mgd54s4guth8"
EMAIL = "juanp.gzmz@gmail.com"

service = _build_calendar_service()
event = service.events().get(calendarId=CALENDAR_ID, eventId=EVENT_ID).execute()

attendees = event.get("attendees", [])
print(f"Evento: {event.get('summary', 'N/A')}")
print(f"Total asistentes: {len(attendees)}")
for a in attendees:
    print(f"  - {a.get('email')}  (status: {a.get('responseStatus', 'N/A')})")

emails = [a.get("email", "").lower() for a in attendees]
if EMAIL.lower() in emails:
    print(f"\n!! {EMAIL} SIGUE en el evento")
else:
    print(f"\nOK: {EMAIL} NO esta en el evento (fue eliminado correctamente)")
