"""
Puente con MetaTrader 5. Datos del broker y envio de ordenes.

POR QUE MT5 Y NO TRADINGVIEW
───────────────────────────
Porque TradingView no ejecuta ordenes por programa. Lo unico que ofrece
es mandar un webhook desde una alerta de Pine Script a un servidor
propio, y eso obligaria a reescribir los nueve modelos en Pine -- un
lenguaje que corre dentro del grafico, sin acceso a Postgres ni a la
serie de otro activo, que es lo que necesita la SMT del capitulo 15.5.

El capitulo 18.5 lista las vias de automatizacion y la primera es esta:
«Python + paquete MetaTrader5: permite leer datos y enviar ordenes a una
terminal MT5 desde Python».

Hay ademas un detalle de Tickmill que conviene saber: su integracion con
TradingView funciona con cuentas «Tickmill Trader», que son un tipo
distinto de las MT4/MT5. No son la misma cuenta y no sincronizan. Lo que
el bot abra por MT5 no aparece en el panel de TradingView.

EL ERROR QUE ESTE ARCHIVO EXISTE PARA EVITAR
Hasta ahora el sistema analizaba `GC=F`, el futuro de oro de COMEX, y se
pretendia operar `XAUUSD`, el oro al contado. No son el mismo
instrumento. Medido el 2026-09-23 con el mercado cerrado:

    GC=F     4.325,50     (lo que analizaba)
    XAUUSD   4.287,08     (lo que se opera)
    ────────────────
    38,42 dolares de diferencia

Un stop calculado sobre el futuro y puesto en el contado queda 38 dolares
corrido. Los stops de este sistema van de 1 a 15 dolares: el desfase es
varias veces el riesgo entero de la operacion. No es un ajuste, es otra
operacion.

Por eso aqui las velas salen del BROKER, no de Yahoo. Se analiza el mismo
instrumento que se opera, con los mismos precios, el mismo spread y el
mismo horario. Es la unica forma de que los niveles signifiquen algo.

LA TRAMPA DE LA ZONA HORARIA
El capitulo 10 avisa: «verifica en que zona muestra el grafico tu broker,
porque marcar las killzones corridas una hora las vuelve inutiles». MT5
devuelve los tiempos en hora del SERVIDOR del broker, que suele ir en
GMT+2 o GMT+3 y cambia con el horario de verano. Aqui se calcula el
desfase real comparando el ultimo tick con la hora UTC, en vez de
suponerlo, y todo se convierte a Nueva York antes de salir de este
archivo.

QUE NO ESTA PROBADO
Este archivo NO se ha ejecutado contra una terminal real: se escribio sin
MT5 instalado. La forma de las llamadas sale de la documentacion del
paquete, pero los detalles que solo se ven en vivo -- modo de llenado que
acepta Tickmill, distancia minima de stops, nombre exacto del simbolo --
pueden fallar en el primer intento. Por eso `enviar` no manda nada a
menos que `MATERIAS_ENVIAR=1` este puesto, y `comprobar()` existe para
verificar todo eso ANTES de operar.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("materias.broker")
NY = ZoneInfo("America/New_York")

SIMBOLO = os.getenv("MT5_SIMBOLO", "XAUUSD")
ENVIAR_DE_VERDAD = os.getenv("MATERIAS_ENVIAR", "").strip() == "1"
"""
Sin esto puesto a 1, `enviar` registra la orden y no la manda.

