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
from cripto.modelo import Modelo
from cripto import aprender as ap
from cripto import saber
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
ORIGEN = (os.getenv("CRIPTO_ORIGEN", "").strip()
          or ("JJ" if os.getenv("RENDER_EXTERNAL_URL") else "JP"))
"""
Quien escribio esta fila.

Varias personas corren el experimento a la vez contra la MISMA base, asi
que sin esto no hay forma de saber si una tanda salio del contenedor o
del portatil de alguien -- y dos tandas pueden caer en el mismo segundo,
que ya paso.

Por defecto `JJ` en Render (la variable RENDER_EXTERNAL_URL solo existe
alla) y `JP` en cualquier otro lado. Con `CRIPTO_ORIGEN` cada uno pone
la suya, que es lo que hace falta cuando los locales son mas de uno.
"""

SABER_TOPE = int(os.getenv("CRIPTO_SABER_TOPE", "4500"))
"""
Cuantos caracteres de notas entran en cada prediccion.

No es una preferencia de estilo: el tier gratuito de Groq admite 8.000
tokens por minuto y las notas completas ya son 10.145 tokens. Con todo
puesto la llamada devuelve 413 y el ciclo se pierde entero.

Y el limite cuenta el `max_tokens` pedido ANTES de generar: con 7.000
caracteres de notas el request entra en ~5.700 tokens y deja lugar para
los 1.800 de respuesta. En 0 se manda todo, que sirve con un proveedor
sin este techo.
"""

SOLO_REGRESION = os.getenv("CRIPTO_SOLO_REGRESION", "").strip().lower() in ("1", "true", "si")
"""
Correr sin modelo de lenguaje: la regresion decide sola.

No es un modo degradado. Medido sobre las mismas 261 operaciones, dejando
un ciclo afuera por vez, la regresion saco 54,4% de acierto y +17,02%
neto, y el modelo de lenguaje leyendo esas mismas tablas saco 47,1% y
-62,15%. La parte que no necesita llave es la que mejor anda.

Se activa sola si falta GEMINI_API_KEY: sin esto, un ciclo sin llave
tiraba ErrorIA y descartaba la p_base que ya tenia calculada.
"""
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
-- La probabilidad de la regresion, aparte de la del modelo de lenguaje.
-- Guardar las dos es lo unico que despues contesta si el LLM aporta o estorba.
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS p_base DOUBLE PRECISION;
-- De donde salio la fila. Ver el comentario de ORIGEN.
ALTER TABLE cripto.predicciones ADD COLUMN IF NOT EXISTS origen TEXT;
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


async def ciclo(pool: asyncpg.Pool, modelo: Modelo) -> int:
    fs, ref = await fotos(PARES)
    if not fs:
        log.warning("sin datos de mercado")
        return 0
    # La regresion primero. El modelo de lenguaje la recibe hecha en vez de
    # tener que interpolar una tabla de cabeza, que es lo que peor hace.
    await modelo.aprender(pool)
    base = modelo.p_base(fs)

    sin_llave = not os.getenv("GEMINI_API_KEY", "").strip()
    if (SOLO_REGRESION or sin_llave) and base:
        preds = [{
            "simbolo": s, "p_sube": p, "razon": "regresion sola, sin modelo de lenguaje",
            "confianza": "media", "fundamento": "regresion",
            "dato": modelo.resumen()[:120],
            # El tamano sale de la regla medida: |mov| ~ volatilidad x 0,83.
            "mov_esperado": round(
                next((f.get("volatilidad_pct", 0.5) for f in fs if f["simbolo"] == s), 0.5)
                * 0.83 * (1 if p > 0.5 else -1), 3),
            "modelo": "regresion",
        } for s, p in base.items()]
        log.info("%d predicciones por regresion sola (%s)", len(preds),
                 "pedido por config" if SOLO_REGRESION else "sin GEMINI_API_KEY")
        return await _guardar(pool, preds, fs, base)

    if not base and (SOLO_REGRESION or sin_llave):
        log.warning("sin modelo de lenguaje y la regresion todavia no tiene "
                    "suficientes resueltas: este ciclo no predice")
        return 0
    try:
        # Se le pasa su propio resultado: cada llamada arranca sin memoria,
        # asi que si nadie le muestra la cuenta, repite el error toda la noche.
        preds = await predecir(fs, historial=await tablero(pool), referencia=ref,
                               base=base, nota_base=modelo.resumen(),
                               saber_texto=await saber.texto(pool, SABER_TOPE))
    except ErrorIA as exc:
        log.warning("el modelo no respondió: %s", exc)
        return 0

    return await _guardar(pool, preds, fs, base)


