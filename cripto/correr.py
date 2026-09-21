"""
El experimento: predecir, esperar, resolver, puntuar.  `python -m cripto.correr`

QUE MIDE
────────
Si un LLM con mucha informacion de mercado le gana a la moneda al aire
prediciendo direccion a 15 minutos. El liston es 0.50 -- si no lo pasa,
la respuesta es que no tiene nada.

Se guardan tres competidores sobre exactamente los mismos momentos:

    agente   lo que dijo el modelo
    azar     0.50 fijo
    inercia  0.5 + el ultimo movimiento de 5 minutos, acotado

La inercia esta a proposito: es la estrategia mas tonta que existe
("siguio subiendo, va a seguir"). Si el modelo no le gana NI A ESO, no
hay nada que construir.

Sin dinero. Solo numeros.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import UTC, datetime, timedelta

import asyncpg
from dotenv import load_dotenv

from cripto.datos import fotos, precios
from cripto.predecir import ErrorIA, predecir

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("cripto")

PARES = os.getenv("CRIPTO_PARES", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,"
                                  "XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT").split(",")
CADA_S = int(os.getenv("CRIPTO_CADA_S", "300"))
HORIZONTE_MIN = int(os.getenv("CRIPTO_HORIZONTE_MIN", "15"))
HASTA = os.getenv("CRIPTO_HASTA", "")

ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS cripto;
CREATE TABLE IF NOT EXISTS cripto.predicciones (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence       TIMESTAMPTZ NOT NULL,
    simbolo     TEXT NOT NULL,
    precio_ini  DOUBLE PRECISION NOT NULL,
    p_agente    DOUBLE PRECISION NOT NULL,
    p_inercia   DOUBLE PRECISION NOT NULL,
    confianza   TEXT,
    razon       TEXT,
    modelo      TEXT,
    contexto    JSONB,
    precio_fin  DOUBLE PRECISION,
    subio       BOOLEAN,
    resuelto_en TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_cp_pend ON cripto.predicciones (vence)
    WHERE subio IS NULL;
"""


def inercia(f: dict) -> float:
    """
    El competidor tonto: si acaba de subir, apuesta a que sigue.

    Acotado a [0.35, 0.65] porque sin tope una vela grande lo llevaria a
    0.99 y perderia por goleada -- y entonces ganarle no probaria nada.
    """
    p = 0.5 + max(-0.15, min(0.15, f.get("cambio_5m", 0.0) / 100 * 8))
    return round(p, 4)


async def ciclo(pool: asyncpg.Pool) -> int:
    fs = await fotos(PARES)
    if not fs:
        log.warning("sin datos de mercado")
        return 0
    try:
        preds = await predecir(fs)
    except ErrorIA as exc:
        log.warning("el modelo no respondió: %s", exc)
        return 0

    porf = {f["simbolo"]: f for f in fs}
    ahora = datetime.now(UTC)
    vence = ahora + timedelta(minutes=HORIZONTE_MIN)
    filas = []
    for p in preds:
        f = porf.get(p["simbolo"])
        if not f:
            continue
        filas.append((vence, p["simbolo"], f["precio"], p["p_sube"], inercia(f),
                      p["confianza"], p["razon"], p["modelo"],
                      __import__("json").dumps(f, ensure_ascii=False)))
    if filas:
        await pool.executemany(
            "INSERT INTO cripto.predicciones (vence,simbolo,precio_ini,p_agente,"
            "p_inercia,confianza,razon,modelo,contexto) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)", filas)
    media = sum(p["p_sube"] for p in preds) / len(preds)
    fuera = sum(1 for p in preds if abs(p["p_sube"] - 0.5) > 0.1)
    log.info("%d predicciones | media %.3f | %d se alejan de 0.50",
             len(filas), media, fuera)
    return len(filas)


async def resolver(pool: asyncpg.Pool) -> int:
    pend = await pool.fetch(
        "SELECT id, simbolo, precio_ini FROM cripto.predicciones "
        "WHERE subio IS NULL AND vence <= now() LIMIT 400")
    if not pend:
        return 0
    px = await precios(sorted({r["simbolo"] for r in pend}))
    if not px:
        return 0
    ahora = datetime.now(UTC)
    up = [(px[r["simbolo"]], px[r["simbolo"]] > r["precio_ini"], ahora, r["id"])
          for r in pend if r["simbolo"] in px]
    if up:
        await pool.executemany(
            "UPDATE cripto.predicciones SET precio_fin=$1, subio=$2, "
            "resuelto_en=$3 WHERE id=$4", up)
        log.info("resueltas %d (subieron %d)", len(up), sum(1 for x in up if x[1]))
    return len(up)


async def tablero(pool: asyncpg.Pool) -> dict:
    r = await pool.fetchrow("""
        SELECT count(*) n,
               avg((p_agente  - (subio)::int::float) ^ 2) brier_agente,
               avg((0.5       - (subio)::int::float) ^ 2) brier_azar,
               avg((p_inercia - (subio)::int::float) ^ 2) brier_inercia,
               avg((subio)::int::float) tasa_subida,
               avg(p_agente) p_media,
               count(*) FILTER (WHERE (p_agente > 0.5) = subio) aciertos,
               count(*) FILTER (WHERE abs(p_agente - 0.5) > 0.05) se_moja
        FROM cripto.predicciones WHERE subio IS NOT NULL""")
    d = {k: (float(v) if isinstance(v, (int, float)) and v is not None else v)
         for k, v in dict(r).items()}
    if d["n"]:
        d["acierto_pct"] = round(100 * d["aciertos"] / d["n"], 1)
    return d


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("-pooler", "")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    async with pool.acquire() as c:
        await c.execute(ESQUEMA)

    fin = datetime.fromisoformat(HASTA) if HASTA else None
    parar = asyncio.Event()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, parar.set)
        except NotImplementedError:
            pass  # Windows

    log.info("arranca | %d pares | cada %ds | horizonte %dmin%s",
             len(PARES), CADA_S, HORIZONTE_MIN,
             f" | hasta {fin:%H:%M}" if fin else "")
    n = 0
    while not parar.is_set():
        if fin and datetime.now(UTC) >= fin:
            log.info("llegó la hora de parar")
            break
        try:
            await resolver(pool)
            n += await ciclo(pool)
            t = await tablero(pool)
            if t["n"]:
                log.info("acumulado: %d resueltas | agente %.4f | azar %.4f | "
                         "inercia %.4f | acierto %.1f%%",
                         t["n"], t["brier_agente"], t["brier_azar"],
                         t["brier_inercia"], t["acierto_pct"])
        except Exception:  # noqa: BLE001
            log.exception("ciclo falló")
        with __import__("contextlib").suppress(TimeoutError):
            await asyncio.wait_for(parar.wait(), timeout=CADA_S)

    # Ultima pasada: sin esto, lo predicho en los ultimos 15 minutos
    # queda sin resolver y se pierde.
    log.info("esperando lo último y resolviendo…")
    await asyncio.sleep(min(HORIZONTE_MIN * 60, 60))
    await resolver(pool)
    log.info("final: %s", await tablero(pool))
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
