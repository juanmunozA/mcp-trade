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
COSTO = float(os.getenv("CRIPTO_COSTO_PCT", "0.10")) / 100
"""Comision de ida y vuelta en futuros, nivel base. Es el listón real."""
UMBRAL_OP = float(os.getenv("CRIPTO_UMBRAL_OP", "0"))
"""
Cuanto tiene que alejarse de 0.50 para que cuente como operacion.

En 0 significa ENTRADA FORZADA: se toma posicion siempre, en todos los
pares y todos los ciclos. Es una directriz del operador, no un hallazgo.

La aritmetica dice que pierde: 0.10% de comision contra 0.077% de
movimiento tipico. Queda medido igual -- pnl_bruto contra pnl_neto en
cada fila -- asi que en doscientas operaciones la curva lo confirma o
lo desmiente con datos propios en vez de con un calculo.
"""
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
    resuelto_en TIMESTAMPTZ,
    -- La plata, que es lo unico que decide. Acertar y perder es posible
    -- y de hecho es lo normal: el peaje se cobra igual.
    opero       BOOLEAN,
    pnl_bruto   DOUBLE PRECISION,
    pnl_neto    DOUBLE PRECISION,
    -- Con que justifico el lado. Guardarlo por separado permite medir
    -- DESPUES cual fundamento acierta y cual es cuento.
    fundamento  TEXT,
    dato        TEXT,
    mov_esperado DOUBLE PRECISION
);
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS opero BOOLEAN;
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS pnl_bruto DOUBLE PRECISION;
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS pnl_neto DOUBLE PRECISION;
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS fundamento TEXT;
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS dato TEXT;
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS mov_esperado DOUBLE PRECISION;
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
    fs, ref = await fotos(PARES)
    if not fs:
        log.warning("sin datos de mercado")
        return 0
    try:
        # Se le pasa su propio resultado: cada llamada arranca sin memoria,
        # asi que si nadie le muestra la cuenta, repite el error toda la noche.
        preds = await predecir(fs, historial=await tablero(pool), referencia=ref)
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
                      __import__("json").dumps(f, ensure_ascii=False),
                      p.get("fundamento", "sin_senal"), p.get("dato", ""),
                      p.get("mov_esperado", 0.0)))
    if filas:
        await pool.executemany(
            "INSERT INTO cripto.predicciones (vence,simbolo,precio_ini,p_agente,"
            "p_inercia,confianza,razon,modelo,contexto,fundamento,dato,mov_esperado) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12)", filas)
    media = sum(p["p_sube"] for p in preds) / len(preds)
    con = sum(1 for p in preds if p.get("fundamento", "sin_senal") != "sin_senal")
    log.info("%d predicciones | media %.3f | %d con fundamento | %s",
             len(filas), media, con,
             ", ".join(sorted({p.get("fundamento", "?") for p in preds})))
    return len(filas)


async def resolver(pool: asyncpg.Pool) -> int:
    pend = await pool.fetch(
        "SELECT id, simbolo, precio_ini, p_agente FROM cripto.predicciones "
        "WHERE subio IS NULL AND vence <= now() LIMIT 400")
    if not pend:
        return 0
    px = await precios(sorted({r["simbolo"] for r in pend}))
    if not px:
        return 0
    ahora = datetime.now(UTC)
    up = []
    for r in pend:
        if r["simbolo"] not in px:
            continue
        ini = r["precio_ini"]
        if not ini:
            # Un precio guardado en cero no se puede resolver. Se marca y
            # sigue: antes una sola fila asi tumbaba toda la tanda.
            log.warning("precio_ini en cero (%s, id %s), se descarta",
                        r["simbolo"], r["id"])
            await pool.execute("UPDATE cripto.predicciones SET subio=NULL, "
                               "vence = now() + interval '100 years' WHERE id=$1",
                               r["id"])
            continue
        fin = px[r["simbolo"]]
        subio = fin > ini
        mov = fin / ini - 1
        opero = abs(r["p_agente"] - 0.5) >= UMBRAL_OP if UMBRAL_OP > 0 else True
        # Largo si dijo que sube, corto si dijo que baja. Sin operar, cero:
        # no cobrar comision por quedarse quieto es parte del resultado.
        bruto = (mov if r["p_agente"] > 0.5 else -mov) if opero else 0.0
        neto = (bruto - COSTO) if opero else 0.0
        up.append((fin, subio, ahora, opero, bruto, neto, r["id"]))
    if up:
        await pool.executemany(
            "UPDATE cripto.predicciones SET precio_fin=$1, subio=$2, resuelto_en=$3,"
            " opero=$4, pnl_bruto=$5, pnl_neto=$6 WHERE id=$7", up)
        neto = sum(x[5] for x in up)
        log.info("resueltas %d | operó %d | neto %+.3f%%",
                 len(up), sum(1 for x in up if x[3]), neto * 100)
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
               count(*) FILTER (WHERE opero) operadas,
               coalesce(sum(pnl_bruto), 0) pnl_bruto,
               coalesce(sum(pnl_neto), 0) pnl_neto
        FROM cripto.predicciones WHERE subio IS NOT NULL""")
    d = {k: (float(v) if isinstance(v, (int, float)) and v is not None else v)
         for k, v in dict(r).items()}
    if d["n"]:
        d["acierto_pct"] = round(100 * d["aciertos"] / d["n"], 1)
        d["pnl_bruto_pct"] = round(d["pnl_bruto"] * 100, 3)
        d["pnl_neto_pct"] = round(d["pnl_neto"] * 100, 3)
        d["sobre_20usd"] = round(20 * d["pnl_neto"], 4)
    return d


async def _servidor(pool: asyncpg.Pool) -> None:
    """
    Tablero y auto-ping.

    Render mata lo que no escucha un puerto y duerme lo que no recibe
    trafico durante 15 minutos. Pegarse a la propia URL publica cuenta
    como trafico entrante: se mantiene despierto solo, sin servicio
    externo ni PC encendido.
    """
    import contextlib

    from cripto.api import servir

    await servir(pool, int(os.getenv("PORT", "10000")))

    url = os.getenv("RENDER_EXTERNAL_URL", "")
    if not url:
        return
    import httpx as _httpx

    async def despertarse():
        while True:
            await asyncio.sleep(600)
            with contextlib.suppress(Exception):
                async with _httpx.AsyncClient(timeout=30) as c:
                    await c.get(url + "/salud")

    asyncio.create_task(despertarse())


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("-pooler", "")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    async with pool.acquire() as c:
        await c.execute(ESQUEMA)

    await _servidor(pool)
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
