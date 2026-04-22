"""
Pruebas de integración con Firestore real.

Escribe registros REALES en la colección 'reportes_intervenciones' de Firestore
vía las rutas LEGACY (sin autenticación requerida), y verifica el aislamiento
por grupo con las rutas GET.

Requisitos:
    - API corriendo localmente (por defecto en http://localhost:8000)
    - Variables de entorno configuradas: FIREBASE_SERVICE_ACCOUNT_JSON,
      AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (o S3 no requerido si no hay fotos)

Ejecución:
    python test_registros_reales_coleccion_unificada.py
    python test_registros_reales_coleccion_unificada.py --url https://web-production-2d737.up.railway.app
"""

import sys
import json
import requests
from datetime import datetime

BASE_URL = "http://localhost:8000"
if "--url" in sys.argv:
    idx = sys.argv.index("--url")
    BASE_URL = sys.argv[idx + 1]

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
IDS_REGISTRADOS = {}

OK = "\033[92m[OK]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"


def log_ok(msg):  print(f"  {OK}  {msg}")
def log_fail(msg): print(f"  {FAIL} {msg}"); sys.exit(1)
def log_info(msg): print(f"  {INFO} {msg}")


def check_api_alive():
    print(f"\n{INFO} Verificando conexión con {BASE_URL} ...")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code in (200, 404):
            log_ok(f"API responde ({r.status_code})")
        else:
            log_fail(f"API retornó {r.status_code}")
    except requests.ConnectionError:
        log_fail(f"No se puede conectar a {BASE_URL}. ¿Está el servidor corriendo?")


def post_reporte(grupo: str, payload: dict, label: str) -> str:
    """POST vía ruta legacy (sin auth). Retorna el ID del reporte creado."""
    url = f"{BASE_URL}/grupo-{grupo}/reporte_intervencion"
    r = requests.post(url, data=payload, timeout=10)
    if r.status_code != 200:
        log_fail(f"{label}: POST falló con {r.status_code} — {r.text[:300]}")
    data = r.json()
    if not data.get("success"):
        log_fail(f"{label}: success=False — {data}")
    doc_id = data["id"]
    log_ok(f"{label}: registrado → ID={doc_id}")
    return doc_id


def get_reportes(grupo: str, params: dict = None) -> dict:
    """GET vía ruta legacy (sin auth)."""
    url = f"{BASE_URL}/grupo-{grupo}/reportes_intervenciones"
    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        log_fail(f"GET /{grupo} falló con {r.status_code} — {r.text[:200]}")
    return r.json()


# ===========================================================================
# SECCIÓN 1: Registrar un reporte por cada grupo
# ===========================================================================

def test_registrar_cuadrilla():
    print("\n--- [1] CUADRILLA: registro con árboles ---")
    payload = {
        "tipo_intervencion": "Poda de emergencia",
        "descripcion_intervencion": "Poda preventiva por tormenta — test integración",
        "registrado_por": f"test_script_{TIMESTAMP}",
        "id_actividad": f"ACT-TEST-{TIMESTAMP}",
        "arboles_data": json.dumps([
            {"especie": "Ceiba pentandra", "cantidad": 3},
            {"especie": "Saman", "cantidad": 2},
        ]),
        "coordinates_type": "Point",
        "coordinates_data": "[-76.5225, 3.4516]",
    }
    doc_id = post_reporte("cuadrilla", payload, "Cuadrilla")
    IDS_REGISTRADOS["cuadrilla"] = doc_id


def test_registrar_vivero():
    print("\n--- [2] VIVERO: registro con tipos de plantas ---")
    payload = {
        "tipo_intervencion": "Siembra de compensación",
        "registrado_por": f"test_script_{TIMESTAMP}",
        "id_actividad": f"ACT-TEST-{TIMESTAMP}",
        "tipos_plantas": json.dumps({
            "Guayacán amarillo": 10,
            "Ceiba tolua": 5,
            "Samán": 8,
        }),
    }
    doc_id = post_reporte("vivero", payload, "Vivero")
    IDS_REGISTRADOS["vivero"] = doc_id


def test_registrar_gobernanza():
    print("\n--- [3] GOBERNANZA: registro con unidades impactadas ---")
    payload = {
        "tipo_intervencion": "Taller comunitario",
        "descripcion_intervencion": "Educación ambiental barrio El Lido",
        "registrado_por": f"test_script_{TIMESTAMP}",
        "id_actividad": f"ACT-TEST-{TIMESTAMP}",
        "unidades_impactadas": 45,
    }
    doc_id = post_reporte("gobernanza", payload, "Gobernanza")
    IDS_REGISTRADOS["gobernanza"] = doc_id


def test_registrar_ecosistemas():
    print("\n--- [4] ECOSISTEMAS: registro con unidad de medida ---")
    payload = {
        "tipo_intervencion": "Monitoreo de fauna",
        "registrado_por": f"test_script_{TIMESTAMP}",
        "id_actividad": f"ACT-TEST-{TIMESTAMP}",
        "unidad_medida": "individuos",
        "unidades_impactadas": 120,
    }
    doc_id = post_reporte("ecosistemas", payload, "Ecosistemas")
    IDS_REGISTRADOS["ecosistemas"] = doc_id


def test_registrar_umata():
    print("\n--- [5] UMATA: registro básico ---")
    payload = {
        "tipo_intervencion": "Asistencia técnica agrícola",
        "registrado_por": f"test_script_{TIMESTAMP}",
        "id_actividad": f"ACT-TEST-{TIMESTAMP}",
        "unidades_impactadas": 30,
    }
    doc_id = post_reporte("umata", payload, "UMATA")
    IDS_REGISTRADOS["umata"] = doc_id


