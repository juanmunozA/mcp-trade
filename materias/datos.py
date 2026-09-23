"""
Los datos del mercado, en las temporalidades que pide el analisis top-down.

DE DONDE SALEN
──────────────
Futuros de CME/COMEX/NYMEX via Yahoo Finance. La guia los prefiere
explicitamente sobre los CFD por una razon concreta (cap. 9.1): los
futuros muestran VOLUMEN REAL de contratos negociados, mientras que un
CFD solo puede dar tick volume porque el spot es descentralizado. Como
el capitulo 9 entero se apoya en leer precio contra volumen, usar la
fuente que lo tiene de verdad no es un detalle.

LAS HORAS YA VIENEN EN NUEVA YORK
La guia advierte que marcar las killzones con la zona horaria corrida
las vuelve inutiles, y que hay que verificar en que huso muestra el
grafico. Aca los timestamps llegan con `America/New_York` desde el
origen, asi que no hay conversion manual donde equivocarse.

QUE TEMPORALIDAD SIRVE PARA QUE (cap. 3)
    1D   sesgo direccional, PDH/PDL
    4H   estructura intermedia, zonas
    1H   mapa de la sesion
    15M  zona de ejecucion, ATR del filtro de stop
    5M   confirmacion y entrada
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("materias.datos")
NY = ZoneInfo("America/New_York")

ACTIVOS = {
    "GC=F": {"nombre": "Oro", "contrato": 100, "tick": 0.10,
             "margen_sl": 0.30, "spread": 0.10, "comision": 4.0},
    "SI=F": {"nombre": "Plata", "contrato": 5000, "tick": 0.005,
             "margen_sl": 0.01, "spread": 0.005, "comision": 4.0},
    "CL=F": {"nombre": "Petroleo WTI", "contrato": 1000, "tick": 0.01,
             "margen_sl": 0.03, "spread": 0.01, "comision": 4.0},
    "HG=F": {"nombre": "Cobre", "contrato": 25000, "tick": 0.0005,
             "margen_sl": 0.002, "spread": 0.0005, "comision": 4.0},
    "NG=F": {"nombre": "Gas natural", "contrato": 10000, "tick": 0.001,
             "margen_sl": 0.005, "spread": 0.001, "comision": 4.0},
}
"""
Los activos y el valor de su movimiento (cap. 1.4).

`contrato` es el tamano: el resultado de una operacion es
|salida - entrada| x contrato x lotes. `margen_sl` es el colchon de
spread que la guia suma al stop, escalado a lo que vale un tick en cada
uno -- 0,30 USD en oro no significa lo mismo en cobre.

El ORO es el principal y los demas son secundarios. No es una
preferencia: es el paso 1 del proceso de ocho pasos (cap. 20.1).

    «Elige UN SOLO ACTIVO (ej. XAUUSD) y una o dos sesiones. La
    especializacion permite conocer su "personalidad" (rango diario,
    horas de movimiento, reaccion a noticias).»

Y la estrategia que la guia desarrolla entera, la del capitulo 20.2, se
llama literalmente «Barrida de sesion en XAUUSD». El oro es el activo
sobre el que esta escrita.

Los otros cuatro siguen definidos y se siguen midiendo en el backtest,
porque comparar dice cosas que un activo solo no dice -- en la ultima
corrida el gas natural fue el unico con expectativa negativa, y eso es un
dato sobre el gas, no sobre el sistema. Pero el que opera en papel es el
oro: `PRINCIPAL`.

`spread` y `comision` existen porque el liston de la guia es explicito:
«basta un win rate sostenido superior a ~35-38 % INCLUYENDO COSTOS». Un
backtest sin costos mide otra cosa. El spread es de un tick, que es lo
tipico en estos futuros en horario liquido, y la comision son 4 USD por
contrato ida y vuelta -- un valor de broker de futuros corriente. En
precio, la comision es `comision / contrato`, y el coste total de una
operacion sale en torno a 1,4 ticks.
"""


def costo(simbolo: str) -> float:
    """
    Lo que cuesta abrir y cerrar una operacion, en unidades de precio.

    Spread (se paga una vez al entrar, porque el TP y el SL ya son
    niveles de salida) mas comision de ida y vuelta traducida a precio.
    Se descuenta del resultado en R dividiendolo por la distancia al
    stop: en una operacion con stop ajustado el mismo coste pesa mas,
    que es exactamente como funciona en la realidad.
    """
    a = ACTIVOS.get(simbolo, {})
    c = a.get("contrato", 1) or 1
    return a.get("spread", 0.0) + a.get("comision", 0.0) / c

PRINCIPAL = "GC=F"
"""
El activo sobre el que se opera. Todo lo demas es referencia.

El capitulo 20.1 pone la especializacion como PRIMER paso de los ocho, y
antes que el backtest y que la gestion. La razon que da es concreta:
conocer el rango diario, las horas en que se mueve y como reacciona a las
noticias. Eso no se aprende mirando cinco activos a la vez.

