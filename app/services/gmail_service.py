"""
Servicio de notificaciones por email para actividades DAGMA.
Estrategia dual:
  1. Gmail API (OAuth2): requiere GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_SENDER.
  2. SMTP (fallback): requiere SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_SENDER_NAME.
Si ninguno está configurado, el envío falla silenciosamente con un log de advertencia.
"""
import os
import re
import base64
import smtplib
import socket
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid

from jinja2 import Environment, FileSystemLoader, select_autoescape
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.ical_service import generate_ics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 environment compartido para plantillas de correo.
# Las plantillas viven en back/app/templates/emails/ con _base.html + _macros.html.
# ---------------------------------------------------------------------------
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'templates' / 'emails'
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(['html', 'xml']),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render_template(name: str, context: dict) -> str:
    """Renderiza una plantilla Jinja2 ubicada en templates/emails/<name>."""
    return _jinja_env.get_template(name).render(**context)


# ---------------------------------------------------------------------------
# Cuota / monitoreo de envío (Gmail ~100 correos/día por cuenta SMTP).
# Registramos cada envío en `notifications_log` y disparamos alertas a admin
# cuando se cruzan los umbrales 80% / 95%.
# ---------------------------------------------------------------------------
DAILY_EMAIL_QUOTA = int(os.getenv('EMAIL_DAILY_QUOTA', '450'))
QUOTA_WARN_THRESHOLD = float(os.getenv('EMAIL_QUOTA_WARN', '0.80'))
QUOTA_BLOCK_THRESHOLD = float(os.getenv('EMAIL_QUOTA_BLOCK', '0.95'))


def _get_db():
    """Obtiene el cliente Firestore (lazy import para no romper tests sin Firebase)."""
    try:
        from app.firebase_config import db  # noqa: WPS433
        return db
    except Exception as e:  # pragma: no cover
        logger.debug(f"[QUOTA] Firestore no disponible: {e}")
        return None


def _count_sent_last_24h() -> int:
    db = _get_db()
    if db is None:
        return 0
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        q = (
            db.collection('notifications_log')
            .where('sent_at', '>=', since)
            .where('status', '==', 'sent')
        )
        return sum(1 for _ in q.stream())
    except Exception as e:  # pragma: no cover
        logger.warning(f"[QUOTA] Error contando envíos 24h: {e}")
        return 0


def _log_notification(to: str, subject: str, template: str, status: str, error: str = '') -> None:
    db = _get_db()
    if db is None:
        return
    try:
        db.collection('notifications_log').add({
            'to': to,
            'subject': subject,
            'template': template,
            'status': status,
            'error': error,
            'sent_at': datetime.now(timezone.utc),
        })
    except Exception as e:  # pragma: no cover
        logger.debug(f"[QUOTA] Error registrando notificación: {e}")


def _admin_alert_email() -> str:
    return os.getenv('ADMIN_ALERT_EMAIL', os.getenv('SMTP_USER', ''))


def _maybe_alert_quota(count: int) -> None:
    """Si el conteo cruzó el umbral del 80% en las últimas 24h, alerta una sola vez."""
    db = _get_db()
    if db is None:
        return
    threshold = int(DAILY_EMAIL_QUOTA * QUOTA_WARN_THRESHOLD)
    if count < threshold:
        return
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    alert_key = f"quota-warn-{today}"
    try:
        doc_ref = db.collection('notifications_alerts').document(alert_key)
        if doc_ref.get().exists:
            return  # ya alertado hoy
        doc_ref.set({
            'kind': 'quota_warning',
            'count_24h': count,
            'quota': DAILY_EMAIL_QUOTA,
            'created_at': datetime.now(timezone.utc),
        })
        admin = _admin_alert_email()
        if admin:
            html = _render_template('generic_announcement.html', {
                'subject': 'Aviso de cuota de correo DAGMA',
                'header_color': '#f9a825',
                'header_title': 'DAGMA — Cuota de correo al 80%',
                'header_subtitle': 'Sistema Artefacto 360',
                'priority': 'warning',
                'message_html': (
                    f'<p>Se han enviado <strong>{count}</strong> correos en las últimas 24 horas '
                    f'(cuota diaria: <strong>{DAILY_EMAIL_QUOTA}</strong>).</p>'
                    '<p>Si se alcanza el 95% los envíos no críticos serán bloqueados para evitar '
                    'que Gmail suspenda la cuenta.</p>'
                ),
                'cta_url': '',
                'cta_label': '',
            })
            # Bypass quota check para esta alerta administrativa.
            _send_raw_email(admin, '[DAGMA] Cuota de correo al 80%', html, template='quota_warning')
    except Exception as e:  # pragma: no cover
        logger.warning(f"[QUOTA] Error generando alerta: {e}")


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


