"""
Lo que la parte numerica le cuenta al agente en cada decision.

El agente razona con la guia. Esto le añade, al mismo tiempo, lo que los
datos midieron, CON SU FIABILIDAD AL LADO: un numero sin la nota de cuanto
vale es una invitacion a creerselo.

DOS COSAS, Y NO VALEN LO MISMO
1. La tabla de pips por hora. Cuanto suele retroceder el precio antes de
   moverse a favor y cuanto se mueve, segun la hora de Nueva York, a 1 y a
   4 horas vista. Es lo mas solido que salio del estudio: ninguna red le
   gano. Sirve para el STOP (que no lo barra el retroceso normal) y para
   la ENTRADA (limitada donde suele llegar el retroceso, no a mercado).
2. La red de direccion. Se le pasa con su AUC medido en datos que no vio.
   Con 553.000 velas y costes reales pierde en todos los umbrales y en los
   dos horizontes: el agente tiene que saberlo para no apoyarse en ella.

POR QUE EL ULTIMO AÑO Y NO TODO EL HISTORICO
Los pips son absolutos y el oro no: en 2019 estaba a 1.300 y en 2026 a
4.100. El mismo susto de mercado son 25 pips entonces y 80 ahora. La
tabla se hace con los ultimos 365 dias, que son los pips de hoy.

POR QUE UN JSON VERSIONADO
Las velas y los modelos viven en state/, que no se sube a git, asi que
Render no los tiene. La tabla son 48 filas: se exporta a
`tabla_pips.json` junto a este archivo y viaja con el codigo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from materias.red import datos as D
from materias.red.pips import DESFASE_NY, hora_ny

ARCHIVO_TABLA = Path(__file__).with_name("tabla_pips.json")

HORIZONTES = {                # nombre: (velas de 5M, objetivo en pips)
    "1h": (12, 50.0),
    "4h": (48, 150.0),
}


def construir_tabla(dias: int = 365) -> dict:
    """La tabla por hora de NY, medida sobre los ultimos `dias` de 5M."""
    from materias.red import etiquetas as E
    v = D.cargar("M5")
    corte = int((datetime.now(UTC) - timedelta(days=dias)).timestamp()) + DESFASE_NY
    v = v[v["time"] >= corte]
    out = {"medido": datetime.now(UTC).strftime("%Y-%m-%d"), "dias": dias,
           "velas": int(len(v)), "pip": D.PIP, "horizontes": {}}
    for nombre, (hz, objetivo) in HORIZONTES.items():
        e = E.calcular(v, hz)
        h = hora_ny(e["tiempo"]).astype(int)
        rango = e["largo_mfe"] + e["largo_mae"]
        filas = {}
        for hh in range(24):
            m = h == hh
            if m.sum() < 100:
                continue
            fila = {"n": int(m.sum()), "rango_p50": round(float(np.median(rango[m])), 1)}
            for lado in ("largo", "corto"):
                g = m & (e[f"{lado}_mfe"] >= objetivo)
                pre = e[f"{lado}_mae_previa"][g]
                fila[lado] = {"llega_pct": round(100 * g.sum() / m.sum(), 1),
                              "p50": round(float(np.percentile(pre, 50)), 1),
                              "p75": round(float(np.percentile(pre, 75)), 1)}
            filas[str(hh)] = fila
        out["horizontes"][nombre] = {"velas_5m": hz, "objetivo_pips": objetivo,
                                     "por_hora": filas}
    return out


def exportar(dias: int = 365) -> Path:
    ARCHIVO_TABLA.write_text(json.dumps(construir_tabla(dias), indent=1),
                             encoding="utf-8")
    tabla.cache_clear()
    return ARCHIVO_TABLA


@lru_cache(maxsize=1)
def tabla() -> dict:
    """La tabla versionada; si no existe, se calcula con los datos locales."""
    try:
        return json.loads(ARCHIVO_TABLA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return construir_tabla()


def metricas() -> dict:
    try:
        return json.loads((D.CARPETA / "metricas.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _a_array(m5: list[dict]) -> np.ndarray:
    """Las velas del contexto del agente al formato de MT5 que usa la red."""
    tipo = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                     ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"),
                     ("real_volume", "i8")])
    a = np.zeros(len(m5), dtype=tipo)
    for i, x in enumerate(m5):
        # hora de NY como si fuera UTC, mas el desfase del servidor
        pared = x["dt"].replace(tzinfo=UTC).timestamp()
        a[i] = (int(pared) + DESFASE_NY, x["o"], x["h"], x["l"], x["c"], 0, 0, 0)
    return a


def prob_subida(m5: list[dict], minutos: int) -> float | None:
    """Probabilidad de que el precio este mas alto dentro de `minutos`."""
    ruta = D.CARPETA / f"red_direccion_{minutos}m.npz"
    if not ruta.exists() or len(m5) < 320:
        return None
    from materias.red import entradas as F
    from materias.red.modelo import Red
    X, _ = F.calcular(_a_array(m5))
    if np.isnan(X[-1]).any():
        return None
    return float(Red.cargar(ruta).predecir(X[-1:]).ravel()[0])


def texto(ctx: dict) -> str:
    """La seccion que se pega en el retrato del agente."""
    ahora = ctx.get("ahora")
    if ahora is None:
        return ""
    t = tabla()
    hh = str(ahora.hour)
    L = ["## Lo que midieron los datos (red de conocimiento numerica)",
         f"Tabla de pips de XAUUSD a las {hh}h de Nueva York, medida en los ultimos "
         f"{t.get('dias', '?')} dias ({t.get('velas', 0):,} velas de 5M, al "
         f"{t.get('medido', '?')}). 1 pip = 0,10 de precio; con 0,01 lotes, 0,10 USD."]
    for nombre, hz in t.get("horizontes", {}).items():
        f = hz["por_hora"].get(hh)
        if not f:
            L.append(f"- {nombre}: sin estadistica para esta hora (mercado cerrado)")
            continue
        obj = hz["objetivo_pips"]
        L.append(f"- a {nombre}: rango tipico {f['rango_p50']:.0f} pips")
        for lado, nom in (("largo", "compra"), ("corto", "venta")):
            x = f[lado]
            L.append(f"  · {nom}: llega a +{obj:.0f} pips el {x['llega_pct']:.0f} % de las veces; "
                     f"las que llegan fueron antes {x['p50']:.0f} pips en contra (mediana) y "
                     f"{x['p75']:.0f} (3 de cada 4). Stop por debajo de {x['p75']:.0f} pips = lo "
                     f"barre el retroceso normal; limitada {x['p50']:.0f} pips mejor que el "
                     f"precio = se llena en la mitad de las ganadoras")
    mt = metricas()
    hubo_red = False
    for nombre, minutos in (("1h", 60), ("4h", 240)):
        p = prob_subida(ctx.get("m5") or [], minutos)
        if p is None:
            continue
        hubo_red = True
        auc = mt.get(f"direccion_{minutos}m", {}).get("auc")
        nota = f"AUC {auc:.3f} en datos no vistos" if auc else "sin medir"
        L.append(f"- red de direccion a {nombre}: {100 * p:.0f} % de que suba · {nota}")
    if hubo_red:
        L.append("  La red de direccion PIERDE dinero con costes reales en todos los umbrales "
                 "probados (553.000 velas, 2019-2026). Es informativa: no decide el lado.")
    from materias.red import autopsia, investigador, largo_rebote, lecciones, rebote, retroceso, utbot, ventaja, zonas
    for extra in (lecciones.texto(), utbot.texto(ctx), zonas.texto(ctx), retroceso.texto(), rebote.texto(),
                  largo_rebote.texto(ctx), ventaja.texto(), investigador.texto(), autopsia.texto()):
        if extra:
            L.append("")
            L.append(extra)
    return "\n".join(L)


if __name__ == "__main__":
    ruta = exportar()
    t = tabla()
    print(f"tabla exportada a {ruta.name} · {t['velas']:,} velas de los ultimos {t['dias']} dias")
    for nombre, hz in t["horizontes"].items():
        print(f"\n{nombre} (objetivo +{hz['objetivo_pips']:.0f} pips)  hora NY: rango | compra p50/p75 | venta p50/p75")
        for hh in ("2", "3", "8", "9", "11", "14", "21"):
            f = hz["por_hora"].get(hh)
            if f:
                print(f"  {hh:>2}h: {f['rango_p50']:5.0f} | {f['largo']['p50']:4.0f}/{f['largo']['p75']:4.0f} "
                      f"| {f['corto']['p50']:4.0f}/{f['corto']['p75']:4.0f}")