Cambiarlo es cambiar de experimento, no de parametro.
"""

SECUNDARIOS = ("SI=F", "HG=F", "CL=F", "NG=F")
"""
Se miden pero no se operan.

Sirven para dos cosas concretas de la guia: la SMT divergence del
capitulo 15.5 necesita la plata para leer el oro, y el backtest comparado
dice si un resultado es del sistema o del activo.
"""

TEMPORALIDADES = [("1d", "60d", "diario"), ("1h", "60d", "h1"),
                  ("15m", "1mo", "m15"), ("5m", "1mo", "m5")]
"""
Cuanta historia pedir de cada temporalidad.

Un mes en 5M es el techo de la fuente: mas atras devuelve "must be
within the last 60 days" y el rango explicito de 60 dias vuelve vacio.
Son ~5.900 velas por activo, seis veces mas que los 5 dias que se usaban
antes -- y aun asi la guia pide 100-300 operaciones para concluir algo
(cap. 18.7), con una estrategia que permite dos por dia como techo.
"""


def _filas(hist) -> list[dict]:
    """Convierte el DataFrame en filas simples, sin arrastrar pandas."""
    out = []
    for idx, r in hist.iterrows():
        dt = idx.to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY)
        out.append({"dt": dt.astimezone(NY), "t": str(dt),
                    "o": float(r["Open"]), "h": float(r["High"]),
                    "l": float(r["Low"]), "c": float(r["Close"]),
                    "v": float(r.get("Volume", 0) or 0)})
    return out


def _bajar(simbolo: str) -> dict:
    """Todas las temporalidades de un activo. Bloqueante; se llama en hilo."""
    import yfinance as yf

    warnings.filterwarnings("ignore")
    t = yf.Ticker(simbolo)
    out = {}
    for iv, per, clave in TEMPORALIDADES:
        try:
            h = t.history(period=per, interval=iv)
            out[clave] = _filas(h) if len(h) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("%s %s: %s", simbolo, iv, exc)
            out[clave] = []
    # 4H no lo sirve Yahoo: se arma agrupando 1H de a cuatro, alineado a
    # las horas pares para que coincida con lo que muestra un grafico.
    out["h4"] = _agrupar(out.get("h1", []), 4)
    return out


def _agrupar(velas: list[dict], n: int) -> list[dict]:
    """Junta n velas en una, conservando OHLC correctamente."""
    out = []
    for k in range(0, len(velas) - n + 1, n):
        g = velas[k:k + n]
        out.append({"dt": g[0]["dt"], "t": g[0]["t"], "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "v": sum(x["v"] for x in g)})
    return out


async def cargar(simbolos: list[str]) -> dict[str, dict]:
    """Baja todos los activos en paralelo."""
    tareas = [asyncio.to_thread(_bajar, s) for s in simbolos]
    res = await asyncio.gather(*tareas, return_exceptions=True)
    out = {}
    for s, r in zip(simbolos, res, strict=False):
        if isinstance(r, Exception):
            log.warning("%s fallo: %s", s, r)
            continue
        if r.get("m5"):
            out[s] = r
    return out


def contexto(simbolo: str, d: dict, ahora: datetime) -> dict:
    """
    Arma el contexto que consume la estrategia: velas + niveles marcados.

    Los niveles son los que el capitulo 10.3 declara obligatorios.
    """
    from materias import estructura as E
    from materias import sesiones as S

    m5 = d["m5"]
    asia = S.rango_asia(m5, ahora)
    niveles = S.niveles_diarios(d.get("diario", []))
    # Rango de Londres: de 03:00 a 12:00 ET del dia en curso.
    lon = [v for v in m5 if v["dt"].date() == ahora.date()
           and 3 <= v["dt"].hour < 12]
    londres = ({"alto": max(v["h"] for v in lon), "bajo": min(v["l"] for v in lon)}
               if lon else {})
    v5 = E.velas(m5)
    eq = E.equal_levels(E.swings(v5), E.atr(v5) * 0.25)
    return {
        "ahora": ahora, "m5": m5, "m15": d.get("m15", []),
        "h1": d.get("h1", []), "h4": d.get("h4", []), "diario": d.get("diario", []),
        "dt5": {i: v["dt"] for i, v in enumerate(m5)},
        "asia": asia, "londres": londres, "niveles": niveles,
        "tdo": S.true_day_open(m5, ahora),
        # El gap de apertura semanal, que el modelo 11.8 usa de objetivo.
        "nwog": E.nwog(d.get("h1", [])),
        "eqh": eq["eqh"], "eql": eq["eql"],
        # El colchon de spread del stop, escalado a este activo.
        "margen_sl": ACTIVOS.get(simbolo, {}).get("margen_sl", 0.30),
        "contrato": ACTIVOS.get(simbolo, {}).get("contrato", 1),
        "precio": m5[-1]["c"] if m5 else 0.0,
    }
