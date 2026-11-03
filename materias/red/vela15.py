"""
Rebotes DENTRO de una vela de 15M, medidos con las velas de 1M (2022-2026).

Cada vez que un minuto marca un extremo nuevo de la vela de 15M en curso
(maximo o minimo desde su apertura) se mira lo que pasa hasta que esa vela
cierra: si el precio retrocede r ATR desde ese extremo ANTES de estirarse
0,15 ATR mas alla («rebota»), si se estira antes («sigue») o si no pasa
ninguna de las dos («nada»). Mismo minuto con las dos = «sigue» (lo
prudente para quien entra en contra). ATR = ATR 14 de 15M de la vela
anterior. Se agrupa por cuanto se ha estirado ya la vela desde su apertura
y por el minuto de la vela.

LO QUE SALIO (2026-11-02): entrar en contra del extremo NO tiene ventaja.
Con stop 0,15 ATR mas alla del extremo, objetivo 0,15-0,4 ATR y salida al
cierre de la vela, la media es -0,01 a -0,03 ATR por operacion en todos los
estiramientos (0,4-1,3 ATR), sesiones, minutos y a favor o en contra de la
EMA 200, igual en 2022-24 que en 2025-26. Gana lo que el azar menos el
spread. Las tablas sirven para poner el objetivo y el stop con numeros, no
para dar entradas.

    .venv/Scripts/python.exe -m materias.red.vela15
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.utbot import agrupar, atr
from materias.red.zonas import CORTE, DESDE

ARCHIVO = Path(__file__).with_name("vela15.json")
MQH = Path(__file__).resolve().parents[1] / "mt5" / "Vela15_tabla.mqh"
SIGUE = 0.15                                   # ATR mas alla del extremo que cuenta como «sigue»
RETROCESOS = (0.15, 0.25, 0.4, 0.6)            # ATR desde el extremo
ESTIRONES = (0.0, 0.4, 0.6, 0.8, 1.0, 1.3)     # limites inferiores, en ATR desde la apertura
MINUTOS = (0, 5, 10)                           # limites inferiores del minuto de la vela


def _cubo(x: float, limites) -> int:
    return int(np.searchsorted(limites, x, side="right") - 1)


def medir() -> dict:
    m = D.cargar("M1")
    m = m[m["time"] >= DESDE - 40 * 86400]
    q = agrupar(m, 900)
    a15 = atr(q["h"], q["l"], q["c"], 14)
    g = m["time"] // 900
    ini = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    fin = np.r_[ini[1:], len(m)]
    h, lo = m["high"], m["low"]
    nr = len(RETROCESOS)
    cuenta = {tr: np.zeros((len(MINUTOS), len(ESTIRONES), nr, 3), int) for tr in ("22-24", "25-26")}
    rango = {tr: [] for tr in cuenta}
    for n in range(220, len(ini)):
        a, i0, i1 = a15[n - 1], ini[n], fin[n]
        if q["t"][n] < DESDE or not a > 0 or i1 - i0 < 10:
            continue
        tr = "22-24" if q["t"][n] < CORTE else "25-26"
        hv, lv = h[i0:i1], lo[i0:i1]
        o = m["open"][i0]
        rango[tr].append((hv.max() - lv.min()) / a)
        for d in (1, -1):
            ext = np.maximum.accumulate(hv) if d > 0 else np.minimum.accumulate(lv)
            nuevo = (hv == ext) if d > 0 else (lv == ext)
            fav = hv if d > 0 else lv            # lo que estira
            con = lv if d > 0 else hv            # lo que retrocede
            for j in np.flatnonzero(nuevo[:-1]):
                e = (ext[j] - o) * d / a
                cm, ce = _cubo(j, MINUTOS), _cubo(e, ESTIRONES)
                sig = np.flatnonzero((fav[j + 1:] - ext[j]) * d >= SIGUE * a)
                p_sig = sig[0] if len(sig) else 99
                for k, r in enumerate(RETROCESOS):
                    reb = np.flatnonzero((ext[j] - con[j + 1:]) * d >= r * a)
                    p_reb = reb[0] if len(reb) else 99
                    res = 0 if p_reb < p_sig else (1 if p_sig < 99 else 2)
                    cuenta[tr][cm, ce, k, res] += 1
    out = {"sigue_atr": SIGUE, "retrocesos": RETROCESOS, "estirones": ESTIRONES, "minutos": MINUTOS}
    for tr, c in cuenta.items():
        tot = c.sum(axis=3, keepdims=True).clip(min=1)
        out[tr] = {"n": c[..., 0, :].sum(axis=2).tolist(), "p": (c / tot).round(4).tolist()}
        rg = np.array(rango[tr])
        out[tr]["rango_vela"] = {f"p{p}": round(float(np.percentile(rg, p)), 3) for p in (25, 50, 75, 90)}
    return out


def exportar_mqh(d: dict) -> Path:
    """Tabla de 2025-26 (lo reciente) para el indicador Vela15_Scalper."""
    t = d["25-26"]
    p = np.array(t["p"])
    filas = []
    for k in range(3):
        cuerpo = ",\n".join("   " + ",".join(f"{x:.4f}" for x in p[cm, ce, :, k])
                            for cm in range(len(MINUTOS)) for ce in range(len(ESTIRONES)))
        filas.append(f"double V15_P{'RSN'[k]}[{len(MINUTOS) * len(ESTIRONES) * len(RETROCESOS)}] = {{\n{cuerpo}\n}};")
    rv = t["rango_vela"]
    MQH.write_text(
        "// Generado por materias/red/vela15.py: no editar a mano.\n"
        "// P[minuto][estiron][retroceso] de 2025-26: R = rebota r ATR antes de seguir "
        f"{d['sigue_atr']} ATR, S = sigue antes, N = ninguna hasta el cierre de la vela.\n"
        f"#define V15_NM {len(MINUTOS)}\n#define V15_NE {len(ESTIRONES)}\n#define V15_NR {len(RETROCESOS)}\n"
        f"double V15_SIGUE = {d['sigue_atr']};\n"
        f"double V15_RETRO[{len(RETROCESOS)}] = {{{','.join(map(str, RETROCESOS))}}};\n"
        f"double V15_ESTIRON[{len(ESTIRONES)}] = {{{','.join(map(str, ESTIRONES))}}};\n"
        f"double V15_MINUTO[{len(MINUTOS)}] = {{{','.join(map(str, MINUTOS))}}};\n"
        f"double V15_RANGO[4] = {{{rv['p25']},{rv['p50']},{rv['p75']},{rv['p90']}}}; // rango de la vela en ATR: p25 p50 p75 p90\n"
        + "\n".join(filas) + "\n", encoding="utf-8")
    return MQH


if __name__ == "__main__":
    d = medir()
    ARCHIVO.write_text(json.dumps(d, indent=1), encoding="utf-8")
    exportar_mqh(d)
    for tr in ("22-24", "25-26"):
        p, n = np.array(d[tr]["p"]), np.array(d[tr]["n"])
        print(f"\n{tr} rango de la vela (ATR): {d[tr]['rango_vela']}")
        for cm, mi in enumerate(MINUTOS):
            for ce, es in enumerate(ESTIRONES):
                cel = " | ".join(f"r{r}: reb {100 * p[cm, ce, k, 0]:.0f} sig {100 * p[cm, ce, k, 1]:.0f}"
                                 for k, r in enumerate(RETROCESOS))
                print(f"  min {mi:>2}+ estiron {es}+ n={n[cm, ce]:6d}  {cel}")
