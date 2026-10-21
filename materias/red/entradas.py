"""
Los numeros que ve la red: los conceptos de la guia convertidos en entradas.

Una neurona recibe numeros, los multiplica por pesos y suma. Aqui cada
numero es un concepto de la guia medido sobre las velas que ya pasaron
(nunca sobre las que vienen: eso seria hacer trampa y la red parecera
genial en el backtest y fallara en vivo).

Todo va dividido por el ATR, para que «estar a 30 pips del minimo» no
signifique lo mismo un dia tranquilo que uno de pánico.

    cap. 4.4  posicion en el rango: premium / descuento / equilibrio
    cap. 6.1  distancia a la liquidez obvia: minimo y maximo del dia previo
    cap. 6.2  barridas: mecha que pasa un minimo/maximo y cierra dentro
    cap. 4.3  BOS: cierre de cuerpo mas alla del ultimo maximo/minimo
    cap. 7.1  FVG: hueco de tres velas, alcista o bajista
    cap. 2.2  desplazamiento: cuerpo grande respecto al ATR
    mechas
    cap. 10.2 killzone: en que franja horaria esta
    cap. 3    contexto de 1H: hacia donde va la hora
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as ventanas

from materias.red.pips import SESIONES, en_sesion, hora_ny

DIA = 288          # velas de 5M en un dia
LOOK = 24          # dos horas de 5M para barridas y BOS


def _atr(h, lo, c, n=14):
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(abs(h - prev), abs(lo - prev)))
    k = np.ones(n) / n
    a = np.convolve(tr, k, mode="full")[:len(tr)]
    a[:n] = tr[:n].mean()
    return a


def _rodante(x, n, fn):
    """fn sobre las n velas ANTERIORES (sin incluir la actual)."""
    out = np.full(len(x), np.nan)
    w = ventanas(x, n)                 # w[i] = x[i : i+n]
    out[n:] = fn(w[:-1], axis=1)       # para la vela i usa x[i-n : i]
    return out


def calcular(v: np.ndarray) -> tuple[np.ndarray, list[str]]:
    o = v["open"].astype(float)
    h = v["high"].astype(float)
    lo = v["low"].astype(float)
    c = v["close"].astype(float)
    t = v["time"]
    atr = _atr(h, lo, c)
    col, nom = [], []

    def add(nombre, x):
        nom.append(nombre)
        col.append(x)

    # cap. 4.4 · posicion en el rango del ultimo dia (0 = minimo, 1 = maximo)
    hi_d = _rodante(h, DIA, np.max)
    lo_d = _rodante(lo, DIA, np.min)
    add("pos_rango_dia", (c - lo_d) / np.maximum(hi_d - lo_d, 1e-9))
    hi_4 = _rodante(h, 48, np.max)
    lo_4 = _rodante(lo, 48, np.min)
    add("pos_rango_4h", (c - lo_4) / np.maximum(hi_4 - lo_4, 1e-9))

    # cap. 6.1 · distancia a la liquidez del dia previo, en ATR
    add("dist_max_dia_atr", (hi_d - c) / atr)
    add("dist_min_dia_atr", (c - lo_d) / atr)

    # cap. 6.2 · barridas en la vela actual contra las 2h anteriores
    hi_l = _rodante(h, LOOK, np.max)
    lo_l = _rodante(lo, LOOK, np.min)
    add("barrida_min", ((lo < lo_l) & (c > lo_l)).astype(float))
    add("barrida_max", ((h > hi_l) & (c < hi_l)).astype(float))
    # cap. 4.3 · BOS: cierre de cuerpo mas alla
    add("bos_alcista", (c > hi_l).astype(float))
    add("bos_bajista", (c < lo_l).astype(float))

    # cap. 7.1 · FVG de las ultimas tres velas
    fvg_up = np.zeros(len(c))
    fvg_dn = np.zeros(len(c))
    fvg_up[2:] = (lo[2:] > h[:-2]).astype(float)
    fvg_dn[2:] = (h[2:] < lo[:-2]).astype(float)
    add("fvg_alcista", fvg_up)
    add("fvg_bajista", fvg_dn)

    # cap. 2.2 · desplazamiento y mechas de la vela actual
    add("cuerpo_atr", (c - o) / atr)
    rango = np.maximum(h - lo, 1e-9)
    add("mecha_inf", (np.minimum(o, c) - lo) / rango)
    add("mecha_sup", (h - np.maximum(o, c)) / rango)
    add("atr_rel", atr / np.maximum(_rodante(atr, DIA, np.mean), 1e-9))

    # momento reciente, en ATR
    for k in (1, 3, 6, 12, 48):
        r = np.zeros(len(c))
        r[k:] = (c[k:] - c[:-k]) / atr[k:]
        add(f"ret_{k}", r)

    # cap. 3 · contexto de una hora: pendiente de los ultimos 12 cierres
    pend = np.full(len(c), np.nan)
    w = ventanas(c, 12)
    xs = np.arange(12) - 5.5
    pend[11:] = (w * xs).sum(axis=1) / (xs ** 2).sum()
    add("pendiente_1h_atr", pend / atr)

    # cap. 10.2 · killzones y hora
    hny = hora_ny(t)
    corto = {"Asia": "kz_asia", "Londres": "kz_londres", "NY AM": "kz_ny_am",
             "London Close": "kz_london_close", "NY PM": "kz_ny_pm"}
    for nombre, (a, b) in SESIONES.items():
        clave = next(v_ for k, v_ in corto.items() if nombre.startswith(k))
        add(clave, en_sesion(hny, a, b).astype(float))
    add("hora_sin", np.sin(2 * np.pi * hny / 24))
    add("hora_cos", np.cos(2 * np.pi * hny / 24))

    X = np.column_stack(col)
    return X, nom