async def _guardar(pool: asyncpg.Pool, preds: list[dict], fs: list[dict],
                   base: dict | None) -> int:
    """Escribe la tanda. Separado para que los dos caminos -- con modelo de
    lenguaje y sin el -- guarden exactamente igual y sean comparables."""
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
                      p.get("mov_esperado", 0.0),
                      (base or {}).get(p["simbolo"]), ORIGEN))
    if filas:
        await pool.executemany(
            "INSERT INTO cripto.predicciones (vence,simbolo,precio_ini,p_agente,"
            "p_inercia,confianza,razon,modelo,contexto,fundamento,dato,mov_esperado,"
            "p_base,origen) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14)", filas)
    media = sum(p["p_sube"] for p in preds) / len(preds)
    con = sum(1 for p in preds if p.get("fundamento", "sin_senal") != "sin_senal")
    desvio = (sum(abs(p["p_sube"] - base[p["simbolo"]])
                  for p in preds if p["simbolo"] in base) / len(preds)) if base else 0.0
    log.info("%d predicciones | media %.3f | %d con fundamento | se aparto %.3f "
             "de la regresion | %s", len(filas), media, con, desvio,
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
        # Cual de sus propias razones sirve. El promedio global le dice que
        # esta perdiendo; esto le dice POR DONDE, que es lo accionable.
        d["por_fundamento"] = [dict(r) for r in await pool.fetch("""
            SELECT fundamento, count(*) n,
                   round(100.0 * count(*) FILTER (WHERE (p_agente > 0.5) = subio)
                         / count(*), 1) acierto_pct,
                   round((sum(pnl_neto) * 100)::numeric, 2) neto_pct
            FROM cripto.predicciones
            WHERE subio IS NOT NULL AND fundamento IS NOT NULL
            GROUP BY fundamento HAVING count(*) >= 5 ORDER BY sum(pnl_neto) DESC""")]
        # Cuanto se mueven de verdad estos pares, contra lo que el dijo que se
        # moverian. La primera noche lo subestimo por mas del doble.
        cal = await pool.fetchrow("""
            SELECT round(avg(abs(mov_esperado))::numeric, 3) esperado,
                   round((percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY abs(precio_fin / precio_ini - 1)) * 100)::numeric, 3) real
            FROM cripto.predicciones
            WHERE subio IS NOT NULL AND precio_ini > 0 AND mov_esperado IS NOT NULL""")
        if cal and cal["real"]:
            d["mov_esperado_medio"] = float(cal["esperado"] or 0)
            d["mov_real_mediano"] = float(cal["real"])
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
    await saber.preparar(pool)

    await _servidor(pool)
    modelo = Modelo()
    fin = datetime.fromisoformat(HASTA) if HASTA else None
    parar = asyncio.Event()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, parar.set)
        except NotImplementedError:
            pass  # Windows

    # Que proveedor va a usar y por que, dicho una sola vez al arrancar.
    # Un cambio de variable de entorno en Render actualiza la configuracion
    # guardada pero NO mata el proceso corriendo -- eso paso de verdad, un
    # `restart` respondio sano en 12 segundos, demasiado rapido para haber
    # reconstruido nada, y el proceso viejo siguio leyendo su copia vieja
    # de SOLO_REGRESION media hora despues de haberla borrado. Sin esta
    # linea, esa clase de desfasaje solo se ve mirando el comportamiento,
    # nunca los logs.
    _prov = ("config: solo regresion" if SOLO_REGRESION else
             "ANTHROPIC" if os.getenv("ANTHROPIC_API_KEY", "").strip() else
             "DEEPSEEK" if os.getenv("DEEPSEEK_API_KEY", "").strip() else
             "GROQ" if os.getenv("GROQ_API_KEY", "").strip() else
             "MISTRAL" if os.getenv("MISTRAL_API_KEY", "").strip() else
             "GEMINI" if os.getenv("GEMINI_API_KEY", "").strip() else
             "ninguno -- solo regresion por falta de llave")
    log.info("proveedor de este arranque: %s", _prov)
    log.info("arranca [%s] | %d pares | cada %ds | horizonte %dmin%s",
             ORIGEN, len(PARES), CADA_S, HORIZONTE_MIN,
             f" | hasta {fin:%H:%M}" if fin else "")
    n = 0
    while not parar.is_set():
        if fin and datetime.now(UTC) >= fin:
            log.info("llegó la hora de parar")
            break
        try:
            await resolver(pool)
            # Aprender antes de predecir: si toca reescribir las notas, que
            # la tanda de este ciclo ya use lo aprendido y no lo anterior.
            if await ap.toca(pool):
                log.info("hay material nuevo: reescribiendo las notas…")
                log.info("aprendizaje: %s", await ap.aprender(pool))
            n += await ciclo(pool, modelo)
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
