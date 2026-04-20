"""
Prueba masiva para el endpoint POST /asignar_personal_actividad.

Flujo:
1) Lee todas las actividades desde GET /actividades (colección actividades).
2) Para cada actividad, genera un número distinto de personas entre 2 y 10.
3) Envía un POST por actividad con la lista de personal.
4) Imprime resumen de resultados.
"""

import json
import random
from datetime import datetime

import requests

API_URL = "http://localhost:8000"
GET_ACTIVIDADES_ENDPOINT = f"{API_URL}/actividades"
POST_ASIGNAR_ENDPOINT = f"{API_URL}/asignar_personal_actividad"


def generar_total_personas_por_actividad(total_actividades: int) -> list[int]:
    """
    Genera una lista de cantidades entre 2 y 10, procurando variar entre actividades.
    """
    if total_actividades <= 0:
        return []

    rng = random.Random(datetime.now().timestamp())
    cantidades: list[int] = []
    anterior = None

    for _ in range(total_actividades):
        cantidad = rng.randint(2, 10)
        if anterior is not None and cantidad == anterior:
            cantidad = 2 if cantidad == 10 else cantidad + 1
        cantidades.append(cantidad)
        anterior = cantidad

    return cantidades


def construir_payload(actividad_id: str, cantidad_personas: int, indice_actividad: int, marca: str) -> list[dict]:
    payload = []
    for persona_idx in range(1, cantidad_personas + 1):
        payload.append(
            {
                "id": actividad_id,
                "email": f"act{indice_actividad}_persona{persona_idx}_{marca}@dagma.gov.co",
                "grupo": f"Grupo Operativo {indice_actividad}",
                "nombre_completo": f"Personal Actividad {indice_actividad} - {persona_idx}",
                "telefono": f"31{indice_actividad % 10}{persona_idx:08d}"[:10],
            }
        )
    return payload


def ejecutar_prueba():
    print("\n" + "=" * 72)
    print("🧪 PRUEBA MASIVA: POST /asignar_personal_actividad")
    print("=" * 72)

    # 1) Leer actividades (colección actividades)
    respuesta_actividades = requests.get(GET_ACTIVIDADES_ENDPOINT, timeout=30)
    print(f"GET /actividades -> {respuesta_actividades.status_code}")
    respuesta_actividades.raise_for_status()

    actividades = respuesta_actividades.json().get("data", [])
    if not actividades:
        raise SystemExit("No hay actividades para ejecutar la prueba.")

    ids_actividades = [actividad.get("id") for actividad in actividades if actividad.get("id")]
    if not ids_actividades:
        raise SystemExit("No se encontraron IDs válidos en las actividades.")

    # 2) Generar cantidades entre 2 y 10 por cada actividad
    cantidades = generar_total_personas_por_actividad(len(ids_actividades))

    # 3) Ejecutar POST por cada actividad
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    resumen = []

    for indice, actividad_id in enumerate(ids_actividades, start=1):
        total_personas = cantidades[indice - 1]
        payload = construir_payload(actividad_id, total_personas, indice, marca)

        print("\n" + "-" * 72)
        print(f"Actividad {indice}/{len(ids_actividades)} | ID: {actividad_id}")
        print(f"Personas a asignar: {total_personas}")

        respuesta_post = requests.post(POST_ASIGNAR_ENDPOINT, json=payload, timeout=60)
        print(f"POST /asignar_personal_actividad -> {respuesta_post.status_code}")

        detalle_respuesta = None
        try:
            detalle_respuesta = respuesta_post.json()
        except Exception:
            detalle_respuesta = {"raw": respuesta_post.text}

        if respuesta_post.status_code != 200:
            print("❌ Error en asignación:")
            print(json.dumps(detalle_respuesta, indent=2, ensure_ascii=False))
            resumen.append(
                {
                    "actividad_id": actividad_id,
                    "personas_enviadas": total_personas,
                    "status_code": respuesta_post.status_code,
                    "ok": False,
                    "respuesta": detalle_respuesta,
                }
            )
            continue

        total_guardado = detalle_respuesta.get("total_registros")
        ok = total_guardado == total_personas

        resumen.append(
            {
                "actividad_id": actividad_id,
                "personas_enviadas": total_personas,
                "personas_guardadas": total_guardado,
                "status_code": respuesta_post.status_code,
                "ok": ok,
            }
        )

        if ok:
            print(f"✅ Guardados correctamente: {total_guardado}")
        else:
            print(
                f"⚠️ Diferencia detectada. Enviadas={total_personas}, guardadas={total_guardado}"
            )

    # 4) Resumen final
    total_actividades = len(resumen)
    exitosas = sum(1 for r in resumen if r.get("ok"))
    fallidas = total_actividades - exitosas

    print("\n" + "=" * 72)
    print("📊 RESUMEN FINAL")
    print("=" * 72)
    print(f"Actividades procesadas: {total_actividades}")
    print(f"Exitosas: {exitosas}")
    print(f"Con observaciones/fallo: {fallidas}")
    print("\nDetalle:")
    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    if fallidas > 0:
        raise SystemExit("La prueba finalizó con actividades fallidas o inconsistentes.")

    print("\n✅ Prueba masiva completada exitosamente para todas las actividades.")


if __name__ == "__main__":
    ejecutar_prueba()
