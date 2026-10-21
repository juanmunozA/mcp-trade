"""
UT Bot Alerts (QuantNomad, TradingView), traducido linea a linea y medido.

El Pine original:
  nLoss = a * atr(c)                       (a = 1, c = 10 por defecto)
  stop  = si src y src[1] por encima del stop anterior -> max(stop[1], src - nLoss)
          si los dos por debajo                        -> min(stop[1], src + nLoss)
          si cruza hacia arriba                        -> src - nLoss
          si cruza hacia abajo                         -> src + nLoss
  Buy  = src cruza el stop hacia arriba;  Sell = lo cruza hacia abajo
  src  = cierre, o cierre Heikin Ashi si h = true

Es un trailing stop de ATR que da la vuelta a la posicion en cada cruce.
No sabe nada de tendencia, sesion ni rango: en lateral el stop pegado
(a = 1) se cruza una y otra vez. Aqui se mide tal cual y con filtros
causales (todo con velas ya cerradas), eligiendo en 2019-2024 y
comprobando en 2025-2026, que no se usan para elegir.

Dos formas de medir cada señal:
  giro    : como el bot, siempre dentro; se sale en la señal contraria.
  bracket : entrada en la apertura siguiente, stop en la linea del bot,
            objetivo a 2 R, maximo 8 h. Esta es la que dice si la ENTRADA
            es precisa, que es lo que se quiere mejorar.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import SESIONES, hora_ny

ARCHIVO = Path(__file__).with_name("utbot.json")
CORTE_TEST = 1735689600 + 7 * 3600          # 2025-01-01 en hora del servidor


def agrupar(v: np.ndarray, seg: int) -> dict[str, np.ndarray]:
    g = v["time"] // seg
    ini = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    fin = np.r_[ini[1:], len(v)]
    return {"t": g[ini] * seg, "o": v["open"][ini], "h": np.maximum.reduceat(v["high"], ini),
            "l": np.minimum.reduceat(v["low"], ini), "c": v["close"][fin - 1],
            "s": v["spread"][ini] * 0.01}


def atr(h, lo, c, n: int) -> np.ndarray:
    """ATR de Wilder (rma), el de ta.atr de Pine."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    out = np.full(len(tr), np.nan)
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def ema(x, n: int) -> np.ndarray:
    out = np.empty(len(x))
    out[0] = x[0]
    k = 2 / (n + 1)
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def utbot(b: dict, a: float = 1.0, c: int = 10, heikin: bool = False):
    """Linea del bot y señales, igual que el Pine."""
    src = (b["o"] + b["h"] + b["l"] + b["c"]) / 4 if heikin else b["c"]
    nl = a * atr(b["h"], b["l"], b["c"], c)
    st = np.zeros(len(src))
    for i in range(1, len(src)):
        p, x, x1, d = st[i - 1], src[i], src[i - 1], nl[i]
        if np.isnan(d):
            st[i] = x
        elif x > p and x1 > p:
            st[i] = max(p, x - d)
        elif x < p and x1 < p:
            st[i] = min(p, x + d)
        elif x > p:
            st[i] = x - d
        else:
            st[i] = x + d
    prev_src, prev_st = np.r_[np.nan, src[:-1]], np.r_[np.nan, st[:-1]]
    compra = (src > st) & (prev_src <= prev_st)
    venta = (src < st) & (prev_src >= prev_st)
    compra[: c + 2] = venta[: c + 2] = False
    return src, st, compra, venta


