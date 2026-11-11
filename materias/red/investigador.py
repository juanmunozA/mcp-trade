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

Los rasgos incluyen lo de la guia: primer regreso a un FVG u order block
de H1 o de 15M, tendencia de H1 y H4, premium/descuento del dia. Y hay dos
entradas: en el toque, o esperando a que 5M rompa la estructura (CHoCH).

Hipotesis = un rasgo, o dos a la vez, con una entrada y un esquema. Se
prueban todas. Los datos se parten en tres tramos en el tiempo (50/25/25):
se adopta una solo si gana en los tres, con casos suficientes y con el
intervalo del 95 % de lo no visto por encima de cero. Lo demas se
descarta: con miles de hipotesis, sin ese filtro se «descubririan»
casualidades (cap. 18.7 de la guia).

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
MIN_ENT, MIN_VAL, MIN_R = 150, 50, 0.05
ESPERA_RUPTURA = 12     # velas de 5M que se espera el cambio de estructura tras el toque
VIDA = {3600: 1440, 900: 576}   # velas de 5M que vive una zona de H1 (5 dias) y de 15M (2 dias)
MINIMO_VELAS = 12000    # velas de 5M para que la EMA 50 de H4 y las zonas de H1 esten formadas
ENTRADAS = {"toque": "al cierre de la vela que toca el extremo",
            "ruptura": "al cierre de la vela de 5M que rompe la estructura a favor (CHoCH) tras el toque"}

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
    "fvg_h1": "primer regreso a un hueco (FVG) de H1 a favor",
    "fvg_m15": "primer regreso a un hueco (FVG) de 15M a favor",
    "ob_h1": "primer regreso a un order block de H1 a favor",
    "ob_m15": "primer regreso a un order block de 15M a favor",
    "h1_a_favor": "el rebote va a favor de la EMA 50 de H1",
    "h4_a_favor": "el rebote va a favor de la EMA 50 de H4",
    "h1_h4_a_favor": "a favor de la EMA 50 de H1 y de H4 a la vez",
    "extremo_dia": "en el cuarto exterior del rango de 24 h (descuento para comprar, premium para vender)",
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


