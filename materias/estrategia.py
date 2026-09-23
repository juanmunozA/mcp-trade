"""
"Barrida de sesion en XAUUSD": la estrategia completa del capitulo 20.2.

QUE ES ESTO
───────────
La traduccion literal a codigo de la tabla del capitulo 20.2 de la guia,
componente por componente. No hay reglas agregadas, ni "mejoras", ni
parametros inventados: si un numero aparece aca es porque aparece en la
guia, y la constante lleva la cita.

La logica es la del capitulo 11: contexto -> zona -> liquidez ->
confirmacion -> ejecucion -> gestion. En esta estrategia concreta:

    1. SESGO      estructura de 4H. En rango, no se opera.
    2. VENTANA    solo London Killzone y NY AM.
    3. BARRIDA    una vela de 5M toma la liquidez de referencia.
    4. CAMBIO     en 60 min, cierre de 5M rompe el swing que origino
                  la caida, con desplazamiento >= 1,2x ATR(14) de 5M.
    5. FVG        ese desplazamiento deja un hueco; se entra en su borde.
    6. RIESGO     SL detras de la barrida; TP en liquidez opuesta a >= 2R.

POR QUE CADA CONDICION PUEDE DECIR QUE NO
El checklist del capitulo 11.9 termina con "si alguna es no -> no hay
operacion". Aca eso es literal: cada paso devuelve su motivo de rechazo
y se guarda. Medir POR QUE no se opero vale tanto como medir las
operaciones: si el 90% de los rechazos son por "sin sesgo de 4H", el
problema no es la entrada sino el filtro.

LO QUE LA GUIA ADVIERTE Y HAY QUE RECORDAR
"Esta estrategia es un punto de partida para probar, no una garantia de
rentabilidad. Con un R:R minimo de 2, basta un win rate sostenido
superior a ~35-38% (incluyendo costos) para tener expectativa positiva.
Tu tarea es medir si lo logra."
"""

from __future__ import annotations

from datetime import datetime, timedelta

from materias import estructura as E
from materias import noticias as NOT
from materias import sesiones as S
from materias.senal import Senal

MIN_RR = 2.0
"""TP en liquidez opuesta "si esta a >= 2R. Si esta a < 2R, no operar"."""

DESPLAZAMIENTO_ATR = 1.2
"""«al menos una vela > 1,2 x ATR(14) de 5M (desplazamiento)»."""

MAX_SL_ATR15 = 1.5
"""«Si el SL supera 1,5 x ATR(14) de 15M, la operacion se descarta»."""

MARGEN_SL = 0.30
"""
«Minimo de la barrida - 0,30 USD (margen de spread)».

Ese 0,30 es el de la guia PARA EL ORO, que cotiza sobre 4.000. Aplicarlo
igual a gas natural (~2,85) seria un colchon del 10% del precio, y a
cobre (~6,83) del 4%. Cada activo trae el suyo en `datos.ACTIVOS`,
escalado a lo que vale su tick; este queda de respaldo.
"""

MIN_SL_ATR15 = 0.25
"""
El stop tiene que ser mayor que el ruido. Suelo, no techo.

`MAX_SL_ATR15` limita el stop por arriba porque la guia lo pide
(«si el SL supera 1,5 x ATR(14) de 15M, la operacion se descarta»).
Nada lo limitaba por abajo, y eso resulto ser el agujero mas grande del
sistema. Medido sobre 157 operaciones:

    Petroleo   entrada 82,5400  SL 82,5500   distancia 0,01 = 1 TICK
               costo de la operacion: 1,400R  (140 % del riesgo)
    Cobre      entrada  6,7305  SL  6,7315   distancia 0,001 = 2 ticks
               costo: 0,660R
    Cobre      entrada  6,5930  SL  6,5865   objetivo 41,9R -> +41,82R

Ese ultimo trade solo valia el 45 % del resultado total. Y las 17
operaciones con objetivo mayor a 5R -- el 11 % de la muestra -- aportaban
+60,63R de los +93,04R. Todas eran lo mismo: un stop tan pegado a la
entrada que cualquier nivel de liquidez queda a decenas de R.

Un stop de un tick no es un stop. El precio no lo respeta, el spread no
lo deja existir y el backtest lo cuenta como un riesgo que nadie puede
tomar. El capitulo 20.4 lo tiene en su lista de errores del principiante,
el numero 9: «Ignorar el spread, comisiones y swap al evaluar
resultados».

EL NUMERO SALE DE LA GUIA, NO DE AJUSTARLO
El capitulo 2.3 fija el suelo del ruido: «una vela de 0,3 x ATR es
RUIDO». El capitulo 16.2 dice que el ATR es «imprescindible para colocar
stops (1-2 x ATR)». Un stop por debajo del ruido lo barre el ruido.
0,25 es algo mas permisivo que ese 0,3 porque un stop estructural --
detras de una barrida concreta -- puede ser legitimamente mas ajustado
que uno calculado con ATR; por debajo de eso ya no.
"""

