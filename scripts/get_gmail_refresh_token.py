"""
Obtiene un refresh_token de Gmail API para uso en backend (Railway).

USO:
  1. En Google Cloud Console (proyecto dagma-85aad):
     - Habilita "Gmail API"
     - APIs & Services → Credentials → Create OAuth Client ID
       - Application type: "Desktop app"
       - Name: "DAGMA 360 Mailer"
     - Descarga el JSON y guárdalo como `back/scripts/oauth_client.json`
       (o pasa --client-secrets <ruta>)
  2. Ejecuta:
       cd back
       .\env\Scripts\python.exe scripts\get_gmail_refresh_token.py
     (abrirá un navegador, autoriza con la cuenta que enviará los emails:
      notificaciones.centraloperativa@gmail.com)
  3. El script imprime los 3 valores que debes poner en Railway:
       GMAIL_CLIENT_ID
       GMAIL_CLIENT_SECRET
       GMAIL_REFRESH_TOKEN
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Falta dependencia: pip install google-auth-oauthlib", file=sys.stderr)
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-secrets",
        default=str(Path(__file__).parent / "oauth_client.json"),
        help="Ruta al JSON de OAuth Client ID descargado de Google Cloud",
    )
    parser.add_argument("--port", type=int, default=0, help="Puerto local (0=auto)")
    args = parser.parse_args()

    secrets_path = Path(args.client_secrets)
    if not secrets_path.exists():
        print(f"ERROR: no existe {secrets_path}", file=sys.stderr)
        print("Descarga el OAuth Client JSON y guárdalo ahí.", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(
        port=args.port,
        prompt="consent",  # fuerza emitir refresh_token
        access_type="offline",
    )

    if not creds.refresh_token:
        print("ERROR: no se obtuvo refresh_token. Reintenta con prompt=consent.", file=sys.stderr)
        return 2

    with secrets_path.open("r", encoding="utf-8") as f:
        client = json.load(f)
    block = client.get("installed") or client.get("web") or {}
    client_id = block.get("client_id", "")
    client_secret = block.get("client_secret", "")

    print()
    print("=" * 70)
    print("ÉXITO — configura estas 3 variables en Railway (servicio web):")
    print("=" * 70)
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 70)
    print()
    print("Comando para Railway CLI (copiar/pegar):")
    print(
        f'railway variables --service web '
        f'--set "GMAIL_CLIENT_ID={client_id}" '
        f'--set "GMAIL_CLIENT_SECRET={client_secret}" '
        f'--set "GMAIL_REFRESH_TOKEN={creds.refresh_token}"'
    )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
