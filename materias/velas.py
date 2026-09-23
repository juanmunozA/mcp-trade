"""
Lectura de velas japonesas. Capitulo 2.

LA ADVERTENCIA VA PRIMERO PORQUE ES LO QUE MANDA
────────────────────────────────────────────────
«Un patron de velas SIN CONTEXTO (zona, liquidez, estructura) tiene POCA
VALIDEZ ESTADISTICA. El mismo martillo vale mucho en un OB de 4H tras
barrer un minimo, y casi nada en medio de un rango.» (cap. 2.3)

Por eso nada de este archivo genera una senal. Los patrones se detectan
y se anotan como confluencia sobre una senal que ya existe por
estructura y liquidez. Es la diferencia entre «hay un martillo, compro»
-- que la guia desaconseja explicitamente -- y «la barrida que ya
detecte cerro con un martillo en el OB, una razon mas».

LAS TRES REGLAS QUE SI SON OPERATIVAS (cap. 2.3)
1. «Compara el tamano de la vela con el ATR: una vela de 2x ATR es
   DESPLAZAMIENTO REAL; una de 0,3x ATR es RUIDO.» Por eso el tamano
   nunca se mide en dolares aqui, siempre en ATR.
2. «Si una vela perfora un maximo con la mecha pero cierra por debajo,
   es una BARRIDA, no una ruptura.» Ya vive en `estructura.barrida`.
3. «Varias mechas en la misma zona = zona defendida. Varios cuerpos que
   cierran fuera = zona rota.» Eso es `zona_defendida()`.
"""

from __future__ import annotations

from materias.estructura import Vela


def _partes(v: Vela) -> tuple[float, float, float, float]:
    """Cuerpo, mecha superior, mecha inferior y rango total (cap. 2.1)."""
    cuerpo = abs(v.c - v.o)
    sup = v.h - max(v.o, v.c)
    inf = min(v.o, v.c) - v.l
    return cuerpo, sup, inf, max(v.h - v.l, 1e-12)


def patron(v: Vela, prev: Vela | None, atr: float) -> str:
    """
    Que patron forma esta vela, segun la tabla del capitulo 2.2.

    Devuelve el nombre o "" si no forma ninguno reconocible. El orden de
    comprobacion va de mas especifico a mas general: una envolvente
    tambien suele ser marubozu, y la envolvente dice mas.
    """
    cuerpo, sup, inf, rango = _partes(v)
    alcista = v.c > v.o

    # «Outside bar: vela que supera maximo y minimo de la anterior.
    # Barre liquidez de AMBOS lados. Giros tras barridas.»
    if prev and v.h > prev.h and v.l < prev.l:
        return "outside_alcista" if alcista else "outside_bajista"

    # «Envolvente: cuerpo que cubre el cuerpo previo. Cambio de control.»
    if prev:
        pc_alto, pc_bajo = max(prev.o, prev.c), min(prev.o, prev.c)
        c_alto, c_bajo = max(v.o, v.c), min(v.o, v.c)
        if c_alto >= pc_alto and c_bajo <= pc_bajo and cuerpo > abs(prev.c - prev.o):
            if alcista and prev.c < prev.o:
                return "envolvente_alcista"
            if not alcista and prev.c > prev.o:
                return "envolvente_bajista"

    # «Inside bar: vela contenida en el rango de la anterior. Compresion;
    # se prepara expansion. Antes de rupturas.»
    if prev and v.h <= prev.h and v.l >= prev.l:
        return "inside"

    # «Marubozu / vela de desplazamiento: cuerpo grande, mechas minimas.
    # Suele dejar FVG. Rupturas de estructura, inicio de impulsos.»
    if atr > 0 and cuerpo >= 1.2 * atr and cuerpo / rango >= 0.75:
        return "marubozu_alcista" if alcista else "marubozu_bajista"

    # «Martillo / Pin bar alcista: cuerpo pequeno arriba, mecha inferior
    # >= 2x cuerpo. Rechazo de precios bajos. En soporte, OB, barrida.»
    if cuerpo > 0 and inf >= 2 * cuerpo and sup <= cuerpo:
        return "martillo"
    # «Estrella fugaz / Pin bar bajista: mecha superior larga.»
    if cuerpo > 0 and sup >= 2 * cuerpo and inf <= cuerpo:
        return "estrella_fugaz"

    # «Doji: apertura = cierre, mechas a ambos lados. Indecision.
    # Tras tendencia larga o en zona clave.»
    if cuerpo / rango <= 0.1:
        return "doji"
    return ""