MINUTOS_CAMBIO = 60
"""«En los siguientes 60 minutos» tras la barrida."""

MINUTOS_VIGENCIA = 45
"""«Expira a los 45 minutos o al final de la killzone»."""

RIESGO_PCT = 0.5
"""«0,5 % por operacion»."""

MAX_OPERACIONES_DIA = 2
"""«Maximo 2 operaciones por dia»."""

MAX_PERDIDAS_DIA = 2
"""«Parar tras 2 perdidas o -1 % diario»."""

MAX_PERDIDA_PCT_DIA = 1.0

VENTANAS_202 = ("london_kz", "ny_am_kz")
"""«Solo London Killzone (02:00-05:00 ET) y NY AM (08:30-11:00 ET)»."""


def _niveles_liquidez(ventana: str, ctx: dict) -> list[tuple[str, float, float]]:
    """
    Que liquidez se vigila segun la ventana (cap. 20.2).

    «Maximo/minimo de Asia (para Londres); maximo/minimo de Londres y
    PDH/PDL (para NY)». En NY se conserva ademas Asia porque sigue
    siendo liquidez sin barrer si Londres no la toco.
    """
    a = ctx.get("asia", {})
    n = ctx.get("niveles", {})
    out = []
    if a.get("alto") is not None:
        out.append(("asia_alto", a["alto"], a["bajo"]))
    if ventana == "ny_am_kz":
        lon = ctx.get("londres", {})
        if lon.get("alto") is not None:
            out.append(("londres_alto", lon["alto"], lon["bajo"]))
        if "pdh" in n:
            out.append(("pd", n["pdh"], n["pdl"]))
    return out


def _tomo_liquidez(vs: list, nivel: float, lado: str):
    """
    La PRIMERA vela de la ventana que perforo el nivel, con mecha o cuerpo.

    Tiene que ser la primera y no la ultima. La condicion 2 pide que el
    cambio de estructura ocurra "en los siguientes 60 minutos", asi que
    si se toma la barrida mas reciente -- que suele ser la vela actual --
    no quedan velas posteriores donde ese cambio pueda haber pasado.

    Medido: devolviendo la mas reciente, 139 de 194 barridas detectadas
    morian en "barrida sin velas posteriores todavia". El setup completo
    necesita que la barrida ya haya ocurrido y que el mercado haya tenido
    tiempo de reaccionar.
    """
    for v in vs:
        if lado == "compra" and v.l < nivel:
            return v
        if lado == "venta" and v.h > nivel:
            return v
    return None


