"""
API HTTP del agente. Es la superficie que n8n orquesta.

POR QUÉ EXISTE
──────────────
El agente sabe hacer su trabajo solo, pero n8n necesita algo a lo que
llamar. En vez de meter la lógica en nodos visuales —donde no se puede
testear ni depurar— el agente expone verbos y n8n decide *cuándo*
invocarlos.

    n8n              →  cuándo pasa cada cosa (cron, reintentos, alertas)
    este proceso     →  qué pasa (investigar, pronosticar, decidir, arriesgar)

ENDPOINTS
    GET  /health     estado, presupuesto, kill switch   (también UptimeRobot)
    GET  /report     tablero de aprendizaje: ¿hay edge?
    POST /cycle      ejecuta un ciclo de pronóstico
    POST /resolve    busca mercados resueltos y aprende
    POST /kill       para todo (botón de pánico desde n8n o Telegram)

SEGURIDAD
Si `AGENT_API_TOKEN` está definido, todo menos /health exige
`Authorization: Bearer <token>`. En Render el servicio es público: sin
token, cualquiera podría disparar ciclos y quemarte el presupuesto.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

log = logging.getLogger("api")

_STATUS = {
    200: "OK", 202: "Accepted", 401: "Unauthorized",
    404: "Not Found", 409: "Conflict", 500: "Internal Server Error",
}


class AgentAPI:
    """
    Servidor HTTP mínimo sobre asyncio. Sin framework: son cinco rutas y
    una dependencia menos que mantener.
    """

    def __init__(
        self,
        *,
        status_fn: Callable[[], dict],
        cycle_fn: Callable[[], Awaitable[dict]],
        resolve_fn: Callable[[], Awaitable[dict]],
        report_fn: Callable[[], Awaitable[dict]],
        kill_fn: Callable[[str], None],
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self.port = port or int(os.getenv("PORT", "10000"))
        self.token = token if token is not None else os.getenv("AGENT_API_TOKEN", "")
        self._status_fn = status_fn
        self._cycle_fn = cycle_fn
        self._resolve_fn = resolve_fn
        self._report_fn = report_fn
        self._kill_fn = kill_fn

        self._started = time.monotonic()
        self._server: asyncio.AbstractServer | None = None
        # Un ciclo a la vez. Dos concurrentes duplicarían el consumo de
        # presupuesto y competirían por el mismo risk kernel.
        self._busy = asyncio.Lock()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self.port
        )
        guard = "con token" if self.token else "SIN TOKEN (público)"
        log.info("API escuchando en 0.0.0.0:%d — %s", self.port, guard)
        if not self.token:
            log.warning(
                "AGENT_API_TOKEN vacío: cualquiera puede disparar ciclos y "
                "agotar el presupuesto de inferencia"
            )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ── enrutado ─────────────────────────────────────────────────────────

    async def _handle(self, reader, writer) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            parts = line.decode("latin-1").split()
            if len(parts) < 2:
                return
            method, raw_path = parts[0], parts[1]
            path = raw_path.split("?")[0].rstrip("/") or "/"

            headers: dict[str, str] = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=10)
                if h in (b"\r\n", b"\n", b""):
                    break
                if b":" in h:
                    k, _, v = h.decode("latin-1").partition(":")
                    headers[k.strip().lower()] = v.strip()

            if (n := int(headers.get("content-length", "0") or 0)) > 0:
                await asyncio.wait_for(reader.readexactly(n), timeout=10)

            code, payload = await self._route(method, path, headers)
            await self._respond(writer, code, payload)

        except (TimeoutError, ConnectionResetError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # noqa: BLE001 — la API nunca tumba el agente
            log.warning("request falló: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _route(self, method: str, path: str, headers: dict) -> tuple[int, dict]:
        if path in ("/", "/health", "/healthz"):
            status = self._safe_status()
            return (503 if status.get("killed") else 200), status

        if self.token:
            sent = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if sent != self.token:
                return 401, {"error": "token inválido o ausente"}

        if path == "/report" and method == "GET":
            return 200, await self._report_fn()

        if path == "/cycle" and method == "POST":
            if self._busy.locked():
                # 409 y no 500: n8n debe reintentar más tarde, no alarmarse.
                return 409, {"error": "ya hay un ciclo corriendo", "retry": True}
            async with self._busy:
                return 200, await self._cycle_fn()

        if path == "/resolve" and method == "POST":
            return 200, await self._resolve_fn()

        if path == "/kill" and method == "POST":
            self._kill_fn("parada manual vía API")
            return 200, {"killed": True, "at": _now()}

        return 404, {"error": "no existe", "rutas": [
            "GET /health", "GET /report", "POST /cycle",
            "POST /resolve", "POST /kill",
        ]}

    # ── helpers ──────────────────────────────────────────────────────────

    def _safe_status(self) -> dict:
        base = {"ok": True, "uptime_s": round(time.monotonic() - self._started, 1),
                "now": _now()}
        try:
            base |= self._status_fn()
        except Exception as exc:  # noqa: BLE001
            base["status_error"] = repr(exc)
        return base

    @staticmethod
    async def _respond(writer, code: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode()
        head = (
            f"HTTP/1.1 {code} {_STATUS.get(code, 'OK')}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
        ).encode()
        writer.write(head + body)
        await writer.drain()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