def _html_to_text(html: str) -> str:
    """Convierte un cuerpo HTML a texto plano legible para la parte text/plain.

    No pretende ser un renderizador completo: elimina <style>/<script>, conserva
    el destino de los enlaces (`texto (url)`), colapsa el resto de etiquetas y
    normaliza el espaciado. El objetivo es que TODO correo lleve una alternativa
    de texto plano — su ausencia es una de las señales de spam más penalizadas.
    """
    if not html:
        return ''
    text = re.sub(r'(?is)<(script|style).*?</\1>', '', html)
    # Desescapar entidades ANTES de remover etiquetas. Si una plantilla autoescapa
    # pseudo-etiquetas (ej. `&lt;strong&gt;`), al desescaparlas se vuelven etiquetas
    # reales y el stripping posterior las elimina — evita que `<strong>` se filtre
    # al texto plano.
    replacements = {
        '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
        '&quot;': '"', '&#39;': "'", '&mdash;': '—', '&ndash;': '–',
    }
    for ent, ch in replacements.items():
        text = text.replace(ent, ch)
    # Saltos de línea para bloques comunes antes de remover etiquetas.
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|tr|h[1-6]|li)\s*>', '\n', text)
    # Conservar el destino de los enlaces: <a href="URL">texto</a> -> texto (URL)
    text = re.sub(
        r'(?is)<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2))} ({m.group(1)})",
        text,
    )
    text = re.sub(r'(?s)<[^>]+>', '', text)  # remover el resto de etiquetas
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def _build_mime_message(sender: str, sender_name: str, to: str, subject: str,
                        html_body: str, ics_bytes: bytes = None,
                        list_unsubscribe: str = None) -> MIMEMultipart:
    """Construye el objeto MIME listo para enviar (compartido por Gmail API y SMTP).

    Estructura:
      - Sin adjunto: ``multipart/alternative`` con text/plain + text/html.
      - Con .ics: ``multipart/mixed`` que envuelve el ``multipart/alternative``
        y el adjunto ``text/calendar``.

    Headers de entregabilidad incluidos SIEMPRE: ``Date`` y ``Message-ID``
    (smtplib no los agrega solo; su ausencia es señal de spam). ``List-Unsubscribe``
    se añade solo para correos masivos/broadcast cuando se provee.
    """
    sender_domain = sender.split('@')[-1] if '@' in sender else 'localhost'

    # Parte alternativa: texto plano + HTML (en ese orden de preferencia ascendente).
    alternative = MIMEMultipart('alternative')
    alternative.attach(MIMEText(_html_to_text(html_body), 'plain', 'utf-8'))
    alternative.attach(MIMEText(html_body, 'html', 'utf-8'))

    if ics_bytes:
        msg = MIMEMultipart('mixed')
        outer = msg
        msg.attach(alternative)
        part = MIMEBase('text', 'calendar', method='REQUEST', charset='utf-8')
        part.set_payload(ics_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='actividad_dagma.ics')
        part.add_header('Content-Type', 'text/calendar; method=REQUEST; charset=utf-8')
        msg.attach(part)
    else:
        msg = alternative
        outer = msg

    outer['From'] = f"{sender_name} <{sender}>"
    outer['To'] = to
    outer['Subject'] = subject
    outer['Date'] = formatdate(localtime=True)
    outer['Message-ID'] = make_msgid(domain=sender_domain)
    reply_to = os.getenv('REPLY_TO_EMAIL', '')
    if reply_to:
        outer['Reply-To'] = reply_to
    if list_unsubscribe:
        outer['List-Unsubscribe'] = list_unsubscribe
        outer['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
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
    """
    Intenta enviar usando SMTP (App Password). Autodetecta el modo de cifrado:
    - Puerto 465 → SMTP_SSL (cifrado implícito desde el handshake).
    - Puerto 587 (o cualquier otro) → SMTP + STARTTLS.
    Retorna True si tuvo éxito.
    """
    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_password = os.getenv('SMTP_PASSWORD', '')

    if not (smtp_host and smtp_user and smtp_password):
        logger.warning("[SMTP] SMTP_HOST, SMTP_USER o SMTP_PASSWORD no configurados")
        return False

    # Forzar IPv4: algunos entornos (Railway, etc.) resuelven smtp.gmail.com a IPv6
    # pero no tienen ruta IPv6 saliente → [Errno 101] Network is unreachable.
    # Monkey-patch temporal de socket.getaddrinfo para filtrar a AF_INET y conservar
    # SNI/hostname original para la validación TLS.
    import socket as _socket
    _orig_getaddrinfo = _socket.getaddrinfo

    def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)

    _socket.getaddrinfo = _ipv4_only_getaddrinfo
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [to], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [to], msg.as_string())
        logger.info(f"[SMTP] Email enviado a {to} (host={smtp_host}, port={smtp_port}, ipv4)")
        return True
    except Exception as e:
        logger.error(f"[SMTP] Error enviando a {to} (host={smtp_host}, port={smtp_port}): {e}")
        return False
    finally:
        _socket.getaddrinfo = _orig_getaddrinfo


