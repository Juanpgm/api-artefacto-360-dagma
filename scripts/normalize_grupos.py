"""
Script de migración: canonicaliza el campo `grupo` en todas las colecciones
relevantes de Firestore usando `normalize_grupo()`:

  - lowercase
  - sin tildes/diacríticos
  - "_" → espacio
  - colapsa whitespace

Ejemplos: "Central_Social" → "central social", "Reacción" → "reaccion".

Colecciones procesadas (solo se toca la variable `grupo` y `grupos_requeridos`):
  - users                    (campo: grupo, + custom claims Firebase Auth)
  - personal_operativo       (campo: grupo)
  - lideres_grupos           (campo: grupo)
  - plan_distrito_verde      (campo: grupos_requeridos[], personal_asignado[].grupo)
  - asistencia_actividades   (campo: asistencia[].grupo / asistentes[].grupo)

NO se toca:
  - grupos                   (es catálogo, doc.id ya es la key canónica)
  - reportes_intervenciones  (campo `grupo` ya es key canónica corta)

Uso:
  python scripts/normalize_grupos.py --dry-run                   # ver todo
  python scripts/normalize_grupos.py --dry-run --collection users
  python scripts/normalize_grupos.py                             # aplicar todo
  python scripts/normalize_grupos.py --collection users          # aplicar solo users
"""
import sys
import argparse
from pathlib import Path

# Windows + Python 3.14: stdout por defecto en cp1252; forzamos utf-8 para
# poder imprimir caracteres de cajas y flechas sin UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.firebase_config import auth_client, db
from app.utils.text_utils import normalize_grupo


COLLECTIONS = ("users", "personal_operativo", "lideres_grupos",
               "plan_distrito_verde", "asistencia_actividades")


class Stats:
    __slots__ = ("updated", "skipped", "errors")

    def __init__(self) -> None:
        self.updated = 0
        self.skipped = 0
        self.errors = 0

    def report(self, label: str, mode: str) -> None:
        print(f"{mode}[{label}] actualizados={self.updated} sin_cambios={self.skipped} errores={self.errors}")


def _canon(value):
    """Devuelve la forma canónica o None si vacío."""
    if value is None:
        return None
    canon = normalize_grupo(value)
    return canon or None


def migrate_users(dry_run: bool, mode: str) -> Stats:
    stats = Stats()
    print(f"\n{mode}── users ───────────────────────────────────────────────")
    for doc in db.collection("users").stream():
        data = doc.to_dict() or {}
        uid = data.get("uid") or doc.id
        raw = data.get("grupo")
        canon = _canon(raw)
        if raw == canon:
            stats.skipped += 1
            continue
        print(f"{mode}  uid={uid}  grupo: {raw!r} → {canon!r}")
        if dry_run:
            stats.updated += 1
            continue
        try:
            doc.reference.update({"grupo": canon})
            claims = auth_client.get_user(uid).custom_claims or {}
            role = data.get("role") or claims.get("role", "operador")
            auth_client.set_custom_user_claims(uid, {"role": role, "grupo": canon})
            auth_client.revoke_refresh_tokens(uid)  # fuerza refresh del token
            stats.updated += 1
        except Exception as e:
            print(f"    ERROR uid={uid}: {e}")
            stats.errors += 1
    return stats


def _migrate_flat_collection(name: str, dry_run: bool, mode: str) -> Stats:
    """Para colecciones con campo `grupo` plano (personal_operativo, lideres_grupos)."""
    stats = Stats()
    print(f"\n{mode}── {name} ───────────────────────────────────────────────")
    for doc in db.collection(name).stream():
        data = doc.to_dict() or {}
        raw = data.get("grupo")
        canon = _canon(raw)
        if raw == canon:
            stats.skipped += 1
            continue
        print(f"{mode}  id={doc.id}  grupo: {raw!r} → {canon!r}")
        if dry_run:
            stats.updated += 1
            continue
        try:
            doc.reference.update({"grupo": canon})
            stats.updated += 1
        except Exception as e:
            print(f"    ERROR id={doc.id}: {e}")
            stats.errors += 1
    return stats


