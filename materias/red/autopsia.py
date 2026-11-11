"""
La autopsia diaria: que giros grandes hubo hoy y quien los aviso.

Nadie le dice donde mirar. Cada tarde, tras el corte de las 17:00 NY:

1. Baja de MT5 las velas de 5M (solo lectura) y marca los GIROS del dia:
   los puntos donde el precio se dio la vuelta 3 ATR o mas (zigzag).
2. Para cada giro apunta que habia en esa vela -- los mismos rasgos que usa
   el investigador -- y tres cosas mas:
     · si alguna regla adoptada del investigador lo habria avisado;
     · cuanto tardo el UT Bot de 5M en dar la señal a favor, y cuantos ATR
       del movimiento se habian ido ya (lo que se pierde por esperarlo);
     · cuanto midio el movimiento.
3. Lo acumula en `autopsia.json` (90 dias) y resume: que fraccion de los
   giros avisa alguien, cuanto llega tarde el UT, y que rasgos aparecen en
   los giros reales mas que en un toque cualquiera del extremo.

Ese resumen es la lista de puntos ciegos. OJO: que un rasgo sea frecuente
en los giros DESCRIBE, no predice (la mecha larga se ve porque ya reboto).
Lo que predice lo decide el investigador con su validacion.

    python -m materias.red.autopsia
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from materias.red import investigador as I
from materias.red import utbot as U

ARCHIVO = Path(__file__).with_name("autopsia.json")
GIRO = 3.0      # ATR de 5M para contar un giro
DIA = 288       # velas de 5M en 24 h
GUARDA = 90     # dias que se conservan


def _ny(t: int) -> dt.datetime:
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=int(t) - 7 * 3600)


def giros(h: np.ndarray, lo: np.ndarray, a: np.ndarray, ini: int) -> list[tuple[int, int]]:
    """Zigzag: (vela, lado) con lado +1 = suelo (despues sube), -1 = techo."""
    out, d, hi, li = [], 0, ini, ini
    for i in range(ini + 1, len(h)):
        if d >= 0 and h[i] > h[hi]:
            hi = i
        if d <= 0 and lo[i] < lo[li]:
            li = i
        if d >= 0 and i > hi and h[hi] - lo[i] >= GIRO * a[hi]:
            out.append((hi, -1))
            d, li = -1, i
        elif d <= 0 and i > li and h[i] - lo[li] >= GIRO * a[li]:
            out.append((li, 1))
            d, hi = 1, i
    return out


def analizar(v: np.ndarray) -> dict:
    """Los giros de las ultimas 24 h de un array de velas de 5M cerradas."""
    h, lo, c = v["high"].astype(float), v["low"].astype(float), v["close"].astype(float)
    R = I.rasgos(v)
    a = R["atr"]
    _, _, co, ve = U.utbot({"o": v["open"], "h": h, "l": lo, "c": c}, 1.0, 14, False)
    piv = giros(h, lo, a, max(300, len(v) - DIA - 100))
    desde = len(v) - DIA
    out = []
    for n, (p, s) in enumerate(piv):
        if p < desde:
            continue
        ext = lo[p] if s > 0 else h[p]
        fin = piv[n + 1][0] if n + 1 < len(piv) else len(v) - 1
        tramo = slice(p, fin + 1)
        mide = ((h[tramo].max() - ext) if s > 0 else (ext - lo[tramo].min())) / a[p]
        sig = np.flatnonzero((co if s > 0 else ve)[p:])
        tarde = None if not len(sig) else {
            "velas": int(sig[0]), "atr": round(float((c[p + sig[0]] - ext) * s / a[p]), 2)}
        # la vela candidata: la del giro o una de las 3 siguientes (segundo toque del doble suelo)
        ks = np.flatnonzero((R["idx"] >= p) & (R["idx"] <= p + 3) & (R["lado"] == s))
        rasgos, reglas = [], []
        if len(ks):
            k = int(ks[-1])
            rasgos = [r for r, x in R["F"].items() if x[k]]
            reglas = [I.clave(x) for x in I.cumple(R, k)]
        out.append({"hora_ny": f"{_ny(v['time'][p]):%H:%M}", "lado": "suelo" if s > 0 else "techo",
                    "precio": round(float(ext), 2), "atr": round(float(a[p]), 2), "mide_atr": round(float(mide), 1),
                    "en_extremo_2h": bool(len(ks)), "rasgos": rasgos, "avisado_por": reglas, "ut_tarde": tarde})
    return {"fecha": f"{_ny(v['time'][-1]):%Y-%m-%d}", "giros": out}


def resumir(dias: dict) -> dict:
    g = [x for d in dias.values() for x in d]
    if not g:
        return {}
    tarde = [x["ut_tarde"]["atr"] for x in g if x["ut_tarde"]]
    frac = [x["ut_tarde"]["atr"] / x["mide_atr"] for x in g if x["ut_tarde"] and x["mide_atr"] > 0]
    base = {k: x["frecuencia"] for k, x in I.datos().get("sueltos", {}).items()}
    cand = [x for x in g if x["en_extremo_2h"]]
    veces = {}
    for k, f in base.items():
        p = 100 * sum(k in x["rasgos"] for x in cand) / max(1, len(cand))
        veces[k] = {"en_giros_pct": round(p, 1), "en_toques_pct": f, "veces": round(p / f, 2) if f else None}
    return {"dias": len(dias), "giros": len(g),
            "mide_atr_mediana": round(float(np.median([x["mide_atr"] for x in g])), 1),
            "en_extremo_2h_pct": round(100 * len(cand) / len(g), 1),
            "avisados_pct": round(100 * sum(bool(x["avisado_por"]) for x in g) / len(g), 1),
            "ut_tarde_atr_mediana": round(float(np.median(tarde)), 2) if tarde else None,
            "ut_pierde_pct_mediana": round(100 * float(np.median(frac)), 0) if frac else None,
            "rasgos": dict(sorted(veces.items(), key=lambda kv: -(kv[1]["veces"] or 0)))}


def datos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def correr() -> dict:
    from materias.red import terminal
    mt5 = terminal.conectar()
    try:
        v = terminal.velas(mt5, "M5", I.MINIMO_VELAS)
    finally:
        mt5.shutdown()
    hoy = analizar(v)
    d = datos()
    dias = d.get("dias", {})
    dias[hoy["fecha"]] = hoy["giros"]
    dias = dict(sorted(dias.items())[-GUARDA:])
    d = {"medido": hoy["fecha"], "giro_atr": GIRO, "resumen": resumir(dias), "dias": dias}
    ARCHIVO.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    return hoy


def rellenar(n_dias: int = GUARDA) -> int:
    """Autopsia de los dias pasados con las velas guardadas (para no empezar de cero)."""
    from materias.red import datos as D
    v = D.cargar("M5")
    dia = v["time"] // 86400
    cortes = np.flatnonzero(np.diff(dia)) + 1          # primera vela de cada dia del servidor
    d = datos()
    dias = d.get("dias", {})
    for fin in cortes[-n_dias:]:
        if fin < I.MINIMO_VELAS:
            continue
        x = analizar(v[fin - I.MINIMO_VELAS:fin])
        dias.setdefault(x["fecha"], x["giros"])
    dias = dict(sorted(dias.items())[-GUARDA:])
    d = {"medido": max(dias), "giro_atr": GIRO, "resumen": resumir(dias), "dias": dias}
    ARCHIVO.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(dias)


def texto() -> str:
    d = datos()
    r = d.get("resumen")
    if not r:
        return ""
    L = [f"## Autopsia de giros (ultimos {r['dias']} dias con datos, al {d['medido']})",
         f"{r['giros']} giros de {d['giro_atr']:.0f} ATR o mas en 5M; el movimiento siguiente midio "
         f"{r['mide_atr_mediana']} ATR de mediana. El {r['en_extremo_2h_pct']:.0f} % nacio en el extremo de 2 h. "
         f"Las reglas adoptadas del investigador avisaron el {r['avisados_pct']:.0f} %."]
    if r["ut_tarde_atr_mediana"] is not None:
        L.append(f"El UT Bot de 5M dio la señal a favor cuando ya se habian ido {r['ut_tarde_atr_mediana']} ATR "
                 f"(mediana), el {r['ut_pierde_pct_mediana']:.0f} % del movimiento: esperar su señal cuesta eso.")
    top = [(k, x) for k, x in r["rasgos"].items() if x["veces"] and x["veces"] >= 1.3][:4]
    if top and r["giros"] >= 30:
        L.append("Rasgos mas frecuentes en los giros reales que en un toque cualquiera (describe, NO predice): "
                 + "; ".join(f"{I.NOMBRES[k]} {x['en_giros_pct']:.0f} % vs {x['en_toques_pct']:.0f} %" for k, x in top) + ".")
    return "\n".join(L)


if __name__ == "__main__":
    from materias.red.avisar import avisar
    try:
        hoy = correr()
    except Exception as exc:
        avisar("MCP Trade: la autopsia fallo", f"{type(exc).__name__}: {exc}")
        raise
    for x in hoy["giros"]:
        t = x["ut_tarde"]
        print(f"{x['hora_ny']} {x['lado']} {x['precio']:.2f} · midio {x['mide_atr']} ATR · "
              f"UT {'sin señal' if not t else f'{t['velas']} velas y {t['atr']} ATR tarde'} · "
              f"{'AVISADO ' + ','.join(x['avisado_por']) if x['avisado_por'] else 'nadie aviso'} · {', '.join(x['rasgos'])}")
    print()
    print(texto())
    r = datos()["resumen"]
    avisar(f"Autopsia {hoy['fecha']}: {len(hoy['giros'])} giros",
           f"Avisados por reglas: {sum(bool(x['avisado_por']) for x in hoy['giros'])}. "
           f"UT tarde (mediana {r['dias']} dias): {r['ut_tarde_atr_mediana']} ATR.", fuerte=False)
