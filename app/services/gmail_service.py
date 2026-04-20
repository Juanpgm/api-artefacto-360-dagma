"""
Servicio de notificaciones por email para actividades DAGMA.
Usa SMTP estándar — compatible con Gmail (App Password), Outlook, o cualquier proveedor.
No requiere domain-wide delegation ni acceso de administrador GCP.
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from app.services.ical_service import generate_ics

logger = logging.getLogger(__name__)


def _get_smtp_config():
    """Lee la configuración SMTP de las variables de entorno (lazy, para que load_dotenv() ya haya corrido)."""
    return {
        'host': os.getenv('SMTP_HOST', 'smtp.gmail.com'),
        'port': int(os.getenv('SMTP_PORT', '587')),
        'user': os.getenv('SMTP_USER', ''),
        'password': os.getenv('SMTP_PASSWORD', ''),
        'sender_name': os.getenv('SMTP_SENDER_NAME', 'DAGMA Artefacto 360'),
    }


def _send_email(to: str, subject: str, html_body: str, ics_bytes: bytes = None) -> bool:
    """Envía un correo vía SMTP con adjunto iCal opcional. Retorna True si tuvo éxito."""
    cfg = _get_smtp_config()
    if not cfg['user'] or not cfg['password']:
        logger.warning("[SMTP] SMTP_USER o SMTP_PASSWORD no configurados -- email omitido")
        return False

    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = f"{cfg['sender_name']} <{cfg['user']}>"
        msg['To'] = to
        msg['Subject'] = subject

        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        if ics_bytes:
            part = MIMEBase('text', 'calendar', method='REQUEST', charset='utf-8')
            part.set_payload(ics_bytes)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename='actividad_dagma.ics')
            part.add_header('Content-Type', 'text/calendar; method=REQUEST; charset=utf-8')
            msg.attach(part)

        with smtplib.SMTP(cfg['host'], cfg['port']) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg['user'], cfg['password'])
            server.sendmail(cfg['user'], to, msg.as_bytes())

        logger.info(f"[SMTP] Email enviado a {to}")
        return True

    except Exception as e:
        logger.error(f"[SMTP] Error enviando a {to}: {e}")
        return False


def _google_maps_url(actividad_data: dict) -> str:
    """Genera URL de Google Maps desde las coordenadas del punto de encuentro."""
    punto = actividad_data.get('punto_encuentro') or {}
    geometry = punto.get('geometry') or {} if isinstance(punto, dict) else {}
    coords = geometry.get('coordinates') or [] if isinstance(geometry, dict) else []
    if len(coords) >= 2:
        lng, lat = coords[0], coords[1]
        return f"https://www.google.com/maps?q={lat},{lng}"
    return ''


def _activity_details_table(actividad_data: dict) -> str:
    punto = actividad_data.get('punto_encuentro') or {}
    direccion = punto.get('direccion', 'N/A') if isinstance(punto, dict) else 'N/A'
    maps_url = _google_maps_url(actividad_data)
    punto_html = direccion
    if maps_url:
        punto_html = f'{direccion} &mdash; <a href="{maps_url}" style="color:#1a73e8;">Ver en Google Maps</a>'
    rows = [
        ('Fecha', actividad_data.get('fecha_actividad', 'N/A')),
        ('Hora de encuentro', actividad_data.get('hora_encuentro', 'N/A')),
        ('Objetivo', actividad_data.get('objetivo_actividad', 'N/A')),
        ('Tipo de jornada', actividad_data.get('tipo_jornada', 'N/A')),
        ('Duración', f"{actividad_data.get('duracion_actividad', 'N/A')} horas"),
        ('Grupos requeridos', ', '.join(actividad_data.get('grupos_requeridos') or []) or 'N/A'),
        ('Líder', actividad_data.get('lider_actividad', 'N/A')),
        ('Teléfono de contacto', actividad_data.get('telefono', 'N/A')),
        ('Punto de encuentro', punto_html),
        ('Observaciones', actividad_data.get('observaciones') or 'Ninguna'),
    ]
    trs = ''.join(
        f'<tr><td style="padding:8px 12px;background:#f5f5f5;font-weight:bold;white-space:nowrap;">{label}</td>'
        f'<td style="padding:8px 12px;">{value}</td></tr>'
        for label, value in rows
    )
    return f'<table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px;">{trs}</table>'


def _calendar_button(actividad_data: dict) -> str:
    link = actividad_data.get('calendar_event_link', '')
    if not link:
        return ''
    return (
        f'<p style="margin-top:20px;">'
        f'<a href="{link}" style="background:#1a73e8;color:white;padding:10px 24px;'
        f'text-decoration:none;border-radius:4px;font-family:Arial,sans-serif;font-size:14px;">'
        f'Ver evento en Google Calendar</a></p>'
    )


def _maps_button(actividad_data: dict) -> str:
    maps_url = _google_maps_url(actividad_data)
    if not maps_url:
        return ''
    return (
        f'<p style="margin-top:10px;">'
        f'<a href="{maps_url}" style="background:#34a853;color:white;padding:10px 24px;'
        f'text-decoration:none;border-radius:4px;font-family:Arial,sans-serif;font-size:14px;">'
        f'Abrir punto de encuentro en Google Maps</a></p>'
    )


def send_activity_confirmation_email(coordinator_email: str, actividad_data: dict) -> bool:
    """
    Envía correo de confirmación al coordinador cuando se programa una actividad.
    Incluye adjunto .ics para agregar al calendario.
    Retorna True si se envió correctamente, False en caso de error (no propaga excepciones).
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        html = f"""
        <html><body style="font-family:Arial,sans-serif;color:#333;max-width:620px;margin:auto;padding:0;">
          <div style="background:#2e7d32;padding:24px;text-align:center;">
            <h2 style="color:white;margin:0;">DAGMA — Actividad Ambiental Programada</h2>
          </div>
          <div style="padding:28px;">
            <p>Estimado/a coordinador/a,</p>
            <p>La siguiente actividad ambiental ha sido registrada exitosamente en el sistema
               <strong>Artefacto 360 DAGMA</strong>.</p>
            <h3 style="color:#2e7d32;margin-bottom:8px;">Detalles de la Actividad</h3>
            {_activity_details_table(actividad_data)}
            {_calendar_button(actividad_data)}
            {_maps_button(actividad_data)}
            <p style="margin-top:24px;">El archivo adjunto <strong>actividad_dagma.ics</strong> le permite
               agregar esta actividad directamente a Google Calendar, Outlook o Apple Calendar.</p>
            <hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
            <p style="font-size:12px;color:#888;margin:0;">
              Mensaje generado automáticamente por el sistema Artefacto 360 DAGMA.<br>
              DAGMA — Departamento Administrativo de Gestión del Medio Ambiente, Santiago de Cali.
            </p>
          </div>
        </body></html>
        """
        ics = generate_ics(actividad_data)
        return _send_email(
            to=coordinator_email,
            subject=f"Actividad Ambiental Programada — {fecha}",
            html_body=html,
            ics_bytes=ics or None,
        )
    except Exception as e:
        logger.error(f"[SMTP] Error enviando confirmación a {coordinator_email}: {e}")
        return False


