"""
El checklist universal del capitulo 11.9, aplicado a toda senal.

LA REGLA TAL CUAL
─────────────────
1. ¿Tengo un sesgo claro de 1D/4H? ¿La operacion va a favor o es un
   contra-tendencia planificado?
2. ¿El precio esta en una zona de mi plan (OB/FVG/soporte-resistencia) y
   en premium/discount correcto?
3. ¿Se barrio liquidez antes de la senal?
4. ¿Hay cambio de estructura con desplazamiento en la temporalidad de
   ejecucion?
5. ¿Estoy dentro de una killzone y fuera de la ventana de noticias de
   alto impacto?
6. ¿El objetivo esta a minimo 2R y hay una liquidez clara alli?
7. ¿Calcule el tamano de lote para arriesgar exactamente mi % definido?
8. ¿Ya alcance mi limite de perdidas o de operaciones del dia?
9. Si todas son "si" -> ejecuto. Si alguna es "no" -> no hay operacion.

QUE HACE ESTE ARCHIVO Y QUE NO
Los nueve modelos del capitulo 11 ya comprueban los puntos 1, 3, 4 y 6
por dentro: cada uno a su manera, porque ahi esta su diferencia. Lo que
faltaba era el resto -- noticias, lotes, limites diarios -- y una
distincion que la guia hace y el codigo no hacia:

    PUNTOS QUE VETAN      5 (noticias) y 8 (limites): si son "no", no hay
                          operacion, por perfecto que sea el setup.
    PUNTOS QUE PUNTUAN    2 (zona correcta), y las confluencias que la
                          guia menciona a lo largo del texto: OTE, SMT,
                          volumen, divergencia de RSI, inducement barrido.

El punto 2 merece explicacion. El capitulo 4.4 dice que en sesgo alcista
solo se compra en discount. Ponerlo como veto rechazaba el 88 % de las
evaluaciones y dejaba el backtest en cero senales, porque la estrategia
del capitulo 20.2 no lo lista entre sus condiciones. Asi que puntua en
vez de vetar: se cuenta, se guarda, y despues se mide si filtrar por zona
habria mejorado el resultado. Medirlo es la unica forma honesta de
decidirlo, que es lo que el capitulo 13.2 exige para la gestion y vale
igual para los filtros.

PARA QUE SIRVE LA PUNTUACION
Para ordenar, no para decidir. Cuando dos modelos disparan sobre la
misma vela -- o cuando hay senal en tres activos correlacionados y el
capitulo 14.1 solo permite el riesgo de uno -- hace falta un criterio
para elegir cual. El numero de confluencias es ese criterio.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from materias import estructura as E
from materias import gestion as G
from materias import indicadores as I
from materias import noticias as N
from materias.senal import Senal

OTE_MIN, OTE_MAX, OTE_CENTRO = 0.618, 0.786, 0.705
"""«0,618-0,786 (0,705 central) — OTE, Optimal Trade Entry» (cap. 17)."""

CONTINUACION = ("11.2", "11.3", "11.8")
"""
Los modelos que operan A FAVOR del movimiento en curso (cap. 11.2, 11.3).

El resto -- 11.1, 11.4, 11.5, 11.6, 11.7 y el 20.2 -- arrancan con una
barrida de liquidez y buscan el GIRO. Son de reversion.

ESTA DISTINCION NO ES COSMETICA
La primera version contaba todas las confluencias por igual, y el
backtest mostro lo que pasa con eso:

    ADX > 25 «hay tendencia»      con ella -0,101R   sin ella +0,191R
    precio del lado de la EMA 200 con ella +0,047R   sin ella +0,113R
    el diario acompana al 4H      con ella -0,037R   sin ella +0,143R

    divergencia de RSI alcista    con ella +0,203R   sin ella +0,048R
    entrada en OTE                con ella +0,181R   sin ella +0,048R
    zona discount para compra     con ella +0,181R   sin ella +0,049R

Las tres primeras son indicadores de TENDENCIA. Las tres ultimas son de
AGOTAMIENTO. Sumarlas al mismo puntaje en un sistema cuyo 94 % de
senales son de reversion no solo no ayudaba: empeoraba la seleccion,
porque una tendencia fuerte a favor es una MALA noticia para quien
apuesta al giro.

