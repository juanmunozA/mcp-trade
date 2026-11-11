"""
Busca una configuracion del UT Bot con ventaja medida, y la valida.

Lo que ya se sabia (utbot.py, investigador.py): con stop y objetivo fijos
el bot no gana; lo poco que gana sale de SEGUIR TENDENCIA y dejar correr
hasta que el propio bot se da la vuelta. Asi que aqui solo se busca en esa
familia, y lo que se mueve es:

  marco      15M, 30M, 1H
  clave a    2, 3, 4          (cuanto ATR se aleja la linea)
  ATR        10, 14
  fuente     cierre o Heikin Ashi
  filtro     ninguno · EMA 200 del marco · EMA 50 de H4 · las dos
  salida     «giro»  : en la señal contraria, apertura de la vela siguiente
             «linea» : stop en la linea del bot, dentro de la vela

Entrada en la apertura de la vela siguiente a la señal, con el spread de
esa vela. Sin objetivo. Cada operacion se mide en pips y en R (riesgo =
distancia de la entrada a la linea del bot en la señal).

VALIDACION. Tres tramos en el tiempo: 2019-2022 para buscar, 2023-2024
para filtrar y 2025-2026 para confirmar. Una configuracion solo pasa si
gana en los tres (factor de beneficio >= 1,10 y R medio > 0) y si, ademas,
ganan por separado las compras y las ventas en el conjunto: el oro subio
mucho en 2024-2026 y un sistema que solo gana comprando no ha demostrado
nada. De las que pasan se elige la de mejor PEOR tramo, no la de mejor
total.

    python -m materias.red.ventaja
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red import utbot as U

ARCHIVO = Path(__file__).with_name("ventaja.json")
CORTES = (int(dt.datetime(2023, 1, 1, tzinfo=dt.UTC).timestamp()),
          int(dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()))
TRAMOS = ("2019-2022", "2023-2024", "2025-2026")
MARCOS = {"15M": 900, "30M": 1800, "1H": 3600}
FILTROS = ("ninguno", "ema200", "h4", "ema200+h4")
SALIDAS = ("giro", "linea")
PF_MIN = 1.10


def _h4(v: np.ndarray, b: dict, seg: int) -> np.ndarray:
    """Signo de (cierre - EMA 50) de la ultima vela de H4 cerrada al cierre de cada vela de `b`."""
    g = U.agrupar(v, 14400)
    sig = np.sign(g["c"] - U.ema(g["c"], 50))
    pos = np.searchsorted(g["t"] + 14400, b["t"] + seg, side="right") - 1
    return np.where(pos >= 0, sig[np.maximum(pos, 0)], 0)


def operar(b: dict, a: float, c: int, heikin: bool, tend: np.ndarray, h4: np.ndarray,
           filtro: str, salida: str) -> dict[str, np.ndarray]:
    """Las operaciones de una configuracion: t (entrada), lado, pips, r."""
    _, st, co, ve = U.utbot(b, a, c, heikin)
    atr = U.atr(b["h"], b["l"], b["c"], c)
    idx = np.flatnonzero(co | ve)
    lados = np.where(co[idx], 1, -1)
    o, h, lo, s = b["o"], b["h"], b["l"], b["s"]
    out = {"t": [], "lado": [], "pips": [], "r": []}
    for k in range(len(idx) - 1):
        i, d = int(idx[k]), int(lados[k])
        e, fin = i + 1, int(idx[k + 1]) + 1
        if fin >= len(o) or i < 250:
            continue
        if "ema200" in filtro and tend[i] != d:
            continue
        if "h4" in filtro and h4[i] != d:
            continue
        precio = o[fin]
        if salida == "linea":
            # la linea de la vela anterior es el stop de esta vela; un hueco sale en la apertura
            w = slice(e, fin)
            toca = np.flatnonzero((lo[w] <= st[e - 1:fin - 1]) if d > 0 else (h[w] + s[w] >= st[e - 1:fin - 1]))
            if len(toca):
                j = e + int(toca[0])
                precio = min(o[j], st[j - 1]) if d > 0 else max(o[j], st[j - 1])
        riesgo = max(abs(o[e] - st[i]), 0.3 * atr[i])
        gana = d * (precio - o[e]) - s[e]
        out["t"].append(b["t"][e])
        out["lado"].append(d)
        out["pips"].append(gana / D.PIP)
        out["r"].append(gana / riesgo)
    return {k: np.asarray(x) for k, x in out.items()}


def resumen(x: dict, m: np.ndarray | None = None) -> dict:
    p, r = (x["pips"], x["r"]) if m is None else (x["pips"][m], x["r"][m])
    if len(p) < 30:
        return {"n": int(len(p))}
    gan, per = p[p > 0].sum(), -p[p < 0].sum()
    return {"n": int(len(p)), "pips": round(float(p.mean()), 1), "r": round(float(r.mean()), 3),
            "pf": round(float(gan / max(per, 1e-9)), 2), "ganan_pct": round(100 * float((p > 0).mean()), 1)}


def detalle(x: dict) -> dict:
    """Por año, racha de perdidas y peor caida, de una configuracion."""
    anos = {}
    for t, p, r in zip(x["t"], x["pips"], x["r"], strict=True):
        a = anos.setdefault(dt.datetime.fromtimestamp(int(t), dt.UTC).year, [0, 0.0, 0.0])
        a[0] += 1
        a[1] += p
        a[2] += r
    eq = np.cumsum(x["r"])
    caida = float((np.maximum.accumulate(eq) - eq).max())
    racha = m = 0
    for r in x["r"]:
        m = m + 1 if r <= 0 else 0
        racha = max(racha, m)
    dias = (x["t"][-1] - x["t"][0]) / 86400
    return {"por_ano": {str(k): {"n": a[0], "pips": round(a[1]), "r": round(a[2], 1)} for k, a in sorted(anos.items())},
            "peor_caida_r": round(caida, 1), "peor_racha_perdidas": int(racha),
            "ops_por_semana": round(len(eq) / dias * 7, 1), "r_total": round(float(eq[-1]), 1)}


def medir() -> dict:
    v = D.cargar("M5")
    filas = []
    for marco, seg in MARCOS.items():
        b = U.agrupar(v, seg)
        tend, h4 = np.sign(b["c"] - U.ema(b["c"], 200)), _h4(v, b, seg)
        for a, c, heikin, filtro, salida in itertools.product((2.0, 3.0, 4.0), (10, 14), (False, True), FILTROS, SALIDAS):
            x = operar(b, a, c, heikin, tend, h4, filtro, salida)
            if len(x["t"]) < 200:
                continue
            tr = np.searchsorted(CORTES, x["t"], side="right")
            fila = {"marco": marco, "a": a, "atr": c, "heikin": heikin, "filtro": filtro, "salida": salida,
                    "tramos": [resumen(x, tr == k) for k in range(3)],
                    "compras": resumen(x, x["lado"] > 0), "ventas": resumen(x, x["lado"] < 0)}
            ok = all(t.get("pf", 0) >= PF_MIN and t.get("r", -1) > 0 for t in fila["tramos"])
            fila["pasa"] = bool(ok and fila["compras"].get("pf", 0) > 1 and fila["ventas"].get("pf", 0) > 1)
            fila["peor_pf"] = min(t.get("pf", 0) for t in fila["tramos"])
            if fila["pasa"]:
                fila |= detalle(x)
            filas.append(fila)
    filas.sort(key=lambda f: (-f["pasa"], -f["peor_pf"]))
    pasan = [f for f in filas if f["pasa"]]
    # la elegida no cambia mientras siga pasando: el indicador y el vigilante la llevan puesta
    previa = datos().get("elegida")
    if previa:
        igual = [f for f in pasan if clave(f) == clave(previa)]
        pasan = igual + [f for f in pasan if f not in igual]
    return {"medido": f"{dt.date.today()}", "tramos": TRAMOS, "probadas": len(filas), "pasan": len(pasan),
            "criterio": f"PF >= {PF_MIN} y R > 0 en los tres tramos; compras y ventas con PF > 1",
            "elegida": pasan[0] if pasan else None, "mejores": pasan[:10],
            "casi": [f for f in filas if not f["pasa"]][:5]}


def clave(f: dict) -> tuple:
    return f["marco"], f["a"], f["atr"], f["heikin"], f["filtro"], f["salida"]


def exportar() -> Path:
    ARCHIVO.write_text(json.dumps(medir(), indent=1, ensure_ascii=False), encoding="utf-8")
    return ARCHIVO


def estado(m5: np.ndarray) -> dict | None:
    """
    La configuracion elegida sobre velas de 5M ya cerradas (>= 12.000).
    Devuelve el lado del bot, la linea y, si la ultima vela de 5M cerro una
    vela del marco con señal, `nueva`: lado y si pasa el filtro (entrada) o
    no (solo salida de la operacion contraria).
    """
    e = datos().get("elegida")
    if not e:
        return None
    seg = MARCOS[e["marco"]]
    b = U.agrupar(m5, seg)
    cerrada = (int(m5["time"][-1]) + 300) % seg == 0
    if not cerrada:                                   # la ultima vela del marco aun se esta formando
        b = {k: x[:-1] for k, x in b.items()}
    src, st, co, ve = U.utbot(b, e["a"], e["atr"], e["heikin"])
    out = {"config": nombre(e), "lado": 1 if src[-1] > st[-1] else -1, "linea": float(st[-1]),
           "precio": float(b["c"][-1]), "nueva": None, "prueba": e["tramos"][2]}
    if cerrada and (co[-1] or ve[-1]):
        d = 1 if co[-1] else -1
        ok = True
        if "ema200" in e["filtro"]:
            ok &= np.sign(b["c"][-1] - U.ema(b["c"], 200)[-1]) == d
        if "h4" in e["filtro"]:
            ok &= _h4(m5, b, seg)[-1] == d
        out["nueva"] = {"lado": d, "entrada": bool(ok)}
    return out


def datos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def nombre(f: dict) -> str:
    return (f"UT {f['marco']} a={f['a']:g} ATR {f['atr']}{' Heikin Ashi' if f['heikin'] else ''} · "
            f"filtro {f['filtro']} · salida {f['salida']}")


def _linea(f: dict) -> str:
    t = " | ".join(f"{n}: PF {x.get('pf', 0):.2f}, {x.get('r', 0):+.2f} R, n={x['n']}"
                   for n, x in zip(TRAMOS, f["tramos"], strict=True))
    return (f"{nombre(f)}\n     {t}\n     compras PF {f['compras'].get('pf', 0):.2f} (n={f['compras']['n']}) · "
            f"ventas PF {f['ventas'].get('pf', 0):.2f} (n={f['ventas']['n']})")


def texto() -> str:
    d = datos()
    e = d.get("elegida")
    if not d:
        return ""
    if not e:
        return (f"## UT Bot: busqueda de ventaja ({d['medido']})\nSe probaron {d['probadas']} configuraciones de "
                f"seguimiento de tendencia; ninguna cumple «{d['criterio']}».")
    return (f"## UT Bot con ventaja medida ({d['medido']})\n{d['pasan']} de {d['probadas']} configuraciones cumplen "
            f"«{d['criterio']}». Elegida (mejor peor tramo): {_linea(e)}\n"
            f"Gana el {e['tramos'][2]['ganan_pct']:.0f} % de las veces: la ventaja son pocas operaciones largas, no "
            f"acertar mucho. {e['ops_por_semana']} operaciones por semana; peor racha {e['peor_racha_perdidas']} "
            f"perdidas seguidas; peor caida {e['peor_caida_r']} R. Sin objetivo: se sale cuando lo dice la salida.")


if __name__ == "__main__":
    exportar()
    d = datos()
    print(f"probadas {d['probadas']} · pasan {d['pasan']}\n")
    for f in d["mejores"]:
        print("PASA ", _linea(f))
    for f in d["casi"]:
        print("casi ", _linea(f))
    if d["elegida"]:
        print()
        print(json.dumps({k: d["elegida"][k] for k in ("por_ano", "peor_caida_r", "peor_racha_perdidas",
                                                        "ops_por_semana", "r_total")}, indent=1))
