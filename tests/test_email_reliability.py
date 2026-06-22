"""
Tests for email reliability improvements in back/app/services/gmail_service.py.

Covers:
- Quota bypass via critical=True flag
- Retry with exponential backoff in _send_raw_email
- Permanent 4xx HttpError short-circuits retry
- Transient 5xx HttpError retries
- Deduplication pattern (setdefault)
- _recuperar_email_persona: email recovery from Firestore collections
"""
import asyncio
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Minimal env setup — must happen before the module is imported.
# Override EMAIL_DAILY_QUOTA to 100 so thresholds are easy to reason about.
# ---------------------------------------------------------------------------
os.environ["SMTP_USER"] = "test@example.com"
os.environ["SMTP_PASSWORD"] = "x"
os.environ["SMTP_HOST"] = "smtp.example.com"
os.environ["SMTP_PORT"] = "587"
os.environ["EMAIL_DAILY_QUOTA"] = "100"
os.environ["EMAIL_QUOTA_WARN"] = "0.80"
os.environ["EMAIL_QUOTA_BLOCK"] = "0.95"

# Mock firebase_config before the service module is loaded.
if "app.firebase_config" not in sys.modules:
    _fake_fb = types.ModuleType("app.firebase_config")
    _fake_fb.db = MagicMock()
    _fake_fb.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = _fake_fb

