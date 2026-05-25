"""
Servicio agregador para el reporte semanal ejecutivo de asistencia por líder de grupo.

Cruza `plan_distrito_verde` (actividades programadas en el rango) con
`asistencia_actividades` (registro de asistencia) y agrupa por grupo/líder.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

from app.firebase_config import db
from app.utils.text_utils import grupos_match
logger = logging.getLogger(__name__)

TZ_COL = pytz.timezone("America/Bogota")


def get_last_week_range(now: Optional[datetime] = None) -> tuple[datetime, datetime, str, str]:
    """Devuelve (start_dt, end_dt, fmt_start, fmt_end) cubriendo la semana anterior
    en zona horaria America/Bogota. Semana = lunes 00:00 → domingo 23:59:59.
    """
    now = now or datetime.now(TZ_COL)
    # Lunes de esta semana
    monday_this = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    monday_prev = monday_this - timedelta(days=7)
    sunday_prev = monday_this - timedelta(seconds=1)
    return (
        monday_prev,
        sunday_prev,
        monday_prev.strftime("%d/%m/%Y"),
        sunday_prev.strftime("%d/%m/%Y"),
    )


def _parse_fecha_actividad(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return TZ_COL.localize(datetime.strptime(raw, fmt))
        except Exception:
            continue
    return None


def aggregate_group_report(grupo_nombre: str, start_dt: datetime, end_dt: datetime) -> dict:
    """Calcula stats + detalle por actividad + top inasistentes para un grupo en el rango."""
    actividades_rows: list[dict] = []
    inasistencias_por_persona: dict[str, dict] = {}
    convocados_total = 0
    asistentes_total = 0
    actividades_total = 0
    actividades_realizadas = 0

    # Cargar actividades del rango
    try:
        for act_doc in db.collection("plan_distrito_verde").stream():
            act = act_doc.to_dict() or {}
            fa = _parse_fecha_actividad(act.get("fecha_actividad", ""))
            if fa is None or fa < start_dt or fa > end_dt:
                continue
            grupos_req = act.get("grupos_requeridos") or []
            if grupos_req and grupo_nombre and grupo_nombre not in grupos_req:
                # filtrar a actividades donde el grupo está convocado (si grupos_req vacío, asumir todos)
                continue
            actividades_total += 1
            personal_asignado = act.get("personal_asignado") or []
            # convocados del grupo
            convocados_grupo = [
                p for p in personal_asignado
                if grupos_match(p.get("grupo"), grupo_nombre)
            ]
            convocados_n = len(convocados_grupo)
            convocados_total += convocados_n

            # buscar asistencia
            asistentes_n = 0
            try:
                asist_q = db.collection("asistencia_actividades").where(
                    "actividad_id", "==", act.get("id", act_doc.id)
                )
                for asd in asist_q.stream():
                    ad = asd.to_dict() or {}
                    asistencia = ad.get("asistencia") or ad.get("asistentes") or []
                    for entry in asistencia:
                        if not isinstance(entry, dict):
                            continue
                        if not grupos_match(entry.get("grupo"), grupo_nombre):
                            continue
                        status = (entry.get("estado") or entry.get("status") or "").lower()
                        if status in ("asistio", "asistió", "presente", "ok"):
                            asistentes_n += 1
                        else:
                            nombre = entry.get("nombre_completo") or entry.get("nombre") or entry.get("email") or "—"
                            key = (entry.get("email") or nombre).lower()
                            rec = inasistencias_por_persona.setdefault(key, {
                                "nombre": nombre, "convocatorias": 0, "inasistencias": 0,
                            })
                            rec["inasistencias"] += 1
            except Exception as e:
                logger.warning(f"[REPORTE] Error leyendo asistencia de {act.get('id')}: {e}")

            # contar convocatorias por persona
            for p in convocados_grupo:
                nombre = p.get("nombre_completo") or p.get("nombre") or p.get("email") or "—"
                key = (p.get("email") or nombre).lower()
                rec = inasistencias_por_persona.setdefault(key, {
                    "nombre": nombre, "convocatorias": 0, "inasistencias": 0,
                })
                rec["convocatorias"] += 1

            asistentes_total += asistentes_n
            if asistentes_n > 0:
                actividades_realizadas += 1
            pct = round((asistentes_n / convocados_n) * 100, 1) if convocados_n else 0
            actividades_rows.append({
                "fecha": act.get("fecha_actividad", ""),
                "objetivo": act.get("objetivo_actividad", "")[:80],
                "convocados": convocados_n,
                "asistentes": asistentes_n,
                "porcentaje": pct,
            })
    except Exception as e:
        logger.error(f"[REPORTE] Error agregando actividades para grupo {grupo_nombre}: {e}")

    pct_global = round((asistentes_total / convocados_total) * 100, 1) if convocados_total else 0
    inasistentes_top = sorted(
        [v for v in inasistencias_por_persona.values() if v["inasistencias"] > 0],
        key=lambda x: x["inasistencias"],
        reverse=True,
    )[:5]
    inasistencias_total = sum(v["inasistencias"] for v in inasistencias_por_persona.values())

    return {
        "stats": {
            "actividades_total": actividades_total,
            "actividades_realizadas": actividades_realizadas,
            "personal_total": len(inasistencias_por_persona),
            "convocados_total": convocados_total,
            "asistentes_total": asistentes_total,
            "porcentaje_asistencia": pct_global,
            "inasistencias_total": inasistencias_total,
        },
        "actividades": actividades_rows,
        "inasistentes_top": inasistentes_top,
    }


def iter_grupos_lideres():
    """Itera (grupo_nombre, lider_email, lider_nombre) para todos los grupos con líder válido."""
    for gdoc in db.collection("grupos").stream():
        gd = gdoc.to_dict() or {}
        grupo_nombre = (gd.get("nombre") or gd.get("grupo") or gdoc.id or "").strip()
        lider_raw = gd.get("lider")
        if isinstance(lider_raw, dict):
            email = (lider_raw.get("email") or "").strip()
            nombre = (lider_raw.get("nombre") or "").strip()
        elif isinstance(lider_raw, str):
            s = lider_raw.strip()
            if "@" in s:
                email, nombre = s, ""
            else:
                email, nombre = "", s
        else:
            email, nombre = "", ""
        if not email:
            email = (gd.get("email") or "").strip()
        if not nombre:
            nombre = (gd.get("nombre_lider") or "").strip()
        if grupo_nombre and email and "@" in email:
            yield grupo_nombre, email, nombre
