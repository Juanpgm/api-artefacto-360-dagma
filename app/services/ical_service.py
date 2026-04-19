"""
Generador de archivos iCalendar (.ics) para actividades DAGMA.
Permite agregar la actividad a cualquier calendario (Google, Outlook, Apple).
"""
import uuid
import logging
from datetime import datetime, timedelta
import pytz
from icalendar import Calendar, Event, Alarm, vText, vDatetime

logger = logging.getLogger(__name__)

COLOMBIA_TZ = pytz.timezone('America/Bogota')


def generate_ics(actividad_data: dict) -> bytes:
    """
    Genera un archivo .ics para la actividad.
    Retorna los bytes del archivo, o b'' si falla.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        hora = actividad_data.get('hora_encuentro', '')
        try:
            dt_inicio = datetime.strptime(f"{fecha} {hora}", "%d/%m/%Y %H:%M")
            dt_inicio = COLOMBIA_TZ.localize(dt_inicio)
        except Exception:
            dt_inicio = datetime.now(COLOMBIA_TZ)

        duracion = float(actividad_data.get('duracion_actividad') or 2)
        dt_fin = dt_inicio + timedelta(hours=duracion)

        punto = actividad_data.get('punto_encuentro') or {}
        direccion = punto.get('direccion', '') if isinstance(punto, dict) else ''
        grupos = ', '.join(actividad_data.get('grupos_requeridos') or [])
        lider = actividad_data.get('lider_actividad', '')
        telefono = actividad_data.get('telefono', '')
        observaciones = actividad_data.get('observaciones') or ''

        cal = Calendar()
        cal.add('prodid', '-//DAGMA Artefacto 360//ES')
        cal.add('version', '2.0')
        cal.add('method', 'REQUEST')
        cal.add('calscale', 'GREGORIAN')

        event = Event()
        event.add('uid', actividad_data.get('id', str(uuid.uuid4())) + '@dagma.cali.gov.co')
        event.add('dtstart', dt_inicio)
        event.add('dtend', dt_fin)
        event.add('dtstamp', datetime.now(COLOMBIA_TZ))
        event.add('summary', f"Actividad DAGMA: {actividad_data.get('objetivo_actividad', '')}")
        event.add('location', direccion)
        # Generar enlace de Google Maps desde coordenadas
        geometry = punto.get('geometry') or {} if isinstance(punto, dict) else {}
        coords = geometry.get('coordinates') or [] if isinstance(geometry, dict) else []
        maps_url = f"https://www.google.com/maps?q={coords[1]},{coords[0]}" if len(coords) >= 2 else ''
        maps_line = f"\nUbicacion en Google Maps: {maps_url}" if maps_url else ''

        event.add('description', (
            f"Tipo de jornada: {actividad_data.get('tipo_jornada', '')}\n"
            f"Grupos requeridos: {grupos}\n"
            f"Líder: {lider}\n"
            f"Teléfono: {telefono}\n"
            f"Observaciones: {observaciones}"
            f"{maps_line}"
        ))
        event.add('status', 'CONFIRMED')
        event.add('sequence', 0)

        # Recordatorio 24 horas antes
        alarm = Alarm()
        alarm.add('action', 'DISPLAY')
        alarm.add('description', 'Recordatorio: Actividad DAGMA mañana')
        alarm.add('trigger', timedelta(hours=-24))
        event.add_component(alarm)

        cal.add_component(event)
        return cal.to_ical()

    except Exception as e:
        logger.error(f"[iCal] Error generando .ics: {e}")
        return b''
