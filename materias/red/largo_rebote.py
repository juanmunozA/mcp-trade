"""
Cuanto llega un rebote: medido en 5M y 15M desde 2022.

Un impulso va de A a B (zigzag de 2 ATR, pierna >= 4 ATR). Desde B el precio
rebota en contra. Se mide, hasta que el precio vuelve a pasar B (o 1 dia):
  profundidad : lo maximo que rebota, en % de la pierna A-B y en ATR
  duracion    : velas desde B hasta el extremo del rebote
  sigue       : si despues vuelve a pasar B (el rebote era un retroceso)
                o no (el rebote era un giro)

Uso en vivo: si el rebote ya va por el X %, la probabilidad de que llegue al
Y % es S(Y) / S(X), donde S es la proporcion de rebotes que llegaron a cada
nivel (curva de supervivencia). Se exporta tambien como include de MQL5
(materias/mt5/Rebote_tabla.mqh) para que el indicador la dibuje.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import SESIONES, hora_ny
from materias.red.utbot import agrupar, atr, ema
from materias.red.zonas import CORTE, DESDE, series

ARCHIVO = Path(__file__).with_name("largo_rebote.json")
MQH = Path(__file__).resolve().parents[1] / "mt5" / "Rebote_tabla.mqh"
K = 2.0                                  # giro del zigzag en ATR (con 1 es ruido de vela a vela)
MIN_PIERNA = 4.0                         # pierna minima en ATR
VELAS_DIA = {"M5": 288, "M15": 96}
REJILLA = np.round(np.arange(0, 2.0001, 0.05), 2)   # profundidad en % de la pierna (0..200 %)
NIVELES = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
TRAMOS_PROF = ("0-38", "38-61", "61-78", "78-100", "100-mas")


def rebotes(v: dict, horizonte: int) -> list[dict]:
    """Cada impulso confirmado y lo que hizo su rebote."""
    a = atr(v["h"], v["l"], v["c"], 14)
    e = ema(v["c"], 200)
    hh = hora_ny(v["t"])
    piv = series(v["h"], v["l"], a, K)
    out, n = [], len(v["c"])
    for p in range(1, len(piv)):
        iA, _, A, _ = piv[p - 1]
        iB, ic, B, tipoB = piv[p]
        lado = -1 if tipoB < 0 else 1          # +1 impulso alcista (B maximo), -1 bajista
        # ic == iB: el extremo y el giro en la misma vela, sin saber cual fue antes
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
            continue                            # se acabaron los datos: no se sabe
        out.append({
            "lado": lado, "ratio": prof / pierna, "atr": prof / a[ic], "velas": iext - iB, "sigue": sigue,
            "pierna_atr": pierna / a[ic],
            "a_favor": (v["c"][ic] > e[ic]) if lado > 0 else (v["c"][ic] < e[ic]),
            "kz": any(x <= hh[ic] < y for x, y in SESIONES.values()),
            "tramo": "2022-2024" if v["t"][ic] < CORTE else "2025-2026",
        })
    return out


def grupos(r: dict) -> list[str]:
    nom = "alcista" if r["lado"] > 0 else "bajista"
    fav = "a favor EMA200" if r["a_favor"] else "contra EMA200"
    pa = r["pierna_atr"]
    tam = "pierna 4-6 ATR" if pa < 6 else ("pierna 6-10 ATR" if pa < 10 else "pierna 10+ ATR")
    return ["todas", fav, tam, f"{fav}|{tam}", f"impulso {nom}|{fav}",
            "killzone" if r["kz"] else "fuera de killzone"]


def resumir(rs: list[dict]) -> dict:
    x = np.array([r["ratio"] for r in rs])
    s = [round(float((x >= g).mean()), 4) for g in REJILLA]
    sig = np.array([r["sigue"] for r in rs])
    # ¿sigue la tendencia segun lo hondo que fue el rebote?
    por_prof = {}
    for lo, hi in ((0, 0.382), (0.382, 0.618), (0.618, 0.786), (0.786, 1.0), (1.0, 9)):
        m = (x >= lo) & (x < hi)
        if m.sum() >= 30:
            por_prof[f"{int(100 * lo)}-{int(100 * hi) if hi < 9 else 'mas'}"] = round(100 * float(sig[m].mean()), 1)
    return {"n": len(rs),
            "p50": round(float(np.median(x)), 3), "p75": round(float(np.percentile(x, 75)), 3),
            "atr_p50": round(float(np.median([r["atr"] for r in rs])), 2),
            "atr_p75": round(float(np.percentile([r["atr"] for r in rs], 75)), 2),
            "velas_p50": int(np.median([r["velas"] for r in rs])),
            "sigue_pct": round(100 * float(sig.mean()), 1),
            "sigue_por_profundidad": por_prof,
            "llega": {f"{int(1000 * g) / 10:g}": round(100 * float((x >= g).mean()), 1) for g in NIVELES},
            "supervivencia": s}


def medir() -> dict:
    m = D.cargar("M5")
    m = m[m["time"] >= DESDE - 30 * 86400]
    marcos = {"M5": {"t": m["time"], "o": m["open"], "h": m["high"], "l": m["low"], "c": m["close"]},
              "M15": agrupar(m, 900)}
    out = {"rejilla": REJILLA.tolist()}
    for tf, v in marcos.items():
        rs = rebotes(v, VELAS_DIA[tf])
        acc: dict[str, list] = {}
        for r in rs:
            for g in grupos(r):
                acc.setdefault(f"{tf}|{g}|{r['tramo']}", []).append(r)
                acc.setdefault(f"{tf}|{g}|todo", []).append(r)
        for k, x in sorted(acc.items()):
            if len(x) >= 50:
                out[k] = resumir(x)
    return out


def exportar_mqh(d: dict) -> None:
    """Tabla de supervivencia para el indicador: [marco][grupo][rejilla], con datos de todo el periodo."""
    claves = ["a favor EMA200|pierna 4-6 ATR", "a favor EMA200|pierna 6-10 ATR", "a favor EMA200|pierna 10+ ATR",
              "contra EMA200|pierna 4-6 ATR", "contra EMA200|pierna 6-10 ATR", "contra EMA200|pierna 10+ ATR"]
    L = ["// Generado por materias/red/largo_rebote.py: no editar a mano.",
         f"// Zigzag de {K:g} ATR, impulso >= {MIN_PIERNA:g} ATR.",
         f"#define REB_K {K:.1f}",
         f"#define REB_MIN_PIERNA {MIN_PIERNA:.1f}",
         "// Supervivencia de la profundidad del rebote (fraccion de rebotes que llegan a cada % de la pierna).",
         "// Grupos: 0-2 impulso a favor EMA200 (pierna 4-6, 6-10, 10+ ATR), 3-5 en contra.",
         f"#define REB_PASOS {len(REJILLA)}",
         f"#define REB_PASO {REJILLA[1]:.2f}"]
    for tf in ("M5", "M15"):
        filas, sig, ns = [], [], []
        for c in claves:
            # grupo con menos de 50 casos: se usa el de todas
            f = d.get(f"{tf}|{c}|todo") or d[f"{tf}|todas|todo"]
            filas.append("{" + ",".join(f"{x:.4f}" for x in f["supervivencia"]) + "}")
            sig.append(f"{f['sigue_pct']:.1f}")
            ns.append(str(f["n"]))
        L.append(f"double REB_{tf}[6][{len(REJILLA)}] = {{\n   " + ",\n   ".join(filas) + "};")
        L.append(f"double REB_SIGUE_{tf}[6] = {{{','.join(sig)}}};")
        L.append(f"int REB_N_{tf}[6] = {{{','.join(ns)}}};")
        # ¿sigue la tendencia despues, segun lo hondo que fue el rebote?
        pp = d[f"{tf}|todas|todo"]["sigue_por_profundidad"]
        L.append(f"double REB_SIGUE_PROF_{tf}[5] = {{{','.join(f'{pp.get(k, 0):.1f}' for k in TRAMOS_PROF)}}};"
                 "   // 0-38, 38-61, 61-78, 78-100, 100+ %")
    MQH.write_text("\n".join(L) + "\n", encoding="utf-8")


def _tabla(d: dict, tf: str, a_favor: bool, pierna_atr: float) -> dict:
    fav = "a favor EMA200" if a_favor else "contra EMA200"
    tam = "pierna 4-6 ATR" if pierna_atr < 6 else ("pierna 6-10 ATR" if pierna_atr < 10 else "pierna 10+ ATR")
    return d.get(f"{tf}|{fav}|{tam}|todo") or d[f"{tf}|todas|todo"]


def _s(f: dict, r: float) -> float:
    return float(np.interp(r, REJILLA, f["supervivencia"]))


def en_curso(h, lo, c, tf: str = "M5", d: dict | None = None) -> dict | None:
    """El impulso mas reciente y su rebote, con la probabilidad de llegar a cada nivel.

    Si el tramo que va desde el ultimo giro confirmado ya mide >= MIN_PIERNA ATR, ese es el
    impulso y su rebote aun no se confirma (probabilidades 'si rebota 2 ATR'). Si no, el
    impulso es el anterior y el rebote ya esta confirmado: la probabilidad de llegar a Y
    sabiendo que ya va por X es S(Y) / S(X).
    """
    d = d or json.loads(ARCHIVO.read_text(encoding="utf-8"))
    h, lo, c = np.asarray(h, float), np.asarray(lo, float), np.asarray(c, float)
    a, e = atr(h, lo, c, 14), ema(c, 200)
    piv = series(h, lo, a, K)
    if len(piv) < 2:
        return None
    n = len(c) - 1
    _, ic, P, tipo = piv[-1]
    # tramo desde el ultimo giro hasta el extremo de ahora
    tramo = lo[ic:] if tipo > 0 else h[ic:]
    ix = ic + int(np.argmin(tramo) if tipo > 0 else np.argmax(tramo))
    X = float(lo[ix] if tipo > 0 else h[ix])
    if abs(P - X) >= MIN_PIERNA * a[n]:
        A, B, lado, i_conf, confirmado = P, X, -tipo, n, False
        despues = slice(ix + 1, n + 1)
        prof = 0.0 if ix == n else float(h[despues].max() - B if lado < 0 else B - lo[despues].min())
    else:
        A = piv[-2][2]
        B, lado, i_conf, confirmado = P, tipo, ic, True
        prof = abs(B - X)
    pierna = abs(B - A)
    pa = pierna / a[i_conf]
    if pa < MIN_PIERNA:
        return None
    a_favor = (c[i_conf] > e[i_conf]) if lado > 0 else (c[i_conf] < e[i_conf])
    f = _tabla(d, tf, a_favor, pa)
    x = max(prof, 0.0) / pierna
    base = _s(f, x)           # sin confirmar, x < giro de K ATR y S(x) = 1: queda 'si rebota'

    def precio(r: float) -> float:
        return B - lado * r * pierna

    niveles = [{"pct": round(100 * r, 1), "precio": round(precio(r), 2),
                "prob": 100.0 if r <= x else round(100 * min(1.0, _s(f, r) / base), 1)} for r in NIVELES]
    # mediana y 3 de cada 4 condicionadas a lo que ya reboto
    rej = [r for r in REJILLA if r > x]
    med = next((r for r in rej if _s(f, r) / base <= 0.5), REJILLA[-1])
    p75 = next((r for r in rej if _s(f, r) / base <= 0.25), REJILLA[-1])
    return {"tf": tf, "lado": "alcista" if lado > 0 else "bajista", "A": round(A, 2), "B": round(B, 2),
            "pierna_atr": round(pa, 1), "a_favor": bool(a_favor), "confirmado": confirmado,
            "rebote_pct": round(100 * x, 1), "rebote_hasta": round(precio(x), 2), "n": f["n"],
            "mediana": {"pct": round(100 * med), "precio": round(precio(med), 2)},
            "p75": {"pct": round(100 * p75), "precio": round(precio(p75), 2)},
            "niveles": niveles, "sigue_por_profundidad": d[f"{tf}|todas|todo"]["sigue_por_profundidad"]}


def describir(r: dict) -> str:
    fav = "a favor" if r["a_favor"] else "en contra"
    est = (f"rebote confirmado, va por el {r['rebote_pct']:.0f} % ({r['rebote_hasta']})" if r["confirmado"]
           else f"rebote aun sin confirmar ({r['rebote_pct']:.0f} %); probabilidades si rebota >= {K:g} ATR")
    nv = " · ".join(f"{x['pct']:g} % {x['precio']}: {x['prob']:.0f} %" for x in r["niveles"])
    sp = r["sigue_por_profundidad"]
    return (f"{r['tf']}: impulso {r['lado']} {r['A']} -> {r['B']} ({r['pierna_atr']} ATR, {fav} de la EMA 200); {est}. "
            f"Llega a: {nv}. La mitad de los rebotes asi para antes de {r['mediana']['precio']} "
            f"({r['mediana']['pct']} %), 3 de cada 4 antes de {r['p75']['precio']} ({r['p75']['pct']} %). "
            f"Si pasa el 100 % ({r['A']}) la tendencia solo sigue el {sp.get('100-mas', 0):.0f} % de las veces "
            f"(con rebote de 78-100 %: {sp.get('78-100', 0):.0f} %). n={r['n']}")


def texto(ctx: dict | None = None) -> str:
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    L = [f"## Cuanto llega un rebote (impulsos de >= {MIN_PIERNA:g} ATR, zigzag de {K:g} ATR, desde 2022)",
         "Profundidad del rebote en % del impulso antes de que el precio vuelva a pasar el extremo. "
         "Si ya va por X %, la probabilidad de llegar a Y % es llega(Y) / llega(X). Cuanto mas largo el "
         "impulso, mas corto el rebote en %: 4-6 ATR rebotan una mediana del 85 %, 10+ ATR del 30 %."]
    for tf in ("M5", "M15"):
        for g in ("a favor EMA200", "contra EMA200"):
            f = d.get(f"{tf}|{g}|todo")
            if f:
                ll = " · ".join(f"{k} %: {v:.0f}" for k, v in f["llega"].items())
                L.append(f"- {tf} impulso {g} (n={f['n']}): mediana {100 * f['p50']:.0f} % ({f['atr_p50']} ATR), "
                         f"3 de cada 4 <= {100 * f['p75']:.0f} %; llegan a {ll}; despues sigue la tendencia "
                         f"{f['sigue_pct']:.0f} %; segun lo hondo del rebote: {f['sigue_por_profundidad']}")
    L.append("Rebote de hasta el 78 %: el impulso sigue casi siempre. Si pasa el 100 % es un giro: en 15M la "
             "tendencia solo vuelve el 40 % de las veces.")
    for clave, tf in (("m5", "M5"), ("m15", "M15")):
        velas = (ctx or {}).get(clave) or []
        if len(velas) >= 250:
            try:
                r = en_curso([x["h"] for x in velas], [x["l"] for x in velas], [x["c"] for x in velas], tf, d)
            except (KeyError, ValueError):
                r = None
            if r:
                L.append(f"- AHORA {describir(r)}")
    return "\n".join(L)


if __name__ == "__main__":
    r = medir()
    ARCHIVO.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    exportar_mqh(r)
    for k, x in r.items():
        if k == "rejilla":
            continue
        print(f"{k:52s} n={x['n']:5d} p50 {100 * x['p50']:4.0f}% p75 {100 * x['p75']:4.0f}% "
              f"atr {x['atr_p50']:.2f}/{x['atr_p75']:.2f} velas {x['velas_p50']:3d} sigue {x['sigue_pct']:4.1f}% "
              f"llega {x['llega']} | {x['sigue_por_profundidad']}")
