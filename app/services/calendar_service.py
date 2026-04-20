"""
Servicio de Google Calendar para actividades DAGMA.
Centraliza la creación y actualización de eventos del calendario institucional.
"""
import logging
from datetime import datetime, timedelta
import pytz
from googleapiclient.discovery import build
from app.services.google_credentials import get_calendar_credentials

logger = logging.getLogger(__name__)

CALENDAR_ID = '19c263371dc17e144c9ee0b12ac40c28339cb20c259f528d348730d98e193eb9@group.calendar.google.com'
COLOMBIA_TZ = pytz.timezone('America/Bogota')


def _build_calendar_service():
    creds = get_calendar_credentials()
    return build('calendar', 'v3', credentials=creds)


def create_activity_event(actividad_data: dict, attendee_emails: list = None) -> dict:
    """
    Crea un evento en el calendario DAGMA para una actividad programada.
    Si se pasan attendee_emails, envía invitaciones de Calendar a cada uno.
    Retorna el evento creado (dict con 'id' y 'htmlLink'), o {} si falla.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        hora = actividad_data.get('hora_encuentro', '')
        try:
            dt_inicio = datetime.strptime(f"{fecha} {hora}", "%d/%m/%Y %H:%M")
            dt_inicio = COLOMBIA_TZ.localize(dt_inicio)
        except Exception:
            dt_inicio = datetime.now(COLOMBIA_TZ)
        dt_fin = dt_inicio + timedelta(hours=float(actividad_data.get('duracion_actividad', 2) or 2))

        punto = actividad_data.get('punto_encuentro') or {}
        direccion = punto.get('direccion', '') if isinstance(punto, dict) else ''
        grupos = ', '.join(actividad_data.get('grupos_requeridos', []) or [])

        # Generar enlace de Google Maps desde coordenadas
        geometry = punto.get('geometry') or {} if isinstance(punto, dict) else {}
        coords = geometry.get('coordinates') or [] if isinstance(geometry, dict) else []
        maps_url = f"https://www.google.com/maps?q={coords[1]},{coords[0]}" if len(coords) >= 2 else ''
        maps_line = f"\nUbicacion en Google Maps: {maps_url}" if maps_url else ''

        event = {
            'summary': f"Actividad DAGMA: {actividad_data.get('objetivo_actividad', '')}",
            'location': direccion,
            'description': (
                f"Tipo de jornada: {actividad_data.get('tipo_jornada', '')}\n"
                f"Grupos requeridos: {grupos}\n"
                f"Líder: {actividad_data.get('lider_actividad', '')}\n"
                f"Teléfono: {actividad_data.get('telefono', '')}\n"
                f"Observaciones: {actividad_data.get('observaciones', '') or ''}"
                f"{maps_line}"
            ),
            'start': {'dateTime': dt_inicio.isoformat(), 'timeZone': 'America/Bogota'},
            'end': {'dateTime': dt_fin.isoformat(), 'timeZone': 'America/Bogota'},
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 60},
                ],
            },
        }
        # No incluir attendees: el service account no tiene Domain-Wide Delegation.
        # Las invitaciones se envían por email con adjunto .ics.
        service = _build_calendar_service()
        created = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event,
            sendUpdates='none'
        ).execute()

        logger.info(f"[CALENDAR] Evento creado: {created.get('id')}")
        return created

    except Exception as e:
        logger.error(f"[CALENDAR] Error creando evento: {e}")
        return {}


import re as _re

_PERSONAL_SECTION_RE = _re.compile(
    r'\n--- Personal asignado ---\n.*',
    _re.DOTALL,
)


def sync_event_personnel(calendar_event_id: str, personal_asignado: list) -> bool:
    """
    Sincroniza la sección 'Personal asignado' en la descripción del evento Calendar.
    Recibe la lista completa de personal_asignado (dicts con nombre_completo, email, grupo).
    No usa attendees (service account sin Domain-Wide Delegation).
    Retorna True si tuvo éxito, False en caso de error.
    """
    try:
        service = _build_calendar_service()

        existing_event = service.events().get(
            calendarId=CALENDAR_ID,
            eventId=calendar_event_id,
        ).execute()

        description = existing_event.get('description', '') or ''

        # Remover sección anterior si existe
        description = _PERSONAL_SECTION_RE.sub('', description).rstrip()

        # Construir nueva sección
        if personal_asignado:
            lines = ['\n--- Personal asignado ---']
            for p in personal_asignado:
                nombre = p.get('nombre_completo', '')
                email = p.get('email', '')
                grupo = p.get('grupo', '')
                lines.append(f'- {nombre} ({email}) - {grupo}')
            description += '\n' + '\n'.join(lines)

        service.events().patch(
            calendarId=CALENDAR_ID,
            eventId=calendar_event_id,
            body={'description': description},
            sendUpdates='none',
        ).execute()

        logger.info(f"[CALENDAR] Descripcion actualizada evento {calendar_event_id}: {len(personal_asignado)} personas")
        return True

    except Exception as e:
        logger.error(f"[CALENDAR] Error actualizando descripcion evento {calendar_event_id}: {e}")
        return False
