"""
Rebote por barrida y recuperacion, medido en 5M desde 2022.

El precio atraviesa un nivel donde hay stops (un minimo/maximo de serie de
15M o un numero redondo multiplo de 25), se los lleva y vuelve a cerrar al
otro lado. 22-10-2026 noche: bajo 4000 hasta 3986,95 dentro de un OB de H1 y
volvio a cerrar arriba; era la compra y vendi.

Compra (venta al reves), con velas de 5M cerradas:
  barrida      : el minimo de la vela k pasa el nivel por mas de 0,3 ATR
  recuperacion : en esa vela o en las 3 siguientes una vela CIERRA por encima del nivel
  entrada      : apertura de la vela siguiente; stop bajo el minimo de la barrida - 0,2 ATR
  salida       : objetivo a 1 R y a 2 R, maximo 4 h
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import SESIONES, hora_ny
from materias.red.utbot import agrupar, atr, ema
from materias.red.zonas import CORTE, DESDE, series

ARCHIVO = Path(__file__).with_name("rebote.json")
HORIZONTE = 48


def niveles(b15: dict, a15, hasta_t: int) -> list[tuple[float, int, str]]:
    """Minimos (-1) y maximos (+1) de serie de 15M confirmados antes de hasta_t."""
    out = []
    for _ie, ic, precio, tipo in series(b15["h"], b15["l"], a15):
        if b15["t"][ic] + 900 <= hasta_t:
            out.append((precio, tipo, "serie"))
    return out[-12:]


def senal(v: dict, a, i: int, niv: list[tuple[float, int, str]]) -> dict | None:
    """Barrida y recuperacion cerrada en la vela i, o None."""
    for precio, tipo, clase in niv:
        lado = 1 if tipo < 0 else -1        # barrer un minimo -> compra
        for k in range(i - 3, i + 1):
            if k < 1:
                continue
            barre = (v["l"][k] < precio - 0.3 * a[k]) if lado > 0 else (v["h"][k] > precio + 0.3 * a[k])
            antes = (v["c"][k - 1] > precio) if lado > 0 else (v["c"][k - 1] < precio)
            if not (barre and antes):
                continue
            ext = v["l"][k:i + 1].min() if lado > 0 else v["h"][k:i + 1].max()
            recupera = (v["c"][i] > precio) if lado > 0 else (v["c"][i] < precio)
            # que sea la PRIMERA vela que recupera
            previas = v["c"][k:i]
            ya = ((previas > precio).any() if lado > 0 else (previas < precio).any()) if len(previas) else False
            if recupera and not ya:
                return {"lado": lado, "nivel": float(precio), "extremo": float(ext), "clase": clase}
    return None


def medir() -> dict:
    m = D.cargar("M5")
    m = m[m["time"] >= DESDE - 30 * 86400]
    v = {"t": m["time"], "o": m["open"], "h": m["high"], "l": m["low"], "c": m["close"], "s": m["spread"] * 0.01}
    a = atr(v["h"], v["l"], v["c"], 14)
    e = ema(v["c"], 200)
    b15 = agrupar(m, 900)
    a15 = atr(b15["h"], b15["l"], b15["c"], 14)
    piv = series(b15["h"], b15["l"], a15)
    hh = hora_ny(v["t"])
    n, j, res, ult = len(v["c"]), 0, {}, -99
    vivos: list[tuple[float, int, str]] = []
    for i in range(30, n - 1):
        while j < len(piv) and b15["t"][piv[j][1]] + 900 <= v["t"][i]:
            vivos = (vivos + [(piv[j][2], piv[j][3], "serie")])[-12:]
            j += 1
        if v["t"][i] < DESDE or i - ult < 6:
            continue
        r25 = round(v["c"][i] / 25) * 25
        niv = vivos + [(r25, -1, "redondo"), (r25, 1, "redondo")]
        g = senal(v, a, i, niv)
        if not g:
            continue
        ult = i
        lado = g["lado"]
        ent = v["o"][i + 1] + (v["s"][i + 1] if lado > 0 else 0)
        stop = g["extremo"] - 0.2 * a[i] if lado > 0 else g["extremo"] + 0.2 * a[i]
        R = abs(ent - stop)
        if R <= 0 or (lado > 0 and ent <= stop) or (lado < 0 and ent >= stop):
            continue
        a_favor = (v["c"][i] > e[i]) if lado > 0 else (v["c"][i] < e[i])
        kz = any(x <= hh[i] < y for x, y in SESIONES.values())
        tramo = "2022-2024" if v["t"][i] < CORTE else "2025-2026"
        for rr in (1.0, 2.0):
            obj = ent + lado * rr * R
            out = None
            for k in range(i + 1, min(i + 1 + HORIZONTE, n)):
                if (v["l"][k] <= stop) if lado > 0 else (v["h"][k] + v["s"][k] >= stop):
                    out = -1.0
                    break
                if (v["h"][k] >= obj) if lado > 0 else (v["l"][k] <= obj):
                    out = rr
                    break
            if out is None:
                f = min(i + HORIZONTE, n - 1)
                out = lado * (v["c"][f] - ent) / R
            out -= v["s"][i + 1] / R
            nom = "compra" if lado > 0 else "venta"
            for clave in (f"{nom}|todas", f"{nom}|{g['clase']}", f"{nom}|{'a favor' if a_favor else 'contra'} EMA200",
                          f"{nom}|{'killzone' if kz else 'fuera de killzone'}"):
                res.setdefault(f"{clave}|{rr:g}R|{tramo}", []).append(out)
    return {k: {"n": len(x), "r_medio": round(float(np.mean(x)), 3), "gana_pct": round(100 * float(np.mean(np.array(x) > 0)), 1)}
            for k, x in sorted(res.items())}


def texto() -> str:
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    L = ["## Rebote por barrida y recuperacion (5M, desde 2022)",
         "El precio pasa un minimo/maximo de serie o un numero redondo (x25), y una vela vuelve a cerrar "
         "al otro lado. Stop tras el extremo de la barrida."]
    for clave in ("compra|todas", "compra|contra EMA200", "compra|killzone", "venta|todas", "venta|a favor EMA200"):
        for rr in ("1R", "2R"):
            a, b = d.get(f"{clave}|{rr}|2022-2024"), d.get(f"{clave}|{rr}|2025-2026")
            if a and b:
                L.append(f"- {clave.replace('|', ' · ')} a {rr}: {a['r_medio']:+.2f} R (n={a['n']}) en 2022-24, "
                         f"{b['r_medio']:+.2f} R (n={b['n']}) en 2025-26")
    return "\n".join(L)


if __name__ == "__main__":
    r = medir()
    ARCHIVO.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, x in r.items():
        print(f"{k:48s} n={x['n']:5d} {x['r_medio']:+.3f} R gana {x['gana_pct']}%")
