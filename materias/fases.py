"""
En que fase esta el mercado. Capitulo 5: Wyckoff, rango y Power of 3.

POR QUE IMPORTA ANTES QUE CUALQUIER SETUP
─────────────────────────────────────────
«La lateralizacion es la fase donde MAS CUENTAS SE PIERDEN porque los
indicadores de tendencia dan senales falsas y las rupturas fallan con
frecuencia.» (cap. 5.2)

Saber en que fase esta el precio decide QUE MODELO aplica. Los de
continuacion (11.2, 11.3) necesitan markup o markdown. Los de reversion
(11.1, 11.4, 11.7) viven justo en el borde entre acumulacion y markup:
el spring. Correr un modelo de continuacion dentro de un rango es
exactamente el error que la guia describe.

LA EQUIVALENCIA QUE LA GUIA DEJA ESCRITA
«Spring / Upthrust: ruptura falsa del rango que barre la liquidez
(stops) y regresa. ES EL EQUIVALENTE DE WYCKOFF A LA BARRIDA DE LIQUIDEZ
DE ICT/SMC.» (cap. 5.1)

Eso significa que Wyckoff y SMC no son dos sistemas que haya que
reconciliar: son dos nombres para lo mismo. El spring que detecta este
archivo y la barrida que detecta `estructura.barrida` son el mismo
evento. Lo que Wyckoff agrega es el CONTEXTO -- si ese spring ocurre tras
un rango largo con climax de volumen, vale mas que uno suelto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from materias import estructura as E
from materias.estructura import Vela


@dataclass
class Fase:
    nombre: str = "indefinida"
    """acumulacion | markup | distribucion | markdown | rango"""
    rango_alto: float = 0.0
    rango_bajo: float = 0.0
    eventos: list[str] = field(default_factory=list)
    permite_continuacion: bool = False
    permite_reversion: bool = False
    detalle: str = ""

    def dict(self) -> dict:
        return self.__dict__.copy()


def _es_rango(vs: list[Vela], atr: float, n: int = 30) -> tuple[bool, float, float]:
    """
    «Varios toques en maximo y minimo SIN CIERRES FUERA» (cap. 5.2).

    Los otros identificadores que la guia lista -- ADX < 20, medias
    planas, ATR decreciente -- viven en `indicadores`. Aqui se usa el que
    depende solo del precio, que es el que no puede fallar por falta de
    velas para calcular una media de 200.
    """
    sel = vs[-n:]
    if len(sel) < 15 or atr <= 0:
        return False, 0.0, 0.0
    alto = max(v.h for v in sel)
    bajo = min(v.l for v in sel)
    ancho = alto - bajo
    # Un rango estrecho respecto a su propia volatilidad: menos de 4 ATR
    # de amplitud en 30 velas es compresion, no tendencia.
    if ancho > 4 * atr:
        return False, alto, bajo
    # «Sin cierres fuera»: se toma el 15% superior e inferior como borde.
    borde = ancho * 0.15
    fuera = sum(1 for v in sel if v.c > alto - borde or v.c < bajo + borde)
    return fuera <= len(sel) * 0.35, alto, bajo


def leer(vs: list[Vela], volumenes: list[float] | None = None,
         atr: float = 0.0) -> Fase:
    """
    Clasifica la fase segun la tabla del capitulo 5.1.

        Acumulacion   rango lateral TRAS UNA BAJADA, falsas rupturas abajo
        Markup        tendencia alcista: HH y HL, BOS alcistas
        Distribucion  rango lateral TRAS UNA SUBIDA, falsas rupturas arriba
        Markdown      tendencia bajista: LH y LL

    Lo que separa acumulacion de distribucion no es el rango -- los dos
    son rangos -- sino DE DONDE VIENE el precio. Por eso se mira el tramo
    anterior al rango, no solo el rango.
    """
    f = Fase()
    if len(vs) < 40:
        f.detalle = "faltan velas para clasificar la fase"
        return f
    a = atr or E.atr(vs)
    est = E.leer_estructura(vs)

    en_rango, alto, bajo = _es_rango(vs, a)
    f.rango_alto, f.rango_bajo = round(alto, 4), round(bajo, 4)

    if not en_rango:
        if est.tendencia == "alcista":
            f.nombre = "markup"
            f.detalle = "«la demanda supera a la oferta: HH y HL, BOS alcistas»"
            f.permite_continuacion = True
        elif est.tendencia == "bajista":
            f.nombre = "markdown"
            f.detalle = "«la oferta domina: LH y LL»"
            f.permite_continuacion = True
        else:
            f.nombre = "indefinida"
            f.detalle = "sin rango claro ni tendencia clara"
        f.permite_reversion = True
        return f

    # Es un rango. ¿De donde venia el precio antes de entrar en el?
    previo = vs[-70:-30] if len(vs) >= 70 else vs[:-30]
    if len(previo) >= 10:
        deriva = previo[-1].c - previo[0].c
        if deriva < -a:
            f.nombre = "acumulacion"
            f.detalle = ("«compradores grandes absorben la oferta a precios "
                         "bajos: rango lateral TRAS UNA BAJADA»")
        elif deriva > a:
            f.nombre = "distribucion"
            f.detalle = ("«vendedores grandes descargan a precios altos: "
                         "rango lateral TRAS UNA SUBIDA»")
        else:
            f.nombre = "rango"
            f.detalle = "lateral sin tramo previo claro"
    else:
        f.nombre = "rango"

    # En rango, los modelos de continuacion no aplican: es el aviso
    # explicito del capitulo 5.2.
    f.permite_continuacion = False
    f.permite_reversion = True
    f.eventos = eventos_wyckoff(vs, alto, bajo, a, volumenes)
    return f


def eventos_wyckoff(vs: list[Vela], alto: float, bajo: float, atr: float,
                    volumenes: list[float] | None = None) -> list[str]:
    """
    Los eventos clave del capitulo 5.1 que se pueden detectar sin juicio.

    Solo tres de los ocho que la guia lista son programables sin
    ambiguedad -- y el capitulo 18.2 es explicito: «solo se puede
    automatizar lo que se puede definir SIN AMBIGUEDAD». PS, AR, ST y LPS
    dependen de leer la intencion del tramo, asi que no se inventan.

        Spring / Upthrust   ruptura falsa del rango que barre stops y regresa
        SC / BC (climax)    vela de panico o euforia con VOLUMEN EXTREMO
        SOS / SOW           ruptura REAL con desplazamiento y volumen
    """
    out: list[str] = []
    if len(vs) < 10 or atr <= 0:
        return out
    ult = vs[-12:]
    borde = atr * 0.15

    # Spring: perforo el minimo del rango y volvio a cerrar dentro.
    if any(v.l < bajo - borde and v.c > bajo for v in ult):
        out.append("spring (barrida del minimo del rango: compras tras la "
                   "recuperacion)")
    # Upthrust / UTAD: lo simetrico arriba.
    if any(v.h > alto + borde and v.c < alto for v in ult):
        out.append("upthrust (barrida del maximo del rango: ventas tras la "
                   "perdida del rango)")

    # Climax: «vela de panico o euforia con volumen extremo; marca un
    # extremo del rango».
    if volumenes and len(volumenes) >= 20:
        media = sum(volumenes[-20:]) / 20
        for v in ult[-4:]:
            if v.i < len(volumenes) and media > 0 and volumenes[v.i] > 2.5 * media \
                    and abs(v.c - v.o) > 1.5 * atr:
                out.append(f"climax de volumen ({volumenes[v.i] / media:.1f}x la media)")
                break

    # SOS / SOW: «ruptura REAL del rango con desplazamiento y volumen».
    ultima = vs[-1]
    if ultima.c > alto and ultima.cuerpo > 1.2 * atr:
        out.append("SOS: ruptura alcista real del rango con desplazamiento")
    elif ultima.c < bajo and ultima.cuerpo > 1.2 * atr:
        out.append("SOW: ruptura bajista real del rango con desplazamiento")
    return out


@dataclass
class PowerOf3:
    fase: str = "acumulacion"
    """acumulacion | manipulacion | distribucion"""
    rango_alto: float = 0.0
    rango_bajo: float = 0.0
    lado_manipulado: str = ""
    direccion_esperada: str = ""
    detalle: str = ""

    def dict(self) -> dict:
        return self.__dict__.copy()


def power_of_3(m5: list[dict], asia: dict, ahora, sesgo: str = "") -> PowerOf3:
    """
    Donde esta el dia dentro del ciclo AMD (cap. 5.3).

    «Acumulacion (rango inicial, tipicamente Asia), Manipulacion
    (movimiento falso que barre un extremo, tipicamente al inicio de
    Londres o NY, tambien llamado Judas swing) y Distribucion (el
    movimiento real del dia EN DIRECCION OPUESTA a la manipulacion).»

    El ejemplo de la guia, literal: «Sesgo diario alcista en oro. Asia
    forma un rango entre 2.640 y 2.646. A las 02:30 ET el precio cae a
    2.638 (barre el minimo de Asia = manipulacion), hace CHoCH alcista en
    5M y sube el resto del dia hasta 2.668 (distribucion).»

    Esto no es un modelo de entrada -- ese es el 11.4 -- sino la lectura
    de en que parte del dia estamos, que es lo que decide si todavia hay
    que esperar o si el movimiento ya arranco.
    """
    p = PowerOf3()
    if not asia.get("alto"):
        p.detalle = "sin rango de Asia marcado todavia"
        return p
    p.rango_alto, p.rango_bajo = round(asia["alto"], 4), round(asia["bajo"], 4)

    hoy = [v for v in m5 if v["dt"].date() == ahora.date() and v["dt"].hour >= 2]
    if not hoy:
        p.fase = "acumulacion"
        p.detalle = "Asia todavia formando el rango; Londres no ha abierto"
        return p

    barrio_bajo = any(v["l"] < asia["bajo"] for v in hoy)
    barrio_alto = any(v["h"] > asia["alto"] for v in hoy)
    precio = hoy[-1]["c"]

    if not barrio_bajo and not barrio_alto:
        p.fase = "acumulacion"
        p.detalle = "el precio sigue dentro del rango de Asia: falta la manipulacion"
        return p

    if barrio_bajo and barrio_alto:
        # Ambos lados barridos: el dia ya se definio o esta en un dia de
        # rango. Manda el que se barrio primero, que es la manipulacion.
        i_bajo = next(k for k, v in enumerate(hoy) if v["l"] < asia["bajo"])
        i_alto = next(k for k, v in enumerate(hoy) if v["h"] > asia["alto"])
        primero = "bajo" if i_bajo < i_alto else "alto"
    else:
        primero = "bajo" if barrio_bajo else "alto"

    p.lado_manipulado = primero
    p.direccion_esperada = "alcista" if primero == "bajo" else "bajista"
    # ¿Ya arranco la distribucion? Si el precio volvio dentro del rango y
    # lo paso del otro lado, el movimiento real esta en marcha.
    if primero == "bajo" and precio > (asia["alto"] + asia["bajo"]) / 2:
        p.fase = "distribucion"
        p.detalle = ("manipulacion abajo completada y el precio recupero el "
                     "rango: el movimiento real es alcista")
    elif primero == "alto" and precio < (asia["alto"] + asia["bajo"]) / 2:
        p.fase = "distribucion"
        p.detalle = ("manipulacion arriba completada y el precio perdio el "
                     "rango: el movimiento real es bajista")
    else:
        p.fase = "manipulacion"
        p.detalle = (f"barrio el extremo {primero} de Asia pero aun no "
                     "recupero el rango: la distribucion no arranco")

    if sesgo and p.direccion_esperada and sesgo != p.direccion_esperada:
        p.detalle += (f" — OJO: el sesgo de HTF es {sesgo} y el AMD apunta "
                      f"{p.direccion_esperada}")
    return p