def _send_raw_email(to: str, subject: str, html_body: str,
                    ics_bytes: bytes = None, template: str = '',
                    max_attempts: int = 3, list_unsubscribe: str = None) -> bool:
    """Envío directo sin chequeo de cuota (uso interno y para alertas).

    Reintenta hasta `max_attempts` veces con backoff exponencial ante fallos
    transitorios. Los errores HTTP 4xx (excepto 429) se tratan como fallos
    permanentes y no se reintenta.
    """
    sender_name = os.getenv('SMTP_SENDER_NAME', 'DAGMA Artefacto 360')
    sender = os.getenv('GMAIL_SENDER') or os.getenv('SMTP_USER', '')
    ok = False
    last_error = ''
    try:
        msg = _build_mime_message(sender, sender_name, to, subject, html_body, ics_bytes,
                                  list_unsubscribe=list_unsubscribe)
        gmail_configured = bool(
            os.getenv('GMAIL_CLIENT_ID') and
            os.getenv('GMAIL_CLIENT_SECRET') and
            os.getenv('GMAIL_REFRESH_TOKEN')
        )
        for attempt in range(max_attempts):
            try:
                if gmail_configured:
                    try:
                        ok = _send_via_gmail_api(msg, to)
                    except HttpError as http_err:
                        status = getattr(http_err.resp, 'status', None)
                        if status is not None and 400 <= int(status) < 500 and int(status) != 429:
                            # Permanent client error — do not retry.
                            logger.error(
                                f"[EMAIL] HttpError permanente {status} enviando a {to}: {http_err}"
                            )
                            _log_notification(to, subject, template, 'failed', error=str(http_err))
                            return False
                        # Transient: 5xx or 429 — fall through to sleep+retry.
                        logger.warning(
                            f"[EMAIL] HttpError transitorio {status} enviando a {to} "
                            f"(intento {attempt + 1}/{max_attempts}): {http_err}"
                        )
                        ok = False
                    if ok:
                        break
                    logger.warning(f"[EMAIL] Gmail API falló para {to}, intentando SMTP...")
                # Gmail not configured OR Gmail failed — try SMTP.
                ok = _send_via_smtp(msg, to)
                if ok:
                    break
            except (smtplib.SMTPException, socket.error, OSError) as transport_err:
                logger.warning(
                    f"[EMAIL] Error de transporte enviando a {to} "
                    f"(intento {attempt + 1}/{max_attempts}): {transport_err}"
                )
                last_error = str(transport_err)
                ok = False

            if not ok and attempt < max_attempts - 1:
                sleep_secs = min(2 ** attempt, 8)
                logger.info(f"[EMAIL] Reintentando en {sleep_secs}s (intento {attempt + 1}/{max_attempts})")
                time.sleep(sleep_secs)

        _log_notification(to, subject, template, 'sent' if ok else 'failed',
                          error='' if ok else last_error)
        return ok
    except Exception as e:
        logger.error(f"[EMAIL] Error inesperado enviando a {to}: {e}")
        _log_notification(to, subject, template, 'failed', error=str(e))
        return False


