"""
Prueba masiva del endpoint POST /asignar_personal_actividad
- Lee todas las actividades de plan_distrito_verde
- Para cada actividad, asigna entre 2 y 10 personas
- La cantidad de personas varía por actividad
"""

import json
from datetime import datetime

import requests

API_URL = "http://localhost:8000"


def generar_total_personas(indice_actividad: int) -> int:
    """
    Genera un total variable entre 2 y 10 por actividad.
    """
    return 2 + (indice_actividad % 9)


def construir_payload(actividad_id: str, total_personas: int, prefijo: str) -> list[dict]:
    payload = []
    for persona_idx in range(1, total_personas + 1):
        payload.append(
            {
                "id": actividad_id,
                "email": f"{prefijo}_p{persona_idx}@dagma.gov.co",
                "grupo": f"Grupo Operativo {((persona_idx - 1) % 5) + 1}",
                "nombre_completo": f"Personal Actividad {prefijo} - {persona_idx}",
                "telefono": f"31{persona_idx:02d}{(100000 + persona_idx):06d}",
            }
        )
    return payload


def test_asignar_personal_todas_actividades() -> None:
    print("\n" + "=" * 80)
    print("🧪 PRUEBA MASIVA: /asignar_personal_actividad para todas las actividades")
    print("=" * 80)

    get_response = requests.get(f"{API_URL}/actividades_plan_distrito_verde", timeout=60)
    print(f"\nGET /actividades_plan_distrito_verde -> {get_response.status_code}")
    get_response.raise_for_status()

    actividades = get_response.json().get("data", [])
    if not actividades:
        raise AssertionError("No hay actividades en plan_distrito_verde para ejecutar la prueba")

    resumen = []
    marca = datetime.now().strftime("%Y%m%d%H%M%S")

    for actividad_idx, actividad in enumerate(actividades):
        actividad_id = (actividad.get("id") or "").strip()
        if not actividad_id:
            raise AssertionError(f"Actividad sin id válido en índice {actividad_idx}")

        total_personas = generar_total_personas(actividad_idx)
        prefijo = f"a{actividad_idx + 1}_{marca}"
        payload = construir_payload(actividad_id, total_personas, prefijo)

        post_response = requests.post(
            f"{API_URL}/asignar_personal_actividad",
            json=payload,
            timeout=60,
        )

        print(
            f"POST /asignar_personal_actividad | actividad={actividad_id} "
            f"personas={total_personas} -> {post_response.status_code}"
        )

        if post_response.status_code != 200:
            detalle_error = post_response.text
            raise AssertionError(
                f"Falló la asignación para actividad {actividad_id} con status "
                f"{post_response.status_code}. Respuesta: {detalle_error}"
            )

        resultado = post_response.json()
        if resultado.get("total_registros") != total_personas:
            raise AssertionError(
                f"Cantidad inesperada para actividad {actividad_id}. "
                f"Esperado: {total_personas}, recibido: {resultado.get('total_registros')}"
            )

        resumen.append(
            {
                "actividad_id": actividad_id,
                "personas_enviadas": total_personas,
                "ids_creados": resultado.get("ids", []),
            }
        )

    print("\n✅ Prueba completada exitosamente para todas las actividades")
    print(json.dumps({"actividades_procesadas": len(resumen), "detalle": resumen}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    test_asignar_personal_todas_actividades()
