"""Helpers para ejecutar llamadas síncronas de google-cloud-firestore sin
bloquear el event loop de FastAPI.

El cliente síncrono de Firestore realiza I/O bloqueante. Llamarlo desde un
endpoint ``async def`` paraliza el event loop y degrada el throughput de todo
el proceso. Este módulo provee un envoltorio fino sobre ``asyncio.to_thread``
para mantener los call sites legibles.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Ejecuta ``fn(*args, **kwargs)`` en el threadpool por defecto.

    Uso típico::

        snapshot = await run_blocking(db.collection("users").document(uid).get)
        docs = await run_blocking(lambda: list(query.stream()))
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def stream_to_list(query: Any) -> list:
    """Materializa ``query.stream()`` en una lista sin bloquear el loop."""
    return await asyncio.to_thread(lambda: list(query.stream()))


__all__ = ["run_blocking", "stream_to_list"]
