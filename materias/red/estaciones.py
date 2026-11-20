"""
Donde para un rebote y hasta donde llega: 15M, 1H y 4H desde 2022.

largo_rebote.py ya mide la profundidad en 5M y 15M. Aqui se agrega:

1. A LA LARGA: la misma medida en 1H y 4H (impulsos de dias, rebotes de dias).
2. ESTACIONES: el extremo del rebote, ¿cae cerca de un nivel que ya se veia al
   cerrar la vela del extremo del impulso (B)? Niveles, todos en el lado del
   rebote y sin tocar en B:
     ob     order block contrario sin tocar (su borde cercano)
     fvg    vacio contrario sin tocar (su borde cercano)
     swing  maximo/minimo relativo (fractal de 2) sin romper
     piv    pivotes clasicos del dia anterior (P, R1, R2, S1, S2)
     r50    numero redondo multiplo de 50
     r10    multiplo de 10
     fib    38,2 / 50 / 61,8 % de la pierna
     ob1h   order block contrario sin tocar de 1H (solo en 15M)
   Para no engañarse con la densidad de niveles, cada rebote se compara con
   un placebo: la misma pierna con la profundidad de otro rebote al azar. Si
   el extremo real cae junto a un nivel mas a menudo que el placebo, el nivel
   frena rebotes (lift > 1). Si no, es casualidad.
3. CORTO O LARGO: cuando hay un nivel cerca, ¿el rebote se queda antes del
   borde o lo pasa un poco? (para saber donde poner la limitada).

Todo con velas cerradas: los niveles salen de velas anteriores a B.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.utbot import agrupar, atr, ema
from materias.red.zonas import CORTE, DESDE, fractales, huecos, order_blocks, series

ARCHIVO = Path(__file__).with_name("estaciones.json")
K = 2.0
MIN_PIERNA = 4.0
HORIZONTE = {"M15": 96, "H1": 120, "H4": 60}       # 1 dia, 5 dias, 10 dias
SEG = {"M15": 900, "H1": 3600, "H4": 14400}
TOL = 0.25                                           # cerca = a 0,25 ATR del nivel
NIVELES = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
SORTEOS = 30


def rebotes(v: dict, a, horizonte: int) -> list[dict]:
    """Igual que largo_rebote.rebotes, guardando velas y precios."""
    e = ema(v["c"], 200)
    piv = series(v["h"], v["l"], a, K)
    out, n = [], len(v["c"])
    for p in range(1, len(piv)):
        iA, icA, A, _ = piv[p - 1]
        iB, ic, B, tipoB = piv[p]
        lado = -1 if tipoB < 0 else 1
        if v["t"][ic] < DESDE or np.isnan(a[ic]) or ic == iB:
            continue
        pierna = abs(B - A)
        if pierna < MIN_PIERNA * a[ic]:
            continue
        fin = min(iB + 1 + horizonte, n)
        prof, iext, sigue = 0.0, iB, False
        for j in range(iB + 1, fin):
            if (v["h"][j] > B) if lado > 0 else (v["l"][j] < B):
                sigue = True
                break
            x = (B - v["l"][j]) if lado > 0 else (v["h"][j] - B)
            if x > prof:
                prof, iext = x, j
        if not sigue and fin < n and fin - iB - 1 < horizonte:
            continue
        out.append({"iA": iA, "icA": icA, "iB": iB, "aA": float(a[icA]), "A": float(A), "B": float(B), "lado": lado, "pierna": pierna,
                    "a": float(a[iB]), "ratio": prof / pierna, "atr": prof / a[iB], "velas": iext - iB,
                    "R": float(B - lado * prof), "sigue": sigue,
                    "a_favor": bool((v["c"][ic] > e[ic]) if lado > 0 else (v["c"][ic] < e[ic])),
                    "tramo": "2022-2024" if v["t"][ic] < CORTE else "2025-2026"})
    return out


def _primer_toque(h, lo, k: int, nivel: float, arriba: bool) -> int:
    """Primera vela despues de k que toca el nivel (arriba: el precio sube hasta el)."""
    x = h[k + 1:] >= nivel if arriba else lo[k + 1:] <= nivel
    j = np.flatnonzero(x)
    return k + 1 + int(j[0]) if len(j) else len(h)


def zonas(v: dict, a) -> dict[str, np.ndarray]:
    """Cada nivel: (vela desde la que se conoce, vela en que se toca, precio, +1 frena subidas / -1 bajadas)."""
    h, lo = v["h"], v["l"]
    out = {}
    filas = []
    for k, lado, bajo, alto, _ in order_blocks(v, a):
        if alto - bajo <= 0:
            continue
        # OB bajista (lado -1) esta arriba y frena subidas: borde cercano = bajo
        if lado < 0:
            filas.append((k, _primer_toque(h, lo, k, bajo, True), bajo, 1))
        else:
            filas.append((k, _primer_toque(h, lo, k, alto, False), alto, -1))
    out["ob"] = np.array(filas, float).reshape(-1, 4)
    filas = []
    for i, lado, bajo, alto in huecos(h, lo, a, t=v["t"], seg=int(np.median(np.diff(v["t"])))):
        if lado < 0:
            filas.append((i, _primer_toque(h, lo, i, bajo, True), bajo, 1))
        else:
            filas.append((i, _primer_toque(h, lo, i, alto, False), alto, -1))
    out["fvg"] = np.array(filas, float).reshape(-1, 4)
    filas = []
    for i, conf, precio, tipo in fractales(h, lo, 2):
        filas.append((conf, _primer_toque(h, lo, conf, precio, tipo > 0), precio, 1 if tipo > 0 else -1))
    out["swing"] = np.array(filas, float).reshape(-1, 4)
    return out


def pivotes(v: dict) -> dict[int, np.ndarray]:
    """Pivotes clasicos del dia anterior. El dia del servidor empieza a las 17:00 NY."""
    dia = v["t"] // 86400
    ini = np.r_[0, np.flatnonzero(np.diff(dia)) + 1]
    fin = np.r_[ini[1:], len(dia)]
    out = {}
    for x in range(1, len(ini)):
        p0, p1 = ini[x - 1], fin[x - 1]
        H, L, C = v["h"][p0:p1].max(), v["l"][p0:p1].min(), v["c"][p1 - 1]
        P = (H + L + C) / 3
        out[int(dia[ini[x]])] = np.array([P, 2 * P - L, 2 * P - H, P + (H - L), P - (H - L)])
    return out


def niveles_en(r: dict, z: dict, piv: dict, t, z1h: dict | None, t1h) -> dict[str, np.ndarray]:
    """Los niveles que se veian al cerrar B, en el lado del rebote, entre B y A + 1 pierna."""
    d = -r["lado"]                     # el rebote va hacia d
    B, iB = r["B"], r["iB"]
    lejos = B + d * 2 * r["pierna"]
    lo_, hi_ = sorted((B, lejos))

    def filtra(m, i_ref):
        if m is None or not len(m):
            return np.empty(0)
        ok = (m[:, 0] <= i_ref) & (m[:, 1] > i_ref) & (m[:, 3] == d) & (m[:, 2] > lo_) & (m[:, 2] < hi_)
        return m[ok, 2]

    out = {k: filtra(z[k], iB) for k in ("ob", "fvg", "swing")}
    p = piv.get(int(t[iB] // 86400))
    out["piv"] = p[(p > lo_) & (p < hi_)] if p is not None else np.empty(0)
    out["r50"] = np.arange(np.ceil(lo_ / 50) * 50, hi_, 50.0)
    out["r10"] = np.arange(np.ceil(lo_ / 10) * 10, hi_, 10.0)
    out["fib"] = np.array([B + d * f * r["pierna"] for f in (0.382, 0.5, 0.618)])
    if z1h is not None:
        i1 = int(np.searchsorted(t1h, t[iB], "right")) - 2      # ultima vela de 1H cerrada
        out["ob1h"] = filtra(z1h["ob"], i1)
        out["fvg1h"] = filtra(z1h["fvg"], i1)
    out["cualquiera"] = np.concatenate([out[k] for k in ("ob", "fvg", "swing", "piv")])
    return out


def _cerca(R: float, niv: np.ndarray, tol: float) -> bool:
    return bool(len(niv)) and bool((np.abs(niv - R) <= tol).any())


def _firmado(R: float, niv: np.ndarray, d: int, a: float) -> float | None:
    """Del extremo al nivel mas cercano, en ATR: + se quedo antes, - lo paso."""
    if not len(niv):
        return None
    k = int(np.argmin(np.abs(niv - R)))
    x = (niv[k] - R) * d / a
    return float(x) if abs(x) <= 1.0 else None


def medir_tf(tf: str, v: dict, z1h=None, t1h=None, rng=None) -> dict:
    a = atr(v["h"], v["l"], v["c"], 14)
    rs = rebotes(v, a, HORIZONTE[tf])
    z = zonas(v, a)
    piv = pivotes(v)
    ratios = np.array([r["ratio"] for r in rs])
    tipos = ["ob", "fvg", "swing", "piv", "r50", "r10", "fib", "cualquiera"] + (["ob1h", "fvg1h"] if z1h else [])
    real = {k: [] for k in tipos}
    placebo = {k: [] for k in tipos}
    firm = {k: [] for k in tipos}
    firm_pl = {k: [] for k in tipos}
    hay = {k: [] for k in tipos}
    for r in rs:
        niv = niveles_en(r, z, piv, v["t"], z1h, t1h)
        d, tol = -r["lado"], TOL * r["a"]
        falsos = r["B"] + d * rng.choice(ratios, SORTEOS) * r["pierna"]
        for k in tipos:
            hay[k].append(len(niv[k]) > 0)
            real[k].append(_cerca(r["R"], niv[k], tol))
            placebo[k].append(np.mean([_cerca(f, niv[k], tol) for f in falsos]))
            s = _firmado(r["R"], niv[k], d, r["a"])
            if s is not None:
                firm[k].append(s)
            for f in falsos[:5]:
                s = _firmado(f, niv[k], d, r["a"])
                if s is not None:
                    firm_pl[k].append(s)
    x = ratios
    atrs = np.array([r["atr"] for r in rs])
    sig = np.array([r["sigue"] for r in rs])
    out = {"n": len(rs),
           "prof_p50": round(float(np.median(x)), 3), "prof_p75": round(float(np.percentile(x, 75)), 3),
           "atr_p50": round(float(np.median(atrs)), 2), "atr_p75": round(float(np.percentile(atrs, 75)), 2),
           "atr_p90": round(float(np.percentile(atrs, 90)), 2),
           "usd_p50": round(float(np.median([abs(r["R"] - r["B"]) for r in rs if r["tramo"] == "2025-2026"])), 1),
           "horas_p50": round(float(np.median([r["velas"] for r in rs])) * SEG[tf] / 3600, 1),
           "pierna_atr_p50": round(float(np.median([r["pierna"] / r["a"] for r in rs])), 1),
           "sigue_pct": round(100 * float(sig.mean()), 1),
           "llega": {f"{100 * g:g}": round(100 * float((x >= g).mean()), 1) for g in NIVELES},
           "por_pierna": {}, "estaciones": {}}
    pa = np.array([r["pierna"] / r["a"] for r in rs])
    for nom, m in (("4-6 ATR", pa < 6), ("6-10 ATR", (pa >= 6) & (pa < 10)), ("10+ ATR", pa >= 10)):
        if m.sum() >= 20:
            out["por_pierna"][nom] = {"n": int(m.sum()), "prof_p50": round(float(np.median(x[m])), 3),
                                      "atr_p50": round(float(np.median(atrs[m])), 2),
                                      "llega": {f"{100 * g:g}": round(100 * float((x[m] >= g).mean()), 1)
                                                for g in NIVELES}}
    tramo = np.array([r["tramo"] for r in rs])
    for k in tipos:
        rr, pp, hh = np.array(real[k]), np.array(placebo[k]), np.array(hay[k])
        fila = {"con_nivel_pct": round(100 * float(hh.mean()), 1)}
        for tr in ("2022-2024", "2025-2026", "todo"):
            m = hh & ((tramo == tr) if tr != "todo" else True)
            if m.sum() >= 30:
                fila[tr] = {"n": int(m.sum()), "real_pct": round(100 * float(rr[m].mean()), 1),
                            "placebo_pct": round(100 * float(pp[m].mean()), 1),
                            "lift": round(float(rr[m].mean() / max(pp[m].mean(), 1e-9)), 2)}
        f, fp = np.array(firm[k]), np.array(firm_pl[k])
        if len(f) >= 30:
            fila["antes_pct"] = round(100 * float((f > 0).mean()), 1)
            fila["antes_pct_placebo"] = round(100 * float((fp > 0).mean()), 1) if len(fp) else None
            fila["antes_mediana_atr"] = round(float(np.median(f[f > 0])), 2) if (f > 0).any() else None
            fila["pasa_mediana_atr"] = round(float(np.median(-f[f < 0])), 2) if (f < 0).any() else None
        out["estaciones"][k] = fila
    out["fin_de_tramo"] = fin_de_tramo(rs, z, piv, v["t"], z1h, t1h, rng)
    return out


def fin_de_tramo(rs, z, piv, t, z1h, t1h, rng) -> dict:
    """¿Donde acaba el impulso A -> B? Niveles que se veian al confirmarse A, en la direccion del impulso.
    Placebo: el mismo A con el largo (en ATR) de otro impulso al azar."""
    largos = np.array([r["pierna"] / r["aA"] for r in rs])
    tipos = ["ob", "fvg", "swing", "piv", "r50", "r10", "cualquiera"] + (["ob1h", "fvg1h"] if z1h else [])
    real, pl, hay = ({k: [] for k in tipos} for _ in range(3))
    tramo = []
    for r in rs:
        falso = {"lado": -r["lado"], "B": r["A"], "iB": r["icA"], "pierna": r["pierna"]}
        niv = niveles_en(falso, z, piv, t, z1h, t1h)
        d, tol = r["lado"], TOL * r["aA"]
        falsos = r["A"] + d * rng.choice(largos, SORTEOS) * r["aA"]
        tramo.append(r["tramo"])
        for k in tipos:
            hay[k].append(len(niv[k]) > 0)
            real[k].append(_cerca(r["B"], niv[k], tol))
            pl[k].append(np.mean([_cerca(f, niv[k], tol) for f in falsos]))
    tramo = np.array(tramo)
    out = {"largo_atr_p50": round(float(np.median(largos)), 2), "largo_atr_p75": round(float(np.percentile(largos, 75)), 2)}
    for k in tipos:
        rr, pp, hh = np.array(real[k]), np.array(pl[k]), np.array(hay[k])
        fila = {}
        for tr in ("2022-2024", "2025-2026", "todo"):
            m = hh & ((tramo == tr) if tr != "todo" else True)
            if m.sum() >= 30:
                fila[tr] = {"n": int(m.sum()), "real_pct": round(100 * float(rr[m].mean()), 1),
                            "placebo_pct": round(100 * float(pp[m].mean()), 1),
                            "lift": round(float(rr[m].mean() / max(pp[m].mean(), 1e-9)), 2)}
        out[k] = fila
    return out


def medir() -> dict:
    rng = np.random.default_rng(7)
    m = D.cargar("M5")
    m = m[m["time"] >= DESDE - 60 * 86400]
    marcos = {tf: agrupar(m, SEG[tf]) for tf in ("M15", "H1", "H4")}
    h1 = marcos["H1"]
    z1h = zonas(h1, atr(h1["h"], h1["l"], h1["c"], 14))
    out = {"tol_atr": TOL, "desde": "2022-01-01"}
    for tf, v in marcos.items():
        out[tf] = medir_tf(tf, v, z1h if tf == "M15" else None, h1["t"] if tf == "M15" else None, rng)
    return out


NOMBRES = {"ob": "order block sin tocar", "fvg": "vacio sin tocar", "swing": "maximo/minimo relativo",
           "piv": "pivote del dia", "r50": "redondo de 50", "r10": "redondo de 10", "fib": "38/50/62 %",
           "ob1h": "OB de 1H", "fvg1h": "vacio de 1H", "cualquiera": "OB, vacio, relativo o pivote"}


def exportar() -> dict:
    r = medir()
    ARCHIVO.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    return r


def texto() -> str:
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    L = [f"## Hasta donde llega un rebote y donde para (15M, 1H, 4H desde 2022; impulso >= {MIN_PIERNA:g} ATR)",
         "El TAMAÑO se repite en todos los marcos medido en ATR de ese marco; los NIVELES casi no explican donde para. "
         "Para proyectar: tramo ~4,7 ATR (p75 5,7); rebote ~3,8 ATR (p75 7); en % de la pierna, cuanto mas larga "
         "la pierna mas corto el rebote. Si ya va por X %, P(llegar a Y) = llega(Y) / llega(X)."]
    for tf in ("M15", "H1", "H4"):
        f = d.get(tf)
        if not f:
            continue
        ll = " · ".join(f"{k} %: {x:.0f}" for k, x in f["llega"].items())
        L.append(f"- {tf} (n={f['n']}): rebote mediana {100 * f['prof_p50']:.0f} % de la pierna = {f['atr_p50']} ATR "
                 f"(p75 {f['atr_p75']}, p90 {f['atr_p90']}), tarda {f['horas_p50']} h hasta su extremo; "
                 f"el impulso sigue despues el {f['sigue_pct']:.0f} %; llegan a {ll}.")
        pp = " · ".join(f"pierna {k}: mediana {100 * x['prof_p50']:.0f} %" for k, x in f["por_pierna"].items())
        L.append(f"  {pp}")
        ft = f["fin_de_tramo"]
        L.append(f"  tramo: mediana {ft['largo_atr_p50']} ATR, p75 {ft['largo_atr_p75']}")
        est = [(k, x["todo"]) for k, x in f["estaciones"].items() if "todo" in x and k != "cualquiera"]
        L.append("  el rebote para junto a (real vs azar): " + " · ".join(
            f"{NOMBRES[k]} {x['real_pct']:.0f}/{x['placebo_pct']:.0f} %" for k, x in est))
        ob = ft.get("ob", {})
        if "2022-2024" in ob and "2025-2026" in ob:
            L.append(f"  el TRAMO acaba en un OB sin tocar {ob['todo']['real_pct']:.0f} % vs azar "
                     f"{ob['todo']['placebo_pct']:.0f} % (x{ob['2022-2024']['lift']} y x{ob['2025-2026']['lift']} "
                     "en los dos periodos): lo unico que se repite, y es poco.")
    L.append("Conclusion: ningun nivel (OB, vacio, relativo, pivote, redondo, Fibonacci) frena el REBOTE mas que el "
             "azar; el extremo queda antes o despues del borde por igual (~50/50). Proyectar con el tamaño en ATR y "
             "dar zonas de 1 ATR, no un precio; los niveles solo sirven para ubicar la zona dentro de ese rango.")
    return "\n".join(L)


if __name__ == "__main__":
    r = exportar()
    for tf in ("M15", "H1", "H4"):
        f = r[tf]
        print(f"\n{tf}: n={f['n']} prof p50 {f['prof_p50']} p75 {f['prof_p75']} | ATR p50 {f['atr_p50']} "
              f"p75 {f['atr_p75']} p90 {f['atr_p90']} | USD p50 (2025-26) {f['usd_p50']} | {f['horas_p50']} h | "
              f"pierna {f['pierna_atr_p50']} ATR | sigue {f['sigue_pct']} % | llega {f['llega']}")
        for k, x in f["por_pierna"].items():
            print(f"   pierna {k}: {x}")
        for k, x in f["estaciones"].items():
            print(f"   {k:10s} {x}")
        print("   FIN DE TRAMO", {k: x for k, x in f["fin_de_tramo"].items() if not isinstance(x, dict)})
        for k, x in f["fin_de_tramo"].items():
            if isinstance(x, dict):
                print(f"   tramo {k:10s} {x}")
