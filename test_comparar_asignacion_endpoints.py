"""
Test funcional: PATCH y PUT /actividades/{id}/personal_asignado

Verifica:
  1. PATCH agrega personal + envío de email Gmail + invitación Calendar
  2. PATCH no duplica al llamar dos veces (ArrayUnion)
  3. PUT reemplaza la lista completa + notificaciones diff
  4. GET devuelve datos consistentes
  5. Limpieza final

Usa correo real: juanp.gzmz@gmail.com

Ejecutar:  python test_comparar_asignacion_endpoints.py
"""

import requests
import time
from datetime import datetime

API_URL = "http://localhost:8000"
TEST_EMAIL = "juanp.gzmz@gmail.com"
TEST_NOMBRE = "Juan Pablo Guzmán (Test)"
TEST_GRUPO = "Cuadrilla"
TEST_CONTACTO = 3001234567


def separador(titulo: str):
    print(f"\n{'='*70}")
    print(f"  {titulo}")
    print(f"{'='*70}")


def obtener_actividad_con_calendar() -> dict | None:
    print("\n🔍 Buscando actividad con calendar_event_id...")
    r = requests.get(f"{API_URL}/actividades", params={"limit": 50})
    r.raise_for_status()
    actividades = r.json().get("data", [])
    print(f"   Total actividades: {len(actividades)}")
    for a in actividades:
        if a.get("calendar_event_id"):
            print(f"   ✅ Con calendario: doc_id={a['id']}  calendar_event_id={a['calendar_event_id']}")
            return a
    if actividades:
        a = actividades[0]
        print(f"   ⚠️  Sin calendar. Usando: doc_id={a['id']}")
        return a
    return None


def limpiar_personal_test(actividad_id: str):
    r = requests.put(
        f"{API_URL}/actividades/{actividad_id}/personal_asignado",
        json={"personal_asignado": []}
    )
    if r.status_code == 200:
        print(f"   🧹 personal_asignado vaciado")
    else:
        print(f"   ⚠️  Error vaciando: {r.status_code} — {r.text[:200]}")


def verificar_get_personal(actividad_id: str) -> list:
    r = requests.get(f"{API_URL}/actividades", params={"id": actividad_id})
    r.raise_for_status()
    data = r.json().get("data", [])
    if data:
        return data[0].get("personal_asignado", [])
    return []


# ===================== TEST 1: PATCH (agregar personal) =====================

def test_patch_asignar(actividad: dict) -> dict:
    separador("TEST 1: PATCH /actividades/{id}/personal_asignado")
    act_id = actividad["id"]
    results = {"errors": [], "checks_passed": 0, "checks_total": 4}

    limpiar_personal_test(act_id)

    payload = {
        "email": TEST_EMAIL,
        "nombre_completo": TEST_NOMBRE,
        "numero_contacto": TEST_CONTACTO,
        "grupo": TEST_GRUPO,
    }

    # --- 1ª llamada ---
    print("\n📤 1ª llamada PATCH (agregar persona)...")
    t0 = time.time()
    r1 = requests.patch(f"{API_URL}/actividades/{act_id}/personal_asignado", json=payload)
    t1 = time.time() - t0
    print(f"   Status: {r1.status_code}  Tiempo: {t1:.3f}s")

    if r1.status_code == 200:
        body = r1.json()
        total = body.get("total_personal", 0)
        print(f"   ✅ Asignado. Total personal: {total}")
        print(f"   📧 Email enviado: {body.get('notificacion_email', 'N/A')}")
        print(f"   📅 Calendar: {body.get('notificacion_calendar', 'N/A')}")
        results["checks_passed"] += 1
    else:
        print(f"   ❌ Error: {r1.text[:300]}")
        results["errors"].append(f"PATCH 1st call: {r1.status_code}")

    # --- 2ª llamada (duplicado) ---
    print("\n📤 2ª llamada PATCH (mismo payload → no debe duplicar)...")
    t0 = time.time()
    r2 = requests.patch(f"{API_URL}/actividades/{act_id}/personal_asignado", json=payload)
    t2 = time.time() - t0
    print(f"   Status: {r2.status_code}  Tiempo: {t2:.3f}s")

    if r2.status_code == 200:
        body2 = r2.json()
        total2 = body2.get("total_personal", 0)
        if total2 == 1:
            print(f"   ✅ ArrayUnion previno duplicado (total={total2})")
            results["checks_passed"] += 1
        else:
            print(f"   ⚠️  total_personal={total2} (esperado: 1)")
            results["errors"].append(f"Duplicado: total={total2}")
    else:
        print(f"   ❌ Error: {r2.text[:200]}")
        results["errors"].append(f"PATCH 2nd call: {r2.status_code}")

    # --- Verificar GET ---
    print("\n🔎 Verificando consistencia con GET...")
    personal = verificar_get_personal(act_id)
    emails_test = [p for p in personal if p.get("email", "").lower() == TEST_EMAIL.lower()]
    print(f"   Total personal en GET: {len(personal)}")
    print(f"   Registros con email test: {len(emails_test)}")

    if len(emails_test) == 1:
        print(f"   ✅ Exactamente 1 registro — GET consistente")
        results["checks_passed"] += 1
    else:
        print(f"   ❌ Esperaba 1, encontró {len(emails_test)}")
        results["errors"].append(f"GET inconsistente: {len(emails_test)} registros")

    results["time_1st"] = round(t1, 3)
    results["time_2nd"] = round(t2, 3)
    return results