def _send_email(to: str, subject: str, html_body: str,
                ics_bytes: bytes = None, template: str = '',
                critical: bool = False, max_attempts: int = 3,
                list_unsubscribe: str = None) -> bool:
    """
    Envía un correo con adjunto iCal opcional y control de cuota diaria.
    Si se supera el 95% de la cuota diaria, NO envía y registra el bloqueo —
    EXCEPTO cuando `critical=True`, en cuyo caso el envío procede siempre.
    Si se supera el 80%, dispara una alerta admin (dedup por día).
    Retorna True si tuvo éxito, False en caso contrario (nunca propaga excepciones).
    """
    count = _count_sent_last_24h()
    if not critical and count >= int(DAILY_EMAIL_QUOTA * QUOTA_BLOCK_THRESHOLD):
        logger.error(
            f"[QUOTA] Bloqueo de envío a {to}: {count}/{DAILY_EMAIL_QUOTA} "
            f"correos en las últimas 24h (≥{int(QUOTA_BLOCK_THRESHOLD*100)}%)."
        )
        _log_notification(to, subject, template, 'blocked_quota')
        _maybe_alert_quota(count)
        return False
    _maybe_alert_quota(count)
    return _send_raw_email(to, subject, html_body, ics_bytes=ics_bytes, template=template,
                           max_attempts=max_attempts, list_unsubscribe=list_unsubscribe)


def _google_maps_url(actividad_data: dict) -> str:
    """Genera URL de Google Maps desde las coordenadas del punto de encuentro."""
    punto = actividad_data.get('punto_encuentro') or {}
    geometry = punto.get('geometry') or {} if isinstance(punto, dict) else {}
    coords = geometry.get('coordinates') or [] if isinstance(geometry, dict) else []
    if len(coords) >= 2:
        lng, lat = coords[0], coords[1]
        return f"https://www.google.com/maps?q={lat},{lng}"
    return ''


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
    """Confirmación al coordinador cuando se programa una actividad (con .ics adjunto)."""
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Actividad Ambiental Programada — {fecha}"
        html = _render_template('activity_confirmation.html', {
            'subject': subject,
            'header_color': '#2e7d32',
            'header_title': 'DAGMA — Actividad Ambiental Programada',
            'header_subtitle': '',
            'actividad': actividad_data,
        })
        ics = generate_ics(actividad_data)
        return _send_email(coordinator_email, subject, html,
                           ics_bytes=ics or None, template='activity_confirmation')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando confirmación a {coordinator_email}: {e}")
        return False