def send_assignment_notification_email(
    person_email: str,
    nombre: str,
    grupo: str,
    actividad_data: dict
) -> bool:
    """
    Envía notificación de asignación a una persona del personal.
    Incluye adjunto .ics para agregar al calendario.
    Retorna True si se envió correctamente, False en caso de error (no propaga excepciones).
    """
    try:
        lider = actividad_data.get('lider_actividad', 'N/A')
        telefono = actividad_data.get('telefono', 'N/A')
        fecha = actividad_data.get('fecha_actividad', '')

        html = f"""
        <html><body style="font-family:Arial,sans-serif;color:#333;max-width:620px;margin:auto;padding:0;">
          <div style="background:#1565c0;padding:24px;text-align:center;">
            <h2 style="color:white;margin:0;">DAGMA — Asignación a Actividad Ambiental</h2>
          </div>
          <div style="padding:28px;">
            <p>Estimado/a <strong>{nombre}</strong>,</p>
            <p>Ha sido asignado/a como integrante del equipo <strong>{grupo}</strong> para participar
               en la siguiente actividad ambiental de DAGMA.</p>
            <h3 style="color:#1565c0;margin-bottom:8px;">Detalles de la Actividad</h3>
            {_activity_details_table(actividad_data)}
            {_calendar_button(actividad_data)}
            {_maps_button(actividad_data)}
            <p style="margin-top:24px;">
              Abra el archivo adjunto <strong>actividad_dagma.ics</strong> para agregar esta actividad
              a su Google Calendar, Outlook o Apple Calendar con recordatorio automático.
            </p>
            <p>
              Por favor, confírmele su asistencia al líder:<br>
              <strong>{lider}</strong> — Tel: <strong>{telefono}</strong>
            </p>
            <p>Preséntese puntualmente en el punto de encuentro indicado.</p>
            <hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
            <p style="font-size:12px;color:#888;margin:0;">
              Mensaje generado automáticamente por el sistema Artefacto 360 DAGMA.<br>
              DAGMA — Departamento Administrativo de Gestión del Medio Ambiente, Santiago de Cali.
            </p>
          </div>
        </body></html>
        """
        ics = generate_ics(actividad_data)
        return _send_email(
            to=person_email,
            subject=f"Asignación Actividad Ambiental DAGMA — {fecha}",
            html_body=html,
            ics_bytes=ics or None,
        )
    except Exception as e:
        logger.error(f"[SMTP] Error enviando notificación a {person_email}: {e}")
        return False


