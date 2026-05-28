"""
email_policy_dryrun.py — Simulación local de la nueva política de notificaciones.

NO envía emails. NO toca Firestore. Calcula cuántos emails se enviarían
ANTES y DESPUÉS de aplicar los bugs #3 y #4, y reporta la reducción esperada.

Ejecutar:
    cd back
    python scripts/email_policy_dryrun.py

Entrada (configurable arriba):
    SCENARIOS = lista de eventos típicos con #destinatarios estimados

Salida:
    Tabla evento | antes | después | reducción_%
    Total y porcentaje global de reducción.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Escenarios típicos (estimados con base en operación diaria real)
# Ajustar las cantidades si los datos reales difieren.
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    # Población afectada
    group_leaders: int       # # líderes en el grupo destino
    activity_leader: int     # 1 si la actividad tiene líder asignado, 0 si no
    is_self_action: bool     # ¿El actor es también el líder de la actividad?
    is_self_role_change: bool = False  # ¿Cambia su propio rol/grupo?
    occurrences_per_week: int = 1
    notes: str = ""


SCENARIOS: List[Scenario] = [
    Scenario(
        name="Crear actividad (líder distinto del creador)",
        group_leaders=4, activity_leader=1, is_self_action=False,
        occurrences_per_week=15,
        notes="Antes: 4 emails a líderes del grupo + 1 al líder + asignados. Ahora: solo líder + asignados.",
    ),
    Scenario(
        name="Crear actividad (creador es el mismo líder)",
        group_leaders=4, activity_leader=1, is_self_action=True,
        occurrences_per_week=8,
        notes="Antes: 4 a líderes + 1 al propio actor. Ahora: 0 a líderes + 0 al actor.",
    ),
    Scenario(
        name="Editar actividad (actor ≠ líder)",
        group_leaders=4, activity_leader=1, is_self_action=False,
        occurrences_per_week=20,
        notes="Resumen al líder + diffs a asignados. Sin cambios para asignados.",
    ),
    Scenario(
        name="Editar actividad (actor = líder, autoedita)",
        group_leaders=4, activity_leader=1, is_self_action=True,
        occurrences_per_week=12,
        notes="Antes: el líder recibía resumen de su propia edición. Ahora: omitido.",
    ),
    Scenario(
        name="Cambio de rol a otro usuario",
        group_leaders=0, activity_leader=0, is_self_action=False,
        is_self_role_change=False, occurrences_per_week=3,
        notes="Sin cambios — la app ya bloqueaba auto-cambio en backend.",
    ),
    Scenario(
        name="Asignación masiva de personal (10 personas, 1 actividad)",
        group_leaders=4, activity_leader=1, is_self_action=False,
        occurrences_per_week=5,
        notes="Antes: resumen líder + 10 asignaciones + 4 líderes grupo. Ahora: resumen líder + 10 asignaciones.",
    ),
    Scenario(
        name="Convocatoria (broadcast)",
        group_leaders=0, activity_leader=0, is_self_action=False,
        occurrences_per_week=4,
        notes="Sin cambios — sigue notificando a convocados.",
    ),
    Scenario(
        name="Reporte de intervención registrado",
        group_leaders=0, activity_leader=0, is_self_action=False,
        occurrences_per_week=80,
        notes="No genera notificaciones por correo (solo Firestore).",
    ),
]


# ---------------------------------------------------------------------------
# Política ANTES (legacy) y DESPUÉS (con bugs #3 #4 corregidos)
# ---------------------------------------------------------------------------

def emails_before(s: Scenario, assigned: int = 0) -> int:
    """
    Política antigua:
      - Crear actividad: notifica a TODOS los líderes del grupo + líder actividad + asignados.
      - Editar actividad: resumen al líder (siempre) + diffs a asignados.
      - Sin guarda de self-action.
    """
    n = 0
    if s.name.startswith("Crear actividad"):
        n += s.group_leaders          # sección C antigua
        n += s.activity_leader        # email al líder de la actividad
    if s.name.startswith("Editar actividad"):
        n += s.activity_leader        # resumen líder (siempre)
    if s.name.startswith("Asignación masiva"):
        n += s.activity_leader        # resumen líder
        n += s.group_leaders          # sección C
        n += assigned                 # 10 emails de asignación (se conservan)
    if s.name.startswith("Convocatoria"):
        n += 0                        # broadcasts no se cuentan aquí (no cambian)
    return n


def emails_after(s: Scenario, assigned: int = 0) -> int:
    """
    Política nueva (con flag NOTIFY_GROUP_LEADERS_ON_CREATE=false):
      - Crear actividad: NO notifica a líderes del grupo. Sí al líder de la actividad.
      - Editar actividad: si actor == líder, NO envía resumen al líder.
      - Asignación masiva: igual que crear/editar — sin sección C.
    """
    n = 0
    if s.name.startswith("Crear actividad"):
        if not s.is_self_action:
            n += s.activity_leader    # líder ≠ actor → sí recibe
    if s.name.startswith("Editar actividad"):
        if not s.is_self_action:
            n += s.activity_leader
    if s.name.startswith("Asignación masiva"):
        if not s.is_self_action:
            n += s.activity_leader
        n += assigned
    return n


# ---------------------------------------------------------------------------
# Ejecutor
# ---------------------------------------------------------------------------

def run() -> None:
    flag_on = os.getenv("NOTIFY_GROUP_LEADERS_ON_CREATE", "false").strip().lower() in ("1", "true", "yes")
    print("=" * 78)
    print("  DAGMA 360 — Dry-run política de notificaciones (bugs #3 y #4)")
    print(f"  Feature flag NOTIFY_GROUP_LEADERS_ON_CREATE = {'ON' if flag_on else 'off'}")
    print("=" * 78)
    print()
    header = f"{'Evento':50s}  {'Antes':>6s}  {'Después':>7s}  {'Δ/sem':>6s}  {'-%':>5s}"
    print(header)
    print("-" * len(header))

    total_before = 0
    total_after = 0

    for s in SCENARIOS:
        assigned = 10 if "masiva" in s.name else 0
        before_per = emails_before(s, assigned)
        if flag_on:
            # Si reactivan el flag, la sección C vuelve a sumar líderes del grupo
            after_per = emails_after(s, assigned) + s.group_leaders if s.name.startswith("Crear actividad") else emails_after(s, assigned)
        else:
            after_per = emails_after(s, assigned)
        before_w = before_per * s.occurrences_per_week
        after_w = after_per * s.occurrences_per_week
        total_before += before_w
        total_after += after_w
        delta_w = before_w - after_w
        pct = (delta_w / before_w * 100.0) if before_w > 0 else 0.0
        print(f"{s.name[:50]:50s}  {before_w:6d}  {after_w:7d}  {delta_w:6d}  {pct:4.0f}%")

    print("-" * len(header))
    delta_total = total_before - total_after
    pct_total = (delta_total / total_before * 100.0) if total_before > 0 else 0.0
    print(f"{'TOTAL semanal':50s}  {total_before:6d}  {total_after:7d}  {delta_total:6d}  {pct_total:4.0f}%")
    print()
    print(f"Reducción esperada: {pct_total:.1f}%  (meta ≥60%)")
    print("Notas:")
    for s in SCENARIOS:
        if s.notes:
            print(f"  • {s.name}: {s.notes}")


if __name__ == "__main__":
    run()