def send_assignment_notification_email(
    person_email: str,
    nombre: str,
    grupo: str,
    actividad_data: dict,
    lider_nombre: str = None,
    lider_telefono: str = None,
) -> bool:
    """Notificación de asignación al personal (con .ics adjunto).

    `lider_nombre` y `lider_telefono` corresponden al LÍDER de la actividad
    (no al coordinador que la programó). Si no se pasan, la plantilla cae a
    los valores de `actividad_data`.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Asignación Actividad Ambiental DAGMA — {fecha}"
        # Vista de la actividad con los datos del LÍDER (no del coordinador) para
        # los campos `lider_actividad` y `telefono` que rinde la tabla de detalles.
        actividad_view = dict(actividad_data)
        if lider_nombre:
            actividad_view['lider_actividad'] = lider_nombre
        if lider_telefono:
            actividad_view['telefono'] = lider_telefono
        html = _render_template('assignment_notification.html', {
            'subject': subject,
            'header_color': '#1565c0',
            'header_title': 'DAGMA — Asignación a Actividad Ambiental',
            'header_subtitle': '',
            'nombre': nombre,
            'grupo': grupo,
            'actividad': actividad_view,
            'lider_nombre': lider_nombre,
            'lider_telefono': lider_telefono,
        })
        ics = generate_ics(actividad_data)
        return _send_email(person_email, subject, html,
                           ics_bytes=ics or None, template='assignment_notification')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando notificación a {person_email}: {e}")
        return False


def send_leaders_notification_email(
    leader_email: str,
    leader_name: str,
    actividad_data: dict,
    app_url: str = None,
    lider_nombre: str = None,
    lider_telefono: str = None,
) -> bool:
    """Notifica al líder de un grupo sobre nueva actividad que requiere asignación.

    `lider_nombre` y `lider_telefono` son del LÍDER de la actividad (no del coordinador).
    Si se proveen, sobreescriben los valores de la plantilla de detalles.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Nueva Actividad DAGMA — Asignar Personal — {fecha}"
        app_url = app_url or os.getenv('FRONTEND_URL', 'https://dagma-360-capture-frontend.vercel.app')
        # Deep-link directo a la sección de convocatorias (programación de actividades).
        # Si el usuario no está autenticado, la app lo llevará al login primero.
        sep = '&' if '?' in app_url else '?'
        deep_link = f"{app_url}{sep}view=convocatorias"
        # Construir vista con datos del LÍDER de la actividad en vez del coordinador.
        actividad_view = dict(actividad_data)
        if lider_nombre:
            actividad_view['lider_actividad'] = lider_nombre
        if lider_telefono:
            actividad_view['telefono'] = lider_telefono
        html = _render_template('leaders_request.html', {
            'subject': subject,
            'header_color': '#1b5e20',
            'header_title': 'DAGMA — Nueva Actividad Programada',
            'header_subtitle': 'Se requiere asignación de personal',
            'leader_name': leader_name,
            'actividad': actividad_view,
            'app_url': deep_link,
        })
        ics = generate_ics(actividad_data)
        return _send_email(leader_email, subject, html,
                           ics_bytes=ics or None, template='leaders_request')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando notificación a líder {leader_email}: {e}")
        return False