def filtros(b: dict, seg: int, h1: dict, st1: np.ndarray, src1: np.ndarray) -> dict[str, np.ndarray]:
    """Filtros causales por vela: +1 permite comprar, -1 vender, 0 ambos/ninguno segun el caso."""
    c = b["c"]
    tend = np.sign(c - ema(c, 200))
    # estado del bot en 1 h con la ultima vela de 1 h YA cerrada
    j = np.searchsorted(h1["t"] + 3600, b["t"] + seg, side="right") - 1
    estado1 = np.where(j >= 0, np.sign(src1[np.clip(j, 0, None)] - st1[np.clip(j, 0, None)]), 0)
    # posicion en el rango de las ultimas 12 h (sin la vela actual)
    n = max(1, int(12 * 3600 / seg))
    from numpy.lib.stride_tricks import sliding_window_view as ventana
    hi = np.full(len(c), np.nan)
    lo = np.full(len(c), np.nan)
    hi[n:] = ventana(b["h"], n).max(axis=1)[:-1]
    lo[n:] = ventana(b["l"], n).min(axis=1)[:-1]
    pos = (c - lo) / np.maximum(hi - lo, 1e-9)
    hh = hora_ny(b["t"] + seg) % 24
    kz = np.zeros(len(c), bool)
    for x, y in SESIONES.values():
        kz |= (hh >= x) & (hh < y)
    return {"tend": tend, "h1": estado1, "pos": pos, "kz": kz}


def bracket(b: dict, i: int, lado: int, linea: float, rr: float = 2.0, horizonte: int = 32):
    """R de una entrada en la apertura de i+1 con stop en la linea y objetivo a rr R."""
    if i + 1 >= len(b["c"]):
        return None
    e = b["o"][i + 1] + (b["s"][i + 1] if lado > 0 else 0.0)
    riesgo = lado * (e - linea)
    if riesgo <= 0:
        return None
    stop, obj = e - lado * riesgo, e + lado * rr * riesgo
    coste = b["s"][i + 1]
    for j in range(i + 1, min(i + 1 + horizonte, len(b["c"]))):
        sp = b["s"][j]
        if lado > 0:
            if b["l"][j] <= stop:
                return -1.0 - coste / riesgo
            if b["h"][j] >= obj:
                return rr - coste / riesgo
        else:
            if b["h"][j] + sp >= stop:
                return -1.0 - coste / riesgo
            if b["l"][j] <= obj:
                return rr - coste / riesgo
    fin = min(i + horizonte, len(b["c"]) - 1)
    return (lado * (b["c"][fin] - e) - coste) / riesgo


def giro(b: dict, idx: np.ndarray, lados: np.ndarray) -> np.ndarray:
    """Pips por operacion del bot tal cual: cada señal cierra la anterior y abre la contraria."""
    out = []
    for k in range(len(idx) - 1):
        i, i2 = idx[k] + 1, idx[k + 1] + 1
        if i2 >= len(b["o"]):
            break
        out.append((lados[k] * (b["o"][i2] - b["o"][i]) - b["s"][i]) / D.PIP)
    return np.array(out)


def _res(r: list | np.ndarray) -> dict:
    a = np.asarray([x for x in r if x is not None], float)
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "gana_pct": round(100 * float((a > 0).mean()), 1),
            "r_medio": round(float(a.mean()), 3)}


VARIANTES = {
    "tal cual":                   lambda f, i, s: True,
    "tendencia (EMA 200)":        lambda f, i, s: f["tend"][i] == s,
    "1 h a favor":                lambda f, i, s: f["h1"][i] == s,
    "zona (compra <50 %, venta >50 %)": lambda f, i, s: (f["pos"][i] < 0.5) if s > 0 else (f["pos"][i] > 0.5),
    "killzone":                   lambda f, i, s: bool(f["kz"][i]),
    "tendencia + 1 h":            lambda f, i, s: f["tend"][i] == s and f["h1"][i] == s,
    "1 h + zona":                 lambda f, i, s: f["h1"][i] == s and ((f["pos"][i] < 0.5) if s > 0 else (f["pos"][i] > 0.5)),
    "1 h + zona + killzone":      lambda f, i, s: f["h1"][i] == s and bool(f["kz"][i]) and ((f["pos"][i] < 0.5) if s > 0 else (f["pos"][i] > 0.5)),
    "tendencia + 1 h + killzone": lambda f, i, s: f["tend"][i] == s and f["h1"][i] == s and bool(f["kz"][i]),
}


