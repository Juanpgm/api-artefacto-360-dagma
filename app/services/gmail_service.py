"""
Servicio de notificaciones por email para actividades DAGMA.
Estrategia dual:
  1. Gmail API (OAuth2): requiere GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_SENDER.
  2. SMTP (fallback): requiere SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_SENDER_NAME.
Si ninguno está configurado, el envío falla silenciosamente con un log de advertencia.
"""
import os
import base64
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.ical_service import generate_ics

logger = logging.getLogger(__name__)


def _get_gmail_service():
    """Construye el servicio Gmail API usando las credenciales OAuth del entorno."""
    client_id = os.getenv('GMAIL_CLIENT_ID', '')
    client_secret = os.getenv('GMAIL_CLIENT_SECRET', '')
    refresh_token = os.getenv('GMAIL_REFRESH_TOKEN', '')

    if not (client_id and client_secret and refresh_token):
        logger.warning("[GMAIL] GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET o GMAIL_REFRESH_TOKEN no configurados")
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=['https://www.googleapis.com/auth/gmail.send'],
    )
    creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def _build_mime_message(sender: str, sender_name: str, to: str, subject: str,
                        html_body: str, ics_bytes: bytes = None) -> MIMEMultipart:
    """Construye el objeto MIMEMultipart listo para enviar (compartido por Gmail API y SMTP)."""
    msg = MIMEMultipart('mixed')
    msg['From'] = f"{sender_name} <{sender}>"
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
    return msg


def _send_via_gmail_api(msg: MIMEMultipart, to: str) -> bool:
    """Intenta enviar usando Gmail API (OAuth2). Retorna True si tuvo éxito."""
    try:
        service = _get_gmail_service()
        if service is None:
            return False
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        logger.info(f"[GMAIL-API] Email enviado a {to}")
        return True
    except HttpError as e:
        logger.error(f"[GMAIL-API] HttpError enviando a {to}: {e}")
        return False
    except Exception as e:
        logger.error(f"[GMAIL-API] Error enviando a {to}: {e}")
        return False


def _send_via_smtp(msg: MIMEMultipart, to: str) -> bool:
    """Intenta enviar usando SMTP (App Password). Retorna True si tuvo éxito."""
    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')

    if not (smtp_host and smtp_user and smtp_password):
        logger.warning("[SMTP] SMTP_HOST, SMTP_USER o SMTP_PASSWORD no configurados")
        return False

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [to], msg.as_string())
        logger.info(f"[SMTP] Email enviado a {to}")
        return True
    except Exception as e:
        logger.error(f"[SMTP] Error enviando a {to}: {e}")
        return False


def _send_email(to: str, subject: str, html_body: str, ics_bytes: bytes = None) -> bool:
    """
    Envía un correo con adjunto iCal opcional.
    Intenta primero Gmail API (OAuth2); si no está configurada, usa SMTP como fallback.
    Retorna True si tuvo éxito, False en caso contrario (nunca propaga excepciones).
    """
    sender_name = os.getenv('SMTP_SENDER_NAME', 'DAGMA Artefacto 360')
    # Gmail API usa GMAIL_SENDER; SMTP usa SMTP_USER como remitente
    sender = os.getenv('GMAIL_SENDER') or os.getenv('SMTP_USER', '')

    try:
        msg = _build_mime_message(sender, sender_name, to, subject, html_body, ics_bytes)

        # Intentar Gmail API primero
        gmail_configured = bool(
            os.getenv('GMAIL_CLIENT_ID') and
            os.getenv('GMAIL_CLIENT_SECRET') and
            os.getenv('GMAIL_REFRESH_TOKEN')
        )
        if gmail_configured:
            result = _send_via_gmail_api(msg, to)
            if result:
                return True
            logger.warning(f"[EMAIL] Gmail API falló para {to}, intentando SMTP...")

        # Fallback: SMTP
        return _send_via_smtp(msg, to)

    except Exception as e:
        logger.error(f"[EMAIL] Error inesperado enviando a {to}: {e}")
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


def send_leaders_notification_email(
    leader_email: str,
    leader_name: str,
    actividad_data: dict,
    app_url: str = "https://dagma-360-capture.vercel.app",
) -> bool:
    """
    Notifica a un líder de grupo sobre una nueva actividad programada, solicitando asignación de personal.
    Retorna True si se envió correctamente, False en caso de error (no propaga excepciones).
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        grupos = ', '.join(actividad_data.get('grupos_requeridos') or []) or 'Todos los grupos'
        saludo = f"Estimado/a líder {leader_name}" if leader_name else "Estimado/a líder"

        html = f"""
        <html><body style="font-family:Arial,sans-serif;color:#333;max-width:620px;margin:auto;padding:0;">
          <div style="background:#1b5e20;padding:24px;text-align:center;">
            <h2 style="color:white;margin:0;">DAGMA — Nueva Actividad Programada</h2>
            <p style="color:#a5d6a7;margin:8px 0 0;">Se requiere asignación de personal</p>
          </div>
          <div style="padding:28px;">
            <p>{saludo},</p>
            <p>Se ha programado una nueva actividad ambiental en el sistema
               <strong>Artefacto 360 DAGMA</strong> que requiere su atención.</p>
            <p><strong>Grupos requeridos:</strong> {grupos}</p>
            <p>Por favor, ingrese a la aplicación y asigne el personal disponible de su grupo
               para participar en esta actividad.</p>
            <h3 style="color:#1b5e20;margin-bottom:8px;">Detalles de la Actividad</h3>
            {_activity_details_table(actividad_data)}
            {_calendar_button(actividad_data)}
            {_maps_button(actividad_data)}
            <p style="margin-top:24px;text-align:center;">
              <a href="{app_url}" style="background:#2e7d32;color:white;padding:12px 32px;
                 text-decoration:none;border-radius:4px;font-family:Arial,sans-serif;font-size:15px;
                 font-weight:bold;display:inline-block;">
                Ir a la App — Asignar Personal
              </a>
            </p>
            <p style="font-size:13px;color:#666;margin-top:16px;">
              Si tiene dudas, comuníquese con el coordinador:<br>
              <strong>{actividad_data.get('lider_actividad', 'N/A')}</strong>
              — Tel: <strong>{actividad_data.get('telefono', 'N/A')}</strong>
            </p>
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
            to=leader_email,
            subject=f"Nueva Actividad DAGMA — Asignar Personal — {fecha}",
            html_body=html,
            ics_bytes=ics or None,
        )
    except Exception as e:
        logger.error(f"[GMAIL] Error enviando notificación a líder {leader_email}: {e}")
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
