"""
Las ocho estrategias del capitulo 11, cada una como un setup separado.

POR QUE OCHO Y NO UNA
─────────────────────
El capitulo 20.2 arma UNA estrategia concreta como ejemplo de como se
integra todo. Pero el capitulo 11 lista ocho modelos, y la guia los
presenta como plantillas que comparten la misma logica -- contexto,
zona, liquidez, confirmacion, ejecucion, gestion -- cambiando la
combinacion de elementos.

Implementar solo la del 20.2 daba 11 senales en un mes sobre cinco
activos: demasiado poco para el backtest de 100-300 operaciones que pide
el capitulo 18.7. Con los ocho modelos cada uno aporta sus momentos y la
muestra alcanza para medir.

COMO CONVIVEN
Cada setup declara en que ventanas aplica y que condiciones pide. El
evaluador los prueba en orden de calidad -- primero los que la guia
describe con mas confluencia -- y devuelve el primero que se cumple,
anotando cual fue. Asi despues se puede medir cual modelo funciona y
cual es cuento, que es justo lo que el capitulo 19.1 pide registrar.

LO QUE TODOS COMPARTEN
El checklist universal del capitulo 11.9 se aplica a todos: sesgo claro,
zona del plan, liquidez barrida, cambio de estructura con desplazamiento,
killzone, objetivo a minimo 2R. Lo que cambia es donde se busca cada
cosa.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from materias import estructura as E
from materias import sesiones as S
from materias.senal import Senal

MIN_RR = 2.0
DESPLAZAMIENTO_ATR = 1.2
MAX_SL_ATR15 = 1.5
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
MINUTOS_VIGENCIA = 45


def _rr(entrada: float, sl: float, tp: float) -> float:
    d = abs(entrada - sl)
    return round(abs(tp - entrada) / d, 2) if d > 0 else 0.0


def _objetivos(lado: str, entrada: float, ctx: dict) -> list[tuple[str, float]]:
    """
    Toda la liquidez que puede servir de objetivo, en orden de cercania.

    Son los niveles que el capitulo 6.1 llama liquidez externa: extremos
    de sesion, del dia y de la semana anteriores, y maximos/minimos
    iguales. El capitulo 11 los nombra como destino en casi todos los
    modelos.
    """
    a, n, lon = ctx.get("asia", {}), ctx.get("niveles", {}), ctx.get("londres", {})
    cand: list[tuple[str, float | None]] = [
        ("asia_alto", a.get("alto")), ("asia_bajo", a.get("bajo")),
        ("londres_alto", lon.get("alto")), ("londres_bajo", lon.get("bajo")),
        ("pdh", n.get("pdh")), ("pdl", n.get("pdl")),
        ("pwh", n.get("pwh")), ("pwl", n.get("pwl")),
    ]
    for eq in (ctx.get("eqh", []) if lado == "compra" else ctx.get("eql", [])):
        cand.append(("eq", eq))
    out = []
    for nom, val in cand:
        if val is None:
            continue
        if lado == "compra" and val > entrada:
            out.append((nom, float(val)))
        elif lado == "venta" and val < entrada:
            out.append((nom, float(val)))
    out.sort(key=lambda x: abs(x[1] - entrada))
    return out


def _mejor_tp(lado: str, entrada: float, sl: float, ctx: dict) -> tuple[str, float] | None:
    """
    El objetivo mas cercano que alcance 2R.

    El capitulo 11 repite en cada modelo que el objetivo tiene que estar
    en liquidez y a minimo 2R. Se toma el mas cercano que cumpla, y no el
    mas lejano: cuanto mas cerca, mas probable que se alcance.
    """
    dist = abs(entrada - sl)
    if dist <= 0:
        return None
    for nom, val in _objetivos(lado, entrada, ctx):
        if abs(val - entrada) >= MIN_RR * dist:
            return nom, val
    return None


def _cierra(s: Senal, entrada: float, sl: float, ctx: dict, ahora: datetime) -> Senal:
    """Aplica los dos filtros finales comunes: tamano de SL y objetivo a 2R."""
    dist = abs(entrada - sl)
    if dist <= 0:
        s.motivo = "distancia al SL en cero"
        return s
    a15 = E.atr(E.velas(ctx.get("m15", [])))
    if a15 > 0 and dist > MAX_SL_ATR15 * a15:
        s.motivo = f"SL de {dist:.4f} supera {MAX_SL_ATR15}x ATR de 15M ({a15:.4f})"
        return s
    if a15 > 0 and dist < MIN_SL_ATR15 * a15:
        s.motivo = (f"SL de {dist:.4f} por debajo de {MIN_SL_ATR15}x ATR de 15M "
                    f"({a15:.4f}): esta dentro del ruido")
        return s
    tp = _mejor_tp(s.lado, entrada, sl, ctx)
    if not tp:
        s.motivo = f"sin liquidez opuesta a {MIN_RR}R o mas"
        return s
    s.entrada, s.sl = round(entrada, 4), round(sl, 4)
    s.tp, s.rr = round(tp[1], 4), _rr(entrada, sl, tp[1])
    s.pasos.append(f"tp={tp[0]}@{s.tp} ({s.rr}R)")
    s.expira = str(ahora + timedelta(minutes=MINUTOS_VIGENCIA))
    s.hay = True
    s.motivo = "checklist completo"
    return s


# ══════════════════════════════════════════════════════════════════════
# 11.1  Barrida de liquidez + CHoCH + entrada en FVG (modelo de reversion)
# ══════════════════════════════════════════════════════════════════════

def barrida_choch_fvg(sim: str, ctx: dict, base: dict) -> Senal:
    """
    «El modelo mas usado en SMC/ICT» (cap. 11.1).

    El precio barre un extremo relevante, cambia la estructura con
    desplazamiento y deja un FVG; se entra en el retroceso a ese hueco.
    Aprovecha a quienes compraron o vendieron la ruptura falsa: sus stops
    alimentan el movimiento contrario.
    """
    s = Senal(simbolo=sim, setup="11.1_barrida_choch_fvg",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    v5, a5, ahora = base["v5"], base["a5"], base["ahora"]
    lado = base["lado"]

    barrido = None
    for nom, alto, bajo in base["niveles"]:
        nivel = bajo if lado == "compra" else alto
        if nivel is None:
            continue
        vb = next((v for v in base["recientes"]
                   if (lado == "compra" and v.l < nivel)
                   or (lado == "venta" and v.h > nivel)), None)
        if vb:
            barrido = (nom, nivel, vb)
            break
    if not barrido:
        s.motivo = "sin barrida de liquidez de referencia"
        return s
    s.nombre_nivel, s.nivel_barrido, vb = barrido
    s.pasos.append(f"barrida={s.nombre_nivel}@{s.nivel_barrido:.4f}")

    post = [v for v in v5 if v.i > vb.i
            and (base["dt"][v.i] - base["dt"][vb.i]).total_seconds() <= MINUTOS_CAMBIO * 60]
    if not post:
        s.motivo = "barrida sin velas posteriores"
        return s
    prev = [v for v in v5 if v.i <= vb.i][-40:]
    sw = E.swings(prev)
    ref = [x for x in sw if x.tipo == ("high" if lado == "compra" else "low")]
    if not ref:
        s.motivo = "sin swing de referencia para el cambio"
        return s
    nivel_cambio = ref[-1].precio
    rompio = next((v for v in post
                   if (lado == "compra" and v.c > nivel_cambio)
                   or (lado == "venta" and v.c < nivel_cambio)), None)
    if not rompio:
        s.motivo = f"no rompio el swing en {MINUTOS_CAMBIO} min"
        return s
    if not any(v.cuerpo >= DESPLAZAMIENTO_ATR * a5 for v in post if v.i <= rompio.i):
        s.motivo = "rompio el swing pero sin desplazamiento"
        return s
    s.pasos.append(f"choch@{nivel_cambio:.4f}")

    tramo = [v for v in v5 if vb.i <= v.i <= rompio.i + 1]
    cand = [f for f in E.fvgs(tramo)
            if f.tipo == ("alcista" if lado == "compra" else "bajista")
            and not f.invalidado]
    if not cand:
        s.motivo = "el desplazamiento no dejo FVG"
        return s
    f = min(cand, key=lambda x: abs(x.ce - nivel_cambio))
    s.zona = {"tipo": "fvg", "piso": round(f.piso, 4), "tope": round(f.tope, 4),
              "ce": round(f.ce, 4)}
    entrada = f.tope if lado == "compra" else f.piso
    sl = (vb.l - base["margen"]) if lado == "compra" else (vb.h + base["margen"])
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.2  Continuacion: BOS + retroceso a FVG / OB
# ══════════════════════════════════════════════════════════════════════

def continuacion_bos(sim: str, ctx: dict, base: dict) -> Senal:
    """
    A favor de tendencia (cap. 11.2).

    Tras un quiebre de estructura, el precio retrocede a la zona que
    origino el impulso. No necesita barrida previa: el gatillo es el BOS
    en 15M/1H que dejo FVG u OB.
    """
    s = Senal(simbolo=sim, setup="11.2_continuacion_bos",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    lado, ahora = base["lado"], base["ahora"]
    v15 = E.velas(ctx.get("m15", []))
    if len(v15) < 30:
        s.motivo = "faltan velas de 15M"
        return s
    e15 = E.leer_estructura(v15)
    # El BOS tiene que ir a favor del sesgo de 4H.
    if e15.ultimo_evento != "BOS" or e15.tendencia != base["sesgo"]:
        s.motivo = f"sin BOS de 15M a favor ({e15.ultimo_evento or 'nada'}/{e15.tendencia})"
        return s
    a15 = E.atr(v15)
    huecos = [f for f in E.fvgs(v15[-40:], minimo=a15 * 0.25)
              if f.tipo == ("alcista" if lado == "compra" else "bajista")
              and not f.invalidado and not f.mitigado]
    obs = [o for o in E.order_blocks(v15[-40:], a15)
           if o.tipo == ("alcista" if lado == "compra" else "bajista")
           and not o.mitigado]
    precio = base["v5"][-1].c
    zonas: list[tuple[str, float, float]] = []
    for f in huecos:
        zonas.append(("fvg", f.tope, f.piso))
    for o in obs:
        zonas.append(("ob", o.tope, o.piso))
    if not zonas:
        s.motivo = "sin FVG ni OB sin mitigar tras el BOS"
        return s
    # La zona tiene que estar en el camino del retroceso, no superada.
    if lado == "compra":
        utiles = [z for z in zonas if z[1] < precio]
        if not utiles:
            s.motivo = "las zonas quedaron por encima del precio"
            return s
        tipo, tope, piso = max(utiles, key=lambda z: z[1])
        entrada, sl = tope, piso - base["margen"]
    else:
        utiles = [z for z in zonas if z[2] > precio]
        if not utiles:
            s.motivo = "las zonas quedaron por debajo del precio"
            return s
        tipo, tope, piso = min(utiles, key=lambda z: z[2])
        entrada, sl = piso, tope + base["margen"]
    s.zona = {"tipo": tipo, "piso": round(piso, 4), "tope": round(tope, 4)}
    s.pasos.append(f"bos_15m + {tipo}@{piso:.4f}-{tope:.4f}")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.3  Retesteo de Order Block de temporalidad alta
# ══════════════════════════════════════════════════════════════════════

def retest_ob_alto(sim: str, ctx: dict, base: dict) -> Senal:
    """
    OB de 4H sin mitigar, con confirmacion en temporalidad baja (cap. 11.3).

    La entrada es el mean threshold del bloque -- su 50%, que la guia
    marca como nivel clave -- y el stop va mas alla del OB completo.
    """
    s = Senal(simbolo=sim, setup="11.3_retest_ob_4h",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    lado, ahora = base["lado"], base["ahora"]
    v4 = E.velas(ctx.get("h4", []))
    if len(v4) < 20:
        s.motivo = "faltan velas de 4H"
        return s
    a4 = E.atr(v4)
    obs = [o for o in E.order_blocks(v4, a4)
           if o.tipo == ("alcista" if lado == "compra" else "bajista")
           and not o.mitigado]
    if not obs:
        s.motivo = "sin OB de 4H sin mitigar"
        return s
    precio = base["v5"][-1].c
    if lado == "compra":
        cerca = [o for o in obs if o.tope < precio]
        if not cerca:
            s.motivo = "los OB de 4H quedaron por encima del precio"
            return s
        ob = max(cerca, key=lambda o: o.tope)
    else:
        cerca = [o for o in obs if o.piso > precio]
        if not cerca:
            s.motivo = "los OB de 4H quedaron por debajo del precio"
            return s
        ob = min(cerca, key=lambda o: o.piso)
    # «El precio entra al OB, idealmente barriendo un inducement antes».
    sw5 = E.swings(base["v5"][-60:])
    ind = E.inducement(sw5, ob.mean_threshold, lado)
    if ind is not None:
        barrido = any((v.l < ind if lado == "compra" else v.h > ind)
                      for v in base["recientes"])
        if not barrido:
            s.motivo = f"hay inducement sin barrer en {ind:.4f}"
            return s
        s.pasos.append(f"inducement barrido@{ind:.4f}")
    s.zona = {"tipo": "ob_4h", "piso": round(ob.piso, 4), "tope": round(ob.tope, 4),
              "mt": round(ob.mean_threshold, 4)}
    entrada = ob.mean_threshold
    sl = (ob.piso - base["margen"]) if lado == "compra" else (ob.tope + base["margen"])
    s.pasos.append(f"ob_4h@{ob.piso:.4f}-{ob.tope:.4f}")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.4  Ruptura del rango asiatico / London Judas Swing
# ══════════════════════════════════════════════════════════════════════

def judas_asia(sim: str, ctx: dict, base: dict) -> Senal:
    """
    Londres barre un lado del rango de Asia y se va al otro (cap. 11.4).

    Es el Power of 3 del capitulo 5.3 en su forma operable: acumulacion
    en Asia, manipulacion al abrir Londres, distribucion el resto del
    dia. El objetivo natural es el extremo opuesto del rango.
    """
    s = Senal(simbolo=sim, setup="11.4_judas_asia",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    if base["ventana"] != "london_kz":
        s.motivo = "este modelo es de la killzone de Londres"
        return s
    a = ctx.get("asia", {})
    if not a.get("alto"):
        s.motivo = "sin rango de Asia"
        return s
    lado, ahora, a5 = base["lado"], base["ahora"], base["a5"]
    # «Rango preferiblemente estrecho respecto al ATR diario».
    ancho = a["alto"] - a["bajo"]
    v5 = base["v5"]
    nivel = a["bajo"] if lado == "compra" else a["alto"]
    vb = next((v for v in base["recientes"]
               if (lado == "compra" and v.l < nivel)
               or (lado == "venta" and v.h > nivel)), None)
    if not vb:
        s.motivo = "Londres todavia no barrio el extremo esperado de Asia"
        return s
    s.nombre_nivel = "asia_bajo" if lado == "compra" else "asia_alto"
    s.nivel_barrido = round(nivel, 4)
    post = [v for v in v5 if v.i > vb.i
            and (base["dt"][v.i] - base["dt"][vb.i]).total_seconds() <= MINUTOS_CAMBIO * 60]
    if not post:
        s.motivo = "barrida sin velas posteriores"
        return s
    prev = [v for v in v5 if v.i <= vb.i][-40:]
    sw = E.swings(prev)
    ref = [x for x in sw if x.tipo == ("high" if lado == "compra" else "low")]
    if not ref:
        s.motivo = "sin swing de referencia"
        return s
    nc = ref[-1].precio
    rompio = next((v for v in post
                   if (lado == "compra" and v.c > nc) or (lado == "venta" and v.c < nc)), None)
    if not rompio:
        s.motivo = "barrio Asia pero sin cambio de estructura en 5M"
        return s
    tramo = [v for v in v5 if vb.i <= v.i <= rompio.i + 1]
    cand = [f for f in E.fvgs(tramo)
            if f.tipo == ("alcista" if lado == "compra" else "bajista") and not f.invalidado]
    if not cand:
        s.motivo = "sin FVG en la confirmacion"
        return s
    f = min(cand, key=lambda x: abs(x.ce - nc))
    s.zona = {"tipo": "fvg", "piso": round(f.piso, 4), "tope": round(f.tope, 4)}
    s.pasos.append(f"judas: barrio {s.nombre_nivel}, rango Asia {ancho:.4f} ({ancho/a5:.1f} ATR)")
    entrada = f.tope if lado == "compra" else f.piso
    sl = (vb.l - base["margen"]) if lado == "compra" else (vb.h + base["margen"])
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.5  Silver Bullet
# ══════════════════════════════════════════════════════════════════════

def silver_bullet(sim: str, ctx: dict, base: dict) -> Senal:
    """
    Ventana horaria fija, «muy mecanico, apto para automatizar» (cap. 11.5).

    Dentro de la hora, el precio barre un nivel menor y crea un FVG en
    direccion del objetivo. La guia pide que el objetivo este al menos a
    1,5-2x el stop de distancia, que es el mismo 2R de siempre.
    """
    s = Senal(simbolo=sim, setup="11.5_silver_bullet",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    ahora = base["ahora"]
    if not S.en_ventanas(ahora, S.SILVER_BULLET):
        s.motivo = "fuera de las ventanas de Silver Bullet"
        return s
    lado, v5, a5 = base["lado"], base["v5"], base["a5"]
    # Dentro de la ventana: solo las velas de esta hora.
    hora = [v for v in v5 if base["dt"][v.i].hour == ahora.hour
            and base["dt"][v.i].date() == ahora.date()]
    if len(hora) < 4:
        s.motivo = "la ventana de Silver Bullet recien empieza"
        return s
    cand = [f for f in E.fvgs(hora, minimo=a5 * 0.2)
            if f.tipo == ("alcista" if lado == "compra" else "bajista")
            and not f.invalidado]
    if not cand:
        s.motivo = "sin FVG dentro de la ventana"
        return s
    f = cand[-1]
    s.zona = {"tipo": "fvg_sb", "piso": round(f.piso, 4), "tope": round(f.tope, 4)}
    entrada = f.tope if lado == "compra" else f.piso
    # «SL detras de la vela 1 del FVG o del swing».
    extremo = min(v.l for v in hora) if lado == "compra" else max(v.h for v in hora)
    sl = (extremo - base["margen"]) if lado == "compra" else (extremo + base["margen"])
    s.pasos.append(f"silver_bullet {ahora:%H}:00 fvg@{f.piso:.4f}-{f.tope:.4f}")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.6  Rechazo o aceptacion en PDH / PDL
# ══════════════════════════════════════════════════════════════════════

def pdh_pdl(sim: str, ctx: dict, base: dict) -> Senal:
    """
    Los niveles del dia anterior son liquidez universal (cap. 11.6).

    Dos escenarios opuestos: rechazo -- el precio los supera con mecha y
    vuelve, y se opera en contra -- o aceptacion -- cierra del otro lado
    y retestea, y se opera a favor. Aca se implementa el rechazo, que es
    el que necesita confirmacion en 5M y va con el sesgo cuando el nivel
    esta del lado correcto.
    """
    s = Senal(simbolo=sim, setup="11.6_pdh_pdl",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    n = ctx.get("niveles", {})
    lado, ahora = base["lado"], base["ahora"]
    nivel = n.get("pdl") if lado == "compra" else n.get("pdh")
    if nivel is None:
        s.motivo = "sin PDH/PDL calculado"
        return s
    v15 = E.velas(ctx.get("m15", []))
    if len(v15) < 10:
        s.motivo = "faltan velas de 15M"
        return s
    # «supera con mecha y cierra de nuevo debajo (15M)».
    rech = next((v for v in reversed(v15[-8:])
                 if (lado == "compra" and v.l < nivel and v.c > nivel)
                 or (lado == "venta" and v.h > nivel and v.c < nivel)), None)
    if not rech:
        s.motivo = "sin rechazo de PDH/PDL en 15M"
        return s
    v5, a5 = base["v5"], base["a5"]
    cand = [f for f in E.fvgs(v5[-20:], minimo=a5 * 0.2)
            if f.tipo == ("alcista" if lado == "compra" else "bajista")
            and not f.invalidado]
    if not cand:
        s.motivo = "rechazo sin FVG de 5M para entrar"
        return s
    f = cand[-1]
    s.nombre_nivel = "pdl" if lado == "compra" else "pdh"
    s.nivel_barrido = round(float(nivel), 4)
    s.zona = {"tipo": "fvg", "piso": round(f.piso, 4), "tope": round(f.tope, 4)}
    entrada = f.tope if lado == "compra" else f.piso
    sl = (rech.l - base["margen"]) if lado == "compra" else (rech.h + base["margen"])
    s.pasos.append(f"rechazo en {s.nombre_nivel}@{nivel:.4f}")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.7  Inverse FVG tras barrida
# ══════════════════════════════════════════════════════════════════════

def inverse_fvg(sim: str, ctx: dict, base: dict) -> Senal:
    """
    IFVG TRAS BARRIDA (cap. 11.7). Las tres palabras del titulo importan.

    La regla, literal: «El precio BARRE UN MINIMO, luego sube y cierra
    por encima de un FVG bajista reciente de 1M/5M. Ese FVG bajista queda
    invertido: ahora actua como soporte. Entrada: retroceso al IFVG.
    SL BAJO EL MINIMO DE LA BARRIDA; TP en la liquidez superior mas
    cercana.»

    LA PRIMERA VERSION SE SALTO LA BARRIDA
    Pedia solo que existiera un FVG invertido en las ultimas 60 velas, y
    ponia el SL en el borde del hueco. Con eso se volvio el modelo mas
    prolifico de los nueve -- 201 de 215 operaciones del backtest, el
    94 % -- no porque funcionara mejor, sino porque era el mas facil de
    cumplir. Un hueco invertido aparece casi siempre; una barrida
    seguida de un hueco invertido, no.

    Y el stop en el borde del hueco es un stop DISTINTO al que la guia
    manda: mas ajustado, lo que infla el R:R aparente de cada operacion
    y lo hace saltar mas seguido. Dos errores que se compensan a medias
    en el promedio y mienten los dos.
    """
    s = Senal(simbolo=sim, setup="11.7_inverse_fvg",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    lado, v5, a5, ahora = base["lado"], base["v5"], base["a5"], base["ahora"]

    # 1. «El precio barre un minimo» — el mismo criterio de toma de
    #    liquidez que usan los demas modelos de reversion.
    barrido = None
    for nom, alto, bajo in base["niveles"]:
        nivel = bajo if lado == "compra" else alto
        if nivel is None:
            continue
        vb = next((v for v in base["recientes"]
                   if (lado == "compra" and v.l < nivel)
                   or (lado == "venta" and v.h > nivel)), None)
        if vb:
            barrido = (nom, nivel, vb)
            break
    if not barrido:
        s.motivo = "sin barrida previa: el modelo es «IFVG TRAS BARRIDA»"
        return s
    s.nombre_nivel, s.nivel_barrido, vb = barrido
    s.pasos.append(f"barrida={s.nombre_nivel}@{s.nivel_barrido:.4f}")

    # 2. «luego sube y cierra por encima de un FVG bajista reciente».
    #    El hueco a invertir tiene que ser anterior a la barrida -- es el
    #    que «debia respetarse» -- y la inversion, posterior.
    antes = [v for v in v5 if v.i <= vb.i][-40:]
    despues = [v for v in v5 if v.i >= vb.i]
    if len(despues) < 3:
        s.motivo = "barrida sin velas posteriores para invertir el hueco"
        return s
    tramo = antes + despues[1:]
    huecos = [f for f in E.fvgs(tramo, minimo=a5 * 0.25)
              if f.tipo == ("bajista" if lado == "compra" else "alcista")]
    if not huecos:
        s.motivo = "sin FVG contrario reciente que pueda invertirse"
        return s
    invertidos = [f for f in E.ifvg(tramo, huecos)
                  if f.tipo == ("alcista" if lado == "compra" else "bajista")]
    if not invertidos:
        s.motivo = "el precio no atraveso ningun FVG con cierre tras la barrida"
        return s

    precio = v5[-1].c
    if lado == "compra":
        utiles = [f for f in invertidos if f.tope < precio]
        if not utiles:
            s.motivo = "los IFVG quedaron por encima del precio"
            return s
        f = max(utiles, key=lambda x: x.tope)
        entrada = f.tope
    else:
        utiles = [f for f in invertidos if f.piso > precio]
        if not utiles:
            s.motivo = "los IFVG quedaron por debajo del precio"
            return s
        f = min(utiles, key=lambda x: x.piso)
        entrada = f.piso

    # 3. «SL bajo el minimo de la barrida» — no bajo el hueco.
    sl = (vb.l - base["margen"]) if lado == "compra" else (vb.h + base["margen"])
    s.zona = {"tipo": "ifvg", "piso": round(f.piso, 4), "tope": round(f.tope, 4)}
    s.pasos.append(f"ifvg@{f.piso:.4f}-{f.tope:.4f} invertido tras la barrida")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════
# 11.8  Llenado del gap semanal (NWOG)
# ══════════════════════════════════════════════════════════════════════

def gap_semanal(sim: str, ctx: dict, base: dict) -> Senal:
    """
    El precio tiende a revisitar el gap de apertura del domingo (cap. 11.8).

    «Condicion: gap significativo (ej. > 0,3 x ATR diario) y sesgo de HTF
    compatible con el llenado». El objetivo es el 50% del gap y el cierre
    del viernes.
    """
    s = Senal(simbolo=sim, setup="11.8_gap_semanal",
              lado=base["lado"], ventana=base["ventana"], sesgo=base["sesgo"])
    g = ctx.get("nwog", {})
    if not g:
        s.motivo = "sin gap semanal identificado"
        return s
    diario = E.velas(ctx.get("diario", []))
    ad = E.atr(diario) if len(diario) > 15 else 0
    if ad > 0 and g["tamano"] < 0.3 * ad:
        s.motivo = f"gap de {g['tamano']:.4f} menor a 0,3x ATR diario ({ad:.4f})"
        return s
    lado, ahora = base["lado"], base["ahora"]
    precio = base["v5"][-1].c
    # El sesgo tiene que apuntar hacia el llenado.
    destino = g["ce"]
    if lado == "compra" and destino <= precio:
        s.motivo = "el gap queda por debajo: el sesgo alcista no lo llena"
        return s
    if lado == "venta" and destino >= precio:
        s.motivo = "el gap queda por encima: el sesgo bajista no lo llena"
        return s
    v5, a5 = base["v5"], base["a5"]
    cand = [f for f in E.fvgs(v5[-20:], minimo=a5 * 0.2)
            if f.tipo == ("alcista" if lado == "compra" else "bajista")
            and not f.invalidado]
    if not cand:
        s.motivo = "sin FVG de confirmacion hacia el gap"
        return s
    f = cand[-1]
    s.zona = {"tipo": "fvg_nwog", "piso": round(f.piso, 4), "tope": round(f.tope, 4)}
    entrada = f.tope if lado == "compra" else f.piso
    extremo = min(v.l for v in v5[-12:]) if lado == "compra" else max(v.h for v in v5[-12:])
    sl = (extremo - base["margen"]) if lado == "compra" else (extremo + base["margen"])
    s.pasos.append(f"nwog {g['piso']:.4f}-{g['tope']:.4f} ce={g['ce']:.4f}")
    return _cierra(s, entrada, sl, ctx, ahora)


# ══════════════════════════════════════════════════════════════════════

SETUPS = [
    ("11.1", barrida_choch_fvg),
    ("11.4", judas_asia),
    ("11.5", silver_bullet),
    ("11.7", inverse_fvg),
    ("11.6", pdh_pdl),
    ("11.3", retest_ob_alto),
    ("11.2", continuacion_bos),
    ("11.8", gap_semanal),
]
"""
El orden importa: se prueban de mas a menos confluencia.