def send_removal_notification_email(
    person_email: str,
    nombre: str,
    actividad_data: dict,
    lider_nombre: str = None,
    lider_telefono: str = None,
) -> bool:
    """Notificación de desasignación al personal removido.

    `lider_nombre` y `lider_telefono` corresponden al LÍDER de la actividad.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Desasignación de Actividad Ambiental DAGMA — {fecha}"
        actividad_view = dict(actividad_data)
        if lider_nombre:
            actividad_view['lider_actividad'] = lider_nombre
        if lider_telefono:
            actividad_view['telefono'] = lider_telefono
        html = _render_template('removal_notification.html', {
            'subject': subject,
            'header_color': '#c62828',
            'header_title': 'DAGMA — Desasignación de Actividad Ambiental',
            'header_subtitle': '',
            'nombre': nombre,
            'actividad': actividad_view,
            'lider_nombre': lider_nombre,
            'lider_telefono': lider_telefono,
        })
        return _send_email(person_email, subject, html, template='removal_notification')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando desasignación a {person_email}: {e}")
        return False


# ===========================================================================
# Nuevos envíos (Fase 3): líder de actividad, resumen al líder, cambios de rol/
# grupo, broadcast genérico, reporte semanal y health check.
# ===========================================================================

def send_activity_leader_assigned_email(
    leader_email: str,
    leader_name: str,
    actividad_data: dict,
    app_url: str = None,
) -> bool:
    """Notifica a la persona designada como LÍDER de una actividad específica."""
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Asignación como Líder de Actividad DAGMA — {fecha}"
        app_url = app_url or os.getenv('FRONTEND_URL', 'https://dagma-360-capture-frontend.vercel.app')
        html = _render_template('activity_leader_assigned.html', {
            'subject': subject,
            'header_color': '#2e7d32',
            'header_title': 'DAGMA — Designación como Líder de Actividad',
            'header_subtitle': '',
            'leader_name': leader_name,
            'actividad': actividad_data,
            'app_url': app_url,
        })
        ics = generate_ics(actividad_data)
        return _send_email(leader_email, subject, html,
                           ics_bytes=ics or None, template='activity_leader_assigned')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando designación líder a {leader_email}: {e}")
        return False


def send_assignment_summary_leader_email(
    leader_email: str,
    leader_name: str,
    actividad_data: dict,
    agregados: list,
    removidos: list,
    app_url: str = None,
) -> bool:
    """CC al líder con el delta (agregados/removidos) tras una asignación/desasignación."""
    try:
        if not agregados and not removidos:
            return True  # nada que reportar
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"Resumen de asignación — Actividad DAGMA {fecha}"
        app_url = app_url or os.getenv('FRONTEND_URL', 'https://dagma-360-capture-frontend.vercel.app')
        html = _render_template('assignment_summary_leader.html', {
            'subject': subject,
            'header_color': '#1565c0',
            'header_title': 'DAGMA — Resumen de Asignación',
            'header_subtitle': fecha,
            'leader_name': leader_name,
            'actividad': actividad_data,
            'agregados': agregados or [],
            'removidos': removidos or [],
            'app_url': app_url,
        })
        return _send_email(leader_email, subject, html, template='assignment_summary_leader')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando resumen líder a {leader_email}: {e}")
        return False


def send_activity_cancellation_email(
    to_email: str,
    nombre: str,
    actividad_data: dict,
    lider_telefono: str = None,
) -> bool:
    """Notifica la cancelación de una actividad a un destinatario.

    `lider_telefono` corresponde al LÍDER de la actividad (no al coordinador).
    Si se provee, sobreescribe el campo `telefono` en la vista de detalles.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"CANCELACIÓN — Actividad Ambiental DAGMA — {fecha}"
        actividad_view = dict(actividad_data)
        if lider_telefono:
            actividad_view['telefono'] = lider_telefono
        html = _render_template('activity_cancellation.html', {
            'subject': subject,
            'header_color': '#b71c1c',
            'header_title': 'DAGMA — Actividad Cancelada',
            'header_subtitle': 'Esta actividad ha sido cancelada',
            'nombre': nombre,
            'actividad': actividad_view,
        })
        return _send_email(to_email, subject, html, template='activity_cancellation', critical=True)
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando cancelación a {to_email}: {e}")
        return False


def send_activity_modification_email(
    to_email: str,
    nombre: str,
    actividad_data: dict,
    cambios: list,
    app_url: str = None,
    lider_telefono: str = None,
) -> bool:
    """Notifica modificaciones en una actividad indicando qué campos cambiaron.

    `cambios` es una lista de dicts: [{"campo": str, "antes": str, "despues": str}].
    `lider_telefono` corresponde al LÍDER de la actividad (no al coordinador).
    Si se provee, sobreescribe el campo `telefono` en la vista de detalles.
    """
    try:
        fecha = actividad_data.get('fecha_actividad', '')
        subject = f"MODIFICACIÓN — Actividad Ambiental DAGMA — {fecha}"
        app_url = app_url or os.getenv('FRONTEND_URL', 'https://dagma-360-capture-frontend.vercel.app')
        actividad_view = dict(actividad_data)
        if lider_telefono:
            actividad_view['telefono'] = lider_telefono
        html = _render_template('activity_modification.html', {
            'subject': subject,
            'header_color': '#f57f17',
            'header_title': 'DAGMA — Modificación de Actividad',
            'header_subtitle': 'Se han realizado cambios en la actividad',
            'nombre': nombre,
            'actividad': actividad_view,
            'cambios': cambios or [],
            'app_url': app_url,
        })
        return _send_email(to_email, subject, html, template='activity_modification')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando modificación a {to_email}: {e}")
        return False


