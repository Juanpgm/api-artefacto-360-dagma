"""
Diagnóstico OFFLINE de la estructura de los correos DAGMA.

No envía nada ni requiere credenciales: construye un mensaje real con el mismo
código que producción (`_build_mime_message`) y verifica las señales de
entregabilidad que mantienen el correo fuera de spam.

Uso:
    python scripts/diagnose_email_message.py
"""
import os
import sys

# Permitir ejecutar desde la carpeta back/ sin instalar el paquete.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub de firebase para no requerir credenciales al importar el servicio.
import types  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

if "app.firebase_config" not in sys.modules:
    _fb = types.ModuleType("app.firebase_config")
    _fb.db = MagicMock()
    _fb.auth_client = MagicMock()
    sys.modules["app.firebase_config"] = _fb

from app.services import gmail_service as gs  # noqa: E402

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = f"{GREEN}OK{RESET}" if ok else f"{RED}FALTA{RESET}"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    sender = os.getenv("GMAIL_SENDER") or os.getenv("SMTP_USER", "dagma.notificaciones@gmail.com")
    html = (
        "<p>Hola <strong>Ana</strong>, se te asigno una actividad ambiental.</p>"
        '<p>Visita <a href="https://dagma-360-capture-frontend.vercel.app">el portal</a>.</p>"'
    )

    print(f"\nRemitente (From): {sender}")
    print("Construyendo mensaje de notificacion con adjunto .ics...\n")

    msg = gs._build_mime_message(
        sender=sender,
        sender_name=os.getenv("SMTP_SENDER_NAME", "DAGMA Artefacto 360"),
        to="destinatario@example.com",
        subject="Asignacion Actividad Ambiental DAGMA — 2026-07-01",
        html_body=html,
        ics_bytes=b"BEGIN:VCALENDAR\nEND:VCALENDAR",
        list_unsubscribe=f"<mailto:{sender}?subject=unsubscribe>",
    )

    content_types = [p.get_content_type() for p in msg.walk()]
    print("Estructura MIME:")
    for ct in content_types:
        print(f"  - {ct}")
    print()

    print("Checklist de entregabilidad:")
    ok = True
    ok &= _check("Parte text/plain presente", "text/plain" in content_types)
    ok &= _check("Parte text/html presente", "text/html" in content_types)
    ok &= _check("Adjunto .ics (text/calendar) presente", "text/calendar" in content_types)
    ok &= _check("Header Date", bool(msg["Date"]), msg["Date"] or "")
    ok &= _check("Header Message-ID", bool(msg["Message-ID"]), msg["Message-ID"] or "")
    ok &= _check("Header From con display name", "<" in (msg["From"] or ""), msg["From"] or "")
    ok &= _check("Header List-Unsubscribe (masivos)", bool(msg["List-Unsubscribe"]), msg["List-Unsubscribe"] or "")

    print("\nDependientes de DNS (verificar en el dominio remitente, NO en codigo):")
    domain = sender.split("@")[-1]
    print(f"  - SPF:   dig TXT {domain}            -> debe incluir el servidor de envio")
    print(f"  - DKIM:  el proveedor debe firmar con la clave del dominio {domain}")
    print(f"  - DMARC: dig TXT _dmarc.{domain}     -> p=quarantine/reject con alineacion")

    print()
    if ok:
        print(f"{GREEN}Mensaje correctamente formado para entregabilidad.{RESET}")
        return 0
    print(f"{RED}Faltan señales de entregabilidad — revisar arriba.{RESET}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