def medir(tfs=(("5M", 300), ("15M", 900)), claves=(1.0, 2.0, 3.0)) -> dict:
    v = D.cargar("M5")
    h1 = agrupar(v, 3600)
    out = {"periodo_eleccion": "2019-2024", "periodo_prueba": "2025-2026", "res": {}}
    for nombre, seg in tfs:
        b = agrupar(v, seg)
        for a in claves:
            for heikin in (False, True):
                src1, st1, _, _ = utbot(h1, a, 10, heikin)
                src, st, cb, cv = utbot(b, a, 10, heikin)
                f = filtros(b, seg, h1, st1, src1)
                idx = np.flatnonzero(cb | cv)
                lados = np.where(cb[idx], 1, -1)
                clave = f"{nombre} a={a:g}{' HA' if heikin else ''}"
                g = giro(b, idx, lados)
                tg = b["t"][idx[:len(g)]] >= CORTE_TEST
                fila = {"giro_pips": {"eleccion": round(float(g[~tg].mean()), 2),
                                      "prueba": round(float(g[tg].mean()), 2), "n": int(len(g))}}
                horizonte = int(8 * 3600 / seg)
                for var, ok in VARIANTES.items():
                    r = {"eleccion": [], "prueba": []}
                    for i, s in zip(idx, lados, strict=True):
                        if np.isnan(f["pos"][i]) or not ok(f, i, s):
                            continue
                        x = bracket(b, int(i), int(s), st[i], 2.0, horizonte)
                        r["prueba" if b["t"][i] >= CORTE_TEST else "eleccion"].append(x)
                    fila[var] = {k: _res(x) for k, x in r.items()}
                out["res"][clave] = fila
                print(clave, "giro", fila["giro_pips"], flush=True)
                for var in VARIANTES:
                    e, p = fila[var]["eleccion"], fila[var]["prueba"]
                    print(f"   {var:34s} elec n={e.get('n',0):6d} {e.get('r_medio',0):+.3f}R {e.get('gana_pct',0):4.1f}%"
                          f" | prueba n={p.get('n',0):5d} {p.get('r_medio',0):+.3f}R {p.get('gana_pct',0):4.1f}%", flush=True)
    return out


# Lo que salio de medir (15M, 2019-2024 para elegir, 2025-2026 para probar),
# con la salida del propio bot (señal contraria) y el coste del spread:
#   a=1 tal cual (el de TradingView)  -1,14 pips/op PF 0,93 | prueba -1,00 PF 0,98
#   a=2 HA tal cual                   +1,07 pips/op PF 1,04 | prueba +11,9 PF 1,13
#   a=2 HA + tendencia (EMA 200)      +3,07 pips/op PF 1,11 | prueba +21,4 PF 1,23   <- elegido
#   a=3 + tendencia                   +3,35 pips/op PF 1,10 | prueba +39,4 PF 1,36
# El filtro de zona ICT (comprar en descuento) EMPEORA siempre: el bot sigue
# tendencia y la zona es contraria. Con stop en la linea y objetivo fijo a
# 2 R ninguna variante pasa de +0,01 R en 2019-2024: la ventaja no esta en
# la entrada, esta en dejar correr hasta la señal contraria.
ELEGIDO = {"a": 2.0, "c": 10, "heikin": True, "filtro": "tendencia (EMA 200)",
           "eleccion": "+3,07 pips/op, PF 1,11, n=2.866 (2019-2024)",
           "prueba": "+21,4 pips/op, PF 1,23, n=841 (2025-2026)"}


def _desde_ctx(velas: list[dict]) -> dict:
    return {"t": np.array([x["dt"].timestamp() for x in velas]),
            "o": np.array([x["o"] for x in velas], float), "h": np.array([x["h"] for x in velas], float),
            "l": np.array([x["l"] for x in velas], float), "c": np.array([x["c"] for x in velas], float)}


