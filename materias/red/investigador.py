"""
El investigador: busca solo que avisa de un rebote, y solo se queda con lo
que aguanta en datos que no uso para buscarlo.

QUE HACE
Un «candidato» es una vela de 5M que toca o rompe el extremo de las dos
horas anteriores: ahi es donde puede haber rebote. Sobre cada candidato se
calculan unos rasgos (mecha de rechazo, doble suelo, barrida, RSI,
volumen, sesion, tramo del UT Bot...) con lo que se sabe AL CIERRE de esa
vela. Se entra al cierre, a favor del rebote, con tres esquemas de
stop/objetivo en ATR, y se resta el spread.

Hipotesis = un rasgo, o dos a la vez, en un esquema. Se prueban todas. Se
adopta una solo si gana en el 70 % mas viejo de los datos Y en el 30 % mas
reciente, con casos suficientes y con el intervalo del 95 % de lo reciente
por encima de cero. Lo demas se descarta: con ~700 hipotesis, sin ese
filtro se «descubririan» casualidades (cap. 18.7 de la guia).

Cada semana `evolucionar` lo vuelve a correr. Una regla que deja de
aguantar pasa a «caidas» con su fecha; no se borra, para no redescubrirla.
La autopsia diaria (`autopsia.py`) dice que giros reales NO aviso ninguna
regla: esos son los puntos ciegos, donde falta un rasgo nuevo.

    python -m materias.red.investigador
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from materias.red import utbot as U

ARCHIVO = Path(__file__).with_name("hallazgos.json")
DIAS = 730
VENTANA = 24            # 2 h de velas de 5M: el extremo que se toca
STOP, OBJ = 1.0, 1.5    # en ATR de 5M
COSTE = 0.12            # spread tipico, en ATR de 5M
HORIZONTE = 96          # 8 h para resolverse
ESQUEMAS = ((1.0, 1.5), (1.0, 3.0), (2.0, 3.0))   # (stop, objetivo) en ATR; el primero es el de referencia
MIN_ENT, MIN_VAL, MIN_R = 150, 60, 0.05

NOMBRES = {
    "mecha60": "la vela cierra con mecha de rechazo >= 60 %",
    "mecha75": "mecha de rechazo >= 75 %",
    "cierra_a_favor": "la vela cierra a favor del rebote",
    "recupera": "rompe el extremo de 2 h y cierra de vuelta dentro (barrida)",
    "doble": "segundo toque del extremo sin romperlo (doble suelo/techo)",
    "barrida_4h": "rompe el extremo de 4 h",
    "barrida_dia": "rompe el extremo de 24 h",
    "rsi_extremo": "RSI 14 extremo (<25 / >75)",
    "rsi_medio": "RSI 14 sin extremo (35-65)",
    "vol_2x": "volumen >= 2x la media de 20 velas",
    "vol_bajo": "volumen < 0,7x la media",
    "vela_grande": "rango de la vela >= 1,5 ATR (climax)",
    "tres_en_contra": "tres velas seguidas en contra antes",
    "ut_en_contra": "el UT Bot de 5M esta en contra del rebote",
    "ext_2atr": "el tramo del UT lleva >= 2 ATR",
    "ext_3atr": "el tramo del UT lleva >= 3 ATR",
    "ema200_a_favor": "el rebote va a favor de la EMA 200 de 5M",
    "asia": "Asia (18-02 NY)",
    "londres": "Londres (02-07 NY)",
    "ny_am": "NY mañana (07-11 NY)",
    "ny_pm": "NY tarde (11-17 NY)",
}


def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    su, ba = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    for x in (su, ba):
        for i in range(1, len(x)):
            x[i] = x[i - 1] + (x[i] - x[i - 1]) / n
    return 100 - 100 / (1 + su / np.maximum(ba, 1e-9))


def _previo(x: np.ndarray, n: int, f) -> np.ndarray:
    """Extremo de las n velas ANTERIORES a cada vela (sin contarla)."""
    out = np.full(len(x), np.nan)
    out[n:] = f(sliding_window_view(x, n), axis=1)[:-1]
    return out


def rasgos(v: np.ndarray) -> dict:
    """
    Candidatos y rasgos de un array de velas de 5M (campos de MT5).

    Devuelve `idx` (velas candidatas), `lado` (+1 rebote al alza, -1 a la
    baja), `atr` y `F`: un booleano por rasgo y candidato. Todo se calcula
    con lo que hay al cierre de la vela candidata.
    """
    o, h, l, c = (v[k].astype(float) for k in ("open", "high", "low", "close"))
    vol = v["tick_volume"].astype(float)
    a = U.atr(h, l, c, 14)
    lo2, hi2 = _previo(l, VENTANA, np.min), _previo(h, VENTANA, np.max)
    tol = 0.15 * a
    abajo, arriba = l <= lo2 + tol, h >= hi2 - tol
    ok = ~np.isnan(a) & ~np.isnan(lo2) & (abajo ^ arriba)
    ok[:300] = False
    idx = np.flatnonzero(ok)
    s = np.where(abajo[idx], 1, -1)
    up = s > 0

    def lado(si_sube, si_baja):
        return np.where(up, si_sube[idx], si_baja[idx])

    rng = np.maximum(h - l, 1e-9)
    mecha = lado((c - l) / rng, (h - c) / rng)
    rompe = lado(l < lo2, h > hi2)
    # el extremo se marco hace 3 velas o mas: las dos ultimas no lo tocaron
    viejo = lado(_previo(l, 2, np.min) > lo2 + tol, _previo(h, 2, np.max) < hi2 - tol)
    rsi = _rsi(c)
    media = _previo(vol, 20, np.mean)
    baj, alc = c < o, c > o
    tres = lado(np.r_[[False] * 3, baj[2:-1] & baj[1:-2] & baj[:-3]],
                np.r_[[False] * 3, alc[2:-1] & alc[1:-2] & alc[:-3]])
    src, st, co, ve = U.utbot({"o": o, "h": h, "l": l, "c": c}, 1.0, 14, False)
    ult = np.maximum.accumulate(np.where(co | ve, np.arange(len(c)), 0))   # vela de la ultima señal
    ut = np.where(src > st, 1, -1)
    ext = lado((c[ult] - l) / np.maximum(a, 1e-9), (h - c[ult]) / np.maximum(a, 1e-9))
    contra = ut[idx] == -s
    hora = ((v["time"][idx] - 7 * 3600) % 86400) / 3600
    F = {
        "mecha60": mecha >= 0.6, "mecha75": mecha >= 0.75,
        "cierra_a_favor": lado(alc, baj),
        "recupera": rompe & lado(c > lo2, c < hi2),
        "doble": ~lado(l < lo2 - tol, h > hi2 + tol) & viejo,
        "barrida_4h": lado(l < _previo(l, 48, np.min), h > _previo(h, 48, np.max)),
        "barrida_dia": lado(l < _previo(l, 288, np.min), h > _previo(h, 288, np.max)),
        "rsi_extremo": lado(rsi < 25, rsi > 75),
        "rsi_medio": (rsi[idx] >= 35) & (rsi[idx] <= 65),
        "vol_2x": vol[idx] >= 2 * media[idx], "vol_bajo": vol[idx] < 0.7 * media[idx],
        "vela_grande": rng[idx] >= 1.5 * a[idx],
        "tres_en_contra": tres.astype(bool),
        "ut_en_contra": contra, "ext_2atr": contra & (ext >= 2), "ext_3atr": contra & (ext >= 3),
        "ema200_a_favor": np.sign(c - U.ema(c, 200))[idx] == s,
        "asia": (hora >= 18) | (hora < 2), "londres": (hora >= 2) & (hora < 7),
        "ny_am": (hora >= 7) & (hora < 11), "ny_pm": (hora >= 11) & (hora < 17),
    }
    return {"idx": idx, "lado": s, "atr": a, "F": {k: np.asarray(x, bool) for k, x in F.items()}}


def resultado(v: np.ndarray, idx: np.ndarray, s: np.ndarray, a: np.ndarray,
              stop: float = STOP, obj: float = OBJ) -> np.ndarray:
    """R neto de cada candidato: +obj/stop si llega al objetivo antes que al stop, -1 si
    stop, nan sin resolver; menos el coste. Stop y objetivo en la misma vela = stop."""
    h, l, c = v["high"], v["low"], v["close"]
    out = np.full(len(idx), np.nan)
    for k, (i, d) in enumerate(zip(idx, s)):
        A, e = a[i], c[i]
        for t in range(i + 1, min(i + 1 + HORIZONTE, len(c))):
            if (l[t] <= e - stop * A) if d > 0 else (h[t] >= e + stop * A):
                out[k] = -1
                break
            if (h[t] >= e + obj * A) if d > 0 else (l[t] <= e - obj * A):
                out[k] = obj / stop
                break
    return out - COSTE / stop


def tamano_rebote(v: np.ndarray) -> dict:
    """Cuanto mide, en ATR, el movimiento contrario cuando acaba un tramo del UT Bot."""
    out = {}
    for nom, b in (("M5", {"o": v["open"], "h": v["high"], "l": v["low"], "c": v["close"]}),
                   ("M15", U.agrupar(v, 900))):
        h, l, c = b["h"], b["l"], b["c"]
        a = U.atr(h, l, c, 14)
        _, _, co, ve = U.utbot(b, 1.0, 14, False)
        sig = np.flatnonzero(co | ve)
        reb = []
        for i, f, g in zip(sig, sig[1:], sig[2:]):
            if co[i]:
                reb.append((h[i:f + 1].max() - l[f:g + 1].min()) / a[i])
            else:
                reb.append((h[f:g + 1].max() - l[i:f + 1].min()) / a[i])
        q = np.nanpercentile(reb, [25, 50, 75, 90])
        out[nom] = {"n": len(reb), "p25": round(q[0], 1), "p50": round(q[1], 1),
                    "p75": round(q[2], 1), "p90": round(q[3], 1)}
    return out


def regla_fija(v: np.ndarray, stop: float = 2.0, obj: float = 3.0) -> dict:
    """La regla «señal del UT de 5M a favor del UT de 15M», a mercado, con stop y objetivo fijos."""
    h, lo, c = v["high"], v["low"], v["close"]
    a = U.atr(h, lo, c, 14)
    _, _, co, ve = U.utbot({"o": v["open"], "h": h, "l": lo, "c": c}, 1.0, 14, False)
    b = U.agrupar(v, 900)
    s15, st15, _, _ = U.utbot(b, 1.0, 14, False)
    # la ultima vela de 15M ya cerrada al cierre de cada vela de 5M
    pos = np.searchsorted(b["t"] + 900, v["time"] + 300, side="right") - 1
    sig = np.flatnonzero(co | ve)
    sig = sig[(sig > 300) & (pos[sig] >= 20)]
    s = np.where(co[sig], 1, -1)
    favor = np.where(s15 > st15, 1, -1)[pos[sig]] == s
    r = resultado(v, sig, s, a, stop, obj)
    corte = int(len(sig) * 0.7)
    out = {"stop_atr": stop, "obj_atr": obj}
    for nom, m in (("a_favor", favor), ("en_contra", ~favor)):
        ok = m & ~np.isnan(r)
        out[nom] = {"n": int(ok.sum()), "r": round(float(r[ok].mean()), 3),
                    "ganan_pct": round(100 * float((r[ok] > 0).mean()), 1),
                    "r_reciente": round(float(r[corte:][ok[corte:]].mean()), 3)}
    return out


def medir() -> dict:
    from materias.red import datos as D
    v = D.cargar("M5")
    v = v[v["time"] >= v["time"][-1] - DIAS * 86400]
    R = rasgos(v)
    F, n = R["F"], len(R["idx"])
    corte = int(n * 0.7)
    combos = [(k,) for k in F] + list(itertools.combinations(F, 2))
    buenas, sueltos, probadas, base = [], {}, 0, {}
    for stop, obj in ESQUEMAS:
        r = resultado(v, R["idx"], R["lado"], R["atr"], stop, obj)
        vale = ~np.isnan(r)
        base[f"{stop}/{obj}"] = {"r": round(float(r[vale].mean()), 3),
                                 "acierta_pct": round(100 * float((r[vale] > 0).mean()), 1)}
        for cb in combos:
            m = vale.copy()
            for k in cb:
                m &= F[k]
            probadas += 1
            me, mv = m[:corte], m[corte:]
            ne, nv = int(me.sum()), int(mv.sum())
            if ne < MIN_ENT or nv < MIN_VAL:
                continue
            xe, xv = r[:corte][me], r[corte:][mv]
            x = {"rasgos": list(cb), "stop_atr": stop, "obj_atr": obj, "n_ent": ne, "n_val": nv,
                 "p_ent": round(100 * float((xe > 0).mean()), 1), "p_val": round(100 * float((xv > 0).mean()), 1),
                 "r_ent": round(float(xe.mean()), 3), "r_val": round(float(xv.mean()), 3),
                 "r_val_min": round(float(xv.mean() - 1.96 * xv.std() / np.sqrt(nv)), 3)}
            if len(cb) == 1 and (stop, obj) == ESQUEMAS[0]:
                sueltos[cb[0]] = x | {"frecuencia": round(100 * float(F[cb[0]].mean()), 1)}
            # gana en lo viejo, gana en lo reciente, y el intervalo de lo reciente no toca el cero
            if x["r_ent"] >= MIN_R and x["r_val"] >= MIN_R and x["r_val_min"] > 0:
                buenas.append(x)
    buenas.sort(key=lambda x: -min(x["r_ent"], x["r_val"]))

    hoy = f"{dt.date.today()}"
    viejo = datos()
    antes = {clave(x): x for x in viejo.get("reglas", [])}
    for x in buenas:
        x["desde"] = antes.get(clave(x), {}).get("desde", hoy)
    ahora = {clave(x) for x in buenas}
    caidas = [c for c in viejo.get("caidas", []) if c["regla"] not in ahora]
    caidas += [{"regla": k, "desde": x.get("desde"), "cayo": hoy} for k, x in antes.items() if k not in ahora]
    return {"medido": hoy, "dias": DIAS, "candidatos": n, "coste_atr": COSTE,
            "entrada": f"al cierre de la vela de 5M que toca el extremo de {VENTANA * 5 // 60} h, a favor del rebote",
            "base": base, "probadas": probadas,
            "reglas": buenas[:12], "sueltos": sueltos, "caidas": caidas[-40:],
            "tamano_rebote": tamano_rebote(v), "regla_fija": regla_fija(v)}


def clave(x: dict) -> str:
    return "+".join(x["rasgos"]) + f"@{x['stop_atr']}/{x['obj_atr']}"


def datos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def exportar() -> Path:
    ARCHIVO.write_text(json.dumps(medir(), indent=1, ensure_ascii=False), encoding="utf-8")
    return ARCHIVO


def nombre(regla: dict) -> str:
    return " + ".join(NOMBRES.get(k, k) for k in regla["rasgos"])


def cumple(F: dict, k: int) -> list[dict]:
    """Reglas adoptadas que cumple el candidato k de unos `rasgos()` en vivo."""
    return [x for x in datos().get("reglas", []) if all(F[r][k] for r in x["rasgos"])]


def texto() -> str:
    d = datos()
    if not d:
        return ""
    L = ["## Rebotes: lo que el investigador busco solo, y lo que aguanto la validacion",
         f"Candidato = vela de 5M que toca el extremo de 2 h; entrada {d['entrada']}, coste {d['coste_atr']} ATR. "
         f"Ultimos {d['dias']} dias, {d['candidatos']:,} candidatos, medido el {d['medido']}. Sin filtro: "
         + "; ".join(f"stop/objetivo {k} ATR {x['r']:+.2f} R ({x['acierta_pct']:.0f} % ganan)" for k, x in d["base"].items())
         + f". Se probaron {d['probadas']} hipotesis (un rasgo o dos, por esquema); adoptadas {len(d['reglas'])}: "
         "solo las que ganan en el 70 % viejo y en el 30 % reciente con el intervalo sobre cero."]
    for x in d["reglas"]:
        L.append(f"- {nombre(x)} · stop {x['stop_atr']} ATR, objetivo {x['obj_atr']} ATR: {x['r_val']:+.2f} R en datos "
                 f"no vistos ({x['p_val']:.0f} % ganan, n={x['n_val']}); antes {x['r_ent']:+.2f} R (n={x['n_ent']}); "
                 f"adoptada el {x['desde']}")
    if not d["reglas"]:
        L.append("- NINGUNA regla pasa: comprar o vender el rebote en el extremo de 2 h, con entrada mecanica al "
                 "cierre, pierde despues del spread con cualquier rasgo probado. Un rebote contra tendencia no es un "
                 "setup por si solo; hace falta lo demas de la guia (zona de marco mayor, CHoCH, killzone).")
    orden = sorted(d["sueltos"].items(), key=lambda kv: kv[1]["r_val"])
    L.append("Rasgo a rasgo (R en datos no vistos, stop 1 / objetivo 1,5 ATR) — peores: "
             + "; ".join(f"{NOMBRES[k]} {x['r_val']:+.2f}" for k, x in orden[:3]) + " — mejores: "
             + "; ".join(f"{NOMBRES[k]} {x['r_val']:+.2f}" for k, x in orden[-3:][::-1]) + ".")
    t = d.get("tamano_rebote", {})
    if t:
        L.append("Cuando acaba un tramo del UT Bot, el movimiento contrario mide: "
                 + "; ".join(f"{k} mediana {x['p50']} ATR, 1 de cada 4 pasa de {x['p75']}, 1 de cada 10 de {x['p90']}"
                             for k, x in t.items()) + ". Un objetivo de 1-1,5 ATR se queda corto.")
    f = d.get("regla_fija")
    if f:
        x, y = f["a_favor"], f["en_contra"]
        L.append(f"Regla «señal del UT de 5M a favor del UT de 15M», a mercado, stop {f['stop_atr']:.0f} / objetivo "
                 f"{f['obj_atr']:.0f} ATR: {x['r']:+.2f} R (n={x['n']}, {x['ganan_pct']:.0f} % ganan; reciente "
                 f"{x['r_reciente']:+.2f}). En contra de 15M: {y['r']:+.2f} R (n={y['n']}). Con salida fija NO tiene "
                 "ventaja: sirve para elegir el momento dentro de un setup de la guia, no como setup.")
    return "\n".join(L)


if __name__ == "__main__":
    exportar()
    print(texto())