Es el seguro. El capitulo 20.1 pone el forward test en demo como paso 7 y
el dinero real como paso final, y este archivo no se ha probado nunca
contra una terminal. Que mandar ordenes exija una variable explicita
obliga a que alguien decida hacerlo, en vez de que pase por defecto.
"""

_mt5 = None
_desfase: timedelta | None = None


def _api():
    """Importa MetaTrader5 al usarlo, no al importar el modulo."""
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as mt5  # noqa: N813
        _mt5 = mt5
    return _mt5


def conectar(login: int | None = None, clave: str = "", servidor: str = "",
             ruta: str = "") -> dict:
    """
    Arranca la terminal y entra a la cuenta.

    Con la terminal ya abierta y logueada basta `initialize()` sin
    argumentos. Las credenciales se pasan solo si se quiere forzar otra
    cuenta; salen de `MT5_LOGIN`, `MT5_CLAVE` y `MT5_SERVIDOR`.
    """
    mt5 = _api()
    login = login or int(os.getenv("MT5_LOGIN") or 0)
    clave = clave or os.getenv("MT5_CLAVE", "")
    servidor = servidor or os.getenv("MT5_SERVIDOR", "")
    ruta = ruta or os.getenv("MT5_RUTA", "")

    args: dict = {}
    if ruta:
        args["path"] = ruta
    if login and clave and servidor:
        args |= {"login": login, "password": clave, "server": servidor}

    if not mt5.initialize(**args):
        code, msg = mt5.last_error()
        raise RuntimeError(f"no se pudo conectar a MT5 ({code}): {msg}")

    info = mt5.account_info()
    if info is None:
        raise RuntimeError("MT5 conecto pero no hay cuenta activa")
    d = info._asdict()
    # `trade_mode` 0 = demo, 1 = concurso, 2 = real. Se informa siempre:
    # es la diferencia entre el paso 7 y el paso 8 del capitulo 20.1.
    d["es_demo"] = d.get("trade_mode") == 0
    log.info("MT5 %s · cuenta %s (%s) · %s %.2f · apalancamiento 1:%s",
             mt5.version()[1] if mt5.version() else "?", d.get("login"),
             "DEMO" if d["es_demo"] else "REAL", d.get("currency"),
             d.get("balance", 0), d.get("leverage"))
    return d


def desconectar() -> None:
    if _mt5 is not None:
        _mt5.shutdown()


# ══════════════════════════════════════════════════════════════════════
# Hora del servidor
# ══════════════════════════════════════════════════════════════════════

def desfase_servidor(simbolo: str = "") -> timedelta:
    """
    Cuanto adelanta el reloj del broker respecto a UTC.

    Se MIDE, no se supone: se compara la marca del ultimo tick con la hora
    UTC y se redondea a la media hora mas cercana. Suponer GMT+2 y que el
    broker este en GMT+3 corre todas las killzones una hora, que es
    exactamente lo que el capitulo 10 dice que las vuelve inutiles.
    """
    global _desfase
    if _desfase is not None:
        return _desfase
    mt5 = _api()
    simbolo = simbolo or SIMBOLO

    # ── El tick, si sirve ─────────────────────────────────────────────
    #
    # Un simbolo recien activado en Observacion del Mercado devuelve un
    # tick con `time=0` durante unos segundos, hasta que llega la primera
    # cotizacion. Sin validarlo, el desfase salia de comparar 1970 con
    # hoy: UTC-497282,5 horas, y las velas aterrizaban en el ano 2083.
    #
    # Lo peor no era el numero: era que NO FALLABA. La comprobacion
    # imprimia "velas 5M: 30" tan tranquila y el sistema habria operado
    # con killzones desplazadas medio siglo.
    epoca = 0
    for _ in range(6):
        t = mt5.symbol_info_tick(simbolo)
        if t is not None and t.time:
            epoca = int(t.time)
            break
        mt5.symbol_select(simbolo, True)
        _dormir(1.0)

    # ── Si no hay tick, la ultima vela sirve igual ────────────────────
    #
    # Las velas tambien vienen en hora del servidor y existen aunque el
    # mercado este cerrado. Es peor ancla -- la ultima vela puede ser de
    # hace horas -- pero se redondea a media hora y eso lo absorbe.
    origen = "tick"
    if not epoca:
        filas = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_M5, 0, 1)
        if filas is not None and len(filas):
            epoca = int(filas[-1]["time"]) + 300  # el cierre, no la apertura
            origen = "ultima vela"

    if not epoca:
        raise RuntimeError(
            f"no hay ni tick ni velas de «{simbolo}»: anadelo a Observacion "
            f"del Mercado (clic derecho > Mostrar todo) y reintenta")

    bruto = (datetime.fromtimestamp(epoca, tz=UTC) - datetime.now(UTC)).total_seconds()
    horas = bruto / 3600
    # Ningun broker esta a mas de medio dia de UTC. Fuera de esa banda el
    # dato esta roto, y es mejor parar que operar con la hora corrida --
    # el capitulo 10 avisa de que eso vuelve inutiles las killzones.
    if not -14 <= horas <= 14:
        raise RuntimeError(
            f"la hora de «{simbolo}» da un desfase imposible ({horas:+.1f} h). "
            f"El simbolo no esta cotizando; anadelo a Observacion del Mercado.")

    _desfase = timedelta(minutes=round(bruto / 1800) * 30)
    log.info("hora del servidor: UTC%+.1f (medido por %s, no supuesto)",
             _desfase.total_seconds() / 3600, origen)
    return _desfase


def _dormir(s: float) -> None:
    import time
    time.sleep(s)


def _a_ny(epoch: int, simbolo: str) -> datetime:
    """Marca del broker -> hora de Nueva York, que es donde viven las killzones."""
    return (datetime.fromtimestamp(epoch, tz=UTC) - desfase_servidor(simbolo)
            ).replace(tzinfo=UTC).astimezone(NY)


# ══════════════════════════════════════════════════════════════════════
# Especificacion del simbolo
# ══════════════════════════════════════════════════════════════════════

def especificacion(simbolo: str = "") -> dict:
    """
    Lo que el broker dice de este instrumento. Nada se da por sabido.

    El capitulo 1.4 insiste en conocer el valor del movimiento antes de
    operar, y esos numeros los pone el BROKER, no una tabla nuestra: el
    tamano de contrato, el lote minimo, el paso del lote y -- el que mas
    duele si se ignora -- `trade_stops_level`, la distancia minima a la
    que el broker acepta un stop. Una orden con el SL mas cerca que eso
    se rechaza entera.
    """
    mt5 = _api()
    simbolo = simbolo or SIMBOLO
    si = mt5.symbol_info(simbolo)
    if si is None:
        raise RuntimeError(f"el broker no conoce el simbolo «{simbolo}»")
    if not si.visible and not mt5.symbol_select(simbolo, True):
        raise RuntimeError(f"no se pudo activar «{simbolo}» en Observacion de Mercado")
    si = mt5.symbol_info(simbolo)
    # Tras activarlo tarda un segundo en llegar la primera cotizacion, y
    # un bid/ask en cero se propaga a todo lo demas sin quejarse.
    tick = mt5.symbol_info_tick(simbolo)
    for _ in range(5):
        if tick is not None and tick.bid:
            break
        _dormir(1.0)
        tick = mt5.symbol_info_tick(simbolo)
    punto = si.point or 0.01
    spread_precio = (tick.ask - tick.bid) if tick else si.spread * punto
    return {
        "simbolo": simbolo,
        "nombre": si.description or simbolo,
        "digitos": si.digits,
        "punto": punto,
        "tick": si.trade_tick_size or punto,
        "contrato": si.trade_contract_size,
        "lote_min": si.volume_min,
        "lote_max": si.volume_max,
        "lote_paso": si.volume_step,
        # En puntos; se pasa a precio para poder compararlo con un stop.
        "stops_min": si.trade_stops_level * punto,
        "congelado": si.trade_freeze_level * punto,
        "spread": round(spread_precio, si.digits),
        "spread_puntos": si.spread,
        "bid": tick.bid if tick else None,
        "ask": tick.ask if tick else None,
        "modo_llenado": si.filling_mode,
        # Los CFD no publican volumen real (cap. 9.1): solo ticks. Se
        # marca para que nadie lea el perfil de volumen creyendo que son
        # contratos negociados.
        "volumen_real": bool(getattr(si, "volume_real", 0)),
    }


def lotes_validos(lotes: float, esp: dict) -> float:
    """
    Redondea el lote a lo que el broker acepta, hacia ABAJO.

    Hacia abajo y no al mas cercano porque redondear hacia arriba sube el
    riesgo por encima del 0,5 % que fija el capitulo 14.1. Mejor
    arriesgar un poco menos de lo planeado que un poco mas.
    """
    paso = esp.get("lote_paso") or 0.01
    n = int(lotes / paso)
    v = round(n * paso, 8)
    return max(esp.get("lote_min", 0.01), min(v, esp.get("lote_max", 100.0)))


# ══════════════════════════════════════════════════════════════════════
# Velas
# ══════════════════════════════════════════════════════════════════════

MARCOS = {"m5": "TIMEFRAME_M5", "m15": "TIMEFRAME_M15", "h1": "TIMEFRAME_H1",
          "h4": "TIMEFRAME_H4", "diario": "TIMEFRAME_D1"}
"""
MT5 sirve 4H de forma nativa.

