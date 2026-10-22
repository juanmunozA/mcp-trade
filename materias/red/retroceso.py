"""
Entrar en el retroceso: medido en 5M desde 2022.

En tendencia, el precio hace un impulso, retrocede contra la tendencia y
sigue. La entrada es al final del retroceso, no en el extremo del impulso
(22-10-2026: el usuario lo marco en el grafico, 4034 -> 4048,6 -> 4033).

Venta (compra al reves), todo con velas cerradas:
  tendencia : cierre por debajo de la EMA 200 de 5M
  impulso   : el minimo L de las ultimas 24 velas viene de un maximo LH de las
              48 anteriores, con LH - L >= 2 ATR
  retroceso : desde L el precio sube hasta H, entre el 30 y el 80 % de LH - L
  giro      : despues de H, una vela cierra por debajo del minimo de las velas
              que hay entre H y ella (rompe la estructura del retroceso)
  entrada   : apertura de la vela siguiente; stop sobre H + 0,2 ATR
  salida    : objetivo en L (o a 2 R); si no, al cabo de 8 h
Se separa por si el retroceso toco un vacio bajista (FVG de 5M) del impulso.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import SESIONES, hora_ny
from materias.red.utbot import atr, ema
from materias.red.zonas import CORTE, DESDE

ARCHIVO = Path(__file__).with_name("retroceso.json")
HORIZONTE = 96


def _salida(v, i, lado, e, stop, obj, coste):
    riesgo = abs(stop - e)
    for j in range(i, min(i + HORIZONTE, len(v["c"]))):
        if lado < 0:
            if v["h"][j] + v["s"][j] >= stop:
                return (e - stop - coste) / riesgo
            if v["l"][j] <= obj:
                return (e - obj - coste) / riesgo
        else:
            if v["l"][j] <= stop:
                return (stop - e - coste) / riesgo
            if v["h"][j] + v["s"][j] >= obj:
                return (obj - e - coste) / riesgo
    f = min(i + HORIZONTE, len(v["c"])) - 1
    return ((e - v["c"][f]) if lado < 0 else (v["c"][f] - e)) / riesgo - coste / riesgo


def senal(v: dict, a, e200, i: int, lado: int) -> dict | None:
    """El retroceso de la vela i (cerrada), o None. Misma regla en el estudio y en vivo."""
    if (lado < 0 and v["c"][i] >= e200[i]) or (lado > 0 and v["c"][i] <= e200[i]):
        return None
    ext = v["l"] if lado < 0 else v["h"]          # extremo del impulso
    contra = v["h"] if lado < 0 else v["l"]       # extremo del retroceso
    ventana = slice(i - 24, i)
    iL = i - 24 + int(np.argmin(ext[ventana]) if lado < 0 else np.argmax(ext[ventana]))
    L = ext[iL]
    previo = slice(max(0, iL - 48), iL)
    if previo.stop - previo.start < 10:
        return None
    iLH = previo.start + int(np.argmax(contra[previo]) if lado < 0 else np.argmin(contra[previo]))
    LH = contra[iLH]
    pierna = abs(LH - L)
    if pierna < 2 * a[i] or iL >= i - 2:
        return None
    tramo = slice(iL + 1, i)
    iH = iL + 1 + int(np.argmax(contra[tramo]) if lado < 0 else np.argmin(contra[tramo]))
    H = contra[iH]
    ratio = abs(H - L) / pierna
    if not 0.3 <= ratio <= 0.8 or iH >= i - 1:
        return None
    entre = slice(iH + 1, i)
    piso = v["l"][entre].min() if lado < 0 else v["h"][entre].max()
    if not ((v["c"][i] < piso) if lado < 0 else (v["c"][i] > piso)):
        return None
    # ¿toco H un vacio del impulso, en contra de el?
    toco = False
    for k in range(iLH + 2, iL + 1):
        if lado < 0 and v["h"][k] < v["l"][k - 2]:
            toco |= v["h"][k] <= H
        if lado > 0 and v["l"][k] > v["h"][k - 2]:
            toco |= v["l"][k] >= H
    return {"L": float(L), "H": float(H), "LH": float(LH), "ratio": float(ratio), "toco": toco}


def medir() -> dict:
    m = D.cargar("M5")
    m = m[m["time"] >= DESDE - 20 * 86400]
    v = {"t": m["time"], "o": m["open"], "h": m["high"], "l": m["low"], "c": m["close"], "s": m["spread"] * 0.01}
    a = atr(v["h"], v["l"], v["c"], 14)
    e200 = ema(v["c"], 200)
    n = len(v["c"])
    res: dict[str, list] = {}
    ultima = -99
    for i in range(80, n - 1):
        if v["t"][i] < DESDE or i - ultima < 12:
            continue
        for lado in (-1, 1):
            g = senal(v, a, e200, i, lado)
            if g is None:
                continue
            L, H, ratio, toco = g["L"], g["H"], g["ratio"], g["toco"]
            ultima = i
            ent = v["o"][i + 1] + (v["s"][i + 1] if lado > 0 else 0)
            stop = H + 0.2 * a[i] if lado < 0 else H - 0.2 * a[i]
            riesgo = abs(stop - ent)
            if riesgo <= 0 or (lado < 0 and ent >= stop) or (lado > 0 and ent <= stop):
                continue
            coste = v["s"][i + 1]
            r_l = _salida(v, i + 1, lado, ent, stop, L, coste) if (ent - L) * -lado > 0 else None
            r_2 = _salida(v, i + 1, lado, ent, stop, ent + lado * 2 * riesgo, coste)
            tramo_t = "2022-2024" if v["t"][i] < CORTE else "2025-2026"
            nom = "venta" if lado < 0 else "compra"
            hh = float(hora_ny(np.array([v["t"][i]]))[0])
            kz = any(x <= hh < y for x, y in SESIONES.values())
            claves = [f"{nom}|todas", f"{nom}|toco vacio" if toco else f"{nom}|sin vacio",
                      f"{nom}|retroceso 50-80 %" if ratio >= 0.5 else f"{nom}|retroceso 30-50 %"]
            if 0.62 <= ratio <= 0.79:
                claves.append(f"{nom}|OTE 62-79 %")
                if kz:
                    claves.append(f"{nom}|OTE 62-79 % en killzone")
            if kz:
                claves.append(f"{nom}|en killzone")
            for clave in claves:
                g = res.setdefault(f"{clave}|{tramo_t}", {"al_minimo": [], "a_2R": []})
                if r_l is not None:
                    g["al_minimo"].append(r_l)
                g["a_2R"].append(r_2)
    out = {}
    for k, g in sorted(res.items()):
        fila = {}
        for nombre, x in g.items():
            arr = np.array(x)
            if len(arr):
                fila[nombre] = {"n": len(arr), "r_medio": round(float(arr.mean()), 3),
                                "gana_pct": round(100 * float((arr > 0).mean()), 1)}
        out[k] = fila
    return out


def texto() -> str:
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    L = ["## Entrar en el retroceso (5M, desde 2022)",
         "Impulso de >= 2 ATR a favor de la EMA 200, retroceso contra el, y una vela que cierra "
         "rompiendo las velas del retroceso. Stop sobre el techo del retroceso."]
    for clave in ("venta|todas", "venta|OTE 62-79 % en killzone", "compra|todas", "compra|OTE 62-79 % en killzone"):
        a, b = d.get(f"{clave}|2022-2024", {}).get("a_2R"), d.get(f"{clave}|2025-2026", {}).get("a_2R")
        if a and b:
            L.append(f"- {clave.replace('|', ' · ')} (objetivo 2 R): {a['r_medio']:+.2f} R (n={a['n']}) en 2022-24, "
                     f"{b['r_medio']:+.2f} R (n={b['n']}) en 2025-26")
    L.append("La unica que gana en los dos tramos: VENTA con retroceso al 62-79 % del impulso dentro de una "
             "killzone. Las demas quedan en cero tras el spread: el retroceso solo no basta.")
    return "\n".join(L)


if __name__ == "__main__":
    r = medir()
    ARCHIVO.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, f in r.items():
        print(f"{k:42s}", " | ".join(f"{n}: n={x['n']} {x['r_medio']:+.3f} R gana {x['gana_pct']}%" for n, x in f.items()))
