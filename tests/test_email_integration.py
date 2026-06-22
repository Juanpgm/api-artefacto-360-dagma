"""
Verificación END-TO-END (sin envío real) del pipeline de correo DAGMA.

Para CADA función pública `send_*`, ejercita el flujo completo de producción:
    render de plantilla Jinja2 real -> _build_mime_message -> transporte
y captura el `MIMEMultipart` que efectivamente llegaría al transporte, para
afirmar que cumple TODAS las señales de entregabilidad sobre las plantillas
reales (no HTML sintético):

- multipart/alternative con text/plain + text/html
- text/plain no vacío y sin etiquetas (derivado de la plantilla real)
- headers Date y Message-ID
- From con display name
- List-Unsubscribe en correos masivos (broadcast)

Esto es lo más cercano a "probar que llega al inbox" que se puede hacer sin
credenciales ni enviar a usuarios reales: prueba que el contenido que se pone
en el cable está bien formado para cada notificación del sistema.
"""
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SMTP_USER", "dagma.notificaciones@gmail.com")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("SMTP_HOST", "smtp.gmail.com")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_SENDER_NAME", "DAGMA Artefacto 360")

if "app.firebase_config" not in sys.modules:
    _fb = types.ModuleType("app.firebase_config")
    _fb.db = MagicMock()
    _fb.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = _fb

from app.services import gmail_service as gs  # noqa: E402


ACTIVIDAD = {
    "fecha_actividad": "2026-07-01",
    "nombre_actividad": "Jornada de Limpieza Río Cali",
    "hora_inicio": "08:00",
    "hora_fin": "12:00",
    "lugar": "Parque Lineal Río Cali",
    "grupo": "Cuadrilla",
    "lider_actividad": "Carlos Méndez",
    "telefono": "3001234567",
    "descripcion": "Recolección de residuos y poda menor.",
    "punto_encuentro": {"geometry": {"coordinates": [-76.53, 3.45]}},
    "calendar_event_link": "https://calendar.google.com/event?eid=abc",
}


def _assert_deliverable(msg, *, expect_unsub=False):
    """Afirma las señales de entregabilidad sobre un mensaje capturado real."""
    types_ = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types_, f"sin text/plain: {types_}"
    assert "text/html" in types_, f"sin text/html: {types_}"
    assert msg["Date"], "sin header Date"
    assert msg["Message-ID"], "sin header Message-ID"
    assert "<" in (msg["From"] or ""), f"From sin display name: {msg['From']}"

    plain = None
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            plain = part.get_payload(decode=True).decode("utf-8")
    assert plain and plain.strip(), "text/plain vacío"
    assert "<" not in plain and ">" not in plain, "text/plain con etiquetas HTML"

    if expect_unsub:
        assert msg["List-Unsubscribe"], "broadcast sin List-Unsubscribe"
        assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


@pytest.fixture
def captured():
    """Captura el MIMEMultipart entregado al transporte y simula éxito de envío."""
    box = {}

    def _capture(msg, to):
        box["msg"] = msg
        box["to"] = to
        return True

    with patch.object(gs, "_count_sent_last_24h", return_value=0), \
         patch.object(gs, "_get_db", return_value=None), \
         patch.object(gs, "_log_notification"), \
         patch.object(gs, "_send_via_smtp", side_effect=_capture), \
         patch.object(gs, "_send_via_gmail_api", side_effect=_capture):
        yield box


def test_activity_confirmation(captured):
    assert gs.send_activity_confirmation_email("coord@example.com", ACTIVIDAD) is True
    _assert_deliverable(captured["msg"])


def test_assignment_notification(captured):
    assert gs.send_assignment_notification_email(
        "op@example.com", "Ana García", "Cuadrilla", ACTIVIDAD,
        lider_nombre="Carlos", lider_telefono="3001234567") is True
    _assert_deliverable(captured["msg"])


def test_leaders_notification(captured):
    assert gs.send_leaders_notification_email(
        "lider@example.com", "Carlos", ACTIVIDAD) is True
    _assert_deliverable(captured["msg"])


def test_removal_notification(captured):
    assert gs.send_removal_notification_email(
        "op@example.com", "Ana", ACTIVIDAD) is True
    _assert_deliverable(captured["msg"])


def test_activity_leader_assigned(captured):
    assert gs.send_activity_leader_assigned_email(
        "lider@example.com", "Carlos", ACTIVIDAD) is True
    _assert_deliverable(captured["msg"])


def test_assignment_summary_leader(captured):
    assert gs.send_assignment_summary_leader_email(
        "lider@example.com", "Carlos", ACTIVIDAD,
        agregados=[{"nombre": "Ana"}], removidos=[]) is True
    _assert_deliverable(captured["msg"])


def test_activity_cancellation(captured):
    assert gs.send_activity_cancellation_email(
        "op@example.com", "Ana", ACTIVIDAD) is True
    _assert_deliverable(captured["msg"])


def test_activity_modification(captured):
    assert gs.send_activity_modification_email(
        "op@example.com", "Ana", ACTIVIDAD,
        cambios=[{"campo": "hora_inicio", "antes": "08:00", "despues": "09:00"}]) is True
    _assert_deliverable(captured["msg"])


def test_role_change(captured):
    assert gs.send_role_change_email(
        "op@example.com", "Ana", "operador", "lider") is True
    _assert_deliverable(captured["msg"])


def test_grupo_change(captured):
    assert gs.send_grupo_change_email(
        "op@example.com", "Ana", "Cuadrilla", "Vivero") is True
    _assert_deliverable(captured["msg"])


def test_weekly_attendance_report(captured):
    assert gs.send_weekly_attendance_report_email(
        "lider@example.com", "Carlos", "Cuadrilla", "2026-06-15", "2026-06-21",
        stats={"actividades_total": 3, "asistencia_pct": 92},
        actividades=[{"fecha": "2026-06-16", "nombre": "Limpieza"}],
        inasistentes_top=[{"nombre": "Pedro", "faltas": 2}]) is True
    _assert_deliverable(captured["msg"])


def test_test_email(captured):
    assert gs.send_test_email("admin@example.com") is True
    _assert_deliverable(captured["msg"])


def test_broadcast_has_list_unsubscribe(captured):
    assert gs.send_broadcast_email(
        "user@example.com", "Comunicado importante",
        "<p>Mensaje a toda la organización.</p>", priority="warning") is True
    _assert_deliverable(captured["msg"], expect_unsub=True)
