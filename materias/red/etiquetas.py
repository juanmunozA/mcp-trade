"""
Que paso despues de cada vela, medido en pips. Es lo que la red aprende.

DOS PREGUNTAS DISTINTAS
1. Direccion: a H velas vista, ¿el precio esta arriba o abajo de donde se
   entro? Es lo que mide el piloto y es casi una moneda al aire.
2. El momento de la entrada: aunque la direccion sea buena, ¿cuanto se
   movio en contra ANTES de ir a favor? Esa es la pregunta de los pips.

POR QUE LA SEGUNDA IMPORTA TANTO
En las diez operaciones de 25 minutos del 24 de septiembre las tres
ganadoras se movieron en contra como mucho 4,7 dolares (47 pips) y las
siete perdedoras entre 6,6 y 14,3 (66-143 pips). Con un stop de 50-60
pips, la diferencia entre acertar y fallar no estaba solo en la
direccion: estaba en haber entrado antes de que terminara el retroceso.
Si una red aprende cuanto suele retroceder el precio antes de moverse,
se puede entrar con una limitada en ese nivel en vez de a mercado.

CONVENCIONES
- Se entra a la APERTURA de la vela siguiente, no al cierre de la actual:
  el cierre ya paso cuando se decide.
- Todo en pips (1 pip = 0,10 de precio, ver datos.PIP).
- `mae_previa` es la excursion adversa hasta el momento en que se alcanza
  el extremo favorable, no en toda la ventana: lo que se sufre por el
  camino, no lo que pasa despues de haber llegado.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as ventanas

from materias.red.datos import PIP


def calcular(velas: np.ndarray, horizonte: int) -> dict[str, np.ndarray]:
    """
    Etiquetas para cada vela con `horizonte` velas por delante.

    Devuelve arrays de longitud n - horizonte; la vela i se etiqueta con lo
    que pasa entre la apertura de i+1 y el cierre de i+horizonte.
    """
    o = velas["open"].astype(float)
    h = velas["high"].astype(float)
    lo = velas["low"].astype(float)
    c = velas["close"].astype(float)
    n = len(velas) - horizonte
    entrada = o[1:n + 1]
    H = ventanas(h[1:], horizonte)[:n]          # (n, horizonte)
    L = ventanas(lo[1:], horizonte)[:n]
    salida = c[horizonte:horizonte + n]

    maxh, minl = H.max(axis=1), L.min(axis=1)
    paso = np.arange(horizonte)

    # Excursion adversa sufrida ANTES de tocar el extremo favorable.
    i_max = H.argmax(axis=1)
    antes_max = np.where(paso <= i_max[:, None], L, np.inf).min(axis=1)
    i_min = L.argmin(axis=1)
    antes_min = np.where(paso <= i_min[:, None], H, -np.inf).max(axis=1)

    return {
        "tiempo": velas["time"][:n],
        "ret": (salida - entrada) / PIP,
        "largo_mfe": (maxh - entrada) / PIP,
        "largo_mae": (entrada - minl) / PIP,
        "largo_mae_previa": np.maximum(0.0, (entrada - antes_max) / PIP),
        "corto_mfe": (entrada - minl) / PIP,
        "corto_mae": (maxh - entrada) / PIP,
        "corto_mae_previa": np.maximum(0.0, (antes_min - entrada) / PIP),
        "spread": velas["spread"][1:n + 1] / 10.0,   # puntos -> pips
    }