def send_role_change_email(
    user_email: str,
    nombre: str,
    old_role: str,
    new_role: str,
    actor_name: str = 'Administrador',
) -> bool:
    """Notifica al usuario que su rol fue modificado por un administrador."""
    try:
        subject = 'Cambio de rol en Artefacto 360 DAGMA'
        html = _render_template('role_change.html', {
            'subject': subject,
            'header_color': '#f9a825',
            'header_title': 'DAGMA — Cambio de Rol',
            'header_subtitle': '',
            'nombre': nombre,
            'old_role': old_role,
            'new_role': new_role,
            'actor_name': actor_name,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        })
        return _send_email(user_email, subject, html, template='role_change')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando cambio de rol a {user_email}: {e}")
        return False


def send_grupo_change_email(
    user_email: str,
    nombre: str,
    old_grupo: str,
    new_grupo: str,
    actor_name: str = 'Administrador',
) -> bool:
    """Notifica al usuario que su grupo fue modificado por un administrador."""
    try:
        subject = 'Cambio de grupo en Artefacto 360 DAGMA'
        html = _render_template('grupo_change.html', {
            'subject': subject,
            'header_color': '#1565c0',
            'header_title': 'DAGMA — Cambio de Grupo',
            'header_subtitle': '',
            'nombre': nombre,
            'old_grupo': old_grupo,
            'new_grupo': new_grupo,
            'actor_name': actor_name,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        })
        return _send_email(user_email, subject, html, template='grupo_change')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando cambio de grupo a {user_email}: {e}")
        return False


def send_broadcast_email(
    to: str,
    subject: str,
    message_html: str,
    priority: str = 'info',
    cta_url: str = '',
    cta_label: str = '',
) -> bool:
    """Envía un anuncio genérico (Comunicados) a un destinatario.

    priority ∈ {'info','warning','urgent'} controla el color del header.
    """
    color_map = {'info': '#2e7d32', 'warning': '#f9a825', 'urgent': '#c62828'}
    header_color = color_map.get(priority, '#2e7d32')
    try:
        html = _render_template('generic_announcement.html', {
            'subject': subject,
            'header_color': header_color,
            'header_title': f'DAGMA — {subject}',
            'header_subtitle': '',
            'priority': priority,
            'message_html': message_html,
            'cta_url': cta_url,
            'cta_label': cta_label,
        })
        # Correo masivo: List-Unsubscribe es obligatorio bajo las reglas de
        # remitentes masivos de Gmail/Yahoo (2024). Usamos un mailto al remitente
        # o, si está configurado, una URL de baja por un solo clic.
        sender = os.getenv('GMAIL_SENDER') or os.getenv('SMTP_USER', '')
        unsub_url = os.getenv('LIST_UNSUBSCRIBE_URL', '')
        parts = []
        if unsub_url:
            parts.append(f'<{unsub_url}>')
        if sender:
            parts.append(f'<mailto:{sender}?subject=unsubscribe>')
        list_unsubscribe = ', '.join(parts) or None
        return _send_email(to, subject, html, template='broadcast',
                           list_unsubscribe=list_unsubscribe)
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando broadcast a {to}: {e}")
        return False