El capitulo 16.3 ya lo dice: «maximo 2-3 indicadores, cada uno con una
FUNCION CONCRETA EN TU PLAN». La funcion depende del plan, y aqui hay
dos planes distintos conviviendo.
"""

TENDENCIA = ("ema200", "adx", "diario", "vwap")
"""Etiquetas de confluencia que solo cuentan para modelos de continuacion."""


@dataclass
class Evaluacion:
    aprobada: bool = False
    veto: str = ""
    puntos: float = 0.0
    maximo: float = 0.0
    confluencias: list[str] = field(default_factory=list)
    faltantes: list[str] = field(default_factory=list)
    lotes: float = 0.0
    riesgo_usd: float = 0.0

    def dict(self) -> dict:
        return self.__dict__.copy()


def fibonacci(inicio: float, fin: float) -> dict:
    """
    Los niveles del capitulo 17 sobre un impulso.

    «Se dibujan desde el inicio al final del impulso (en alcista: del
    minimo al maximo).» Y la regla que lo acompana: «Fibonacci NO es una
    senal: es una regla para medir si el retroceso esta en zona barata o
    cara. Usalo siempre sobre el impulso que produjo el ULTIMO BOS, no
    sobre movimientos arbitrarios.»
    """
    d = fin - inicio
    if d == 0:
        return {}
    niv = {}
    for r in (0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1.0):
        niv[str(r)] = round(fin - d * r, 4)
    # Extensiones: «-0,27 / -0,62 (1,27 / 1,618) objetivos de toma de
    # beneficio» y «-1 / -2, proyeccion tras manipulacion».
    for r in (1.27, 1.618, 2.0):
        niv[f"ext_{r}"] = round(inicio + d * r, 4)
    return niv


def en_ote(precio: float, inicio: float, fin: float) -> tuple[bool, float]:
    """Si el precio esta en la zona OTE del impulso, y en que fraccion."""
    d = fin - inicio
    if d == 0:
        return False, 0.0
    frac = (fin - precio) / d
    return (OTE_MIN <= frac <= OTE_MAX), round(frac, 3)


def evaluar(s: Senal, ctx: dict, estado_dia: G.EstadoDia | None = None,
            capital: float = 10000.0, lectura: I.Lectura | None = None,
            smt=None) -> Evaluacion:
    """
    Corre el checklist completo sobre una senal ya construida por su modelo.

    Devuelve una Evaluacion; si `veto` trae texto, la senal no se opera
    aunque su modelo la haya dado por buena. El resto es puntuacion.
    """
    ev = Evaluacion()
    if not s.hay:
        ev.veto = s.motivo
        return ev
    ahora = ctx["ahora"]

    # ══ VETOS ═════════════════════════════════════════════════════════

    # Punto 5, segunda mitad: fuera de la ventana de noticias.
    bloq = N.bloqueo(ahora, s.simbolo)
    if bloq:
        ev.veto = f"noticia de alto impacto: {bloq}"
        return ev

    # Punto 8: limites del dia (cap. 14.1).
    if estado_dia is not None:
        freno = G.puede_operar(estado_dia)
        if freno:
            ev.veto = f"limite alcanzado: {freno}"
            return ev

    # Punto 7: el lote tiene que poder calcularse con el riesgo definido.
    dist = abs(s.entrada - s.sl)
    contrato = ctx.get("contrato", 1)
    ev.lotes = G.lotes(capital, G.RIESGO_POR_OPERACION, dist, contrato)
    ev.riesgo_usd = round(ev.lotes * dist * contrato, 2)
    if ev.lotes <= 0:
        ev.veto = "no se puede calcular un lote valido para el riesgo definido"
        return ev

    # Veto de indicadores: solo para modelos de continuacion (cap. 16.3).
    if lectura is not None:
        v = I.veto(lectura, s.lado, s.setup)
        if v:
            ev.veto = v
            return ev

    ev.aprobada = True

    # ══ CONFLUENCIAS ══════════════════════════════════════════════════
    # Cada una vale 1. No hay pesos inventados: la guia no los da, y
    # ponerlos seria sobreajuste con pasos extra.
    de_continuacion = s.setup in CONTINUACION

    def si(cond: bool, texto: str, familia: str = "ambas") -> None:
        """
        Suma una confluencia, si aplica a la familia del modelo.

        `familia` puede ser 'tendencia' (solo cuenta en continuacion),
        'reversion' (solo cuenta en reversion) o 'ambas'. Las que no
        aplican se calculan igual y se guardan en `faltantes` con un
        prefijo, porque su valor sigue siendo informacion -- solo no
        entra en el puntaje que decide.
        """
        aplica = (familia == "ambas"
                  or (familia == "tendencia" and de_continuacion)
                  or (familia == "reversion" and not de_continuacion))
        if not aplica:
            ev.faltantes.append(f"[no aplica a {s.setup}] {texto}")
            return
        ev.maximo += 1
        (ev.confluencias if cond else ev.faltantes).append(texto)

    v5 = E.velas(ctx["m5"])
    precio = v5[-1].c if v5 else s.entrada

    # Punto 2: premium/discount correcto (cap. 4.4).
    e4 = E.leer_estructura(E.velas(ctx.get("h4", [])))
    zona_ok = False
    if e4.ultimo_alto and e4.ultimo_bajo:
        pd = E.premium_discount(e4.ultimo_alto, e4.ultimo_bajo, precio)
        zona_ok = ((s.lado == "compra" and pd["zona"] == "discount")
                   or (s.lado == "venta" and pd["zona"] == "premium"))
        si(zona_ok, f"zona {pd['zona']} correcta para {s.lado}", "reversion")
        # OTE sobre el mismo tramo (cap. 17).
        ini, fin = ((e4.ultimo_bajo, e4.ultimo_alto) if s.lado == "compra"
                    else (e4.ultimo_alto, e4.ultimo_bajo))
        dentro, frac = en_ote(s.entrada, ini, fin)
        si(dentro, f"entrada en OTE ({frac} del impulso; zona {OTE_MIN}-{OTE_MAX})",
           "reversion")

    # Sesgo de 1D, no solo de 4H (punto 1 pide «1D/4H»).
    vd = E.velas(ctx.get("diario", []))
    if len(vd) >= 20:
        ed = E.leer_estructura(vd)
        si(ed.tendencia == s.sesgo,
           f"el diario acompana al 4H ({ed.tendencia})", "tendencia")

    # Volumen detras del movimiento (cap. 9.3).
    if v5:
        vr = E.volumen_relativo(v5)
        si(vr >= 1.3, f"volumen {vr:.1f}x sobre su media en la confirmacion")
        # El volumen cuenta para las dos familias: el capitulo 9.3 lo usa
        # tanto para validar rupturas como para marcar climax de giro.

    # SMT divergence (cap. 15.5).
    if smt is not None:
        esperado = "alcista" if s.lado == "compra" else "bajista"
        si(bool(getattr(smt, "hay", False)) and smt.tipo == esperado,
           f"SMT {esperado} contra {getattr(smt, 'pareja', '?')}", "reversion")

    # Divergencia de RSI (cap. 16.3: «divergencia sola no basta; con
    # barrida tiene contexto» -- aqui SIEMPRE viene con barrida).
    if lectura is not None:
        esperado = "alcista" if s.lado == "compra" else "bajista"
        si(lectura.divergencia == esperado, f"divergencia de RSI {esperado}",
           "reversion")
        # EMA 200 a favor, como confluencia cuando no es veto.
        if lectura.ema200_1h > 0:
            a_favor = ((s.lado == "compra" and lectura.precio_sobre_ema200)
                       or (s.lado == "venta" and not lectura.precio_sobre_ema200))
            si(a_favor, "precio del lado correcto de la EMA 200 de 1H",
               "tendencia")
        # Regimen de tendencia (cap. 16.2).
        if lectura.adx:
            si(lectura.adx.get("adx", 0) > 25,
               f"ADX {lectura.adx.get('adx')} > 25: hay tendencia", "tendencia")
        # VWAP como referencia institucional intradia (cap. 16.3).
        if lectura.vwap:
            a_favor = ((s.lado == "compra" and lectura.vwap.get("precio_sobre"))
                       or (s.lado == "venta" and not lectura.vwap.get("precio_sobre")))
            si(a_favor, "precio del lado correcto del VWAP de la sesion",
               "tendencia")

    # NO se cuenta «objetivo a mas de 3R».
    #
    # Era una suposicion mia, no de la guia: el capitulo 11.9 pide
    # «minimo 2R», y en ningun lado dice que mas sea mejor. Medido, era
    # lo contrario: las senales con objetivo >3R dieron -0,114R y las de
    # 2-3R dieron +0,123R. Tiene sentido -- un objetivo mas lejos se
    # alcanza menos veces y da mas tiempo a que la operacion se tuerza.
    # Se deja anotado para poder seguir midiendolo, sin puntuar.
    if s.rr >= 3.0:
        ev.faltantes.append(f"[medido, no puntua] objetivo a {s.rr}R")

    ev.puntos = len(ev.confluencias)
    s.confluencias = list(ev.confluencias)
    s.calidad = round(ev.puntos / ev.maximo, 3) if ev.maximo else 0.0
    return ev


def elegir(cands: list[tuple[Senal, Evaluacion]],
           abiertas: list[dict] | None = None) -> list[tuple[Senal, Evaluacion]]:
    """
    De varias senales simultaneas, cuales se toman.

    Dos reglas, ambas del capitulo 14.1:

    1. «Riesgo total abierto: no superar 2-3 % sumando todas las
       posiciones, CONSIDERANDO CORRELACION (oro y plata largos a la vez
       = aproximadamente la misma operacion)». Se van aceptando por
       calidad descendente hasta que la exposicion efectiva toca el tope.

    2. Cuando dos compiten por el mismo cupo, gana la de mas
       confluencias. Es el unico criterio que el capitulo 11.9 ofrece
       para comparar setups entre si.
    """
    abiertas = list(abiertas or [])
    orden = sorted([c for c in cands if c[1].aprobada],
                   key=lambda c: (-c[1].puntos, -c[0].rr))
    tomadas: list[tuple[Senal, Evaluacion]] = []
    for s, ev in orden:
        nueva = {"simbolo": s.simbolo, "lado": s.lado,
                 "riesgo_pct": G.RIESGO_POR_OPERACION}
        exp = G.exposicion(abiertas, nueva)
        if exp["excede"]:
            ev.veto = (f"riesgo total abierto llegaria a {exp['efectivo']}% "
                       f"contando correlacion (tope {exp['tope']}%)")
            ev.aprobada = False
            continue
        abiertas.append(nueva)
        tomadas.append((s, ev))
    return tomadas