from app.services import gmail_service as gs  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_error(status: int) -> HttpError:
    """Build a googleapiclient HttpError with the given HTTP status code."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"Error")


# ---------------------------------------------------------------------------
# TestQuotaBypass
# ---------------------------------------------------------------------------

class TestQuotaBypass:
    """Verify that critical=False respects the quota block and critical=True bypasses it."""

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_gmail_api")
    @patch("app.services.gmail_service._send_via_smtp")
    @patch("app.services.gmail_service._count_sent_last_24h", return_value=96)
    @patch("app.services.gmail_service.DAILY_EMAIL_QUOTA", 100)
    def test_critical_false_is_blocked_at_95_percent(
        self, mock_count, mock_smtp, mock_gmail_api, mock_log, mock_db
    ):
        """count=96 >= 95% of 100 → non-critical email must be blocked."""
        result = gs._send_email(
            to="user@example.com",
            subject="Test",
            html_body="<p>Hi</p>",
            template="test_template",
            critical=False,
        )

        assert result is False
        mock_gmail_api.assert_not_called()
        mock_smtp.assert_not_called()
        mock_log.assert_called_once()
        _args = mock_log.call_args[0]
        assert _args[3] == "blocked_quota"

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_gmail_api", return_value=False)
    @patch("app.services.gmail_service._send_via_smtp", return_value=True)
    @patch("app.services.gmail_service._count_sent_last_24h", return_value=96)
    @patch("app.services.gmail_service.DAILY_EMAIL_QUOTA", 100)
    @patch("app.services.gmail_service.time")
    def test_critical_true_bypasses_quota_block(
        self, mock_time, mock_count, mock_smtp, mock_gmail_api, mock_log, mock_db
    ):
        """count=96 but critical=True → email must be sent regardless of quota."""
        result = gs._send_email(
            to="user@example.com",
            subject="Test Critical",
            html_body="<p>Critical</p>",
            template="test_critical",
            critical=True,
        )

        assert result is True
        # Transport must have been called at some point.
        assert mock_smtp.called or mock_gmail_api.called
        # Must never log 'blocked_quota'.
        for c in mock_log.call_args_list:
            status_arg = c[0][3] if len(c[0]) > 3 else c[1].get("status", "")
            assert status_arg != "blocked_quota", "Critical email was incorrectly blocked by quota"

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_gmail_api", return_value=False)
    @patch("app.services.gmail_service._send_via_smtp", return_value=True)
    @patch("app.services.gmail_service._count_sent_last_24h", return_value=96)
    @patch("app.services.gmail_service.DAILY_EMAIL_QUOTA", 100)
    @patch("app.services.gmail_service.time")
    def test_cancellation_email_uses_critical_true(
        self, mock_time, mock_count, mock_smtp, mock_gmail_api, mock_log, mock_db
    ):
        """send_activity_cancellation_email must reach transport even when quota is exhausted."""
        actividad_data = {
            "fecha_actividad": "2026-07-01",
            "nombre_actividad": "Limpieza Río Cali",
            "hora_inicio": "08:00",
            "hora_fin": "12:00",
            "lugar": "Río Cali",
            "grupo": "GrupoA",
            "lider_actividad": "Carlos",
            "telefono": "3001234567",
            "descripcion": "Jornada de limpieza",
        }

        result = gs.send_activity_cancellation_email(
            to_email="participant@example.com",
            nombre="María",
            actividad_data=actividad_data,
        )

        assert result is True
        # Transport must have been reached.
        assert mock_smtp.called or mock_gmail_api.called
        # Must NOT log 'blocked_quota'.
        for c in mock_log.call_args_list:
            status_arg = c[0][3] if len(c[0]) > 3 else c[1].get("status", "")
            assert status_arg != "blocked_quota"


# ---------------------------------------------------------------------------
# TestRetryBackoff
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    """Verify exponential-backoff retry behaviour in _send_raw_email."""

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_smtp", return_value=False)
    @patch("app.services.gmail_service._send_via_gmail_api", side_effect=[False, False, True])
    @patch("app.services.gmail_service.time")
    @patch.dict(os.environ, {
        "GMAIL_CLIENT_ID": "fake-id",
        "GMAIL_CLIENT_SECRET": "fake-secret",
        "GMAIL_REFRESH_TOKEN": "fake-token",
    })
    def test_retry_succeeds_on_third_attempt(
        self, mock_time, mock_gmail_api, mock_smtp, mock_log, mock_db
    ):
        """Gmail API fails twice then succeeds — function must succeed after 3 attempts."""
        result = gs._send_raw_email(
            to="user@example.com",
            subject="Retry test",
            html_body="<p>Hi</p>",
            template="retry_test",
            max_attempts=3,
        )

        assert result is True
        assert mock_gmail_api.call_count == 3
        # sleep must be called between attempts (2 times for 3 attempts).
        assert mock_time.sleep.call_count == 2
        sleep_calls = [c[0][0] for c in mock_time.sleep.call_args_list]
        assert sleep_calls[0] == 1  # 2**0 = 1
        assert sleep_calls[1] == 2  # 2**1 = 2
        # _log_notification called exactly once, at the end.
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] == "sent"

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_smtp", return_value=False)
    @patch("app.services.gmail_service._send_via_gmail_api", return_value=False)
    @patch("app.services.gmail_service.time")
    def test_all_attempts_fail_logs_failed(
        self, mock_time, mock_gmail_api, mock_smtp, mock_log, mock_db
    ):
        """All 3 attempts fail → return False, log once as 'failed', sleep twice."""
        result = gs._send_raw_email(
            to="user@example.com",
            subject="All fail",
            html_body="<p>Hi</p>",
            template="fail_test",
            max_attempts=3,
        )

        assert result is False
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] == "failed"
        assert mock_time.sleep.call_count == 2

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_smtp", return_value=False)
    @patch("app.services.gmail_service._send_via_gmail_api",
           side_effect=lambda msg, to: (_ for _ in ()).throw(_make_http_error(400)))
    @patch("app.services.gmail_service.time")
    @patch.dict(os.environ, {
        "GMAIL_CLIENT_ID": "fake-id",
        "GMAIL_CLIENT_SECRET": "fake-secret",
        "GMAIL_REFRESH_TOKEN": "fake-token",
    })
    def test_no_retry_on_permanent_4xx_http_error(
        self, mock_time, mock_gmail_api, mock_smtp, mock_log, mock_db
    ):
        """HttpError 400 is a permanent client error — must not retry."""
        result = gs._send_raw_email(
            to="user@example.com",
            subject="4xx test",
            html_body="<p>Hi</p>",
            template="perm_fail",
            max_attempts=3,
        )

        assert result is False
        # Must only call Gmail API once — no retry.
        assert mock_gmail_api.call_count == 1
        # Must never sleep.
        mock_time.sleep.assert_not_called()
        # Must log exactly once as 'failed'.
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] == "failed"

    @patch("app.services.gmail_service._get_db", return_value=None)
    @patch("app.services.gmail_service._log_notification")
    @patch("app.services.gmail_service._send_via_smtp", return_value=False)
    @patch("app.services.gmail_service.time")
    @patch.dict(os.environ, {
        "GMAIL_CLIENT_ID": "fake-id",
        "GMAIL_CLIENT_SECRET": "fake-secret",
        "GMAIL_REFRESH_TOKEN": "fake-token",
    })
    def test_retry_on_500_http_error(
        self, mock_time, mock_smtp, mock_log, mock_db
    ):
        """HttpError 500 is transient — must retry and succeed on the third call."""
        err_500 = _make_http_error(500)
        call_results = [err_500, err_500, True]

        def _gmail_side_effect(msg, to):
            val = call_results.pop(0)
            if isinstance(val, Exception):
                raise val
            return val

        with patch(
            "app.services.gmail_service._send_via_gmail_api",
            side_effect=_gmail_side_effect,
        ) as mock_gmail_api:
            result = gs._send_raw_email(
                to="user@example.com",
                subject="500 retry test",
                html_body="<p>Hi</p>",
                template="server_error",
                max_attempts=3,
            )

        assert result is True
        assert mock_gmail_api.call_count == 3
        assert mock_time.sleep.call_count == 2
        mock_log.assert_called_once()
        assert mock_log.call_args[0][3] == "sent"


# ---------------------------------------------------------------------------
# TestRecipientEmailRecovery
# ---------------------------------------------------------------------------

class TestRecipientEmailRecovery:
    """Tests for _recuperar_email_persona in artefacto_360_routes.

    The function is async and uses stream_to_list + db.collection.
    We patch stream_to_list at the routes module level and run the coroutine
    synchronously with asyncio.run().
    """

    @staticmethod
    def _user_doc(nombre: str, email: str) -> MagicMock:
        doc = MagicMock()
        doc.id = nombre.lower().replace(" ", "_")
        doc.to_dict.return_value = {"full_name": nombre, "email": email, "role": "operador"}
        return doc

    @staticmethod
    def _po_doc(nombre: str, email: str) -> MagicMock:
        doc = MagicMock()
        doc.to_dict.return_value = {"nombre_completo": nombre, "email": email}
        return doc

    def test_valid_email_returns_immediately(self):
        """A valid email_actual is returned without any Firestore query."""
        from app.routes.artefacto_360_routes import _recuperar_email_persona

        with patch("app.routes.artefacto_360_routes.stream_to_list") as mock_stream:
            result = asyncio.run(_recuperar_email_persona("Juan Pérez", "juan@example.com"))

        assert result == "juan@example.com"
        mock_stream.assert_not_called()

    def test_recovers_from_users_collection(self):
        """When email_actual is empty, email is found in the `users` collection by name."""
        from app.routes.artefacto_360_routes import _recuperar_email_persona

        user_doc = self._user_doc("Ana García", "ana.garcia@example.com")

        async def fake_stream(query):
            return [user_doc]

        with patch("app.routes.artefacto_360_routes.stream_to_list", side_effect=fake_stream):
            result = asyncio.run(_recuperar_email_persona("Ana García", ""))

        assert result == "ana.garcia@example.com"

    def test_recovers_from_personal_operativo(self):
        """Falls back to `personal_operativo` when name is not found in `users`."""
        from app.routes.artefacto_360_routes import _recuperar_email_persona

        po_doc = self._po_doc("Carlos Ruiz", "carlos.ruiz@example.com")
        call_count = {"n": 0}

        async def fake_stream(query):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return []  # users query: not found
            return [po_doc]  # personal_operativo query

        with patch("app.routes.artefacto_360_routes.stream_to_list", side_effect=fake_stream):
            result = asyncio.run(_recuperar_email_persona("Carlos Ruiz", ""))

        assert result == "carlos.ruiz@example.com"
        assert call_count["n"] == 2

    def test_returns_none_when_not_found(self):
        """Returns None when the name is not found in either collection."""
        from app.routes.artefacto_360_routes import _recuperar_email_persona

        async def fake_stream(query):
            return []

        with patch("app.routes.artefacto_360_routes.stream_to_list", side_effect=fake_stream):
            result = asyncio.run(_recuperar_email_persona("Desconocido Total", ""))

        assert result is None


# ---------------------------------------------------------------------------
# TestDeduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Pure logic test for the setdefault deduplication pattern."""

    def test_duplicate_email_appears_once_in_recipient_dict(self):
        """setdefault must preserve the first insertion and ignore subsequent ones."""
        recipients = {}
        email = "test@example.com"
        recipients.setdefault(email.lower(), "Person A")
        recipients.setdefault(email.lower(), "Person B")  # must NOT overwrite

        assert len(recipients) == 1
        assert recipients[email.lower()] == "Person A"
