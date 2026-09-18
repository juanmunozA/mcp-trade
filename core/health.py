"""
Endpoint de salud. Cumple tres funciones a la vez:

 1. **Render exige** que un web service escuche en `$PORT`. Si no, lo marca
    caído y lo reinicia en bucle.
 2. **UptimeRobot** pega aquí cada 5 minutos para evitar el spin-down de los
    15 minutos del tier gratuito.
 3. **Tú** lo abres en el navegador y ves de un vistazo si el bot está vivo.

El punto 3 es el que justifica escribirlo bien. Un endpoint que solo
devuelve `OK` te entrena a ignorarlo. Este devuelve el estado real: si el
kill switch saltó, si los datos están frescos, cuánto lleva corriendo.
Así el ping deja de ser un pulso y pasa a ser un diagnóstico.

Sin dependencias nuevas: asyncio de la stdlib.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime

log = logging.getLogger("health")

_STATUS_TEXT = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}


class HealthServer:
    """
    `status_fn` devuelve un dict con lo que el proceso quiera publicar.
    Se llama en cada request, así que debe ser barata y no tocar la red.
    """

    def __init__(
        self,
        status_fn: Callable[[], dict] | None = None,
        *,
        port: int | None = None,
    ) -> None:
        # Render inyecta PORT. 10000 es su default para servicios propios.
        self.port = port or int(os.getenv("PORT", "10000"))
        self._status_fn = status_fn or (lambda: {})
        self._started = time.monotonic()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self.port
        )
        log.info("health escuchando en 0.0.0.0:%d", self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ── interno ──────────────────────────────────────────────────────────

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = request.decode("latin-1").split()
            path = parts[1] if len(parts) > 1 else "/"

            # descartamos cabeceras; no nos importan
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break

            if path.split("?")[0] not in ("/", "/health", "/healthz"):
                await self._respond(writer, 404, {"error": "not found"})
                return

            payload = self._build_status()
            # 503 cuando el bot está muerto: así UptimeRobot te avisa solo,
            # sin necesidad de que nadie mire un dashboard.
            code = 503 if payload.get("killed") else 200
            await self._respond(writer, code, payload)

        except (TimeoutError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 — el health nunca tumba el bot
            log.warning("health request falló: %s", exc)
        finally:
            with _suppress():
                writer.close()
                await writer.wait_closed()

    def _build_status(self) -> dict:
        base = {
            "ok": True,
            "uptime_s": round(time.monotonic() - self._started, 1),
            "now": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            base |= self._status_fn()
        except Exception as exc:  # noqa: BLE001
            base["status_error"] = repr(exc)
        return base

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, code: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, default=str).encode()
        head = (
            f"HTTP/1.1 {code} {_STATUS_TEXT.get(code, 'OK')}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(head + body)
        await writer.drain()


class _suppress:
    """contextlib.suppress(Exception) sin el import."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, Exception)
