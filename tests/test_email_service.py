"""
Tests para back/app/services/gmail_service.py
Sin Firebase real: mockeamos Firestore + SMTP + Gmail API.
Validan: renderizado de plantillas, cuota, dedupe de alertas, dispatch.
"""
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# Forzar config mínima ANTES de importar el módulo
os.environ.setdefault("SMTP_USER", "test@example.com")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("SMTP_HOST", "smtp.example.com")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("EMAIL_DAILY_QUOTA", "100")
os.environ.setdefault("EMAIL_QUOTA_WARN", "0.80")
os.environ.setdefault("EMAIL_QUOTA_BLOCK", "0.95")


# Mock firebase_config antes de cargar el servicio
if "app.firebase_config" not in sys.modules:
    fake_mod = types.ModuleType("app.firebase_config")
    fake_mod.db = MagicMock()
    fake_mod.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = fake_mod


from app.services import gmail_service as gs  # noqa: E402


# ---------------------------------------------------------------------------
# Plantillas
# ---------------------------------------------------------------------------

class TestTemplateRendering:
    def test_render_role_change_contains_fields(self):
        html = gs._render_template("role_change.html", {
            "subject": "Cambio rol",
            "header_color": "#f9a825",
            "header_title": "Tu rol fue actualizado",
            "header_subtitle": "DAGMA",
            "nombre": "Ana Pérez",
            "old_role": "operador",
            "new_role": "lider",
            "actor_name": "admin@dagma.gov.co",
        })
        assert "Ana Pérez" in html
        assert "operador" in html
        assert "lider" in html

    def test_render_assignment_summary_includes_delta(self):
        html = gs._render_template("assignment_summary_leader.html", {
            "subject": "Resumen",
            "header_color": "#1565c0",
            "header_title": "Resumen de cambios",
            "header_subtitle": "DAGMA",
            "leader_name": "Carlos",
            "actividad": {"objetivo_actividad": "Limpieza"},
            "agregados": [{"nombre": "A", "email": "a@x.com"}],
            "removidos": [{"nombre": "B", "email": "b@x.com"}],
        })
        assert "Carlos" in html
        assert "A" in html and "B" in html


# ---------------------------------------------------------------------------
# Cuota
# ---------------------------------------------------------------------------

class TestQuotaSystem:
    def test_blocks_at_95_percent(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 95)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        sent_raw = MagicMock(return_value=True)
        monkeypatch.setattr(gs, "_send_raw_email", sent_raw)
        ok = gs._send_email("a@b.com", "X", "<p>X</p>")
        assert ok is False
        sent_raw.assert_not_called()

    def test_allows_below_threshold(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 10)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        sent_raw = MagicMock(return_value=True)
        monkeypatch.setattr(gs, "_send_raw_email", sent_raw)
        ok = gs._send_email("a@b.com", "X", "<p>X</p>")
        assert ok is True
        sent_raw.assert_called_once()


# ---------------------------------------------------------------------------
# Dispatch SMTP (mockeado)
# ---------------------------------------------------------------------------

class TestSendDispatch:
    def test_role_change_uses_smtp_when_no_gmail_api(self, monkeypatch):
        monkeypatch.setattr(gs, "_get_gmail_service", lambda: None)
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 0)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        monkeypatch.setattr(gs, "_log_notification", lambda *a, **k: None)
        smtp_mock = MagicMock(return_value=True)
        monkeypatch.setattr(gs, "_send_via_smtp", smtp_mock)
        ok = gs.send_role_change_email(
            "user@x.com", "Ana", "operador", "lider", "admin@x.com"
        )
        assert ok is True
        assert smtp_mock.called


# ---------------------------------------------------------------------------
# Teléfono del líder en plantillas de asignación/desasignación
# ---------------------------------------------------------------------------

class TestLiderPhoneInTemplates:
    """Verifica que `lider_telefono`/`lider_nombre` se renderizan correctamente
    en las plantillas, ignorando el `telefono` del coordinador.
    """

    ACTIVIDAD = {
        "fecha_actividad": "2025-12-01",
        "hora_encuentro": "08:00",
        "punto_encuentro": "Sede DAGMA",
        "objetivo_actividad": "Limpieza",
        "lider_actividad": "Coord. Default",
        "telefono": "3001111111",  # coordinador (NO debe aparecer si hay lider_*)
    }

    def test_assignment_template_uses_lider_telefono_when_provided(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 0)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        monkeypatch.setattr(gs, "_log_notification", lambda *a, **k: None)
        captured = {}
        monkeypatch.setattr(
            gs, "_send_raw_email",
            lambda to, subject, html, **kw: captured.update(html=html) or True,
        )
        ok = gs.send_assignment_notification_email(
            person_email="juan@x.com",
            nombre="Juan",
            grupo="Acústica",
            actividad_data=self.ACTIVIDAD,
            lider_nombre="Líder Real",
            lider_telefono="3009999999",
        )
        assert ok is True
        html = captured["html"]
        assert "Líder Real" in html
        assert "3009999999" in html
        # No debe filtrarse el teléfono del coordinador cuando hay lider_telefono
        assert "3001111111" not in html

    def test_assignment_template_falls_back_to_actividad_when_no_lider(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 0)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        monkeypatch.setattr(gs, "_log_notification", lambda *a, **k: None)
        captured = {}
        monkeypatch.setattr(
            gs, "_send_raw_email",
            lambda to, subject, html, **kw: captured.update(html=html) or True,
        )
        ok = gs.send_assignment_notification_email(
            person_email="juan@x.com",
            nombre="Juan",
            grupo="Acústica",
            actividad_data=self.ACTIVIDAD,
            lider_nombre=None,
            lider_telefono=None,
        )
        assert ok is True
        html = captured["html"]
        assert "Coord. Default" in html
        assert "3001111111" in html

    def test_removal_template_uses_lider_telefono_when_provided(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 0)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        monkeypatch.setattr(gs, "_log_notification", lambda *a, **k: None)
        captured = {}
        monkeypatch.setattr(
            gs, "_send_raw_email",
            lambda to, subject, html, **kw: captured.update(html=html) or True,
        )
        ok = gs.send_removal_notification_email(
            person_email="juan@x.com",
            nombre="Juan",
            actividad_data=self.ACTIVIDAD,
            lider_nombre="Líder Real",
            lider_telefono="3009999999",
        )
        assert ok is True
        html = captured["html"]
        assert "Líder Real" in html
        assert "3009999999" in html
        assert "3001111111" not in html

    def test_send_assignment_passes_lider_to_template(self, monkeypatch):
        monkeypatch.setattr(gs, "_count_sent_last_24h", lambda: 0)
        monkeypatch.setattr(gs, "_maybe_alert_quota", lambda c: None)
        monkeypatch.setattr(gs, "_log_notification", lambda *a, **k: None)
        captured = {}

        def fake_send_raw(to, subject, html, ics_bytes=None, template=None):
            captured["html"] = html
            captured["to"] = to
            return True

        monkeypatch.setattr(gs, "_send_raw_email", fake_send_raw)
        ok = gs.send_assignment_notification_email(
            person_email="juan@x.com",
            nombre="Juan",
            grupo="Acústica",
            actividad_data=self.ACTIVIDAD,
            lider_nombre="Líder Real",
            lider_telefono="3009999999",
        )
        assert ok is True
        assert "3009999999" in captured["html"]
        assert "3001111111" not in captured["html"]