def estado(b: dict) -> dict | None:
    """Estado del bot elegido sobre velas de 15M ya cerradas."""
    if len(b["c"]) < 260:
        return None
    src, st, cb, cv = utbot(b, ELEGIDO["a"], ELEGIDO["c"], ELEGIDO["heikin"])
    tend = np.sign(b["c"] - ema(b["c"], 200))
    idx = np.flatnonzero(cb | cv)
    ult = int(idx[-1]) if len(idx) else None
    lado = 1 if src[-1] > st[-1] else -1
    return {"lado": lado, "linea": float(st[-1]), "tendencia": int(tend[-1]),
            "ultima": None if ult is None else {
                "velas_atras": len(src) - 1 - ult, "lado": 1 if cb[ult] else -1,
                "precio": float(b["c"][ult]), "pasa_filtro": bool(tend[ult] == (1 if cb[ult] else -1)),
                "t": float(b["t"][ult])}}


def texto(ctx: dict) -> str:
    m15 = (ctx.get("m15") or [])[:-1]           # la ultima puede estar formandose
    try:
        e = estado(_desde_ctx(m15))
    except (KeyError, TypeError, ValueError):
        return ""
    if not e:
        return ""
    nom = {1: "COMPRA", -1: "VENTA"}
    L = ["## UT Bot (a=2, ATR 10, Heikin Ashi, 15M) con filtro de tendencia EMA 200",
         f"Medido: {ELEGIDO['eleccion']}; prueba {ELEGIDO['prueba']}. Ventaja pequeña: "
         "sigue tendencia y sale en la señal contraria; con objetivo fijo no gana.",
         f"- ahora el bot esta en {nom[e['lado']]}, linea {e['linea']:.2f}; "
         f"EMA 200 dice {nom[e['tendencia']]}"]
    u = e["ultima"]
    if u:
        L.append(f"- ultima señal: {nom[u['lado']]} hace {u['velas_atras']} velas de 15M en "
                 f"{u['precio']:.2f}, {'PASA' if u['pasa_filtro'] else 'NO pasa'} el filtro de tendencia")
    return "\n".join(L)


def vivo(minutos: float = 28) -> None:
    """Alerta en vivo sobre MT5: imprime cada señal nueva del bot elegido."""
    import time
    from datetime import datetime, timedelta
    mt5 = D._conectar()
    fin, visto = time.time() + 60 * minutos, None
    while time.time() < fin:
        r = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 1, 800)   # solo cerradas
        if r is not None and len(r) and r["time"][-1] != visto:
            primera = visto is None
            visto = r["time"][-1]
            b = {"t": r["time"], "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"]}
            e = estado(b)
            if e and e["ultima"] and e["ultima"]["velas_atras"] == 0 and not primera:
                u = e["ultima"]
                ny = datetime.utcfromtimestamp(int(u["t"])) - timedelta(seconds=DESFASE)
                print(f"UT {'BUY' if u['lado'] > 0 else 'SELL'} {ny:%H:%M} NY @ {u['precio']:.2f} · "
                      f"linea {e['linea']:.2f} · filtro EMA200 {'PASA' if u['pasa_filtro'] else 'NO pasa'}",
                      flush=True)
            elif primera and e:
                print(f"UT estado {'COMPRA' if e['lado'] > 0 else 'VENTA'} · linea {e['linea']:.2f} · "
                      f"EMA200 {'arriba' if e['tendencia'] > 0 else 'abajo'}", flush=True)
        time.sleep(30)
    print("UT fin de ventana", flush=True)
    mt5.shutdown()


DESFASE = 7 * 3600


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "vivo":
        vivo(float(sys.argv[2]) if len(sys.argv) > 2 else 28)
    else:
        res = medir()
        ARCHIVO.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print("guardado en", ARCHIVO.name)
