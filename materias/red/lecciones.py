"""
Lecciones medidas: como ENTRAR a una señal, decidido por los datos.

El 21-10-2026 la misma lectura (barrida del maximo de 12 h en tendencia
bajista) dio dos resultados: la limitada en el retroceso se lleno a las
07:39 y gano; la de las 13:03 no se lleno y el precio cayo 11 dolares sin
nosotros. Una anecdota no decide una regla. Esto mide, sobre el ultimo
año de velas, las dos formas de entrar a la misma señal:

  mercado   : se vende/compra en la apertura de la vela siguiente al cierre
              de la vela de 15M que confirma la barrida.
  limitada  : se espera el retroceso mediano de esa hora (tabla de pips)
              durante 2 h; si no llega, no hay operacion (0 R).

Mismo stop (encima/debajo de la mecha de la barrida, nunca mas cerca que
el p75 de la hora) y mismo objetivo (el otro extremo del rango de 12 h)
en las dos, para que la unica diferencia sea la entrada. Un toque del
stop y del objetivo en la misma vela cuenta como stop.

La comparacion justa es R POR SEÑAL, no R por operacion: la limitada gana
mas cuando se llena, pero las que no se llenan tambien cuentan, como cero.

Se vuelve a medir con `python -m materias.red.lecciones`; el resultado va
a `lecciones.json`, junto a este archivo, y viaja a Render con el codigo.
Las operaciones cerradas del diario se suman al texto del agente cada vez
que decide: lo que medimos en el historico y lo que nos paso de verdad.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from materias.red import consejo
from materias.red import datos as D
from materias.red.pips import DESFASE_NY, SESIONES, hora_ny

ARCHIVO = Path(__file__).with_name("lecciones.json")

VENTANA_RANGO = 48      # velas de 15M = 12 h
VALIDEZ_LIMITADA = 24   # velas de 5M = 2 h
HORIZONTE = 96          # velas de 5M = 8 h; si no toca nada, sale al cierre
MIN_RR = 1.5            # el objetivo tiene que pagar al menos 1,5 veces el stop


def _a_15m(v: np.ndarray):
    """Agrupa 5M en 15M. `sig` = indice de 5M de la primera vela despues."""
    g = v["time"] // 900
    ini = np.r_[0, np.flatnonzero(np.diff(g)) + 1]
    sig = np.r_[ini[1:], len(v)]
    return (g[ini] * 900, v["open"][ini], np.maximum.reduceat(v["high"], ini),
            np.minimum.reduceat(v["low"], ini), v["close"][sig - 1], sig)


def _simular(v, i, lado, entrada, stop, objetivo, coste):
    """R de una operacion abierta en la vela de 5M `i` (incluida)."""
    riesgo = abs(stop - entrada)
    fin = min(i + HORIZONTE, len(v))
    for j in range(i, fin):
        dif = v["spread"][j] * 0.01
        if lado == "corto":
            if v["high"][j] + dif >= stop:
                return (entrada - stop - coste) / riesgo
            if v["low"][j] <= objetivo:
                return (entrada - objetivo - coste) / riesgo
        else:
            if v["low"][j] <= stop:
                return (stop - entrada - coste) / riesgo
            if v["high"][j] + dif >= objetivo:
                return (objetivo - entrada - coste) / riesgo
    salida = v["close"][fin - 1]
    return (((entrada - salida) if lado == "corto" else (salida - entrada)) - coste) / riesgo


def _resumen(r: list[float]) -> dict:
    if not r:
        return {"n": 0}
    a = np.array(r)
    return {"n": len(a), "gana_pct": round(100 * float((a > 0).mean()), 1),
            "r_medio": round(float(a.mean()), 3), "r_total": round(float(a.sum()), 1)}


def medir(dias: int = 365) -> dict:
    v = D.cargar("M5")
    corte = int((datetime.now(UTC) - timedelta(days=dias)).timestamp()) + DESFASE_NY
    v = v[v["time"] >= corte - 30 * 86400]          # margen para la media de 20 dias
    t, _, h, lo, c, sig = _a_15m(v)
    n = len(t)
    from numpy.lib.stride_tricks import sliding_window_view as ventana
    techo = np.full(n, np.nan)
    suelo = np.full(n, np.nan)
    techo[VENTANA_RANGO:] = ventana(h, VENTANA_RANGO).max(axis=1)[:-1]
    suelo[VENTANA_RANGO:] = ventana(lo, VENTANA_RANGO).min(axis=1)[:-1]
    media = np.full(n, np.nan)                        # tendencia: media de 20 dias de 15M
    m = 20 * 96
    cs = np.cumsum(np.r_[0, c])
    media[m:] = (cs[m + 1:] - cs[1:-m]) / m
    tabla = consejo.tabla()["horizontes"]["1h"]["por_hora"]

    grupos: dict[str, dict[str, list]] = {}
    ultima = {"corto": -99, "largo": -99}
    for k in range(VENTANA_RANGO, n):
        if t[k] < corte or np.isnan(media[k]):
            continue
        if t[k] - t[k - VENTANA_RANGO] > 14 * 3600:   # rango partido por un fin de semana
            continue
        for lado in ("corto", "largo"):
            if lado == "corto":
                señal = h[k] > techo[k] and c[k] < techo[k]
            else:
                señal = lo[k] < suelo[k] and c[k] > suelo[k]
            if not señal or k - ultima[lado] < 16:   # una por lado cada 4 h
                continue
            ultima[lado] = k
            i0 = int(sig[k])
            if i0 >= len(v) - HORIZONTE:
                continue
            hh = int(hora_ny(np.array([t[k] + 900]))[0])
            f = tabla.get(str(hh))
            if not f:
                continue
            p50, p75 = f[lado]["p50"] * D.PIP, f[lado]["p75"] * D.PIP
            signo = 1 if lado == "corto" else -1
            stop = (max(h[k] + 0.5, c[k] + p75) if lado == "corto"
                    else min(lo[k] - 0.5, c[k] - p75))
            objetivo = suelo[k] + 1.0 if lado == "corto" else techo[k] - 1.0
            a_favor = (c[k] < media[k]) if lado == "corto" else (c[k] > media[k])
            sesion = next((s for s, (x, y) in SESIONES.items()
                           if x <= (hh + 0.5) % 24 < y), "fuera de killzone")
            claves = [f"{lado}|todas", f"{lado}|{'con' if a_favor else 'contra'} tendencia",
                      f"{lado}|{sesion}"]
            coste = v["spread"][i0] * 0.01

            # A: a mercado en la apertura de la vela siguiente
            e = v["open"][i0] if lado == "corto" else v["open"][i0] + coste
            ok_a = signo * (e - objetivo) >= MIN_RR * signo * (stop - e) > 0
            ra = _simular(v, i0, lado, e, stop, objetivo, coste) if ok_a else None

            # B: limitada en el retroceso mediano de la hora
            lim = c[k] + signo * p50
            rb, lleno = None, False
            ok_b = signo * (lim - objetivo) >= MIN_RR * signo * (stop - lim) > 0
            if ok_b:
                rb = 0.0
                for j in range(i0, min(i0 + VALIDEZ_LIMITADA, len(v))):
                    toca = (v["high"][j] >= lim) if lado == "corto" else (v["low"][j] + coste <= lim)
                    if toca:
                        lleno = True
                        rb = _simular(v, j, lado, lim, stop, objetivo, coste)
                        break
                    llego = (v["low"][j] <= objetivo) if lado == "corto" else (v["high"][j] >= objetivo)
                    if llego:                       # se fue sin nosotros
                        break
            if ra is None or rb is None:            # comparar solo señales validas en los dos modos
                continue
            for cl in claves:
                g = grupos.setdefault(cl, {"mercado": [], "limitada": [], "llenas": []})
                g["mercado"].append(ra)
                g["limitada"].append(rb)
                if lleno:
                    g["llenas"].append(rb)

    out = {"medido": datetime.now(UTC).strftime("%Y-%m-%d"), "dias": dias,
           "senal": "barrida del extremo de 12 h con cierre 15M de vuelta dentro",
           "grupos": {}}
    for cl, g in sorted(grupos.items()):
        merc, lim = _resumen(g["mercado"]), _resumen(g["limitada"])
        lim["llenadas_pct"] = round(100 * len(g["llenas"]) / max(1, len(g["limitada"])), 1)
        lim["r_medio_llenas"] = _resumen(g["llenas"]).get("r_medio")
        mejor = "mercado" if merc.get("r_medio", -9) >= lim.get("r_medio", -9) else "limitada"
        out["grupos"][cl] = {"mercado": merc, "limitada": lim, "mejor": mejor}
    return out


def exportar(dias: int = 365) -> Path:
    ARCHIVO.write_text(json.dumps(medir(dias), indent=1, ensure_ascii=False), encoding="utf-8")
    datos.cache_clear()
    return ARCHIVO


@lru_cache(maxsize=1)
def datos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _diario(n: int = 8) -> list[str]:
    """Lo que nos paso de verdad: las ultimas operaciones cerradas con su nota."""
    try:
        from materias import diario
        ops = [o for o in diario.leer() if o.get("origen") in ("claude", "agente")]
    except Exception:
        return []
    L = []
    for o in ops[-n:]:
        nota = o.get("nota") or ""
        try:                        # las notas viejas son JSON: se queda la leccion o la razon
            j = json.loads(nota)
            nota = next((str(j[k]) for k in ("nota_honesta", "leccion_7", "mejora", "en_contra", "razon")
                         if k in j), "")
        except (ValueError, TypeError):
            pass
        L.append(f"  · {o.get('fecha')} {o.get('lado')} {o.get('setup')} "
                 f"@{o.get('entrada')} → {o.get('r'):+.2f} R. {nota[:220]}".rstrip())
    return L


def texto() -> str:
    d = datos()
    if not d.get("grupos"):
        return ""
    L = ["## Lecciones medidas: como entrar",
         f"Señal: {d['senal']}. Ultimos {d['dias']} dias, medido el {d['medido']}. "
         "Mismo stop y objetivo; solo cambia la entrada. R por señal (las limitadas "
         "que no se llenan cuentan 0)."]
    for cl, g in d["grupos"].items():
        m, li = g["mercado"], g["limitada"]
        if m.get("n", 0) < 30:
            continue
        L.append(f"- {cl.replace('|', ' · ')} (n={m['n']}): mercado {m['r_medio']:+.2f} R "
                 f"({m['gana_pct']:.0f} % ganan) · limitada {li['r_medio']:+.2f} R por señal, "
                 f"se llena el {li['llenadas_pct']:.0f} % → mejor {g['mejor'].upper()}")
    L.append("Con n < 100 la diferencia puede ser ruido (cap. 18.7): no la conviertas en dogma.")
    vivido = _diario()
    if vivido:
        L.append("Lo que nos paso de verdad (diario):")
        L.extend(vivido)
    return "\n".join(L)


if __name__ == "__main__":
    ruta = exportar()
    print(f"lecciones exportadas a {ruta.name}\n")
    for cl, g in datos()["grupos"].items():
        m, li = g["mercado"], g["limitada"]
        print(f"{cl:38s} n={m['n']:4d} · mercado {m['r_medio']:+.3f} R gana {m['gana_pct']:4.1f}% "
              f"· limitada {li['r_medio']:+.3f} R/señal llena {li['llenadas_pct']:4.1f}% "
              f"(llenas {li['r_medio_llenas']}) → {g['mejor']}")