# ===========================================================================
# SECCIÓN 2: Verificar que cada GET retorna sólo el grupo correcto
# ===========================================================================

def test_get_por_id_actividad():
    print(f"\n--- [6] VERIFICAR: GET por id_actividad={f'ACT-TEST-{TIMESTAMP}'} ---")
    id_actividad = f"ACT-TEST-{TIMESTAMP}"
    for grupo in ["cuadrilla", "vivero", "gobernanza", "ecosistemas", "umata"]:
        data = get_reportes(grupo, {"id_actividad": id_actividad})
        total = data["total"]
        if total < 1:
            log_fail(f"GET /{grupo}?id_actividad={id_actividad} retornó total={total} (esperaba ≥1)")
        docs = data["data"]
        for doc in docs:
            if doc.get("grupo") != grupo:
                log_fail(f"Aislamiento roto: documento de grupo '{doc.get('grupo')}' apareció en GET /{grupo}")
        log_ok(f"GET /{grupo}: total={total}, todos los docs tienen grupo='{grupo}'")


def test_get_por_id_especifico():
    print("\n--- [7] VERIFICAR: GET por id de documento específico ---")
    for grupo, doc_id in IDS_REGISTRADOS.items():
        data = get_reportes(grupo, {"id": doc_id})
        if data["total"] != 1:
            log_fail(f"GET /{grupo}?id={doc_id}: esperaba total=1, obtuvo {data['total']}")
        doc = data["data"][0]
        if doc.get("grupo") != grupo:
            log_fail(f"Documento retornó grupo='{doc.get('grupo')}', esperaba '{grupo}'")
        log_ok(f"GET /{grupo}?id={doc_id}: encontrado OK, grupo='{doc['grupo']}'")


def test_aislamiento_cross_grupo():
    print("\n--- [8] VERIFICAR: aislamiento — ID de cuadrilla NO aparece en vivero ---")
    id_cuadrilla = IDS_REGISTRADOS.get("cuadrilla")
    if not id_cuadrilla:
        log_info("Skipped — cuadrilla no registrado")
        return
    data = get_reportes("vivero", {"id": id_cuadrilla})
    if data["total"] != 0:
        log_fail(f"Aislamiento roto: ID de cuadrilla apareció en GET /vivero (total={data['total']})")
    log_ok(f"ID de cuadrilla no aparece en GET /vivero (total=0) — aislamiento OK")


def test_campos_especificos_guardados():
    print("\n--- [9] VERIFICAR: campos específicos por grupo ---")
    # Cuadrilla: árboles
    id_cuadrilla = IDS_REGISTRADOS.get("cuadrilla")
    if id_cuadrilla:
        data = get_reportes("cuadrilla", {"id": id_cuadrilla})
        doc = data["data"][0]
        arboles = doc.get("arboles")
        if not arboles or len(arboles) != 2:
            log_fail(f"Cuadrilla: esperaba 2 árboles, obtuvo: {arboles}")
        log_ok(f"Cuadrilla: arboles={[a['especie'] for a in arboles]}")

    # Vivero: tipos_plantas + cantidad_total_plantas
    id_vivero = IDS_REGISTRADOS.get("vivero")
    if id_vivero:
        data = get_reportes("vivero", {"id": id_vivero})
        doc = data["data"][0]
        tipos = doc.get("tipos_plantas")
        total_plantas = doc.get("cantidad_total_plantas")
        if not tipos:
            log_fail(f"Vivero: tipos_plantas no guardado")
        if total_plantas != 23:
            log_fail(f"Vivero: cantidad_total_plantas={total_plantas}, esperaba 23")
        log_ok(f"Vivero: tipos_plantas OK, cantidad_total_plantas={total_plantas}")

    # Ecosistemas: unidad_medida
    id_eco = IDS_REGISTRADOS.get("ecosistemas")
    if id_eco:
        data = get_reportes("ecosistemas", {"id": id_eco})
        doc = data["data"][0]
        if doc.get("unidad_medida") != "individuos":
            log_fail(f"Ecosistemas: unidad_medida='{doc.get('unidad_medida')}', esperaba 'individuos'")
        if doc.get("unidades_impactadas") != 120:
            log_fail(f"Ecosistemas: unidades_impactadas={doc.get('unidades_impactadas')}, esperaba 120")
        log_ok(f"Ecosistemas: unidad_medida='individuos', unidades_impactadas=120")


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print(f" PRUEBA DE INTEGRACIÓN REAL — {TIMESTAMP}")
    print(f" URL: {BASE_URL}")
    print("=" * 60)

    check_api_alive()

    # Registrar
    test_registrar_cuadrilla()
    test_registrar_vivero()
    test_registrar_gobernanza()
    test_registrar_ecosistemas()
    test_registrar_umata()

    # Verificar
    test_get_por_id_actividad()
    test_get_por_id_especifico()
    test_aislamiento_cross_grupo()
    test_campos_especificos_guardados()

    print("\n" + "=" * 60)
    print(" TODOS LOS CHECKS PASARON")
    print(f" IDs registrados en Firestore (colección: reportes_intervenciones):")
    for grupo, doc_id in IDS_REGISTRADOS.items():
        print(f"   {grupo}: {doc_id}")
    print("=" * 60)