def migrate_plan_distrito_verde(dry_run: bool, mode: str) -> Stats:
    """Actividades: normaliza grupos_requeridos[] y personal_asignado[].grupo."""
    stats = Stats()
    print(f"\n{mode}── plan_distrito_verde ─────────────────────────────")
    for doc in db.collection("plan_distrito_verde").stream():
        data = doc.to_dict() or {}
        update: dict = {}

        grupos_req = data.get("grupos_requeridos")
        if isinstance(grupos_req, list):
            new_req = [_canon(g) for g in grupos_req if g]
            new_req = [g for g in new_req if g]
            if new_req != grupos_req:
                update["grupos_requeridos"] = new_req

        personal = data.get("personal_asignado")
        if isinstance(personal, list):
            new_personal = []
            changed = False
            for p in personal:
                if not isinstance(p, dict):
                    new_personal.append(p)
                    continue
                raw = p.get("grupo")
                canon = _canon(raw)
                if raw != canon:
                    p = {**p, "grupo": canon}
                    changed = True
                new_personal.append(p)
            if changed:
                update["personal_asignado"] = new_personal

        if not update:
            stats.skipped += 1
            continue
        print(f"{mode}  id={doc.id}  cambios={list(update.keys())}")
        if dry_run:
            stats.updated += 1
            continue
        try:
            doc.reference.update(update)
            stats.updated += 1
        except Exception as e:
            print(f"    ERROR id={doc.id}: {e}")
            stats.errors += 1
    return stats


def migrate_asistencia_actividades(dry_run: bool, mode: str) -> Stats:
    """Normaliza asistencia[].grupo dentro de cada doc."""
    stats = Stats()
    print(f"\n{mode}── asistencia_actividades ───────────────────────────")
    for doc in db.collection("asistencia_actividades").stream():
        data = doc.to_dict() or {}
        asistencia = data.get("asistencia") or data.get("asistentes")
        field = "asistencia" if "asistencia" in data else "asistentes"
        if not isinstance(asistencia, list):
            stats.skipped += 1
            continue
        new_list = []
        changed = False
        for entry in asistencia:
            if not isinstance(entry, dict):
                new_list.append(entry)
                continue
            raw = entry.get("grupo")
            canon = _canon(raw)
            if raw != canon:
                entry = {**entry, "grupo": canon}
                changed = True
            new_list.append(entry)
        if not changed:
            stats.skipped += 1
            continue
        print(f"{mode}  id={doc.id}  field={field}")
        if dry_run:
            stats.updated += 1
            continue
        try:
            doc.reference.update({field: new_list})
            stats.updated += 1
        except Exception as e:
            print(f"    ERROR id={doc.id}: {e}")
            stats.errors += 1
    return stats




DISPATCH = {
    "users": migrate_users,
    "personal_operativo": lambda d, m: _migrate_flat_collection("personal_operativo", d, m),
    "lideres_grupos": lambda d, m: _migrate_flat_collection("lideres_grupos", d, m),
    "plan_distrito_verde": migrate_plan_distrito_verde,
    "asistencia_actividades": migrate_asistencia_actividades,
}


def main(dry_run: bool, collections: tuple[str, ...]) -> int:
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"{mode}Canonicalizando grupos en Firestore — colecciones: {', '.join(collections)}")

    totals = Stats()
    for name in collections:
        fn = DISPATCH[name]
        s = fn(dry_run, mode)
        s.report(name, mode)
        totals.updated += s.updated
        totals.skipped += s.skipped
        totals.errors += s.errors

    print(f"\n{mode}═════════════════════════════════════════════════════")
    totals.report("TOTAL", mode)
    if dry_run:
        print(f"\n{mode}Ejecuta sin --dry-run para aplicar los cambios.")
    return 1 if totals.errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Canonicaliza grupos con normalize_grupo()")
    parser.add_argument("--dry-run", action="store_true", help="No aplica cambios, solo muestra")
    parser.add_argument(
        "--collection",
        choices=COLLECTIONS,
        action="append",
        help=f"Procesar solo esta(s) colección(es). Default: todas {COLLECTIONS}",
    )
    args = parser.parse_args()
    cols = tuple(args.collection) if args.collection else COLLECTIONS
    sys.exit(main(dry_run=args.dry_run, collections=cols))