Yahoo no lo hace y por eso `datos._agrupar` arma las velas de 4H juntando
cuatro de 1H. Aqui no hace falta: las del broker son las que el broker
usa, que es lo que ve cualquiera mirando el mismo grafico.
"""

CUANTAS = {"m5": 1500, "m15": 700, "h1": 700, "h4": 400, "diario": 250}


def velas(simbolo: str, marco: str, n: int = 0) -> list[dict]:
    """
    Velas del broker, en el mismo formato que produce `datos.py`.

    Mismo formato a proposito: asi todo lo que hay aguas abajo --
    estructura, setups, indicadores, backtest -- funciona igual sin saber
    de donde vinieron.
    """
    mt5 = _api()
    n = n or CUANTAS.get(marco, 500)
    tf = getattr(mt5, MARCOS[marco])
    filas = mt5.copy_rates_from_pos(simbolo, tf, 0, n)
    if filas is None or not len(filas):
        code, msg = mt5.last_error()
        log.warning("sin velas de %s %s (%s): %s", simbolo, marco, code, msg)
        return []
    out = []
    for r in filas:
        dt = _a_ny(int(r["time"]), simbolo)
        # `real_volume` es el volumen de verdad y solo lo hay en futuros;
        # en un CFD viene en cero y se cae a `tick_volume`, que cuenta
        # cambios de precio, no contratos. La distincion es del cap. 9.1.
        vol = float(r["real_volume"]) or float(r["tick_volume"])
        out.append({"dt": dt, "t": str(dt), "o": float(r["open"]),
                    "h": float(r["high"]), "l": float(r["low"]),
                    "c": float(r["close"]), "v": vol})
    return out


def datos_completos(simbolo: str = "") -> dict:
    """
    Las cinco temporalidades del analisis top-down (cap. 3).

    Devuelve el mismo diccionario que `datos.cargar` da por activo, para
    que `datos.contexto` lo consuma sin cambios.
    """
    simbolo = simbolo or SIMBOLO
    return {clave: velas(simbolo, clave) for clave in MARCOS}


# ══════════════════════════════════════════════════════════════════════
# Ordenes
# ══════════════════════════════════════════════════════════════════════

def _modo_llenado(esp: dict):
    """
    El modo de llenado que acepta este simbolo.

    `filling_mode` es una mascara de bits y cada broker permite unos
    distintos. Mandar uno que el simbolo no acepta devuelve
    «Unsupported filling mode» y la orden se pierde sin mas explicacion,
    asi que se elige entre los que el propio simbolo declara.
    """
    mt5 = _api()
    m = esp.get("modo_llenado", 0)
    # Los bits van literales porque el paquete de Python NO exporta las
    # constantes `SYMBOL_FILLING_*` -- solo las `ORDER_FILLING_*`, que son
    # otra cosa y valen otros numeros. Usarlas por su nombre reventaba con
    # AttributeError justo al enviar la primera orden.
    #
    #   mascara del simbolo        orden que hay que mandar
    #   bit 1 = SYMBOL_FILLING_FOK -> ORDER_FILLING_FOK (0)
    #   bit 2 = SYMBOL_FILLING_IOC -> ORDER_FILLING_IOC (1)
    #
    # Medido en Tickmill: XAUUSD devuelve mascara 2, o sea SOLO IOC.
    # Mandar FOK ahi da «Unsupported filling mode» y la orden se pierde.
    for bit, modo in ((1, mt5.ORDER_FILLING_FOK),
                      (2, mt5.ORDER_FILLING_IOC)):
        if m & bit:
            return modo
    # Las pendientes suelen admitir RETURN aunque no este en la mascara.
    return mt5.ORDER_FILLING_RETURN


def enviar(sen, lotes: float, simbolo: str = "",
           minutos_vigencia: int = 45, magia: int = 20260923) -> dict:
    """
    Coloca la orden limite con su SL, su TP y su caducidad.

    Es una LIMITE y no una orden a mercado porque eso es lo que dice la
    estrategia: «Buy Limit en el borde superior del FVG. Expira a los 45
    minutos o al final de la killzone» (cap. 20.2). Entrar a mercado
    seria otra estrategia, con otro precio de entrada y otro R.

    El SL y el TP van ADJUNTOS en el envio, no despues. El capitulo 19 lo
    pone entre las soluciones a «mover el stop»: «SL fijo desde la
    entrada; automatizar SL/TP». Una orden que llega sin stop y espera a
    que un segundo mensaje se lo ponga es una orden sin stop durante ese
    intervalo.

    Sin `MATERIAS_ENVIAR=1` no manda nada: devuelve lo que habria mandado.
    """
    mt5 = _api()
    simbolo = simbolo or SIMBOLO
    esp = especificacion(simbolo)
    lotes = lotes_validos(lotes, esp)

    compra = sen.lado == "compra"
    dig = esp["digitos"]
    entrada = round(sen.entrada, dig)
    sl = round(sen.sl, dig)
    tp = round(sen.tp, dig)

    # ── Lo que el broker puede rechazar, comprobado antes de mandar ───
    problemas = []
    ref = esp["ask"] if compra else esp["bid"]
    if ref is not None:
        # Una Buy Limit tiene que estar POR DEBAJO del precio; si no, el
        # broker la rechaza con «Invalid price».
        if compra and entrada >= ref:
            problemas.append(f"Buy Limit en {entrada} no esta por debajo del ask {ref}")
        if not compra and entrada <= ref:
            problemas.append(f"Sell Limit en {entrada} no esta por encima del bid {ref}")
    minimo = esp["stops_min"]
    if minimo > 0:
        if abs(entrada - sl) < minimo:
            problemas.append(f"SL a {abs(entrada - sl):.{dig}f} del precio, "
                             f"por debajo del minimo del broker ({minimo})")
        if abs(tp - entrada) < minimo:
            problemas.append(f"TP a {abs(tp - entrada):.{dig}f} del precio, "
                             f"por debajo del minimo del broker ({minimo})")
    if lotes < esp["lote_min"]:
        problemas.append(f"lote {lotes} por debajo del minimo {esp['lote_min']}")

    caduca = datetime.now(UTC) + desfase_servidor(simbolo) + timedelta(
        minutes=minutos_vigencia)
    peticion = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": simbolo,
        "volume": lotes,
        "type": mt5.ORDER_TYPE_BUY_LIMIT if compra else mt5.ORDER_TYPE_SELL_LIMIT,
        "price": entrada,
        "sl": sl,
        "tp": tp,
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(caduca.timestamp()),
        "type_filling": _modo_llenado(esp),
        "magic": magia,
        # El comentario viaja al historial del broker y sobrevive alli
        # aunque nuestra base se pierda: es la unica forma de saber, seis
        # meses despues, que modelo abrio una operacion concreta.
        "comment": f"materias {sen.setup}"[:31],
    }

    if problemas:
        log.warning("orden NO enviada: %s", " · ".join(problemas))
        return {"enviada": False, "motivo": " · ".join(problemas),
                "peticion": peticion}

    if not ENVIAR_DE_VERDAD:
        log.info("SECO · %s %s %s lotes @ %s  SL %s  TP %s  (no se envia: "
                 "falta MATERIAS_ENVIAR=1)", simbolo, sen.lado, lotes,
                 entrada, sl, tp)
        return {"enviada": False, "motivo": "modo seco", "peticion": peticion}

    res = mt5.order_send(peticion)
    if res is None:
        code, msg = mt5.last_error()
        return {"enviada": False, "motivo": f"order_send devolvio None ({code}): {msg}",
                "peticion": peticion}
    ok = res.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        log.warning("el broker rechazo la orden (%s): %s", res.retcode, res.comment)
    else:
        log.info("ORDEN %s · ticket %s · %s %s lotes @ %s", simbolo, res.order,
                 sen.lado, lotes, entrada)
    return {"enviada": ok, "ticket": getattr(res, "order", None),
            "retcode": res.retcode, "comentario": res.comment,
            "peticion": peticion}


def cancelar(ticket: int) -> bool:
    mt5 = _api()
    res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
    return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)


def pendientes(simbolo: str = "", magia: int = 20260923) -> list[dict]:
    """Ordenes colocadas y todavia sin llenar, solo las nuestras."""
    mt5 = _api()
    ords = mt5.orders_get(symbol=simbolo or SIMBOLO) or []
    return [o._asdict() for o in ords if o.magic == magia]


def posiciones(simbolo: str = "", magia: int = 20260923) -> list[dict]:
    """Posiciones abiertas, solo las que abrio este sistema."""
    mt5 = _api()
    pos = mt5.positions_get(symbol=simbolo or SIMBOLO) or []
    return [p._asdict() for p in pos if p.magic == magia]


def cerradas(desde: datetime, magia: int = 20260923) -> list[dict]:
    """
    Operaciones ya cerradas por el broker, con su resultado real.

    Es lo que permite comparar lo que el sistema creia que iba a pasar
    con lo que el broker hizo: el precio de llenado real, el slippage y
    la comision de verdad. El capitulo 20.1 pide justamente eso en el
    paso 7 -- «comparar resultados reales vs backtest».
    """
    mt5 = _api()
    ops = mt5.history_deals_get(desde, datetime.now(UTC) + timedelta(days=1)) or []
    return [d._asdict() for d in ops if d.magic == magia]


# ══════════════════════════════════════════════════════════════════════
# Comprobacion previa
# ══════════════════════════════════════════════════════════════════════

def comprobar(simbolo: str = "") -> str:
    """
    Todo lo que hay que verificar ANTES de dejar que opere. `python -m materias.broker`

    Este archivo se escribio sin una terminal delante. Lo que falla en el
    primer intento contra un broker de verdad casi siempre es una de
    estas cosas: el simbolo se llama distinto, el servidor va en otra
    hora, el broker exige una distancia minima de stops mayor que los
    stops del sistema, o el modo de llenado no es el que se manda. Esto
    las mira todas y las cuenta, en vez de que aparezcan una a una
    mientras hay dinero en juego.
    """
    simbolo = simbolo or SIMBOLO
    L = [f"COMPROBACION · {simbolo}", ""]
    try:
        cta = conectar()
    except Exception as exc:  # noqa: BLE001
        return (f"{L[0]}\n\n  NO CONECTA: {exc}\n\n"
                "  Abri la terminal de MT5, entra a la cuenta, y en\n"
                "  Herramientas > Opciones > Asesores Expertos activa\n"
                "  «Permitir trading algoritmico».")

    L.append(f"  cuenta       {cta.get('login')}  "
             f"{'DEMO' if cta['es_demo'] else '*** REAL ***'}")
    L.append(f"  balance      {cta.get('balance', 0):,.2f} {cta.get('currency')}")
    # ── Los DOS permisos, que son distintos ──────────────────────────
    #
    # `account_info().trade_allowed` es lo que el BROKER permite a la
    # cuenta. `terminal_info().trade_allowed` es el boton «Trading
    # algoritmico» de la barra del MT5. Hacen falta los dos, y mirar solo
    # el primero es lo que hizo que esta comprobacion dijera «permitido»
    # mientras el broker rechazaba con 10027 «AutoTrading disabled by
    # client». Medido en vivo: cuenta True, terminal False.
    term = _api().terminal_info()
    t_ok = bool(term and term.trade_allowed)
    c_ok = bool(cta.get("trade_allowed"))
    L.append(f"  cuenta       {'permite operar' if c_ok else 'BLOQUEADA por el broker'}")
    L.append(f"  terminal     {'AutoTrading activo' if t_ok else 'AutoTrading APAGADO'}")
    if not t_ok:
        L.append("               -> pulsa «Trading algoritmico» en la barra del MT5")
        L.append("                  (o Herramientas > Opciones > Asesores Expertos)")
        L.append("               Sin esto el broker rechaza con 10027.")

    try:
        esp = especificacion(simbolo)
    except Exception as exc:  # noqa: BLE001
        mt5 = _api()
        todos = [s.name for s in (mt5.symbols_get() or [])
                 if "XAU" in s.name.upper() or "GOLD" in s.name.upper()]
        L.append("")
        L.append(f"  SIMBOLO: {exc}")
        if todos:
            L.append(f"  parecidos en este broker: {', '.join(todos[:8])}")
            L.append("  pone el que corresponda en MT5_SIMBOLO")
        return chr(10).join(L)

    L.append("")
    L.append(f"  {esp['nombre']}")
    L.append(f"  bid/ask      {esp['bid']} / {esp['ask']}   spread {esp['spread']}")
    L.append(f"  contrato     {esp['contrato']}  ·  lote {esp['lote_min']} "
             f"a {esp['lote_max']} paso {esp['lote_paso']}")
    L.append(f"  stop minimo  {esp['stops_min']}  (el broker rechaza stops mas cerca)")
    L.append(f"  volumen      {'real' if esp['volumen_real'] else 'de ticks'}"
             + ("" if esp["volumen_real"] else
                "  <- es un CFD; el cap. 9.1 avisa que no son contratos"))

    d = desfase_servidor(simbolo)
    ahora_srv = datetime.now(UTC) + d
    L.append("")
    L.append(f"  hora servidor UTC{d.total_seconds() / 3600:+.1f}  "
             f"({ahora_srv:%H:%M})   Nueva York {datetime.now(NY):%H:%M}")

    v = velas(simbolo, "m5", 30)
    if v:
        L.append(f"  velas 5M     {len(v)}, la ultima {v[-1]['dt']:%Y-%m-%d %H:%M} ET")
    else:
        L.append("  velas 5M     NINGUNA  <- ¿mercado cerrado o simbolo sin activar?")

    # El stop minimo del broker contra los stops que el sistema produce.
    if esp["stops_min"] > 0:
        L.append("")
        L.append(f"  OJO: el broker exige el SL a mas de {esp['stops_min']} del precio.")
        L.append("  Los stops de este sistema salen de la estructura y en oro suelen")
        L.append("  ir de 1 a 15 dolares. Si alguno cae por debajo de ese minimo, la")
        L.append("  orden se rechaza entera y queda anotada como no enviada.")

    L.append("")
    L.append(f"  envio real   {'SI' if ENVIAR_DE_VERDAD else 'NO (modo seco)'}")
    if not ENVIAR_DE_VERDAD:
        L.append("               pone MATERIAS_ENVIAR=1 cuando quieras que mande")
    return chr(10).join(L)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(comprobar())
    desconectar()


def enviar_mercado(lado: str, lotes: float, sl: float, tp: float,
                   simbolo: str = "", magia: int = 20260923,
                   comentario: str = "") -> dict:
    """
    Entra AHORA, al precio que haya. Para el piloto de entrada forzada.

    `enviar` coloca una limite porque la estrategia del capitulo 20.2 lo
    pide: se entra en un retroceso a un FVG. El piloto es otra cosa --
    cada cuatro horas toma posicion pase lo que pase -- y una limite ahi
    no sirve: si el precio no vuelve, no hay entrada, y una entrada que a
    veces no ocurre rompe la cadencia que hace medible el experimento.

    El SL y el TP van adjuntos en el mismo envio. El capitulo 19 lo pone
    entre las soluciones a «mover el stop»: «SL fijo desde la entrada;
    automatizar SL/TP». Una orden que llega sin stop y espera a que un
    segundo mensaje se lo ponga esta sin stop durante ese intervalo.
    """
    mt5 = _api()
    simbolo = simbolo or SIMBOLO
    esp = especificacion(simbolo)
    lotes = lotes_validos(lotes, esp)
    compra = lado == "compra"
    dig = esp["digitos"]
    precio = esp["ask"] if compra else esp["bid"]
    if precio is None:
        return {"enviada": False, "motivo": "sin cotizacion"}
    sl, tp = round(sl, dig), round(tp, dig)

    problemas = []
    term = mt5.terminal_info()
    if term is not None and not term.trade_allowed:
        problemas.append("el AutoTrading de la terminal esta apagado: pulsa "
                         "«Trading algoritmico» en la barra del MT5")
    minimo = esp["stops_min"]
    if minimo > 0:
        if abs(precio - sl) < minimo:
            problemas.append(f"SL a {abs(precio - sl):.{dig}f}, por debajo del "
                             f"minimo del broker ({minimo})")
        if abs(tp - precio) < minimo:
            problemas.append(f"TP a {abs(tp - precio):.{dig}f}, por debajo del "
                             f"minimo del broker ({minimo})")
    if compra and not (sl < precio < tp):
        problemas.append(f"compra con SL {sl} y TP {tp} sobre un ask de {precio}")
    if not compra and not (tp < precio < sl):
        problemas.append(f"venta con SL {sl} y TP {tp} sobre un bid de {precio}")

    peticion = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": simbolo,
        "volume": lotes,
        "type": mt5.ORDER_TYPE_BUY if compra else mt5.ORDER_TYPE_SELL,
        "price": precio,
        "sl": sl, "tp": tp,
        # Cuanto slippage se tolera, en puntos. Con entrada a mercado hay
        # que dar alguno o el broker rechaza la orden en cuanto el precio
        # se mueve entre que se calcula y que llega.
        "deviation": int(os.getenv("MT5_SLIPPAGE_PUNTOS", "20")),
        "type_filling": _modo_llenado(esp),
        "magic": magia,
        "comment": (comentario or "piloto 4h")[:31],
    }
    if problemas:
        log.warning("orden NO enviada: %s", " · ".join(problemas))
        return {"enviada": False, "motivo": " · ".join(problemas),
                "peticion": peticion}
    if not ENVIAR_DE_VERDAD:
        log.info("SECO · %s %s %s lotes @ %s  SL %s  TP %s  (falta "
                 "MATERIAS_ENVIAR=1)", simbolo, lado, lotes, precio, sl, tp)
        return {"enviada": False, "motivo": "modo seco", "peticion": peticion,
                "precio": precio}

    res = mt5.order_send(peticion)
    if res is None:
        code, msg = mt5.last_error()
        return {"enviada": False, "motivo": f"order_send None ({code}): {msg}",
                "peticion": peticion}
    ok = res.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        log.warning("el broker rechazo la orden (%s): %s", res.retcode, res.comment)
    else:
        log.info("EJECUTADA %s %s %s lotes @ %s · ticket %s", simbolo, lado,
                 lotes, res.price, res.order)
    return {"enviada": ok, "ticket": getattr(res, "order", None),
            "precio": getattr(res, "price", precio), "retcode": res.retcode,
            "comentario": res.comment, "peticion": peticion}