# ===================== TEST 2: PUT (reemplazar lista completa) =====================

def test_put_reemplazar(actividad: dict) -> dict:
    separador("TEST 2: PUT /actividades/{id}/personal_asignado")
    act_id = actividad["id"]
    results = {"errors": [], "checks_passed": 0, "checks_total": 3}

    # Preparar: poner 2 personas con PATCH
    print("\n📦 Preparando: asignar 2 personas via PATCH...")
    persona_a = {
        "email": TEST_EMAIL,
        "nombre_completo": TEST_NOMBRE,
        "numero_contacto": TEST_CONTACTO,
        "grupo": TEST_GRUPO,
    }
    persona_b = {
        "email": "test-persona-b@example.com",
        "nombre_completo": "Persona B (Test)",
        "numero_contacto": 3009999999,
        "grupo": "Vivero",
    }
    requests.patch(f"{API_URL}/actividades/{act_id}/personal_asignado", json=persona_a)
    requests.patch(f"{API_URL}/actividades/{act_id}/personal_asignado", json=persona_b)

    personal_pre = verificar_get_personal(act_id)
    print(f"   Antes de PUT: {len(personal_pre)} personas")

    # PUT: reemplazar con solo persona_a (elimina persona_b)
    print("\n📤 PUT: reemplazar con solo 1 persona (eliminar persona_b)...")
    nueva_lista = [persona_a]
    t0 = time.time()
    r = requests.put(
        f"{API_URL}/actividades/{act_id}/personal_asignado",
        json={"personal_asignado": nueva_lista}
    )
    t1 = time.time() - t0
    print(f"   Status: {r.status_code}  Tiempo: {t1:.3f}s")

    if r.status_code == 200:
        body = r.json()
        total = body.get("total_personal", 0)
        print(f"   Total personal tras PUT: {total}")
        if total == 1:
            print(f"   ✅ PUT reemplazó correctamente")
            results["checks_passed"] += 1
        else:
            print(f"   ⚠️  Esperaba 1, tiene {total}")
            results["errors"].append(f"PUT total: {total}")

        # Notificaciones diff
        notif = body.get("notificaciones", {})
        print(f"   Agregados notificados: {notif.get('agregados', 'N/A')}")
        print(f"   Removidos notificados: {notif.get('removidos', 'N/A')}")
    else:
        print(f"   ❌ Error: {r.text[:300]}")
        results["errors"].append(f"PUT: {r.status_code}")

    # Verificar GET
    print("\n🔎 Verificando GET post-PUT...")
    personal_post = verificar_get_personal(act_id)
    print(f"   Total personal en GET: {len(personal_post)}")
    if len(personal_post) == 1:
        print(f"   ✅ GET consistente con PUT")
        results["checks_passed"] += 1
    else:
        print(f"   ❌ GET muestra {len(personal_post)} (esperado: 1)")
        results["errors"].append(f"GET post-PUT: {len(personal_post)}")

    # PUT vacío (limpiar todo)
    print("\n📤 PUT: vaciar lista completa...")
    r2 = requests.put(
        f"{API_URL}/actividades/{act_id}/personal_asignado",
        json={"personal_asignado": []}
    )
    if r2.status_code == 200:
        personal_vacio = verificar_get_personal(act_id)
        if len(personal_vacio) == 0:
            print(f"   ✅ Lista vaciada correctamente")
            results["checks_passed"] += 1
        else:
            print(f"   ❌ Aún tiene {len(personal_vacio)} registros")
            results["errors"].append(f"PUT vacío: {len(personal_vacio)}")
    else:
        print(f"   ❌ Error vaciando: {r2.status_code}")
        results["errors"].append(f"PUT vacío: {r2.status_code}")

    results["time_put"] = round(t1, 3)
    return results