def evaluar(simbolo: str, ctx: dict) -> Senal:
    """
    El checklist completo, en orden, parando en el primer NO.

    `ctx` trae las velas ya cargadas por temporalidad y los niveles ya
    calculados. Devuelve una Senal con `hay=True` solo si las seis
    condiciones se cumplen.
    """
    s = Senal(simbolo=simbolo, setup="20.2")
    ahora: datetime = ctx["ahora"]

    # ── 1. VENTANA ────────────────────────────────────────────────────
    #
    # `OPERABLES` se amplio a cuatro ventanas para que quepan los modelos
    # del capitulo 11 que operan fuera de estas dos. Esta estrategia no
    # se amplia con ellas: el capitulo 20.2 dice "solo London Killzone y
    # NY AM", y un setup perfecto a las 13:00 no se opera CON ESTE
    # MODELO, aunque otro modelo si pueda tomarlo.
    est = S.estado(ahora)
    if est["ventana_operable"] not in VENTANAS_202:
        s.motivo = (f"fuera de ventana de esta estrategia (son las "
                    f"{est['hora_ny']} ET; proxima: {S.proxima_operable(ahora)})")
        return s
    s.ventana = est["ventana_operable"]
    # El filtro de noticias propio de esta estrategia (cap. 20.2), que es
    # mas estricto que la regla general de 15/30 minutos del capitulo 15.
    bloq = NOT.filtro_202(ahora, simbolo)
    if bloq:
        s.motivo = bloq
        return s
    s.pasos.append(f"ventana={s.ventana}")

    # ── 2. SESGO de 4H ────────────────────────────────────────────────
    v4 = E.velas(ctx["h4"])
    e4 = E.leer_estructura(v4)
    if e4.tendencia == "rango":
        s.motivo = "4H en rango sin quiebre claro: la guia dice no operar"
        return s
    s.sesgo = e4.tendencia
    lado = "compra" if e4.tendencia == "alcista" else "venta"
    s.lado = lado
    s.pasos.append(f"sesgo_4h={e4.tendencia} ({e4.ultimo_evento})")

    # ── 3. ZONA (se informa, NO filtra) ───────────────────────────────
    #
    # El capitulo 4.4 dice que en sesgo alcista solo se compra en
    # discount, y es un buen principio general. Pero la tabla del
    # capitulo 20.2 -- que es LA estrategia -- no lo incluye entre sus
    # condiciones: son sesgo, niveles, barrida, cambio, FVG, SL y TP.
    #
    # Ponerlo como filtro rechazaba el 88% de las evaluaciones y dejaba
    # el backtest en cero senales. Y tiene sentido que sobre: despues de
    # barrer el minimo de referencia en sesgo alcista ya estas, por
    # construccion, en la parte baja del rango reciente.
    #
    # Se calcula igual y se guarda, para poder medir despues si filtrar
    # por zona hubiera mejorado el resultado.
    v5 = E.velas(ctx["m5"])
    if len(v5) < 30:
        s.motivo = "faltan velas de 5M"
        return s
    precio = v5[-1].c
    if e4.ultimo_alto and e4.ultimo_bajo:
        pd = E.premium_discount(e4.ultimo_alto, e4.ultimo_bajo, precio)
        s.pasos.append(f"zona={pd['zona']} ({pd['pct']:.0%})")

    # ── 4. BARRIDA de liquidez ────────────────────────────────────────
    a5 = E.atr(v5)
    if a5 <= 0:
        s.motivo = "ATR de 5M en cero"
        return s
    corte = ahora - timedelta(minutes=MINUTOS_CAMBIO + 30)
    recientes = [v for v in v5 if ctx["dt5"][v.i] >= corte]
    if len(recientes) < 5:
        s.motivo = "sin velas recientes suficientes"
        return s

    barrido = None
    for nombre, alto, bajo in _niveles_liquidez(s.ventana, ctx):
        # En sesgo alcista se busca barrida del MINIMO (sell-side).
        nivel = bajo if lado == "compra" else alto
        if nivel is None:
            continue
        # «una vela de 5M supera con mecha o cuerpo el minimo de
        # referencia» (cap. 20.2, condicion 1). Es mas amplio que la
        # barrida del capitulo 6.2, que exige que el cuerpo vuelva
        # adentro: esa distincion sirve para separar barrida de ruptura
        # en el analisis general, pero esta estrategia pide solo que la
        # liquidez haya sido tomada. Lo que despues separa la trampa de
        # la ruptura real es la condicion 2, el cambio de estructura.
        v = _tomo_liquidez(recientes, nivel, lado)
        if v:
            barrido = (nombre, nivel, v)
            break
    if not barrido:
        s.motivo = "no hubo barrida de liquidez de referencia en la ultima hora"
        return s
    s.nombre_nivel, s.nivel_barrido, vela_barrida = barrido
    s.pasos.append(f"barrida={s.nombre_nivel}@{s.nivel_barrido:.2f} "
                   f"en {ctx['dt5'][vela_barrida.i]:%H:%M}")

    # ── 5. CAMBIO DE ESTRUCTURA con desplazamiento ────────────────────
    post = [v for v in v5 if v.i > vela_barrida.i
            and (ctx["dt5"][v.i] - ctx["dt5"][vela_barrida.i]).total_seconds()
            <= MINUTOS_CAMBIO * 60]
    if not post:
        s.motivo = "barrida sin velas posteriores todavia"
        return s
    previos = [v for v in v5 if v.i <= vela_barrida.i][-40:]
    sw = E.swings(previos)
    if lado == "compra":
        ref = [x for x in sw if x.tipo == "high"]
        if not ref:
            s.motivo = "sin swing high previo para medir el cambio"
            return s
        nivel_cambio = ref[-1].precio
        rompio = next((v for v in post if v.c > nivel_cambio), None)
    else:
        ref = [x for x in sw if x.tipo == "low"]
        if not ref:
            s.motivo = "sin swing low previo para medir el cambio"
            return s
        nivel_cambio = ref[-1].precio
        rompio = next((v for v in post if v.c < nivel_cambio), None)
    if not rompio:
        s.motivo = (f"barrio {s.nombre_nivel} pero no rompio el swing "
                    f"{nivel_cambio:.2f} en {MINUTOS_CAMBIO} min")
        return s
    desplaz = [v for v in post if v.i <= rompio.i
               and v.cuerpo >= DESPLAZAMIENTO_ATR * a5]
    if not desplaz:
        s.motivo = (f"rompio el swing pero sin desplazamiento "
                    f"(ninguna vela >= {DESPLAZAMIENTO_ATR}x ATR de {a5:.2f})")
        return s
    s.pasos.append(f"cambio_estructura@{nivel_cambio:.2f} con desplazamiento "
                   f"{max(v.cuerpo for v in desplaz):.2f}")

    # ── 6. FVG del desplazamiento ─────────────────────────────────────
    tramo = [v for v in v5 if vela_barrida.i <= v.i <= rompio.i + 1]
    cand = [f for f in E.fvgs(tramo)
            if f.tipo == ("alcista" if lado == "compra" else "bajista")
            and not f.invalidado]
    if not cand:
        s.motivo = "el desplazamiento no dejo FVG utilizable"
        return s
    # «Si hay varios, usar el mas cercano al swing roto».
    f = min(cand, key=lambda x: abs(x.ce - nivel_cambio))
    s.fvg = {"tipo": f.tipo, "tope": round(f.tope, 4), "piso": round(f.piso, 4),
             "ce": round(f.ce, 4), "tamano": round(f.tamano, 4)}
    s.zona = dict(s.fvg)
    # «Buy Limit en el borde superior del FVG» (el que toca al volver).
    s.entrada = round(f.tope if lado == "compra" else f.piso, 4)
    s.pasos.append(f"fvg={f.piso:.2f}-{f.tope:.2f}")

    # ── 7. STOP LOSS ──────────────────────────────────────────────────
    margen = ctx.get("margen_sl", MARGEN_SL)
    if lado == "compra":
        s.sl = round(vela_barrida.l - margen, 4)
    else:
        s.sl = round(vela_barrida.h + margen, 4)
    dist = abs(s.entrada - s.sl)
    a15 = E.atr(E.velas(ctx["m15"]))
    if a15 > 0 and dist > MAX_SL_ATR15 * a15:
        s.motivo = (f"SL de {dist:.2f} supera {MAX_SL_ATR15}x ATR de 15M "
                    f"({a15:.2f}): se descarta")
        return s
    if a15 > 0 and dist < MIN_SL_ATR15 * a15:
        s.motivo = (f"SL de {dist:.4f} por debajo de {MIN_SL_ATR15}x ATR de 15M "
                    f"({a15:.4f}): esta dentro del ruido")
        return s
    if dist <= 0:
        s.motivo = "distancia al SL en cero"
        return s
    s.pasos.append(f"sl={s.sl:.2f} (dist {dist:.2f})")

    # ── 8. TAKE PROFIT en liquidez opuesta, minimo 2R ─────────────────
    #
    # El objetivo es el EXTREMO OPUESTO del rango cuyo nivel se barrio
    # -- «Extremo opuesto del rango de Asia (TP1) y PDH/PDL (TP2)»
    # (cap. 11.4) -- no simplemente el nivel mas cercano que quede en
    # direccion de la ganancia.
    #
    # La diferencia importa: si se barrio el minimo de Asia, el destino
    # logico es el maximo de Asia, porque ahi esta la liquidez del otro
    # lado. Tomar "el mas cercano" elegia a veces un nivel pegado a la
    # entrada y descartaba la operacion por no llegar a 2R cuando el
    # objetivo real si llegaba.
    a = ctx.get("asia", {})
    n = ctx.get("niveles", {})
    lon = ctx.get("londres", {})
    opuestos: list[tuple[str, float]] = []
    if s.nombre_nivel == "asia_alto" and a:
        opuestos.append(("asia_opuesto",
                         a["alto"] if lado == "compra" else a["bajo"]))
    elif s.nombre_nivel == "londres_alto" and lon:
        opuestos.append(("londres_opuesto",
                         lon["alto"] if lado == "compra" else lon["bajo"]))
    elif s.nombre_nivel == "pd":
        opuestos.append(("pd_opuesto",
                         n.get("pdh") if lado == "compra" else n.get("pdl")))
    # Secundarios, en el orden que la guia sugiere.
    for nom, val in (("pdh" if lado == "compra" else "pdl",
                      n.get("pdh") if lado == "compra" else n.get("pdl")),
                     ("pwh" if lado == "compra" else "pwl",
                      n.get("pwh") if lado == "compra" else n.get("pwl"))):
        opuestos.append((nom, val))
    for eq in (ctx.get("eqh", []) if lado == "compra" else ctx.get("eql", [])):
        opuestos.append(("eq", eq))

    dist_min = MIN_RR * dist
    elegido = None
    for nom, val in opuestos:
        if val is None:
            continue
        if lado == "compra" and val <= s.entrada:
            continue
        if lado == "venta" and val >= s.entrada:
            continue
        if abs(val - s.entrada) >= dist_min:
            elegido = (nom, val)
            break
    if not elegido:
        cercanos = [(nom, val) for nom, val in opuestos if val is not None
                    and ((lado == "compra" and val > s.entrada)
                         or (lado == "venta" and val < s.entrada))]
        if not cercanos:
            s.motivo = "no hay liquidez opuesta identificable como objetivo"
            return s
        nom, val = min(cercanos, key=lambda x: abs(x[1] - s.entrada))
        s.motivo = (f"la liquidez opuesta esta a {abs(val - s.entrada) / dist:.2f}R "
                    f"({nom}), menos de {MIN_RR}R: no se opera")
        return s
    nom_tp, tp = elegido
    s.tp = round(tp, 4)
    s.rr = round(abs(tp - s.entrada) / dist, 2)
    s.pasos.append(f"tp={nom_tp}@{s.tp} ({s.rr}R)")

    s.expira = str(ahora + timedelta(minutes=MINUTOS_VIGENCIA))
    s.hay = True
    s.motivo = "checklist completo"
    return s


