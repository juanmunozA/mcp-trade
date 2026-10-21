"""
Vacios (FVG) y maximos/minimos de serie: que hace el precio cuando vuelve.

VACIO (FVG, cap. 7.1): tres velas donde la tercera no toca a la primera.
  alcista: minimo[i] > maximo[i-2]  ->  hueco [maximo[i-2], minimo[i]]
  Se mide el PRIMER regreso del precio al hueco y que pasa despues:
    reacciona : se aleja 1 ATR desde el borde antes de cerrar al otro lado
    rompe     : cierra con cuerpo al otro lado del hueco (se invalida)
  Base justa: si el precio fuese una moneda, desde el borde la
  probabilidad de recorrer 1 ATR antes que el ancho del hueco es
  ancho / (ancho + ATR). Lo que importa es cuanto se separa la real de esa.
  Cuando un hueco se rompe pasa a ser inverso (IFVG, cap. 11.7) y se mide
  igual su primer retesteo desde el otro lado.

MAXIMO/MINIMO DE SERIE: el extremo de un tramo entero, no un pico
cualquiera. Un tramo termina cuando el precio se da la vuelta 3 ATR desde
su extremo (zigzag); ese extremo es el maximo o minimo de la serie. Se
confirma solo cuando llega ese giro, asi que no se usa antes de saberlo.
Se compara con los relativos (fractal de 2 velas a cada lado), que son
los que marcaria cualquiera mirando el grafico.
  Cuando el precio vuelve a un extremo:
    rechaza : se aleja 1 ATR hacia dentro antes de cerrar al otro lado
    rompe   : cierra al otro lado; y entonces, ¿sigue 1 ATR o vuelve
              a cerrar dentro (ruptura falsa)?

Datos: 15M desde 2022. Se separa 2022-2024 de 2025-2026 para ver si lo
que se ve en un tramo se mantiene en el otro.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import SESIONES, hora_ny
from materias.red.utbot import agrupar, atr, ema

ARCHIVO = Path(__file__).with_name("zonas.json")
DESDE = 1640995200 + 7 * 3600      # 2022-01-01 en hora del servidor
CORTE = 1735689600 + 7 * 3600      # 2025-01-01
K_SERIE = 3.0                      # giro en ATR que cierra un tramo
MIN_HUECO = 0.2                    # huecos menores de 0,2 ATR son ruido
ESPERA = 200                       # velas de 15M que se espera el regreso (~2 dias)
HORIZONTE = 96                     # velas de 15M para resolver (24 h)


def series(h, lo, a, k: float = K_SERIE) -> list[tuple[int, int, float, int]]:
    """Zigzag causal: (vela del extremo, vela en que se confirma, precio, +1 max / -1 min)."""
    out, lado = [], 0
    ie, ext = 0, h[0]
    for i in range(1, len(h)):
        if np.isnan(a[i]):
            continue
        if lado >= 0:
            if h[i] > ext or lado == 0 and h[i] >= ext:
                ie, ext = i, h[i]
            if lo[i] < ext - k * a[i]:
                out.append((ie, i, float(ext), 1))
                lado, ie, ext = -1, i, lo[i]
        else:
            if lo[i] < ext:
                ie, ext = i, lo[i]
            if h[i] > ext + k * a[i]:
                out.append((ie, i, float(ext), -1))
                lado, ie, ext = 1, i, h[i]
    return out


def fractales(h, lo, n: int = 2) -> list[tuple[int, int, float, int]]:
    out = []
    for i in range(n, len(h) - n):
        if h[i] == h[i - n:i + n + 1].max():
            out.append((i, i + n, float(h[i]), 1))
        if lo[i] == lo[i - n:i + n + 1].min():
            out.append((i, i + n, float(lo[i]), -1))
    return out


def huecos(h, lo, a, min_atr: float = MIN_HUECO) -> list[tuple[int, int, float, float]]:
    """(vela en que se forma, +1 alcista / -1 bajista, borde bajo, borde alto)."""
    out = []
    for i in range(2, len(h)):
        if np.isnan(a[i]):
            continue
        if lo[i] > h[i - 2] and lo[i] - h[i - 2] >= min_atr * a[i]:
            out.append((i, 1, float(h[i - 2]), float(lo[i])))
        elif h[i] < lo[i - 2] and lo[i - 2] - h[i] >= min_atr * a[i]:
            out.append((i, -1, float(h[i]), float(lo[i - 2])))
    return out


def _resolver(b, desde, lado, borde, lejos, objetivo):
    """
    Desde la vela `desde`: 'rompe' si CIERRA mas alla de `lejos`, 'reacciona'
    si CIERRA mas alla de `objetivo`. Los dos con cierres, para que la
    comparacion con el azar sea justa (las mechas llegan mas lejos que los
    cierres). La vela del toque solo puede romper: su maximo pudo ocurrir
    antes de tocar y no seria una reaccion.
    """
    c = b["c"]
    for k in range(desde, min(desde + HORIZONTE, len(c))):
        if (c[k] < lejos) if lado > 0 else (c[k] > lejos):
            return "rompe", k
        if k > desde and ((c[k] >= objetivo) if lado > 0 else (c[k] <= objetivo)):
            return "reacciona", k
    return "nada", None


def _sesion(t) -> str:
    hh = float(hora_ny(np.array([t]))[0])
    return next((s for s, (x, y) in SESIONES.items() if x <= hh < y), "fuera de killzone")


def medir_huecos(b, a, tend) -> list[dict]:
    h, lo, c = b["h"], b["l"], b["c"]
    ev = []
    for i, lado, bajo, alto in huecos(h, lo, a):
        if b["t"][i] < DESDE:
            continue
        ancho = alto - bajo
        borde, lejos = (alto, bajo) if lado > 0 else (bajo, alto)
        ce = (alto + bajo) / 2
        j = next((k for k in range(i + 1, min(i + 1 + ESPERA, len(c)))
                  if ((lo[k] <= borde) if lado > 0 else (h[k] >= borde))), None)
        base = {"t": int(b["t"][i]), "lado": lado, "ancho_atr": ancho / a[i],
                "a_favor": bool(tend[i] == lado)}
        if j is None:
            ev.append({**base, "volvio": False})
            continue
        objetivo = borde + lado * a[i]
        res, k = _resolver(b, j, lado, borde, lejos, objetivo)
        llega_ce = bool((lo[j] <= ce) if lado > 0 else (h[j] >= ce))
        ev.append({**base, "volvio": True, "edad": j - i, "res": res, "llega_ce": llega_ce,
                   "base": ancho / (ancho + a[i]), "sesion": _sesion(b["t"][j]), "tipo": "FVG"})
        if res != "rompe":
            continue
        # Hueco inverso: lo que era soporte, ahora resistencia (y al reves)
        m = next((x for x in range(k + 1, min(k + 1 + ESPERA, len(c)))
                  if ((h[x] >= bajo) if lado > 0 else (lo[x] <= alto))), None)
        if m is None:
            continue
        inv = -lado
        borde_i, lejos_i = (bajo, alto) if lado > 0 else (alto, bajo)
        res_i, _ = _resolver(b, m, inv, borde_i, lejos_i, borde_i + inv * a[i])
        ev.append({**base, "lado": inv, "a_favor": bool(tend[m] == inv), "volvio": True,
                   "edad": m - k, "res": res_i, "llega_ce": False,
                   "base": ancho / (ancho + a[i]), "sesion": _sesion(b["t"][m]), "tipo": "IFVG"})
    return ev


def medir_extremos(b, a, pivotes, nombre: str) -> list[dict]:
    h, lo, c = b["h"], b["l"], b["c"]
    ev = []
    for ie, ic, precio, tipo in pivotes:
        if b["t"][ic] < DESDE:
            continue
        j = next((k for k in range(ic + 1, min(ic + 1 + ESPERA, len(c)))
                  if ((h[k] >= precio) if tipo > 0 else (lo[k] <= precio))), None)
        if j is None:
            continue
        dentro = -tipo                        # un maximo rechaza hacia abajo
        res, k = _resolver(b, j, dentro, precio, precio, precio + dentro * a[ie])
        fila = {"t": int(b["t"][ic]), "tipo": nombre, "extremo": "max" if tipo > 0 else "min",
                "res": res, "sigue": None}
        if res == "rompe":
            sig, _ = _resolver(b, k + 1, tipo, precio, precio, precio + tipo * a[ie])
            fila["sigue"] = sig == "reacciona"
        ev.append(fila)
    return ev


def _pct(x) -> float:
    return round(100 * float(np.mean(x)), 1) if len(x) else float("nan")


def resumir_huecos(ev: list[dict]) -> dict:
    out = {}
    tramos = {"2022-2024": lambda e: e["t"] < CORTE, "2025-2026": lambda e: e["t"] >= CORTE}
    grupos = {
        "FVG todos": lambda e: e["tipo"] == "FVG",
        "FVG a favor de la tendencia": lambda e: e["tipo"] == "FVG" and e["a_favor"],
        "FVG contra la tendencia": lambda e: e["tipo"] == "FVG" and not e["a_favor"],
        "FVG chico (<0,5 ATR)": lambda e: e["tipo"] == "FVG" and e["ancho_atr"] < 0.5,
        "FVG mediano (0,5-1 ATR)": lambda e: e["tipo"] == "FVG" and 0.5 <= e["ancho_atr"] < 1,
        "FVG grande (>1 ATR)": lambda e: e["tipo"] == "FVG" and e["ancho_atr"] >= 1,
        "FVG primer toque se queda en el borde": lambda e: e["tipo"] == "FVG" and not e["llega_ce"],
        "FVG primer toque llega a la mitad (CE)": lambda e: e["tipo"] == "FVG" and e["llega_ce"],
        "FVG vuelve en <2 h": lambda e: e["tipo"] == "FVG" and e["edad"] <= 8,
        "FVG vuelve en 2-12 h": lambda e: e["tipo"] == "FVG" and 8 < e["edad"] <= 48,
        "FVG vuelve en >12 h": lambda e: e["tipo"] == "FVG" and e["edad"] > 48,
        "IFVG (hueco roto, retesteado)": lambda e: e["tipo"] == "IFVG",
    }
    for s in SESIONES:
        grupos[f"FVG tocado en {s}"] = lambda e, s=s: e["tipo"] == "FVG" and e["sesion"] == s
    volvieron = [e for e in ev if e.get("volvio")]
    for nom, g in grupos.items():
        fila = {}
        for tr, f in tramos.items():
            x = [e for e in volvieron if f(e) and g(e)]
            if not x:
                continue
            fila[tr] = {"n": len(x), "reacciona_pct": _pct([e["res"] == "reacciona" for e in x]),
                        "rompe_pct": _pct([e["res"] == "rompe" for e in x]),
                        "azar_pct": _pct([e["base"] for e in x])}
        out[nom] = fila
    fvg = [e for e in ev if e.get("tipo", "FVG") == "FVG" or "tipo" not in e]
    out["_vuelve_pct"] = _pct([e["volvio"] for e in fvg])
    return out


def resumir_extremos(ev: list[dict]) -> dict:
    out = {}
    for tipo in ("serie", "relativo"):
        for tr, f in (("2022-2024", lambda e: e["t"] < CORTE), ("2025-2026", lambda e: e["t"] >= CORTE)):
            x = [e for e in ev if e["tipo"] == tipo and f(e)]
            rotos = [e for e in x if e["res"] == "rompe"]
            out.setdefault(tipo, {})[tr] = {
                "n": len(x), "rechaza_pct": _pct([e["res"] == "reacciona" for e in x]),
                "rompe_pct": _pct([e["res"] == "rompe" for e in x]),
                "ruptura_sigue_pct": _pct([e["sigue"] for e in rotos])}
    return out


def medir() -> dict:
    v = D.cargar("M5")
    v = v[v["time"] >= DESDE - 30 * 86400]
    b = agrupar(v, 900)
    a = atr(b["h"], b["l"], b["c"], 14)
    tend = np.sign(b["c"] - ema(b["c"], 200))
    ev_h = medir_huecos(b, a, tend)
    ev_e = (medir_extremos(b, a, series(b["h"], b["l"], a), "serie")
            + medir_extremos(b, a, fractales(b["h"], b["l"]), "relativo"))
    return {"desde": "2022-01-01", "marco": "15M", "k_serie_atr": K_SERIE,
            "huecos": resumir_huecos(ev_h), "extremos": resumir_extremos(ev_e)}


# --- lo que ve el agente -------------------------------------------------

def datos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _vivo(m15: list[dict]) -> dict | None:
    if len(m15) < 300:
        return None
    b = {k: np.array([x[c] for x in m15], float) for k, c in (("h", "h"), ("l", "l"), ("c", "c"), ("o", "o"))}
    a = atr(b["h"], b["l"], b["c"], 14)
    p, ai = b["c"][-1], a[-1]
    piv = series(b["h"], b["l"], a)
    abiertos = []
    for i, lado, bajo, alto in huecos(b["h"], b["l"], a):
        resto = slice(i + 1, len(b["c"]))
        roto = (b["c"][resto] < bajo).any() if lado > 0 else (b["c"][resto] > alto).any()
        toques = np.flatnonzero(b["l"][resto] <= alto) if lado > 0 else np.flatnonzero(b["h"][resto] >= bajo)
        if roto or abs((alto + bajo) / 2 - p) > 4 * ai:
            continue
        if not len(toques):
            estado = "sin tocar"
        else:
            j = i + 1 + int(toques[0])
            ce = (alto + bajo) / 2
            llego = (b["l"][j] <= ce) if lado > 0 else (b["h"][j] >= ce)
            estado = "primer toque llego a la mitad" if llego else "primer toque se quedo en el borde"
        abiertos.append((len(b["c"]) - 1 - i, lado, bajo, alto, estado))
    return {"precio": p, "atr": ai, "pivotes": piv[-6:], "huecos": abiertos[-6:]}


def texto(ctx: dict) -> str:
    d = datos()
    if not d.get("huecos"):
        return ""
    m15 = (ctx.get("m15") or [])[:-1]
    L = ["## Vacios (FVG) y maximos/minimos de serie, 15M (medido desde 2022)"]
    hh = d["huecos"]
    L.append("Reacciona = cierra 1 ATR mas alla del borde antes de cerrar al otro lado del vacio. "
             "Azar = lo que daria una moneda con esas distancias. La diferencia es la ventaja.")
    for nom in ("FVG todos", "FVG primer toque se queda en el borde", "FVG primer toque llega a la mitad (CE)",
                "FVG chico (<0,5 ATR)", "FVG grande (>1 ATR)", "FVG tocado en London Close 10-12",
                "FVG tocado en NY PM 13:30-16", "IFVG (hueco roto, retesteado)"):
        f = hh.get(nom, {}).get("2022-2024")
        g = hh.get(nom, {}).get("2025-2026")
        if f:
            L.append(f"- {nom}: reacciona {f['reacciona_pct']:.0f} % (azar {f['azar_pct']:.0f} %), "
                     f"rompe {f['rompe_pct']:.0f} %, n={f['n']}"
                     + (f" · 2025-26: reacciona {g['reacciona_pct']:.0f} % (azar {g['azar_pct']:.0f} %)" if g else ""))
    for tipo, nom in (("serie", "maximo/minimo de SERIE"), ("relativo", "maximo/minimo relativo")):
        f = d["extremos"].get(tipo, {}).get("2022-2024")
        if f:
            L.append(f"- al volver a un {nom}: rechaza {f['rechaza_pct']:.0f} %, rompe {f['rompe_pct']:.0f} %; "
                     f"de las rupturas sigue 1 ATR el {f['ruptura_sigue_pct']:.0f} % (n={f['n']})")
    L.append("Lectura: un vacio cuyo primer toque NO llega a la mitad es el que se defiende; si llega "
             "a la mitad, lo normal es que se llene. Los extremos se rompen 3 de cada 4 veces: "
             "no son muros. La ruptura de un extremo de SERIE sigue mas que la de uno relativo.")
    try:
        vv = _vivo(m15)
    except (KeyError, TypeError, ValueError):
        vv = None
    if vv:
        etiqueta = []
        piv = vv["pivotes"]
        for n, (_, _, precio, tipo) in enumerate(piv):
            prev = next((x for x in reversed(piv[:n]) if x[3] == tipo), None)
            nombre = ("HH" if prev and precio > prev[2] else "LH") if tipo > 0 else \
                     ("HL" if prev and precio > prev[2] else "LL")
            etiqueta.append(f"{nombre if prev else ('max' if tipo > 0 else 'min')} {precio:.2f}")
        L.append("- ahora, extremos de serie (del mas viejo al ultimo): " + " → ".join(etiqueta))
        for edad, lado, bajo, alto, estado in vv["huecos"]:
            L.append(f"- vacio {'alcista' if lado > 0 else 'bajista'} abierto {bajo:.2f}-{alto:.2f} "
                     f"(hace {edad} velas de 15M, {estado})")
    return "\n".join(L)


if __name__ == "__main__":
    res = medir()
    ARCHIVO.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=1, ensure_ascii=False))