Adelante van los que combinan barrida de liquidez + cambio de estructura
+ desplazamiento, que es lo que el capitulo 8.3 llama zona de alta
probabilidad. Atras, los que se apoyan en una sola condicion.

Se devuelve el PRIMERO que se cumple, no todos: dos setups sobre el
mismo instante y el mismo activo serian la misma apuesta contada dos
veces, que es el error que el capitulo 14.1 senala al hablar de riesgo
correlacionado.
"""


# ══════════════════════════════════════════════════════════════════════
# El contexto comun y el orquestador
# ══════════════════════════════════════════════════════════════════════

def _niveles_de_liquidez(ventana: str, ctx: dict) -> list[tuple[str, float, float]]:
    """
    Que liquidez se vigila en cada ventana (cap. 20.2 y 6.1).

    «Maximo/minimo de Asia (para Londres); maximo/minimo de Londres y
    PDH/PDL (para NY)». Fuera de esas dos ventanas se vigila todo lo que
    haya: la liquidez del dia y de la semana anterior no caduca al salir
    de una killzone.
    """
    a, n, lon = ctx.get("asia", {}), ctx.get("niveles", {}), ctx.get("londres", {})
    out: list[tuple[str, float, float]] = []
    if a.get("alto") is not None:
        out.append(("asia_alto", a["alto"], a["bajo"]))
    if ventana != "london_kz":
        if lon.get("alto") is not None:
            out.append(("londres_alto", lon["alto"], lon["bajo"]))
        if "pdh" in n:
            out.append(("pd", n["pdh"], n["pdl"]))
        if "pwh" in n:
            out.append(("pw", n["pwh"], n["pwl"]))
    return out


def preparar(ctx: dict) -> dict | str:
    """
    Lo que todos los modelos necesitan antes de mirar su condicion propia.

    Devuelve el contexto comun, o un string con el motivo si ya se sabe
    que no hay nada que evaluar. Los dos filtros universales -- ventana y
    sesgo -- se aplican aca una sola vez en lugar de repetirse nueve
    veces: el capitulo 11.9 los pone al principio del checklist para
    todos los modelos, no para uno.
    """
    ahora: datetime = ctx["ahora"]
    est = S.estado(ahora)
    if not est["operable"]:
        return (f"fuera de ventana operable (son las {est['hora_ny']} ET; "
                f"proxima: {S.proxima_operable(ahora)})")
    v5 = E.velas(ctx["m5"])
    if len(v5) < 30:
        return "faltan velas de 5M"
    a5 = E.atr(v5)
    if a5 <= 0:
        return "ATR de 5M en cero"
    v4 = E.velas(ctx.get("h4", []))
    e4 = E.leer_estructura(v4) if len(v4) >= 10 else None
    if e4 is None or e4.tendencia == "rango":
        return "4H en rango sin quiebre claro: la guia dice no operar"
    lado = "compra" if e4.tendencia == "alcista" else "venta"
    dt = ctx["dt5"]
    corte = ahora - timedelta(minutes=MINUTOS_CAMBIO + 30)
    recientes = [v for v in v5 if dt[v.i] >= corte]
    if len(recientes) < 5:
        return "sin velas recientes suficientes"
    return {
        "ahora": ahora, "v5": v5, "a5": a5, "dt": dt,
        "lado": lado, "sesgo": e4.tendencia, "ventana": est["ventana_operable"],
        "margen": ctx.get("margen_sl", 0.30),
        "recientes": recientes,
        "niveles": _niveles_de_liquidez(est["ventana_operable"], ctx),
        "estructura_4h": e4,
    }


def evaluar_todos(sim: str, ctx: dict, extra: list | None = None) -> tuple[Senal, list[Senal]]:
    """
    Prueba cada modelo en orden y devuelve el primero que se cumple.

    `extra` permite anteponer modelos que viven fuera de este archivo --
    en la practica, la estrategia armada del capitulo 20.2, que es la
    unica que la guia desarrolla entera y por eso se prueba primero.

    Devuelve tambien la lista completa de intentos: los rechazos de los
    modelos que no se cumplieron son lo que despues se cuenta para ver
    donde muere el embudo de cada uno.
    """
    base = preparar(ctx)
    if isinstance(base, str):
        s = Senal(simbolo=sim, motivo=base, setup="previo")
        return s, [s]
    intentos: list[Senal] = []
    for nombre, fn in (extra or []) + SETUPS:
        try:
            s = fn(sim, ctx, base)
        except Exception as exc:  # noqa: BLE001
            s = Senal(simbolo=sim, setup=nombre, motivo=f"error en el modelo: {exc}")
        s.setup = s.setup or nombre
        intentos.append(s)
        if s.hay:
            return s, intentos
    # Ninguno se cumplio: se informa el que llego mas lejos, medido por
    # cuantos pasos del checklist logro registrar antes de parar.
    mejor = max(intentos, key=lambda x: len(x.pasos)) if intentos else None
    if mejor is None:
        mejor = Senal(simbolo=sim, motivo="sin modelos aplicables")
    return mejor, intentos