def send_weekly_attendance_report_email(
    leader_email: str,
    leader_name: str,
    grupo: str,
    period_start: str,
    period_end: str,
    stats: dict,
    actividades: list,
    inasistentes_top: list = None,
    app_url: str = None,
) -> bool:
    """Envía el reporte ejecutivo semanal de asistencia al líder de un grupo."""
    try:
        subject = f"Reporte Semanal de Asistencia — {grupo} ({period_start} a {period_end})"
        app_url = app_url or os.getenv('FRONTEND_URL', 'https://dagma-360-capture-frontend.vercel.app')
        html = _render_template('weekly_attendance_leader.html', {
            'subject': subject,
            'header_color': '#2e7d32',
            'header_title': 'DAGMA — Reporte Semanal de Asistencia',
            'header_subtitle': grupo,
            'leader_name': leader_name,
            'grupo': grupo,
            'period_start': period_start,
            'period_end': period_end,
            'stats': stats or {},
            'actividades': actividades or [],
            'inasistentes_top': inasistentes_top or [],
            'app_url': app_url,
        })
        return _send_email(leader_email, subject, html, template='weekly_attendance')
    except Exception as e:
        logger.error(f"[EMAIL] Error enviando reporte semanal a {leader_email}: {e}")
        return False


def send_test_email(to: str) -> bool:
    """Envío de prueba/smoke (utilizado por /admin/notifications/health)."""
    try:
        subject = '[DAGMA] Prueba de notificaciones'
        html = _render_template('generic_announcement.html', {
            'subject': subject,
            'header_color': '#2e7d32',
            'header_title': 'DAGMA — Test de notificaciones',
            'header_subtitle': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
            'priority': 'info',
            'message_html': (
                '<p>Este es un correo de prueba enviado desde el servicio de notificaciones '
                'de Artefacto 360 DAGMA. Si lo recibe, la configuración SMTP/Gmail está OK.</p>'
            ),
            'cta_url': '',
            'cta_label': '',
        })
        return _send_raw_email(to, subject, html, template='test')
    except Exception as e:
        logger.error(f"[EMAIL] Error en send_test_email a {to}: {e}")
        return False


def get_notifications_health() -> dict:
    """Reporte de estado para el endpoint admin/notifications/health."""
    count = _count_sent_last_24h()
    smtp_ok = bool(os.getenv('SMTP_HOST') and os.getenv('SMTP_USER') and os.getenv('SMTP_PASSWORD'))
    gmail_ok = bool(
        os.getenv('GMAIL_CLIENT_ID') and os.getenv('GMAIL_CLIENT_SECRET')
        and os.getenv('GMAIL_REFRESH_TOKEN')
    )
    sender = os.getenv('GMAIL_SENDER') or os.getenv('SMTP_USER', '')
    sender_domain = sender.split('@')[-1] if '@' in sender else ''
    return {
        'smtp_configured': smtp_ok,
        'gmail_api_configured': gmail_ok,
        'primary_transport': 'gmail_api' if gmail_ok else ('smtp' if smtp_ok else 'none'),
        'smtp_host': os.getenv('SMTP_HOST', ''),
        'smtp_port': int(os.getenv('SMTP_PORT', '587') or 587),
        'sender': sender,
        'sender_domain': sender_domain,
        'sender_name': os.getenv('SMTP_SENDER_NAME', ''),
        'reply_to_configured': bool(os.getenv('REPLY_TO_EMAIL', '')),
        'list_unsubscribe_url_configured': bool(os.getenv('LIST_UNSUBSCRIBE_URL', '')),
        'frontend_url': os.getenv('FRONTEND_URL', ''),
        'daily_quota': DAILY_EMAIL_QUOTA,
        'sent_last_24h': count,
        'warn_threshold': int(DAILY_EMAIL_QUOTA * QUOTA_WARN_THRESHOLD),
        'block_threshold': int(DAILY_EMAIL_QUOTA * QUOTA_BLOCK_THRESHOLD),
        'quota_usage_pct': round((count / DAILY_EMAIL_QUOTA) * 100, 1) if DAILY_EMAIL_QUOTA else 0,
    }
