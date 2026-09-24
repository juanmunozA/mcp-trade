"""
El sistema operando contra el broker: `python -m materias.operar`

QUE LO SEPARA DE `correr.py`
────────────────────────────
`correr.py` analiza futuros de COMEX bajados de Yahoo y opera en papel
contra esa misma serie. Sirve para medir la logica sin broker.

Esto analiza EL MISMO INSTRUMENTO QUE OPERA -- XAUUSD en la cuenta de
Tickmill, leido de MT5 -- y manda las ordenes alli. No es una variante
del otro: es lo que hace que los niveles signifiquen algo, porque el oro
al contado y el futuro de COMEX cotizan con decenas de dolares de
diferencia y un stop calculado en uno queda corrido en el otro.

EL CICLO
    0. Reconciliar. Lo que el broker hizo manda: llenados, cierres,
       precio real y comision real se escriben en el diario tal como
       pasaron, no como se habian previsto.
    1. ¿Killzone? Si no, solo reconcilia y se calla.
    2. Velas del broker -> los nueve modelos -> checklist del 11.9.
    3. Lote por la formula del capitulo 14.2, redondeado a lo que el
       broker acepta.
    4. Orden limite con SL y TP adjuntos y caducidad a 45 minutos.

DOS SEGUROS
`MATERIAS_ENVIAR=1` para que mande de verdad; sin eso registra lo que
habria mandado. Y si la cuenta es real en vez de demo, exige ademas
`MATERIAS_ACEPTO_REAL=1`. El capitulo 20.1 pone el forward test en demo
como paso 7 y el dinero real despues; saltarse eso por una variable de
entorno mal puesta no deberia ser posible.

LO QUE NO ESTA PROBADO
Este archivo y `broker.py` se escribieron sin una terminal MT5 delante.
La logica de analisis es la misma que lleva 153 operaciones de backtest,
pero el trozo que habla con el broker no se ha ejecutado nunca. Antes de
dejarlo suelto: `python -m materias.broker`, que comprueba simbolo,
horario, stops minimos y permisos.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from materias import almacen as AL
from materias import broker as B
from materias import calidad as C
from materias import datos as D
from materias import diario as DI
from materias import estrategia as ES
from materias import gestion as G
from materias import indicadores as I
from materias import sesiones as S

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("materias.operar")

NY = ZoneInfo("America/New_York")
CADA_S = int(os.getenv("MATERIAS_CADA_S", "300"))
MAGIA = int(os.getenv("MT5_MAGIA", "20260923"))
ACEPTO_REAL = os.getenv("MATERIAS_ACEPTO_REAL", "").strip() == "1"
COMISION = float(os.getenv("MT5_COMISION", "0") or 0)
"""
Comision de ida y vuelta por lote, en la moneda de la cuenta.

No se puede leer del simbolo: depende del tipo de cuenta. En Tickmill,
las Raw/Pro cobran comision y spread casi cero; las Classic no cobran
comision y la meten en el spread. Ponerla mal desvia la expectativa en
la direccion optimista, que es la peor.