def _zonas(v: np.ndarray, seg: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """
    Por tipo de zona (FVG y order block del marco `seg`), dos booleanos por
    vela de 5M: «esta en el primer regreso a una zona alcista / bajista».
    La zona solo cuenta desde el cierre de la vela que la forma, vive
    VIDA[seg] velas, y solo vale el primer regreso (tres velas), mientras
    el cierre no la haya atravesado.
    """
    from materias.red import zonas as Z
    b = U.agrupar(v, seg)
    a = U.atr(b["h"], b["l"], b["c"], 14)
    t5, lo, h, c, n = v["time"], v["low"], v["high"], v["close"], len(v)
    listas = {"fvg": Z.huecos(b["h"], b["l"], a, t=b["t"], seg=seg),
              "ob": [x[:4] for x in Z.order_blocks(b, a)]}
    out = {}
    for nom, zs in listas.items():
        sube, baja = np.zeros(n, bool), np.zeros(n, bool)
        for k, lado, zlo, zhi in zs:
            j0 = int(np.searchsorted(t5, b["t"][k] + seg))
            j1 = min(n, j0 + VIDA[seg])
            toca = np.flatnonzero((lo[j0:j1] <= zhi) if lado > 0 else (h[j0:j1] >= zlo))
            if not len(toca):
                continue
            j = j0 + int(toca[0])
            w = slice(j, min(n, j + 3))
            if lado > 0:
                sube[w] |= (lo[w] <= zhi) & (c[w] >= zlo)
            else:
                baja[w] |= (h[w] >= zlo) & (c[w] <= zhi)
        out[nom] = (sube, baja)
    return out


def _tendencia(v: np.ndarray, seg: int, n: int = 50) -> np.ndarray:
    """Signo de (cierre - EMA n) de la ultima vela de `seg` ya cerrada, por vela de 5M."""
    b = U.agrupar(v, seg)
    sig = np.sign(b["c"] - U.ema(b["c"], n))
    pos = np.searchsorted(b["t"] + seg, v["time"] + 300, side="right") - 1
    return np.where(pos >= 0, sig[np.maximum(pos, 0)], 0)


def _confirmar(v: np.ndarray, idx: np.ndarray, s: np.ndarray, a: np.ndarray) -> np.ndarray:
    """
    Vela de la entrada «ruptura» de cada candidato, o -1: la primera vela
    de 5M, en las ESPERA_RUPTURA siguientes al toque, cuyo cierre rompe el
    ultimo maximo/minimo relativo a favor del rebote, sin que antes el
    precio haya pasado del extremo del toque.
    """
    from materias.red import zonas as Z
    h, lo = v["high"], v["low"]
    n = len(h)
    rompe = np.zeros(n, int)
    for k, lado, *_ in Z.order_blocks({"o": v["open"], "h": h, "l": lo, "c": v["close"]}, a):
        rompe[k] = lado
    out = np.full(len(idx), -1)
    for k, (i, d) in enumerate(zip(idx, s)):
        for j in range(i + 1, min(n, i + 1 + ESPERA_RUPTURA)):
            if (lo[j] < lo[i] - 0.15 * a[i]) if d > 0 else (h[j] > h[i] + 0.15 * a[i]):
                break
            if rompe[j] == d:
                out[k] = j
                break
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
    # ── lo de la guia: zonas de marco mayor sin tocar, tendencia de H1/H4, premium/descuento ──
    for seg, marco in ((3600, "h1"), (900, "m15")):
        for nom, (sube, baja) in _zonas(v, seg).items():
            F[f"{nom}_{marco}"] = lado(sube, baja)
    h1, h4 = _tendencia(v, 3600)[idx] == s, _tendencia(v, 14400)[idx] == s
    lo24, hi24 = _previo(l, 288, np.min), _previo(h, 288, np.max)
    sitio = (c - lo24) / np.maximum(hi24 - lo24, 1e-9)
    F |= {"h1_a_favor": h1, "h4_a_favor": h4, "h1_h4_a_favor": h1 & h4,
          "extremo_dia": lado(sitio <= 0.25, sitio >= 0.75)}
    return {"idx": idx, "lado": s, "atr": a, "F": {k: np.asarray(x, bool) for k, x in F.items()},
            "conf": _confirmar(v, idx, s, a)}


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
    # tres tramos en el tiempo: se busca en el primero, se filtra en el segundo y se confirma en el tercero
    c1, c2 = int(n * 0.5), int(n * 0.75)
    tramos = (slice(0, c1), slice(c1, c2), slice(c2, n))
    combos = [(k,) for k in F] + list(itertools.combinations(F, 2))
    buenas, sueltos, probadas, base = [], {}, 0, {}
    for entrada in ENTRADAS:
        donde = R["idx"] if entrada == "toque" else np.where(R["conf"] >= 0, R["conf"], 0)
        for stop, obj in ESQUEMAS:
            r = resultado(v, donde, R["lado"], R["atr"], stop, obj)
            vale = ~np.isnan(r) & ((R["conf"] >= 0) | (entrada == "toque"))
            base[f"{entrada} {stop}/{obj}"] = {"n": int(vale.sum()), "r": round(float(r[vale].mean()), 3),
                                               "acierta_pct": round(100 * float((r[vale] > 0).mean()), 1)}
            for cb in combos:
                m = vale.copy()
                for k in cb:
                    m &= F[k]
                probadas += 1
                xs = [r[t][m[t]] for t in tramos]
                if len(xs[0]) < MIN_ENT or len(xs[1]) < MIN_VAL or len(xs[2]) < MIN_VAL:
                    continue
                reciente = np.r_[xs[1], xs[2]]
                x = {"rasgos": list(cb), "entrada": entrada, "stop_atr": stop, "obj_atr": obj,
                     "n_ent": len(xs[0]), "n_val": len(reciente),
                     "p_val": round(100 * float((reciente > 0).mean()), 1),
                     "r_ent": round(float(xs[0].mean()), 3), "r_val": round(float(reciente.mean()), 3),
                     "r_filtro": round(float(xs[1].mean()), 3), "r_prueba": round(float(xs[2].mean()), 3),
                     "r_val_min": round(float(reciente.mean() - 1.96 * reciente.std() / np.sqrt(len(reciente))), 3)}
                if len(cb) == 1 and entrada == "toque" and (stop, obj) == ESQUEMAS[0]:
                    sueltos[cb[0]] = x | {"frecuencia": round(100 * float(F[cb[0]].mean()), 1)}
                # gana en los tres tramos y el intervalo de lo no visto no toca el cero
                if min(x["r_ent"], x["r_filtro"], x["r_prueba"]) >= MIN_R and x["r_val_min"] > 0:
                    buenas.append(x)
    buenas.sort(key=lambda x: -min(x["r_ent"], x["r_filtro"], x["r_prueba"]))

    hoy = f"{dt.date.today()}"
    viejo = datos()
    antes = {clave(x): x for x in viejo.get("reglas", [])}
    for x in buenas:
        x["desde"] = antes.get(clave(x), {}).get("desde", hoy)
    ahora = {clave(x) for x in buenas}
    caidas = [c for c in viejo.get("caidas", []) if c["regla"] not in ahora]
    caidas += [{"regla": k, "desde": x.get("desde"), "cayo": hoy} for k, x in antes.items() if k not in ahora]
    return {"medido": hoy, "dias": DIAS, "candidatos": n, "coste_atr": COSTE,
            "base": base, "probadas": probadas,
            "reglas": buenas[:12], "sueltos": sueltos, "caidas": caidas[-40:],
            "tamano_rebote": tamano_rebote(v), "regla_fija": regla_fija(v)}


def clave(x: dict) -> str:
    return "+".join(x["rasgos"]) + f"@{x.get('entrada', 'toque')}/{x['stop_atr']}/{x['obj_atr']}"


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


def cumple(R: dict, k: int, entrada: str | None = None) -> list[dict]:
    """Reglas adoptadas que cumple el candidato k de unos `rasgos()`. `entrada` filtra por tipo."""
    return [x for x in datos().get("reglas", [])
            if all(R["F"][r][k] for r in x["rasgos"]) and entrada in (None, x.get("entrada", "toque"))
            and (x.get("entrada", "toque") == "toque" or R["conf"][k] >= 0)]


def en_vivo(v: np.ndarray) -> list[tuple[dict, int]]:
    """(regla, lado) que se disparan en la ULTIMA vela cerrada de `v` (hacen falta MINIMO_VELAS)."""
    R = rasgos(v)
    ult, out = len(v) - 1, []
    for k in np.flatnonzero(R["idx"] >= ult - ESPERA_RUPTURA):
        if R["idx"][k] == ult:
            out += [(x, int(R["lado"][k])) for x in cumple(R, int(k), "toque")]
        if R["conf"][k] == ult:
            out += [(x, int(R["lado"][k])) for x in cumple(R, int(k), "ruptura")]
    return out


def texto() -> str:
    d = datos()
    if not d:
        return ""
    L = ["## Rebotes: lo que el investigador busco solo, y lo que aguanto la validacion",
         f"Candidato = vela de 5M que toca el extremo de 2 h; a favor del rebote, coste {d['coste_atr']} ATR. Dos "
         "entradas: «toque» (al cierre de esa vela) y «ruptura» (al cierre de la vela de 5M que rompe la estructura "
         f"a favor, CHoCH). Ultimos {d['dias']} dias, {d['candidatos']:,} candidatos, medido el {d['medido']}. "
         "Sin filtro (entrada stop/objetivo en ATR): "
         + "; ".join(f"{k} {x['r']:+.2f} R" for k, x in d["base"].items())
         + f". Se probaron {d['probadas']} hipotesis (un rasgo o dos, por entrada y esquema); adoptadas "
         f"{len(d['reglas'])}: solo las que ganan en los tres tramos del tiempo (50/25/25 %) con el intervalo de lo "
         "no visto sobre cero."]
    for x in d["reglas"]:
        L.append(f"- {nombre(x)} · entrada «{x['entrada']}», stop {x['stop_atr']} ATR, objetivo {x['obj_atr']} ATR: "
                 f"{x['r_val']:+.2f} R en datos no vistos ({x['p_val']:.0f} % ganan, n={x['n_val']}; ultimo tramo "
                 f"{x['r_prueba']:+.2f}); busqueda {x['r_ent']:+.2f} R (n={x['n_ent']}); adoptada el {x['desde']}")
    if not d["reglas"]:
        L.append("- NINGUNA regla pasa: comprar o vender el rebote en el extremo de 2 h pierde despues del spread "
                 "con cualquier rasgo probado, tambien con zona de H1/15M, tendencia de H1/H4 y esperando el CHoCH. "
                 "Un rebote no es un setup por si solo.")
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
