"""
Lo que dicen los datos sobre los pips: cuanto retrocede el oro antes de
moverse, segun la sesion. Es la leccion que la red tiene que aprender, y
antes de entrenarla hay que saber si existe.

LA PREGUNTA
Una entrada «temprana» es una que se toma antes de que el precio termine
de retroceder. Si en las operaciones que acabaron bien el precio suele ir
X pips en contra antes de arrancar, entrar a mercado regala esos X pips y
pone el stop mas cerca de lo necesario. Si X depende de la sesion, la
red tiene que saber en que sesion esta.

HORA DE NUEVA YORK
El servidor de Tickmill alinea su medianoche con el cierre de Nueva York
(17:00 ET), asi que la hora de las velas es la de Nueva York mas siete
horas todo el ano. Las dos o tres semanas en que el cambio de hora de
EE. UU. y el de Europa no coinciden quedan desplazadas una hora; para
estadisticas de sesion es un error pequeno y conocido.
"""

from __future__ import annotations

import numpy as np

from materias.red import datos as D
from materias.red import etiquetas as E

DESFASE_NY = 7 * 3600


def hora_ny(tiempo: np.ndarray) -> np.ndarray:
    """Hora de Nueva York (0-23, con fraccion) de cada vela."""
    return ((tiempo.astype(np.int64) - DESFASE_NY) % 86400) / 3600.0


SESIONES = {
    # Killzones del capitulo 10.2, en hora de Nueva York.
    "Asia 20-00":        (20.0, 24.0),
    "Londres KZ 02-05":  (2.0, 5.0),
    "NY AM 07-10":       (7.0, 10.0),
    "London Close 10-12": (10.0, 12.0),
    "NY PM 13:30-16":    (13.5, 16.0),
}


def en_sesion(h: np.ndarray, desde: float, hasta: float) -> np.ndarray:
    return (h >= desde) & (h < hasta)


def resumen(tf: str = "M5", horizonte: int = 12, objetivo: float = 50.0):
    """
    Para cada sesion: de las entradas que acabaron ganando `objetivo` pips
    (llegaron a +objetivo dentro del horizonte), cuanto se fueron en contra
    antes de llegar. Mediana y percentiles, en compras y en ventas.
    """
    v = D.cargar(tf)
    e = E.calcular(v, horizonte)
    h = hora_ny(e["tiempo"])
    filas = []
    for nombre, (a, b) in {"TODAS": (0, 24), **SESIONES}.items():
        m = en_sesion(h, a, b)
        for lado in ("largo", "corto"):
            gano = m & (e[f"{lado}_mfe"] >= objetivo)
            pre = e[f"{lado}_mae_previa"][gano]
            if len(pre) < 50:
                continue
            filas.append({
                "sesion": nombre, "lado": lado, "n": int(gano.sum()),
                "pct_llega": 100 * gano.sum() / max(1, m.sum()),
                "p25": np.percentile(pre, 25), "p50": np.percentile(pre, 50),
                "p75": np.percentile(pre, 75), "p90": np.percentile(pre, 90),
                "rango_med": float(np.median((e["largo_mfe"] + e["largo_mae"])[m])),
            })
    return filas


if __name__ == "__main__":
    for hz, nom in ((12, "1 hora"), (48, "4 horas")):
        obj = 50 if hz == 12 else 150
        print(f"\n=== Horizonte {nom} · entradas que llegaron a +{obj} pips ===")
        print(f"{'sesion':20} {'lado':6} {'n':>7} {'llega%':>7} "
              f"{'p25':>6} {'MEDIANA':>8} {'p75':>6} {'p90':>6}   rango tipico")
        for f in resumen("M5", hz, obj):
            print(f"{f['sesion']:20} {f['lado']:6} {f['n']:7,} {f['pct_llega']:6.1f}% "
                  f"{f['p25']:6.0f} {f['p50']:8.0f} {f['p75']:6.0f} {f['p90']:6.0f}"
                  f"   {f['rango_med']:5.0f} pips")