def send_removal_notification_email(
    person_email: str,
    nombre: str,
    actividad_data: dict
) -> bool:
    """
    Envía notificación de desasignación a una persona del personal.
    Informa que ya no necesita presentarse a la actividad.
    Retorna True si se envió correctamente, False en caso de error (no propaga excepciones).
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        lider = actividad_data.get('lider_actividad', 'N/A')
        telefono = actividad_data.get('telefono', 'N/A')

        html = f"""
        <html><body style="font-family:Arial,sans-serif;color:#333;max-width:620px;margin:auto;padding:0;">
          <div style="background:#c62828;padding:24px;text-align:center;">
            <h2 style="color:white;margin:0;">DAGMA — Desasignación de Actividad Ambiental</h2>
          </div>
          <div style="padding:28px;">
            <p>Estimado/a <strong>{nombre}</strong>,</p>
            <p>Le informamos que ha sido <strong>desasignado/a</strong> de la siguiente actividad ambiental.
               <strong>No es necesario que se presente</strong> al punto de encuentro.</p>
            <h3 style="color:#c62828;margin-bottom:8px;">Detalles de la Actividad</h3>
            {_activity_details_table(actividad_data)}
            <p style="margin-top:24px;">
              Si tiene alguna duda, comuníquese con el líder:<br>
              <strong>{lider}</strong> — Tel: <strong>{telefono}</strong>
            </p>
            <hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
            <p style="font-size:12px;color:#888;margin:0;">
              Mensaje generado automáticamente por el sistema Artefacto 360 DAGMA.<br>
              DAGMA — Departamento Administrativo de Gestión del Medio Ambiente, Santiago de Cali.
            </p>
          </div>
        </body></html>
        """
        return _send_email(
            to=person_email,
            subject=f"Desasignación de Actividad Ambiental DAGMA — {fecha}",
            html_body=html,
        )
    except Exception as e:
        logger.error(f"[SMTP] Error enviando desasignación a {person_email}: {e}")
        return False
