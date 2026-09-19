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

DÓNDE ESTÁ EL CORTE
───────────────────
La unidad que n8n orquesta es **un mercado**, no un ciclo entero:

    GET  /candidates   el explorador. Sin IA, sin costo.
    POST /forecast     investiga y pronostica. NO toca el mercado.
    POST /trade        cotiza, dimensiona, valida riesgo y ordena.

El corte entre `/forecast` y `/trade` es donde el orquestador decide: ve
la probabilidad, la divergencia y los vetos, y elige si vale la pena
operarlo. Esa política deja de estar enterrada en Python.

`/market` hace las dos de un tirón, para quien no quiera orquestar.

MÁS FINO NO SE PUEDE. `validar` y `ejecutar` comparten el estado en
memoria del risk kernel y corren bajo un lock: entre `check()` y
`on_order_sent()` hay awaits, y dos mercados que los crucen a la vez pasan
el mismo límite antes de que ninguno lo consuma. Un salto HTTP ahí
reintroduce esa carrera —ya la tuvimos— y la hace sobrevivir a reinicios.

LO QUE EL ORQUESTADOR NO DECIDE
Los tres vetos y el risk kernel. Son seguridad, no política: si vivieran
en nodos editables, un workflow mal tocado los apaga sin que nadie lo
note. `/trade` los aplica siempre, aunque el orquestador insista.

ENDPOINTS
    GET  /health      estado, presupuesto, kill switch  (también UptimeRobot)
    GET  /report      tablero de aprendizaje: ¿hay edge?
    GET  /candidates  mercados elegidos por el explorador  [?n=8]
    GET  /geo         desde qué país ve Polymarket al bot
    POST /market      un mercado por el pipeline           {"slug": "..."}
    POST /cycle       ciclo completo (equivale a candidates + todos)
    POST /resolve     busca mercados resueltos y aprende
    POST /kill        para todo (botón de pánico desde n8n o Telegram)

SEGURIDAD
Si `AGENT_API_TOKEN` está definido, todo menos /health exige
`Authorization: Bearer <token>`. En Render el servicio es público: sin
token, cualquiera podría disparar ciclos y quemarte el presupuesto.

`/market` solo acepta slugs que el explorador ya eligió en `/candidates`.
No es una comodidad: es lo que impide que quien pueda llamar a la API
—n8n, un workflow mal editado, cualquiera con el token— meta un mercado
arbitrario en la ruta de ejecución saltándose el filtro y la whitelist
de tokens del kernel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote

log = logging.getLogger("api")

_STATUS = {
    200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized",
    404: "Not Found", 409: "Conflict", 413: "Payload Too Large",
    500: "Internal Server Error",
}

# Los bodies aquí son `{"slug": "..."}`. Cualquier cosa más grande es un
# error o un abuso, y leerla entera sería el abuso.
_MAX_BODY = 64 * 1024


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
        candidates_fn: Callable[[int], Awaitable[dict]] | None = None,
        market_fn: Callable[[str], Awaitable[dict]] | None = None,
        forecast_fn: Callable[[str], Awaitable[dict]] | None = None,
        trade_fn: Callable[[str], Awaitable[dict]] | None = None,
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
        self._candidates_fn = candidates_fn
        self._market_fn = market_fn
        self._forecast_fn = forecast_fn
        self._trade_fn = trade_fn

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
            path_part, _, query_part = raw_path.partition("?")
            path = path_part.rstrip("/") or "/"
            query = _parse_query(query_part)

            headers: dict[str, str] = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=10)
                if h in (b"\r\n", b"\n", b""):
                    break
                if b":" in h:
                    k, _, v = h.decode("latin-1").partition(":")
                    headers[k.strip().lower()] = v.strip()

            body: dict = {}
            n = int(headers.get("content-length", "0") or 0)
            if 0 < n <= _MAX_BODY:
                raw = await asyncio.wait_for(reader.readexactly(n), timeout=10)
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    parsed = json.loads(raw.decode("utf-8"))
                    if isinstance(parsed, dict):
                        body = parsed
            elif n > _MAX_BODY:
                # Se drena igual: dejar bytes sin leer en el socket hace que
                # el cliente vea un reset en vez de la respuesta.
                await asyncio.wait_for(reader.readexactly(n), timeout=10)
                await self._respond(writer, 413, {"error": "body demasiado grande"})
                return

            code, payload = await self._route(method, path, headers, query, body)
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

    async def _route(
        self, method: str, path: str, headers: dict, query: dict, body: dict
    ) -> tuple[int, dict]:
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

        # ── orquestación fina: n8n hace el fan-out ───────────────────────
        if path == "/candidates" and method == "GET":
            if self._candidates_fn is None:
                return 404, {"error": "no disponible"}
            if self._busy.locked():
                # /candidates reabre la whitelist de tokens del kernel.
                # Hacerlo a mitad de un ciclo le cambiaría el suelo al ciclo
                # que ya está corriendo.
                return 409, {"error": "ya hay un ciclo corriendo", "retry": True}
            n = _as_int(query.get("n"), default=0)
            return 200, await self._candidates_fn(n)

        if path == "/market" and method == "POST":
            if self._market_fn is None:
                return 404, {"error": "no disponible"}
            if self._busy.locked():
                return 409, {"error": "ya hay un ciclo corriendo", "retry": True}
            slug = str(body.get("slug", "")).strip()
            if not slug:
                return 400, {"error": "falta 'slug' en el body"}
            return 200, await self._market_fn(slug)

        for ruta, fn in (("/forecast", self._forecast_fn), ("/trade", self._trade_fn)):
            if path == ruta and method == "POST":
                if fn is None:
                    return 404, {"error": "no disponible"}
                if self._busy.locked():
                    return 409, {"error": "ya hay un ciclo corriendo", "retry": True}
                slug = str(body.get("slug", "")).strip()
                if not slug:
                    return 400, {"error": "falta 'slug' en el body"}
                return 200, await fn(slug)

        if path == "/geo" and method == "GET":
            return 200, await _geoblock()

        if path == "/resolve" and method == "POST":
            return 200, await self._resolve_fn()

        if path == "/kill" and method == "POST":
            motivo = str(body.get("reason", "")).strip() or "parada manual vía API"
            self._kill_fn(motivo[:300])
            return 200, {"killed": True, "reason": motivo[:300], "at": _now()}

        return 404, {"error": "no existe", "rutas": [
            "GET /health", "GET /report", "GET /candidates", "GET /geo",
            "POST /forecast", "POST /trade", "POST /market",
            "POST /cycle", "POST /resolve", "POST /kill",
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


async def _geoblock() -> dict:
    """
    Desde dónde sale el bot, según Polymarket.

    Responde lo que hace la *web*, no el CLOB: hay países con la web
    cerrada y la API abierta. Por eso `blocked: true` es señal para
    revisar antes de pasar a real, no veredicto de que no se pueda.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get("https://polymarket.com/api/geoblock")
        datos = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {"raw": r.text[:200]}
        return {"http": r.status_code, "web": datos, "at": _now()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "at": _now()}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_query(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        return {k: v[0] for k, v in parse_qs(unquote(raw)).items() if v}
    except Exception:  # noqa: BLE001 — un query roto no es motivo de 500
        return {}


def _as_int(raw: object, *, default: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default