CONFIRMAN = {
    "compra": ("martillo", "envolvente_alcista", "marubozu_alcista",
               "outside_alcista"),
    "venta": ("estrella_fugaz", "envolvente_bajista", "marubozu_bajista",
              "outside_bajista"),
}
"""
Que patron acompana a que lado.

La columna «donde importa» de la tabla 2.2 encaja con lo que estos
modelos ya buscan: el martillo importa «en soporte, OB, barrida de
minimos» -- exactamente el contexto de una senal de compra por barrida.
"""


def confirma(vs: list[Vela], lado: str, atr: float, n: int = 3) -> str:
    """
    Si alguna de las ultimas n velas confirma el lado, dice cual.

    n=3 porque la confirmacion tiene que ser reciente: un martillo de
    hace veinte velas no dice nada del momento de entrada.
    """
    for i in range(max(1, len(vs) - n), len(vs)):
        p = patron(vs[i], vs[i - 1], atr)
        if p in CONFIRMAN.get(lado, ()):
            return p
    return ""


def desplazamiento(v: Vela, atr: float) -> str:
    """
    Clasifica el tamano contra el ATR (cap. 2.3, regla 2).

    «Una vela de 2x ATR es desplazamiento real; una de 0,3x ATR es
    ruido.» Los tramos de en medio se nombran con la escala de la propia
    guia, que usa 1,2x como umbral de desplazamiento en el capitulo 20.2
    y 1,5x en el capitulo 16.2.
    """
    if atr <= 0:
        return ""
    r = abs(v.c - v.o) / atr
    if r >= 2.0:
        return "desplazamiento_fuerte"
    if r >= 1.2:
        return "desplazamiento"
    if r <= 0.3:
        return "ruido"
    return "normal"


def zona_defendida(vs: list[Vela], nivel: float, tol: float) -> dict:
    """
    «Varias mechas en la misma zona = zona defendida. Varios cuerpos que
    cierran fuera = zona rota.» (cap. 2.3, regla 4)

    Cuenta cuantas velas TOCARON el nivel con mecha sin cerrar del otro
    lado, y cuantas cerraron fuera. Es la forma de distinguir un nivel
    que el mercado esta sosteniendo de uno que ya cedio.
    """
    mechas = cuerpos_arriba = cuerpos_abajo = 0
    for v in vs:
        toco = v.l <= nivel + tol and v.h >= nivel - tol
        if not toco:
            continue
        if abs(v.c - nivel) <= tol:
            continue
        if v.c > nivel + tol:
            if v.l < nivel - tol:
                mechas += 1
            cuerpos_arriba += 1
        elif v.c < nivel - tol:
            if v.h > nivel + tol:
                mechas += 1
            cuerpos_abajo += 1
    rota = cuerpos_arriba >= 2 and cuerpos_abajo >= 2
    return {"mechas": mechas, "cierres_arriba": cuerpos_arriba,
            "cierres_abajo": cuerpos_abajo,
            "defendida": mechas >= 2 and not rota,
            "rota": rota}


def compresion(vs: list[Vela], atr: float, n: int = 5) -> bool:
    """
    Inside bars encadenadas: «compresion; se prepara expansion».

    Util antes de la apertura de Londres: un rango de Asia que termina
    comprimido suele preceder al Judas swing del capitulo 11.4.
    """
    if len(vs) < n + 1 or atr <= 0:
        return False
    ult = vs[-n:]
    rango = max(v.h for v in ult) - min(v.l for v in ult)
    dentro = sum(1 for i in range(1, len(ult))
                 if ult[i].h <= ult[i - 1].h and ult[i].l >= ult[i - 1].l)
    return rango < 1.5 * atr or dentro >= 2
