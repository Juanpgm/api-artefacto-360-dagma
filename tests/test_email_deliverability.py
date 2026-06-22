"""
Tests for email deliverability / anti-spam hardening in
back/app/services/gmail_service.py.

These tests pin the MIME structure and headers that keep DAGMA notifications
out of the spam folder:

- Every message MUST carry a text/plain alternative alongside text/html
  (HTML-only mail is a strong spam signal for Gmail/Outlook).
- Every message MUST carry Date and Message-ID headers (smtplib does NOT add
  them automatically; their absence is a classic spam heuristic).
- Bulk/broadcast mail MUST carry a List-Unsubscribe header (required by the
  Gmail/Yahoo 2024 bulk-sender rules).
- The iCal attachment must still ride along on activity emails, now nested
  inside multipart/mixed -> multipart/alternative.
"""
import os
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Minimal env + firebase stub before importing the service module.
# ---------------------------------------------------------------------------
os.environ.setdefault("SMTP_USER", "dagma.notificaciones@gmail.com")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("SMTP_HOST", "smtp.gmail.com")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_SENDER_NAME", "DAGMA Artefacto 360")

if "app.firebase_config" not in sys.modules:
    _fake_fb = types.ModuleType("app.firebase_config")
    _fake_fb.db = MagicMock()
    _fake_fb.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = _fake_fb

from app.services import gmail_service as gs  # noqa: E402


def _walk_parts(msg):
    return [p.get_content_type() for p in msg.walk()]


class TestMimeAlternative:
    """The message must always offer a text/plain alternative to text/html."""

    def test_plain_and_html_parts_present_without_ics(self):
        msg = gs._build_mime_message(
            sender="dagma.notificaciones@gmail.com",
            sender_name="DAGMA Artefacto 360",
            to="user@example.com",
            subject="Asunto de prueba",
            html_body="<p>Hola <strong>mundo</strong></p>",
        )
        types_ = _walk_parts(msg)
        assert "text/plain" in types_, f"missing text/plain, got {types_}"
        assert "text/html" in types_, f"missing text/html, got {types_}"

    def test_plain_text_is_derived_from_html(self):
        msg = gs._build_mime_message(
            sender="s@example.com",
            sender_name="DAGMA",
            to="user@example.com",
            subject="Asunto",
            html_body="<p>Hola <strong>Ana</strong>, ten un buen dia</p>",
        )
        plain = None
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                plain = part.get_payload(decode=True).decode("utf-8")
        assert plain is not None
        assert "Hola" in plain and "Ana" in plain
        # Tags must be stripped from the plain part.
        assert "<" not in plain and ">" not in plain

    def test_ics_attachment_preserved_with_alternative(self):
        msg = gs._build_mime_message(
            sender="s@example.com",
            sender_name="DAGMA",
            to="user@example.com",
            subject="Actividad",
            html_body="<p>Detalles</p>",
            ics_bytes=b"BEGIN:VCALENDAR\nEND:VCALENDAR",
        )
        types_ = _walk_parts(msg)
        assert "text/plain" in types_
        assert "text/html" in types_
        assert "text/calendar" in types_
        assert msg.get_content_type() == "multipart/mixed"


class TestDeliverabilityHeaders:
    """Date and Message-ID must always be present; List-Unsubscribe when asked."""

    def test_date_and_message_id_present(self):
        msg = gs._build_mime_message(
            sender="dagma.notificaciones@gmail.com",
            sender_name="DAGMA",
            to="user@example.com",
            subject="Asunto",
            html_body="<p>x</p>",
        )
        assert msg["Date"], "Date header missing"
        assert msg["Message-ID"], "Message-ID header missing"
        # Message-ID domain should track the sender domain.
        assert "gmail.com" in msg["Message-ID"]

    def test_list_unsubscribe_absent_by_default(self):
        msg = gs._build_mime_message(
            sender="s@example.com",
            sender_name="DAGMA",
            to="user@example.com",
            subject="Asunto",
            html_body="<p>x</p>",
        )
        assert msg["List-Unsubscribe"] is None

    def test_list_unsubscribe_present_when_requested(self):
        msg = gs._build_mime_message(
            sender="s@example.com",
            sender_name="DAGMA",
            to="user@example.com",
            subject="Asunto",
            html_body="<p>x</p>",
            list_unsubscribe="<mailto:s@example.com?subject=unsubscribe>",
        )
        assert msg["List-Unsubscribe"] == "<mailto:s@example.com?subject=unsubscribe>"
        # One-Click post header should accompany it.
        assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


class TestHtmlToText:
    """The HTML->text helper must produce readable plain text."""

    def test_strips_tags_and_keeps_links(self):
        text = gs._html_to_text(
            '<p>Visita <a href="https://dagma.gov.co">el portal</a> hoy.</p>'
        )
        assert "Visita" in text
        assert "el portal" in text
        assert "https://dagma.gov.co" in text  # link target should survive
        assert "<a" not in text
