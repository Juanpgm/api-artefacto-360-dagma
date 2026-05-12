"""
Smoke test contra el backend en producción (Railway).

Uso:
    set BACKEND_URL=https://web-production-2d737.up.railway.app
    set CRON_TOKEN=...
    set ADMIN_TOKEN=<id token Firebase de un admin>
    python verify_prod_emails.py
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

BACKEND = os.environ.get("BACKEND_URL", "https://web-production-2d737.up.railway.app").rstrip("/")
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def _req(method: str, path: str, headers: dict = None, body: dict = None) -> tuple[int, str]:
    url = BACKEND + path
    data = None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check_health():
    print(f"\n[1/3] GET /admin/notifications/health (admin token)")
    if not ADMIN_TOKEN:
        print("  ⚠ ADMIN_TOKEN no configurado — saltando.")
        return
    code, body = _req("GET", "/admin/notifications/health",
                      headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    print(f"  HTTP {code}  body={body[:300]}")


def check_test_email():
    print(f"\n[2/3] POST /admin/notifications/test")
    if not ADMIN_TOKEN:
        print("  ⚠ ADMIN_TOKEN no configurado — saltando.")
        return
    code, body = _req("POST", "/admin/notifications/test",
                      headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    print(f"  HTTP {code}  body={body[:300]}")


def check_weekly_dryrun():
    print(f"\n[3/3] POST /admin/reports/weekly-attendance/run?dry_run=true (cron token)")
    if not CRON_TOKEN:
        print("  ⚠ CRON_TOKEN no configurado — saltando.")
        return
    code, body = _req("POST", "/admin/reports/weekly-attendance/run?dry_run=true",
                      headers={"X-Cron-Token": CRON_TOKEN})
    print(f"  HTTP {code}  body={body[:600]}")


if __name__ == "__main__":
    print(f"Backend: {BACKEND}")
    check_health()
    check_test_email()
    check_weekly_dryrun()
