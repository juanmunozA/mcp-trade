"""
Superficie HTTP del experimento de cripto.

Sirve el tablero y los datos que consume. Todo lectura: este servicio
mide, no decide nada que alguien de afuera pueda disparar.

Sin token a proposito. No hay nada que proteger -- son predicciones
sobre precios publicos, sin claves ni dinero -- y pedirlo obligaria a
manejarlo en el navegador para ver un grafico.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pathlib
from datetime import UTC, datetime

import asyncpg

log = logging.getLogger("cripto.api")

DESDE = os.getenv("CRIPTO_DESDE", "").strip()
"""
Desde cuando mostrar. Las predicciones anteriores siguen en la base
-- sirven de comparacion: mismo modelo y metodo sobre pares que no
daban -- pero mezclarlas en el tablero ensucia la unica pregunta que
importa ahora, que es si esto funciona con pares que se mueven.

Vacio muestra todo.
"""


def _corte() -> tuple[str, list]:
    """
    Condicion y argumentos del filtro, para pegar en cada consulta.

    La fecha se parsea a datetime: asyncpg rechaza el texto aunque la
    consulta lleve el cast, porque valida el tipo antes de mandarlo.
    """
    if not DESDE:
        return "true", []
    try:
        d = datetime.fromisoformat(DESDE.replace("Z", "+00:00"))
    except ValueError:
        log.warning("CRIPTO_DESDE ilegible (%r), se muestra todo", DESDE)
        return "true", []
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return "ts >= $1", [d]
UI = pathlib.Path(__file__).with_name("ui.html")
NL = chr(10)
CRLF = chr(13) + chr(10)


async def resumen(pool: asyncpg.Pool) -> dict:
    cond, args = _corte()
    r = await pool.fetchrow("""
        SELECT count(*) n,
               count(*) FILTER (WHERE opero) operadas,
               avg((p_agente  - (subio)::int::float) ^ 2) brier_agente,
               avg((0.5       - (subio)::int::float) ^ 2) brier_azar,
               avg((p_inercia - (subio)::int::float) ^ 2) brier_inercia,
               avg((subio)::int::float) tasa_subida,
               avg(p_agente) p_media,
               count(*) FILTER (WHERE (p_agente > 0.5) = subio) aciertos,
               count(*) FILTER (WHERE opero AND (p_agente > 0.5) = subio) aciertos_op,
               coalesce(sum(pnl_bruto), 0) pnl_bruto,
               coalesce(sum(pnl_neto), 0) pnl_neto,
               min(ts) desde, max(ts) hasta
        FROM cripto.predicciones WHERE subio IS NOT NULL AND """ + cond,
        *args)
    d = dict(r)
    pend = await pool.fetchrow(
        "SELECT count(*) n, min(vence) prox FROM cripto.predicciones "
        "WHERE subio IS NULL AND " + cond, *args)
    d["en_vuelo"] = pend["n"]
    d["proxima_resolucion"] = pend["prox"]
    if d["n"]:
        d["acierto_pct"] = round(100 * d["aciertos"] / d["n"], 1)
        d["acierto_operadas_pct"] = (
            round(100 * d["aciertos_op"] / d["operadas"], 1) if d["operadas"] else None)
        d["pnl_bruto_pct"] = round(float(d["pnl_bruto"]) * 100, 4)
        d["pnl_neto_pct"] = round(float(d["pnl_neto"]) * 100, 4)
        # Sobre 20 dolares, que es el capital del que hablamos. Un
        # porcentaje suelto no dice nada; los centavos si.
        d["sobre_20"] = round(20 * float(d["pnl_neto"]), 4)
    return d


async def predicciones(pool: asyncpg.Pool, n: int = 120,
                       simbolo: str = "", solo_op: bool = False) -> list[dict]:
    c0, args = _corte()
    cond = [c0]
    if simbolo:
        args.append(simbolo)
        cond.append(f"simbolo = ${len(args)}")
    if solo_op:
        cond.append("opero")
    filas = await pool.fetch(
        f"SELECT * FROM cripto.predicciones WHERE {' AND '.join(cond)} "
        f"ORDER BY id DESC LIMIT {max(1, min(n, 400))}", *args)
    return [dict(f) for f in filas]


async def por_simbolo(pool: asyncpg.Pool) -> list[dict]:
    cond, args = _corte()
    filas = await pool.fetch("""
        SELECT simbolo, count(*) n,
               count(*) FILTER (WHERE opero) operadas,
               avg((p_agente - (subio)::int::float) ^ 2) brier,
               avg((0.5      - (subio)::int::float) ^ 2) brier_azar,
               count(*) FILTER (WHERE (p_agente > 0.5) = subio) aciertos,
               coalesce(sum(pnl_neto), 0) pnl
        FROM cripto.predicciones WHERE subio IS NOT NULL AND """ + cond + """
        GROUP BY 1 ORDER BY 7 DESC NULLS LAST""", *args)
    out = []
    for f in filas:
        d = dict(f)
        d["acierto_pct"] = round(100 * d["aciertos"] / d["n"], 1) if d["n"] else 0
        d["pnl_pct"] = round(float(d["pnl"]) * 100, 4)
        out.append(d)
    return out


async def serie(pool: asyncpg.Pool) -> list[dict]:
    """PnL acumulado en el tiempo. Es la curva que decide si sirve o no."""
    # coalesce porque las primeras resoluciones son anteriores a que
    # existieran estas columnas: sin eso la suma acumulada devuelve NULL
    # desde la primera fila vieja y la curva entera queda vacia.
    cond, args = _corte()
    filas = await pool.fetch("""
        SELECT ts,
               sum(coalesce(pnl_neto, 0))  OVER (ORDER BY id) acum_neto,
               sum(coalesce(pnl_bruto, 0)) OVER (ORDER BY id) acum_bruto
        FROM cripto.predicciones WHERE subio IS NOT NULL AND """ + cond + """
        ORDER BY id""", *args)
    return [{"t": f["ts"].isoformat(),
             "neto": round(float(f["acum_neto"]) * 100, 4),
             "bruto": round(float(f["acum_bruto"]) * 100, 4)} for f in filas]


def _json(obj) -> bytes:
    return json.dumps(obj, default=str, ensure_ascii=False).encode()


async def servir(pool: asyncpg.Pool, puerto: int) -> None:
    async def atender(reader, writer):
        # No se usa suppress a secas: un error dentro cortaba la conexion
        # sin dejar rastro, y depurar eso desde el navegador es adivinar.
        try:
            linea = await asyncio.wait_for(reader.readline(), timeout=8)
            partes = linea.decode("latin-1").split()
            ruta = partes[1] if len(partes) > 1 else "/"
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=8)
                if not h.strip():
                    break

            camino, _, consulta = ruta.partition("?")
            q = dict(p.split("=", 1) for p in consulta.split("&") if "=" in p)
            camino = camino.rstrip("/") or "/"
            tipo, cuerpo = "application/json; charset=utf-8", b"{}"

            if camino in ("/", "/ui"):
                tipo = "text/html; charset=utf-8"
                cuerpo = UI.read_bytes() if UI.exists() else b"<p>sin tablero"
            elif camino == "/api/resumen":
                cuerpo = _json(await resumen(pool))
            elif camino == "/api/predicciones":
                cuerpo = _json({"items": await predicciones(
                    pool, int(q.get("n", 120)), q.get("simbolo", ""),
                    q.get("op") == "1")})
            elif camino == "/api/simbolos":
                cuerpo = _json({"items": await por_simbolo(pool)})
            elif camino == "/api/serie":
                cuerpo = _json({"items": await serie(pool)})
            elif camino == "/salud":
                cuerpo = _json({"ok": True,
                                "at": datetime.now(UTC).isoformat(timespec="seconds")})
            else:
                cuerpo = _json({"error": "no existe", "rutas": [
                    "/ui", "/api/resumen", "/api/predicciones", "/api/simbolos",
                    "/api/serie", "/salud"]})

            cab = (f"HTTP/1.1 200 OK{NL}Content-Type: {tipo}{NL}"
                   f"Content-Length: {len(cuerpo)}{NL}"
                   f"Cache-Control: no-store{NL}"
                   f"Access-Control-Allow-Origin: *{NL}"
                   f"Connection: close{NL}{NL}").replace(NL, "\r\n").encode()
            writer.write(cab + cuerpo)
            await writer.drain()
        except Exception as exc:  # noqa: BLE001
            log.warning("fallo sirviendo %s: %r", ruta if "ruta" in dir() else "?", exc)
            with contextlib.suppress(Exception):
                err = _json({"error": repr(exc)[:200]})
                writer.write((f"HTTP/1.1 500 Internal Server Error{NL}"
                              f"Content-Type: application/json{NL}"
                              f"Content-Length: {len(err)}{NL}"
                              f"Connection: close{NL}{NL}")
                             .replace(NL, CRLF).encode() + err)
                await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()

    await asyncio.start_server(atender, "0.0.0.0", puerto)
