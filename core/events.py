"""
Lo que el agente está haciendo, mientras lo hace.

POR QUÉ EXISTE
──────────────
El tablero mostraba agregados: cuántos pronósticos, cuánto presupuesto.
Eso no dice nada cuando el agente lleva diez minutos investigando un
mercado, y tampoco distingue "no encontró nada" de "se cayó".

Los logs sí lo cuentan, pero viven en Render y nadie los mira.

CÓMO FUNCIONA
─────────────
Un anillo en memoria con lo último, más suscriptores que reciben cada
evento apenas ocurre. `/events` devuelve el anillo; `/stream` deja la
conexión abierta y empuja.

El anillo es a propósito: un suscriptor que llega tarde ve los últimos
300 eventos en vez de una pantalla en blanco. Y en disco efímero no
tiene sentido persistirlos — lo que importa para siempre ya está en
Postgres.

Publicar NUNCA puede romper al agente: si una cola está llena se tira
el evento, no se espera. Un tablero lento no puede frenar un ciclo.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("events")

MAX_ANILLO = 300
MAX_COLA = 100


class EventBus:
    def __init__(self, maxlen: int = MAX_ANILLO) -> None:
        self._anillo: deque[dict] = deque(maxlen=maxlen)
        self._subs: set[asyncio.Queue] = set()
        self._n = 0

    def emit(self, kind: str, msg: str, **datos: Any) -> dict:
        """
        Publica un evento. Sincrono y sin await a proposito: se llama
        desde dentro del pipeline, y un `await` ahi seria un punto de
        cancelacion nuevo en medio de una seccion critica.
        """
        self._n += 1
        ev = {
            "id": self._n,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "mono": round(time.monotonic(), 3),
            "kind": kind,
            "msg": msg,
        }
        if datos:
            ev["datos"] = datos
        self._anillo.append(ev)

        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # El suscriptor no lee al ritmo que emitimos. Se le tira
                # este evento; el anillo le queda para reconstruir.
                pass
        return ev

    def recent(self, n: int = 100, desde: int = 0) -> list[dict]:
        """Los ultimos `n`, o los posteriores a `desde` si se pide."""
        datos = list(self._anillo)
        if desde:
            datos = [e for e in datos if e["id"] > desde]
        return datos[-max(1, min(n, MAX_ANILLO)):]

    @property
    def total(self) -> int:
        return self._n

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_COLA)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def n_suscriptores(self) -> int:
        return len(self._subs)


BUS = EventBus()
"""
Uno por proceso. Cada agente corre en su propio contenedor, asi que no
hay nada que compartir entre ellos: el tablero lee los dos streams.
"""


def emit(kind: str, msg: str, **datos: Any) -> dict:
    return BUS.emit(kind, msg, **datos)