# ===================== RESUMEN =====================

def imprimir_resumen(r_patch: dict, r_put: dict):
    separador("RESUMEN DE PRUEBAS")

    total_checks = r_patch["checks_total"] + r_put["checks_total"]
    total_passed = r_patch["checks_passed"] + r_put["checks_passed"]
    total_errors = len(r_patch["errors"]) + len(r_put["errors"])

    print(f"\n   PATCH /actividades/{{id}}/personal_asignado")
    print(f"     Checks: {r_patch['checks_passed']}/{r_patch['checks_total']}")
    print(f"     Tiempos: 1ª={r_patch.get('time_1st','?')}s  2ª={r_patch.get('time_2nd','?')}s")
    if r_patch["errors"]:
        for e in r_patch["errors"]:
            print(f"     ❌ {e}")

    print(f"\n   PUT /actividades/{{id}}/personal_asignado")
    print(f"     Checks: {r_put['checks_passed']}/{r_put['checks_total']}")
    print(f"     Tiempo PUT: {r_put.get('time_put','?')}s")
    if r_put["errors"]:
        for e in r_put["errors"]:
            print(f"     ❌ {e}")

    print(f"\n   {'='*40}")
    print(f"   TOTAL: {total_passed}/{total_checks} checks passed")
    if total_errors == 0:
        print(f"   ✅ TODOS LOS TESTS PASARON")
    else:
        print(f"   ❌ {total_errors} errores encontrados")

    print(f"""
📋 Endpoints activos para personal_asignado:
  - PATCH /actividades/{{id}}/personal_asignado → agregar 1 persona (ArrayUnion, no duplica)
  - PUT   /actividades/{{id}}/personal_asignado → reemplazar lista completa (diff + notificaciones)
  - GET   /actividades?id={{id}}                 → leer personal_asignado (campo del doc)

📧 Revisa la bandeja de {TEST_EMAIL} para confirmar emails recibidos
📅 Revisa Google Calendar para confirmar invitaciones
""")


def main():
    print(f"🕐 Inicio: {datetime.now().isoformat()}")
    print(f"📧 Email de prueba: {TEST_EMAIL}")
    print(f"🌐 API: {API_URL}")

    actividad = obtener_actividad_con_calendar()
    if not actividad:
        print("❌ No hay actividades disponibles.")
        return

    print(f"\n🎯 Actividad: {actividad['id']}")
    print(f"   Fecha: {actividad.get('fecha_actividad', 'N/A')}")
    print(f"   calendar_event_id: {actividad.get('calendar_event_id', 'N/A')}")

    r_patch = test_patch_asignar(actividad)
    r_put = test_put_reemplazar(actividad)
    imprimir_resumen(r_patch, r_put)

    separador("LIMPIEZA FINAL")
    limpiar_personal_test(actividad["id"])
    print(f"\n🕐 Fin: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
