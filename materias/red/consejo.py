"""
Lo que la parte numerica le cuenta al agente en cada decision.

El agente razona con la guia. Esto le añade, al mismo tiempo, lo que los
datos midieron, CON SU FIABILIDAD AL LADO: un numero sin la nota de cuanto
vale es una invitacion a creerselo.

DOS COSAS, Y NO VALEN LO MISMO
1. La tabla de pips por hora. Sale de 100.000 velas de 5M: cuanto suele
   retroceder el precio antes de moverse a favor y cuanto se mueve en una
   hora, segun la hora de Nueva York. Es lo mas solido que salio del
   estudio: ninguna red le gano. Sirve para el STOP (que no lo barra el
   retroceso normal) y para la ENTRADA (limitada donde suele llegar el
   retroceso en vez de a mercado).
2. La red de direccion. Se le pasa como dato, pero con su AUC medido en
   datos que no vio. Con AUC 0,51 no tiene ventaja y el agente tiene que
   saberlo para no apoyarse en ella.
"""

from __future__ import annotations

import json
from datetime import UTC
from functools import lru_cache

import numpy as np

from materias.red import datos as D
from materias.red import etiquetas as E
from materias.red.pips import DESFASE_NY, hora_ny

OBJETIVO_1H = 50.0     # pips: las entradas «que funcionaron» en una hora


@lru_cache(maxsize=1)
def tabla() -> dict[int, dict]:
    """Por hora de NY: retroceso previo de las ganadoras y rango de la hora."""
    v = D.cargar("M5")
    e = E.calcular(v, 12)
    h = hora_ny(e["tiempo"]).astype(int)
    rango = e["largo_mfe"] + e["largo_mae"]
    out = {}
    for hh in range(24):
        m = h == hh
        if m.sum() < 200:
            continue
        fila = {"n": int(m.sum()), "rango_p50": float(np.median(rango[m]))}
        for lado in ("largo", "corto"):
            g = m & (e[f"{lado}_mfe"] >= OBJETIVO_1H)
            pre = e[f"{lado}_mae_previa"][g]
            fila[lado] = {"p50": float(np.percentile(pre, 50)),
                          "p75": float(np.percentile(pre, 75))}
        out[hh] = fila
    return out


def metricas() -> dict:
    try:
        return json.loads((D.CARPETA / "metricas.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _a_array(m5: list[dict]) -> np.ndarray:
    """Las velas del contexto del agente al formato de MT5 que usa la red."""
    dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                   ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"),
                   ("real_volume", "i8")])
    a = np.zeros(len(m5), dtype=dt)
    for i, x in enumerate(m5):
        # hora de NY como si fuera UTC, mas el desfase del servidor
        pared = x["dt"].replace(tzinfo=UTC).timestamp()
        a[i] = (int(pared) + DESFASE_NY, x["o"], x["h"], x["l"], x["c"], 0, 0, 0)
    return a


def prob_subida(m5: list[dict]) -> float | None:
    """Probabilidad de que el precio este mas alto dentro de una hora."""
    ruta = D.CARPETA / "red_direccion.npz"
    if not ruta.exists() or len(m5) < 320:
        return None
    from materias.red import entradas as F
    from materias.red.modelo import Red
    X, _ = F.calcular(_a_array(m5))
    if np.isnan(X[-1]).any():
        return None
    return float(Red.cargar(ruta).predecir(X[-1:]).ravel()[0])


def texto(ctx: dict) -> str:
    """La seccion que se pega en el retrato del agente."""
    L = ["## Lo que midieron los datos (red de conocimiento numerica)"]
    ahora = ctx.get("ahora")
    if ahora is None:
        return ""
    hh = ahora.hour
    t = tabla().get(hh)
    if t:
        L.append(f"Medido en {t['n']:,} velas de 5M de XAUUSD a las {hh:02d}h de NY "
                 f"(1 pip = 0,10 de precio):")
        L.append(f"- rango tipico de la proxima hora: {t['rango_p50']:.0f} pips")
        for lado, nom in (("largo", "compra"), ("corto", "venta")):
            x = t[lado]
            L.append(f"- en una {nom} que acabo ganando +{OBJETIVO_1H:.0f} pips, el precio "
                     f"fue en contra antes {x['p50']:.0f} pips (mediana) y {x['p75']:.0f} "
                     f"(3 de cada 4). Un stop mas corto que {x['p75']:.0f} pips lo barre el "
                     f"retroceso normal; una limitada {x['p50']:.0f} pips mejor que el precio "
                     f"se llena en la mitad de las ganadoras")
    else:
        L.append("- sin estadistica para esta hora (mercado cerrado o pocos datos)")
    p = prob_subida(ctx.get("m5") or [])
    mt = metricas().get("direccion", {})
    if p is not None:
        nota = (f"AUC {mt['auc']:.3f} en datos no vistos" if mt.get("auc") else "sin medir")
        L.append(f"- red de direccion (1 hora): {100*p:.0f} % de que suba · {nota}. "
                 f"Con AUC cercano a 0,50 NO tiene ventaja demostrada: es informativo, "
                 f"no una senal para decidir el lado")
    return "\n".join(L)
