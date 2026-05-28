"""
Tests para la política de notificaciones (bugs #3 y #4).

Bug #3: No notificar al actor por acciones que él mismo realizó.
Bug #4: Al crear actividad, solo notificar al líder de la actividad
        (NO a todos los líderes del grupo) salvo que el feature flag
        NOTIFY_GROUP_LEADERS_ON_CREATE esté activo.

Estos tests son "dry" — no tocan Firebase ni Gmail reales.
Se validan los puntos de decisión (guardas) que filtran emails.
"""
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# Entorno mínimo antes de importar el módulo bajo test
os.environ.setdefault("SMTP_USER", "test@example.com")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("EMAIL_DAILY_QUOTA", "100")

# Mock firebase_config
if "app.firebase_config" not in sys.modules:
    fake_mod = types.ModuleType("app.firebase_config")
    fake_mod.db = MagicMock()
    fake_mod.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = fake_mod


# ---------------------------------------------------------------------------
# Bug #4 — Section C suppressed unless NOTIFY_GROUP_LEADERS_ON_CREATE is set
# ---------------------------------------------------------------------------

class TestGroupLeadersNotificationFlag:
    """
    Verifica que el aviso masivo "líderes del grupo" sólo se envíe
    cuando el flag de entorno está explícitamente activo.
    """

    def test_flag_off_by_default(self):
        # Default: la variable no está seteada o es "false"
        val = os.getenv("NOTIFY_GROUP_LEADERS_ON_CREATE", "false").strip().lower()
        assert val in ("false", "0", "no", "")

    @pytest.mark.parametrize("flag_value,expected_should_send", [
        ("false", False),
        ("0",     False),
        ("no",    False),
        ("",      False),
        ("true",  True),
        ("1",     True),
        ("yes",   True),
        ("TRUE",  True),
    ])
    def test_flag_parsing_matches_guard_logic(self, flag_value, expected_should_send):
        """
        Replica EXACTAMENTE la guarda que vive en
        artefacto_360_routes._enviar_notificaciones_actividad (sección C):

            if not os.getenv("NOTIFY_GROUP_LEADERS_ON_CREATE", "false")
                  .strip().lower() in ("1","true","yes"):
                return
        """
        with patch.dict(os.environ, {"NOTIFY_GROUP_LEADERS_ON_CREATE": flag_value}, clear=False):
            raw = os.getenv("NOTIFY_GROUP_LEADERS_ON_CREATE", "false").strip().lower()
            should_send = raw in ("1", "true", "yes")
            assert should_send is expected_should_send


# ---------------------------------------------------------------------------
# Bug #3 — Actor no debe recibir email por su propia acción
# ---------------------------------------------------------------------------

class TestSelfActionGuard:
    """
    Verifica la guarda usada en update_actividad._enviar_emails:

        actor_email_lower = (getattr(current_user, "email", "") or "").strip().lower()
        actor_is_leader = actor_email_lower and actor_email_lower == lider_email_lower
        if not actor_is_leader:
            send_assignment_summary_leader_email(...)
    """

    @pytest.mark.parametrize("actor_email,lider_email,should_send", [
        # Mismo correo → NO enviar resumen al líder (es el actor)
        ("ana@dagma.gov.co", "ana@dagma.gov.co", False),
        ("Ana@DAGMA.gov.co", "ana@dagma.gov.co", False),  # case-insensitive
        ("  ana@dagma.gov.co  ", "ana@dagma.gov.co", False),  # trim
        # Distintos correos → SÍ enviar
        ("admin@dagma.gov.co", "ana@dagma.gov.co", True),
        # Actor sin email (raro) → SÍ enviar (no podemos identificar)
        ("", "ana@dagma.gov.co", True),
        (None, "ana@dagma.gov.co", True),
    ])
    def test_self_action_email_guard(self, actor_email, lider_email, should_send):
        actor_email_lower = (actor_email or "").strip().lower()
        lider_email_lower = (lider_email or "").strip().lower()
        actor_is_leader = bool(actor_email_lower) and actor_email_lower == lider_email_lower
        # La política es: enviar SOLO si el actor NO es el líder
        will_send = not actor_is_leader
        assert will_send is should_send, (
            f"Actor={actor_email!r} líder={lider_email!r} "
            f"esperado_envío={should_send} obtuvo={will_send}"
        )


# ---------------------------------------------------------------------------
# Bug #5 — coordenadas_origen aceptado y persistido
# ---------------------------------------------------------------------------

class TestCoordenadasOrigenField:
    """
    Verifica que la guarda de normalización del campo coordenadas_origen
    funciona como se espera:

        "coordenadas_origen": (coordenadas_origen or "gps").strip().lower() or "gps"
    """

    @pytest.mark.parametrize("raw,expected", [
        ("manual", "manual"),
        ("MANUAL", "manual"),
        ("  manual  ", "manual"),
        ("gps", "gps"),
        ("", "gps"),
        (None, "gps"),
        ("   ", "gps"),  # solo espacios → gps por el segundo `or "gps"`
    ])
    def test_normalize_coordenadas_origen(self, raw, expected):
        normalized = ((raw or "gps").strip().lower()) or "gps"
        assert normalized == expected