Si se deja en 0 se asume cuenta tipo Classic y el unico coste es el
spread, que si se lee del broker en vivo.
"""

_parar = False
_cuenta: dict = {}


def registrar_simbolo() -> dict:
    """
    Mete el simbolo del broker en `datos.ACTIVOS` con SUS numeros.

    `datos.contexto` y `gestion.lotes` buscan ahi el tamano de contrato y
    el colchon del stop. Para un futuro de Yahoo esos valores son fijos y
    estan escritos; para el simbolo de un broker no: el contrato, el
    spread y el minimo de stops los define la cuenta, y cambian entre
    brokers y entre tipos de cuenta del mismo broker.

    El capitulo 1.4 insiste en conocer el valor del movimiento antes de
    operar. Leerlo en vez de suponerlo es lo unico que hace que el lote
    calculado sea el lote que de verdad arriesga lo que se queria.
    """
    esp = B.especificacion()
    s = esp["simbolo"]
    D.ACTIVOS[s] = {
        "nombre": esp["nombre"],
        "contrato": esp["contrato"],
        "tick": esp["tick"],
        # El colchon que el capitulo 20.2 suma al stop («minimo de la
        # barrida - 0,30 USD, margen de spread»). Se ata al spread real
        # del broker con un suelo: un spread momentaneamente estrecho no
        # deberia poner el stop pegado a la mecha.
        "margen_sl": max(esp["spread"] * 2, esp["stops_min"], esp["tick"] * 3),
        "spread": esp["spread"],
        "comision": COMISION,
    }
    return esp


# ══════════════════════════════════════════════════════════════════════
# Reconciliar: lo que el broker hizo manda
# ══════════════════════════════════════════════════════════════════════

def reconciliar(simbolo: str) -> None:
    """
    Trae del broker el estado real y lo escribe en el diario.

    Va PRIMERO en el ciclo y corre siempre, dentro o fuera de killzone:
    una orden colocada a las 10:55 puede llenarse a las 11:20 y cerrar a
    las 14:00, y si la reconciliacion solo pasara en ventana el diario
    contaria otra historia que la cuenta.

    Lo que el broker dice pisa lo que el sistema creia. El precio de
    llenado real trae slippage, la comision real puede no ser la
    supuesta, y esconder eso convertiria el forward test en un backtest
    con mejor letra.
    """
    vivas = DI.leer_estado(("pendiente", "abierta"), origen="broker")
    if not vivas:
        return
    abiertas = {p["ticket"]: p for p in B.posiciones(simbolo, MAGIA)}
    pend = {o["ticket"] for o in B.pendientes(simbolo, MAGIA)}
    desde = datetime.now(NY) - timedelta(days=7)
    hechos: dict[int, list[dict]] = {}
    for d in B.cerradas(desde, MAGIA):
        hechos.setdefault(d.get("position_id") or d.get("order"), []).append(d)

    for o in vivas:
        tk = _ticket(o)
        if tk is None:
            continue
        if tk in pend:
            continue                       # sigue esperando, nada que anotar
        if tk in abiertas:
            if o["estado"] != "abierta":
                DI.actualizar(o["id"], {"estado": "abierta"})
                log.info("ENTRA  ticket %s a %s", tk, abiertas[tk].get("price_open"))
            continue

        ds = hechos.get(tk) or []
        if not ds:
            # Ni pendiente, ni abierta, ni en el historial: la orden
            # caduco sin llenarse. Es el caso que el capitulo 20.2
            # contempla — «expira a los 45 minutos».
            DI.actualizar(o["id"], {"estado": "expirada", "r": 0.0,
                                    "salida": str(datetime.now(NY))})
            log.info("EXPIRA ticket %s sin llenarse", tk)
            continue

        # Cerrada: el resultado sale del dinero, no de una simulacion.
        bruto = sum(d.get("profit", 0) for d in ds)
        costes = sum((d.get("commission", 0) or 0) + (d.get("swap", 0) or 0)
                     for d in ds)
        neto = bruto + costes
        riesgo = _riesgo_en_dinero(o)
        r = round(neto / riesgo, 4) if riesgo > 0 else 0.0
        estado = "ganancia" if r > 0.05 else "perdida" if r < -0.05 else "be"
        DI.actualizar(o["id"], {
            "estado": estado, "r": r,
            "r_bruto": round(bruto / riesgo, 4) if riesgo > 0 else 0.0,
            "costo_r": round(-costes / riesgo, 4) if riesgo > 0 else 0.0,
            "salida": str(datetime.now(NY)),
            "nota": f"{o.get('nota') or ''} · broker: {neto:+.2f} "
                    f"({bruto:+.2f} bruto, {costes:+.2f} costes)"[:900],
        })
        log.info("CIERRA ticket %s -> %s  %+.2fR  (%.2f en dinero)",
                 tk, estado, r, neto)


def _ticket(o: dict) -> int | None:
    """El ticket vive en la nota, que es texto libre en el diario."""
    import re
    m = re.search(r"ticket=(\d+)", str(o.get("nota") or ""))
    return int(m.group(1)) if m else None


def _riesgo_en_dinero(o: dict) -> float:
    """Cuanto valia 1R en dinero para esta operacion concreta."""
    dist = abs((o.get("entrada") or 0) - (o.get("sl") or 0))
    esp = D.ACTIVOS.get(o["simbolo"], {})
    return dist * (o.get("lotes") or 0) * (esp.get("contrato") or 1)


# ══════════════════════════════════════════════════════════════════════
# El ciclo
# ══════════════════════════════════════════════════════════════════════

def _estado_dia(simbolo: str, hoy: str) -> G.EstadoDia:
    """
    Los limites del dia, reconstruidos del diario y no de memoria.

    Si el proceso se reinicia a media sesion, una variable en memoria
    volveria a cero y el sistema creeria que no ha operado hoy. El
    capitulo 14.1 dice «parar tras 2 perdidas»: un contador que se
    reinicia no para nunca.
    """
    e = G.EstadoDia(fecha=hoy)
    for o in DI.leer(hoy, hoy, origen="broker"):
        if o["simbolo"] == simbolo and o.get("r") is not None:
            e.registrar(o["r"])
    return e


def ciclo() -> None:
    ahora = datetime.now(NY)
    simbolo = B.SIMBOLO
    est = S.estado(ahora)

    # ── 0. Lo que el broker hizo ──────────────────────────────────────
    reconciliar(simbolo)

    if not est["operable"]:
        vivas = len(DI.leer_estado(("pendiente", "abierta"), origen="broker"))
        log.info("%s ET · fuera de ventana (%s). Proxima: %s · %d vivas",
                 est["hora_ny"], ", ".join(est["sesiones"]) or "sin sesion",
                 S.proxima_operable(ahora), vivas)
        return

    hoy = str(ahora.date())
    e_dia = _estado_dia(simbolo, hoy)
    freno = G.puede_operar(e_dia)
    if freno:
        log.info("%s: %s", simbolo, freno)
        return

    # Con una orden nuestra viva no se abre otra. El capitulo 20.2 da un
    # tope de dos por dia, pero solapar dos entradas del mismo modelo en
    # el mismo activo es la misma apuesta contada dos veces.
    if B.pendientes(simbolo, MAGIA) or B.posiciones(simbolo, MAGIA):
        log.info("%s: ya hay una orden o posicion viva", simbolo)
        return

    # ── 1. Velas del broker ───────────────────────────────────────────
    d = B.datos_completos(simbolo)
    if len(d.get("m5") or []) < 200:
        log.warning("%s: faltan velas de 5M (%d)", simbolo, len(d.get("m5") or []))
        return
    ctx = D.contexto(simbolo, d, ahora)

    # ── 2. Los nueve modelos y el checklist ───────────────────────────
    sen, _ = ES.evaluar_todo(simbolo, ctx)
    if not sen.hay:
        log.info("%s: %s", simbolo, sen.motivo)
        return
    lect = I.leer(ctx)
    ev = C.evaluar(sen, ctx, e_dia, _cuenta.get("balance", 10000.0), lect, None)
    if not ev.aprobada:
        log.info("%s: senal %s VETADA — %s", simbolo, sen.setup, ev.veto)
        DI.guardar({"simbolo": simbolo, "setup": sen.setup, "lado": sen.lado,
                    "ventana": sen.ventana, "sesgo_htf": sen.sesgo,
                    "estado": "vetada", "nota": ev.veto, "origen": "broker"})
        return

    # ── 3 y 4. Lote y orden ───────────────────────────────────────────
    res = B.enviar(sen, ev.lotes, simbolo, ES.MINUTOS_VIGENCIA, MAGIA)
    nota = " · ".join(sen.pasos)
    if res.get("ticket"):
        nota = f"ticket={res['ticket']} · {nota}"
    DI.guardar({
        "simbolo": simbolo, "nombre": D.ACTIVOS[simbolo]["nombre"],
        "ventana": sen.ventana, "sesgo_htf": sen.sesgo, "setup": sen.setup,
        "zona": sen.zona, "lado": sen.lado,
        "entrada": sen.entrada, "sl": sen.sl, "tp": sen.tp,
        "rr_objetivo": sen.rr, "lotes": res["peticion"]["volume"],
        "riesgo_pct": G.RIESGO_POR_OPERACION,
        "confluencias": ev.confluencias, "calidad": int(ev.puntos),
        "estado": "pendiente" if res.get("enviada") else "no_enviada",
        "esquema": "todo_en_tp", "origen": "broker",
        "nota": (nota if res.get("enviada")
                 else f"NO ENVIADA: {res.get('motivo')} · {nota}")[:900],
    })
    if res.get("enviada"):
        log.info("ORDEN %s %s %s @ %.2f  SL %.2f  TP %.2f  %.2fR  %s lotes  "
                 "%d confluencias", simbolo, sen.lado.upper(), sen.setup,
                 sen.entrada, sen.sl, sen.tp, sen.rr,
                 res["peticion"]["volume"], ev.puntos)


async def main() -> None:
    global _cuenta

    def _fin(*_):
        global _parar
        _parar = True
        log.info("parando al terminar el ciclo")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _fin)
        except (ValueError, AttributeError):
            pass

    try:
        _cuenta = B.conectar()
    except Exception as exc:  # noqa: BLE001
        # Es lo primero que falla si algo no esta puesto, asi que dice que
        # hacer en vez de soltar un traceback. El detalle esta en
        # materias/CONECTAR.md.
        log.error("%s", exc)
        log.error("")
        log.error("  1. Abri la terminal de MetaTrader 5 y entra a la cuenta.")
        log.error("  2. Herramientas > Opciones > Asesores Expertos:")
        log.error("     marca «Permitir trading algoritmico».")
        log.error("  3. Corre  python -m materias.broker  para comprobarlo todo.")
        log.error("")
        log.error("  Si todavia no tenes cuenta MT5: la de TradingView no sirve,")
        log.error("  es de tipo «Tickmill Trader». Hay que abrir una de MT5 aparte.")
        return
    if not _cuenta["es_demo"] and not ACEPTO_REAL:
        log.error("la cuenta %s es REAL y no hay MATERIAS_ACEPTO_REAL=1.",
                  _cuenta.get("login"))
        log.error("El capitulo 20.1 pone el forward test en demo (paso 7) "
                  "antes del dinero real, y el backtest todavia no pasa")
        log.error("tres de los ocho listones del 18.7. Usa una cuenta demo.")
        B.desconectar()
        return
    if not _cuenta.get("trade_allowed"):
        log.warning("el trading algoritmico esta BLOQUEADO en la terminal: "
                    "Herramientas > Opciones > Asesores Expertos")

    esp = registrar_simbolo()
    log.info("%s · contrato %s · spread %.2f · stop minimo %.2f · lote %s-%s",
             esp["simbolo"], esp["contrato"], esp["spread"], esp["stops_min"],
             esp["lote_min"], esp["lote_max"])
    log.info("diario en %s", AL.destino())
    if not B.ENVIAR_DE_VERDAD:
        log.warning("MODO SECO: se calcula y se registra, pero NO se manda "
                    "ninguna orden. Pone MATERIAS_ENVIAR=1 para operar.")

    # El tablero, en el mismo proceso. Ver lo que hace importa mas cuando
    # hay ordenes de verdad que cuando solo hay papel.
    from materias import api as API
    puerto = int(os.getenv("PORT", os.getenv("MATERIAS_PUERTO", "8790")))

    async def bucle():
        while not _parar:
            t0 = datetime.now()
            try:
                await asyncio.to_thread(ciclo)
            except Exception:  # noqa: BLE001
                log.exception("el ciclo fallo; se reintenta en el siguiente")
            resto = CADA_S - (datetime.now() - t0).total_seconds()
            while resto > 0 and not _parar:
                await asyncio.sleep(min(resto, 5))
                resto -= 5
        B.desconectar()

    await asyncio.gather(API.servir(puerto), bucle())


if __name__ == "__main__":
    asyncio.run(main())