def lotes(capital: float, riesgo_pct: float, dist_precio: float,
          tamano_contrato: float) -> float:
    """
    Tamano de posicion (cap. 14.2).

    Lotes = Riesgo en dinero / (Distancia al SL en precio x Tamano del
    contrato). Es la formula tal cual, y es lo que hace que refinar la
    entrada aumente el tamano sin aumentar el riesgo.
    """
    if dist_precio <= 0 or tamano_contrato <= 0:
        return 0.0
    return round((capital * riesgo_pct / 100) / (dist_precio * tamano_contrato), 2)


def evaluar_todo(simbolo: str, ctx: dict) -> tuple[Senal, list[Senal]]:
    """
    Los nueve modelos: la estrategia del 20.2 primero, luego el capitulo 11.

    POR QUE 20.2 VA PRIMERO
    Es la unica que la guia desarrolla entera -- con sus niveles, su
    tamano de stop, su vigencia y sus limites diarios escritos -- y por
    eso es la que tiene menos margen de interpretacion mia. Cuando
    coincide con otro modelo sobre la misma vela, se prefiere la version
    que la guia especifico.

    Devuelve la senal elegida y todos los intentos, para poder contar
    despues cual modelo aporta y cual solo hace ruido (cap. 19.1).
    """
    from materias import setups as U

    def _202(sim: str, c: dict, base: dict) -> Senal:
        # El 20.2 arma su propio contexto: se le pasa el `ctx` crudo y se
        # ignora el `base` comun. Mantenerlo intacto es deliberado --
        # es el unico modelo verificado contra la letra de la guia.
        return evaluar(sim, c)

    return U.evaluar_todos(simbolo, ctx, extra=[("20.2", _202)])
