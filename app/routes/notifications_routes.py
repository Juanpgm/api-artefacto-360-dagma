"""
Endpoints de notificaciones administrativas:
- POST /admin/notifications/broadcast  → anuncios masivos a una audiencia.
- GET  /admin/notifications/health     → estado de la configuración SMTP/Gmail + cuota.
- POST /admin/reports/weekly-attendance/run → cron-only (X-Cron-Token).
"""
from __future__ import annotations
import os
import time
import logging
from typing import Optional, List, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.utils.text_utils import grupos_match
from app.firebase_config import db
from app.deps.authz import require_min_role, require_role, CurrentUser
from app.models.roles import Role
from app.services.gmail_service import (
    send_broadcast_email,
    send_weekly_attendance_report_email,
    send_test_email,
    get_notifications_health,
)
from app.services.weekly_report_service import (
    get_last_week_range,
    aggregate_group_report,
    iter_grupos_lideres,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

class BroadcastRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message_html: str = Field(..., min_length=1)
    audience: str = Field(
        ...,
        description="all | lideres | grupo:<nombre> | uids:uid1,uid2 | emails:a@b.com,c@d.com",
    )
    priority: Literal["info", "warning", "urgent"] = "info"
    cta_url: Optional[str] = ""
    cta_label: Optional[str] = ""


def _resolve_audience(audience: str) -> List[tuple[str, str]]:
    """Devuelve lista de (email, nombre) según la audiencia solicitada."""
    audience = (audience or "").strip()
    out: list[tuple[str, str]] = []

    if audience == "all":
        for udoc in db.collection("users").stream():
            ud = udoc.to_dict() or {}
            email = (ud.get("email") or "").strip()
            if email and "@" in email:
                out.append((email, ud.get("full_name", "")))
    elif audience == "lideres":
        for udoc in db.collection("users").stream():
            ud = udoc.to_dict() or {}
            role = (ud.get("role") or ud.get("rol") or "").lower()
            if "lider" in role or "líder" in role:
                email = (ud.get("email") or "").strip()
                if email and "@" in email:
                    out.append((email, ud.get("full_name", "")))
    elif audience.startswith("grupo:"):
        grupo = audience.split(":", 1)[1]
        for udoc in db.collection("users").stream():
            ud = udoc.to_dict() or {}
            if grupos_match(ud.get("grupo"), grupo):
                email = (ud.get("email") or "").strip()
                if email and "@" in email:
                    out.append((email, ud.get("full_name", "")))
    elif audience.startswith("uids:"):
        for uid in audience.split(":", 1)[1].split(","):
            uid = uid.strip()
            if not uid:
                continue
            udoc = db.collection("users").document(uid).get()
            if udoc.exists:
                ud = udoc.to_dict() or {}
                email = (ud.get("email") or "").strip()
                if email and "@" in email:
                    out.append((email, ud.get("full_name", "")))
    elif audience.startswith("emails:"):
        for em in audience.split(":", 1)[1].split(","):
            em = em.strip()
            if em and "@" in em:
                out.append((em, ""))
    else:
        raise HTTPException(status_code=400, detail=f"Audiencia inválida: {audience}")
    return out


# Pausa entre envíos de un broadcast para no gatillar el rate-limit de Gmail
# (envíos masivos demasiado rápidos devuelven 429 y algunos correos no salen).
BROADCAST_PACING_SECONDS = float(os.getenv("BROADCAST_PACING_SECONDS", "0.4"))


def _do_broadcast(recipients: list[tuple[str, str]], subject: str,
                  message_html: str, priority: str, cta_url: str, cta_label: str) -> None:
    sent = 0
    errors: list[str] = []
    total = len(recipients)
    for idx, (email, _) in enumerate(recipients):
        try:
            ok = send_broadcast_email(
                to=email,
                subject=subject,
                message_html=message_html,
                priority=priority,
                cta_url=cta_url or "",
                cta_label=cta_label or "",
            )
            if ok:
                sent += 1
            else:
                errors.append(email)
        except Exception as e:
            errors.append(f"{email}: {e}")
        # Espaciar los envíos salvo en el último destinatario.
        if BROADCAST_PACING_SECONDS > 0 and idx < total - 1:
            time.sleep(BROADCAST_PACING_SECONDS)
    logger.info(f"[BROADCAST] subject={subject!r} sent={sent}/{total} errors={len(errors)}")


@router.post("/admin/notifications/broadcast",
             dependencies=[Depends(require_role(Role.DIRECTOR, Role.DESARROLLADOR))])
async def broadcast_announcement(
    body: BroadcastRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_role(Role.DIRECTOR, Role.DESARROLLADOR)),
):
    """Envía un anuncio genérico a una audiencia. Procesado en background."""
    recipients = _resolve_audience(body.audience)
    if not recipients:
        raise HTTPException(status_code=404, detail="No se encontraron destinatarios para la audiencia.")
    background_tasks.add_task(
        _do_broadcast,
        recipients,
        body.subject,
        body.message_html,
        body.priority,
        body.cta_url or "",
        body.cta_label or "",
    )
    return {
        "success": True,
        "audience": body.audience,
        "recipients_count": len(recipients),
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Health + Test
# ---------------------------------------------------------------------------

@router.get("/admin/notifications/health",
            dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def notifications_health():
    return get_notifications_health()


@router.post("/admin/notifications/test",
             dependencies=[Depends(require_min_role(Role.ADMINISTRADOR))])
async def notifications_test(to: Optional[EmailStr] = None,
                             current_user: CurrentUser = Depends(require_min_role(Role.ADMINISTRADOR))):
    target = (to or current_user.email or os.getenv("SMTP_USER", ""))
    if not target or "@" not in target:
        raise HTTPException(status_code=400, detail="No se pudo resolver destinatario para el test.")
    ok = send_test_email(target)
    return {"sent": ok, "to": target}


# ---------------------------------------------------------------------------
# Reporte semanal de asistencia — cron (X-Cron-Token)
# ---------------------------------------------------------------------------

def _verify_cron_token(token_header: Optional[str]) -> None:
    expected = os.getenv("REPORTS_CRON_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="REPORTS_CRON_TOKEN no configurado en el servidor.")
    if not token_header or token_header.strip() != expected:
        raise HTTPException(status_code=401, detail="X-Cron-Token inválido.")


def _run_weekly_attendance(dry_run: bool) -> dict:
    start_dt, end_dt, fmt_s, fmt_e = get_last_week_range()
    sent = 0
    skipped = 0
    errors: list[str] = []
    samples: list[dict] = []
    for grupo, lider_email, lider_nombre in iter_grupos_lideres():
        try:
            report = aggregate_group_report(grupo, start_dt, end_dt)
            if report["stats"]["actividades_total"] == 0:
                skipped += 1
                continue
            if dry_run:
                samples.append({
                    "grupo": grupo, "lider_email": lider_email,
                    "stats": report["stats"],
                    "actividades_count": len(report["actividades"]),
                })
                continue
            ok = send_weekly_attendance_report_email(
                leader_email=lider_email,
                leader_name=lider_nombre,
                grupo=grupo,
                period_start=fmt_s,
                period_end=fmt_e,
                stats=report["stats"],
                actividades=report["actividades"],
                inasistentes_top=report["inasistentes_top"],
            )
            if ok:
                sent += 1
            else:
                errors.append(f"{grupo}/{lider_email}: send returned False")
        except Exception as e:
            errors.append(f"{grupo}/{lider_email}: {e}")
            logger.exception(f"[WEEKLY] Error procesando grupo {grupo}")
    result = {
        "period_start": fmt_s, "period_end": fmt_e,
        "sent": sent, "skipped": skipped,
        "errors": errors, "dry_run": dry_run,
    }
    if dry_run:
        result["samples"] = samples
    logger.info(f"[WEEKLY] {result}")
    return result


@router.post("/admin/reports/weekly-attendance/run")
async def run_weekly_attendance_report(
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    x_cron_token: Optional[str] = Header(None, alias="X-Cron-Token"),
):
    """Ejecuta el reporte semanal. Protegido por X-Cron-Token (no requiere JWT)."""
    _verify_cron_token(x_cron_token)
    if dry_run:
        # Síncrono para devolver muestras
        return _run_weekly_attendance(dry_run=True)
    background_tasks.add_task(_run_weekly_attendance, False)
    return {"status": "accepted", "dry_run": False}
