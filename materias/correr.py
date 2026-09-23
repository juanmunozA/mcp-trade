"""
El sistema en vivo: `python -m materias.correr`

QUE HACE EN CADA CICLO
──────────────────────
0. Resuelve lo que ya estaba abierto. Va PRIMERO y corre siempre, dentro
   o fuera de killzone: una operacion abierta a las 10:55 puede tocar su
   stop a las 13:00, y si el ciclo se saltara la resolucion por estar
   fuera de ventana el resultado se anotaria tarde o no se anotaria.
1. Mira la hora. Si no estamos en killzone, no busca senales nuevas y
   dice cuanto falta. «Fuera de ellas el precio suele lateralizar»
   (cap. 10) y buscar setups donde la guia dice que no los hay es
   gastar por gusto.
2. Al abrir la primera killzone del dia, escribe el PLAN (cap. 3.2) de
   cada activo y lo guarda. El plan va antes que cualquier senal.
3. Dentro de killzone: baja datos, corre los nueve modelos, pasa las
   senales por el checklist universal (cap. 11.9) y elige cuales caben
   dentro del riesgo total permitido contando correlacion (cap. 14.1).
4. Guarda todo en el diario (cap. 19.1): las senales, los vetos y el
   plan. Sin registro no hay revision, y sin revision no hay mejora.

SOBRE QUE OPERA
Solo sobre el ORO. Es el paso 1 del capitulo 20.1 -- «elige un solo
activo» -- y es el activo sobre el que la guia escribio su estrategia
completa, la del capitulo 20.2: «Barrida de sesion en XAUUSD». Los demas
se descargan porque la SMT divergence del capitulo 15.5 los necesita para
leer el oro, pero no generan senales.

LO QUE NO HACE, Y HAY QUE DECIRLO CLARO
No envia ordenes a ningun broker. Opera EN PAPEL: emite la senal con sus
niveles y su lote, la registra en el momento de emitirla, y despues la
sigue contra el precio real hasta que toca stop u objetivo.

Eso es exactamente el paso 7 del proceso de ocho pasos -- «forward test
en demo o micro-riesgo durante 1-3 meses» -- y es lo que falta antes de
poner dinero. El backtest da expectativa positiva, pero una sola
operacion vale el 54 % de su resultado y el Sharpe se queda en 0,14: es
una hipotesis, no un permiso.

El punto de enganche con un broker real es `ejecutar()`. Esta aislado a
proposito para que enchufarlo sea cambiar una funcion y no reescribir el
ciclo.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    # Comodidad local: en Render las variables llegan del panel y esto
    # no hace nada. No se pone como dependencia obligatoria porque el
    # sistema tiene que arrancar sin ella.
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from materias import almacen as AL
from materias import calidad as C
from materias import correlaciones as COR
from materias import datos as D
from materias import diario as DI
from materias import estrategia as ES
from materias import gestion as G
from materias import indicadores as I
from materias import papel as PA
from materias import rutina as R
from materias import sesiones as S

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("materias")

NY = ZoneInfo("America/New_York")
CADA_S = int(os.getenv("MATERIAS_CADA_S", "300"))
PUERTO = int(os.getenv("PORT", os.getenv("MATERIAS_PUERTO", "8790")))
"""
Render inyecta `PORT` y espera que algo escuche ahi.

Si nadie escucha, Render da el despliegue por fallido y reinicia el
contenedor en bucle, aunque el ciclo este funcionando perfectamente. Por
eso el tablero y el ciclo viven en el mismo proceso: no es una comodidad,
es lo que hace que el servicio arranque.
"""
CAPITAL = float(os.getenv("MATERIAS_CAPITAL", "10000"))
ACTIVOS = [s for s in os.getenv("MATERIAS_ACTIVOS", D.PRINCIPAL).split(",") if s]
"""
Sobre que se opera en papel. Por defecto, solo el oro.

Es el paso 1 del capitulo 20.1: «elige UN SOLO activo». Operar los cinco
a la vez multiplica por cinco las operaciones y por cinco el ruido, y
divide entre cinco lo que se aprende de cada uno.

Los demas se siguen descargando -- la SMT del capitulo 15.5 los necesita
-- pero no generan senales.
"""

_parar = False
_plan_de = ""
"""La fecha cuyo plan ya se escribio, para no repetirlo cada ciclo."""

def _comprometido() -> list[dict]:
    """
    Lo que hoy consume riesgo, leido del diario y no de memoria.

    Una limite pendiente ya compromete riesgo: el tope del capitulo 14.1
    es sobre riesgo COMPROMETIDO, no sobre posiciones llenadas. Y se lee
    de la base en vez de guardarlo en una variable porque el proceso se
    reinicia en cada despliegue, y una lista en memoria volveria a cero
    dejando al sistema creyendo que no tiene nada abierto.
    """
    return [{"simbolo": o["simbolo"], "lado": o["lado"],
             "riesgo_pct": o.get("riesgo_pct") or G.RIESGO_POR_OPERACION}
            for o in DI.leer_estado(("pendiente", "abierta"), origen="papel")]


_estado_dia: dict[str, G.EstadoDia] = {}


def _estado(simbolo: str, hoy: str) -> G.EstadoDia:
    e = _estado_dia.get(simbolo)
    if e is None or e.fecha != hoy:
        e = G.EstadoDia(fecha=hoy)
        _estado_dia[simbolo] = e
    return e


def ejecutar(sen, ev: C.Evaluacion, ctx: dict) -> None:
    """
    Emite la operacion en papel. El enganche con un broker va aqui.

    Cuando se conecte a uno real, lo que se agrega es el envio: orden
    limite en `sen.entrada` con vigencia de 45 minutos, SL y TP adjuntos
    DESDE EL ENVIO -- «SL fijo desde la entrada; automatizar SL/TP»
    (cap. 19) -- y el tamano `ev.lotes`, que sale de la formula del
    capitulo 14.2. El registro en el diario se queda igual: es lo que
    permite comparar lo que el sistema quiso hacer con lo que el broker
    hizo de verdad.
    """
    PA.abrir(sen, ev, CAPITAL)
    log.info("SENAL %s %s %s  entrada %.4f  SL %.4f  TP %.4f  %.2fR  "
             "%.2f lotes  %d confluencias  [%s]",
             sen.simbolo, sen.lado.upper(), sen.setup, sen.entrada, sen.sl,
             sen.tp, sen.rr, ev.lotes, ev.puntos, sen.ventana)


async def ciclo() -> None:
    global _plan_de
    ahora = datetime.now(NY)
    est = S.estado(ahora)

    # Fuera de ventana solo hace falta el principal, y solo para cerrar
    # lo que quedo abierto. Bajar los auxiliares de la SMT para eso seria
    # gastar por gusto.
    buscar = est["operable"]
    pedir = (ACTIVOS + list(COR.AUXILIARES)) if buscar else [D.PRINCIPAL]
    datos = await D.cargar(pedir)
    if not datos:
        log.warning("no se pudo bajar ningun activo")
        return

    # ── 0. Resolver lo abierto. Siempre, dentro o fuera de killzone ───
    for s, d in datos.items():
        for c in PA.resolver(s, d.get("m5", [])):
            if c["estado"] in ("ganancia", "perdida", "be"):
                log.info("CIERRA %s %s %s -> %s  %+.2fR  (bruto %+.2fR)",
                         c["simbolo"], c["lado"], c["setup"], c["estado"],
                         c.get("r") or 0, c.get("r_bruto") or 0)
                _estado(s, str(ahora.date())).registrar(c.get("r") or 0)
            elif c["estado"] == "abierta":
                log.info("ENTRA  %s %s %s en %.4f", c["simbolo"], c["lado"],
                         c["setup"], c["entrada"])
            elif c["estado"] == "expirada":
                log.info("EXPIRA %s %s: el precio no volvio al nivel en %d min",
                         c["simbolo"], c["setup"], ES.MINUTOS_VIGENCIA)

    if not buscar:
        r = PA.resumen()
        log.info("%s ET · fuera de ventana (%s). Proxima: %s · papel: "
                 "%d cerradas %+.2fR, %d abiertas, %d pendientes",
                 est["hora_ny"], ", ".join(est["sesiones"]) or "sin sesion",
                 S.proxima_operable(ahora), r["cerradas"], r["r_total"],
                 r["abiertas"], r["pendientes"])
        return

    hoy = str(ahora.date())
    if _plan_de != hoy:
        for s in ACTIVOS:
            if s in datos:
                p = R.analizar(s, datos[s], ahora)
                DI.guardar_plan(p.dict())
                log.info("PLAN %s: sesgo %s (%s), fase %s, busca %s",
                         s, p.sesgo, p.confianza, p.fase, p.objetivo_liquidez)
        _plan_de = hoy

    # ── Los nueve modelos sobre el activo principal ───────────────────
    candidatas: list[tuple] = []
    abiertas = _comprometido()
    for s in ACTIVOS:
        d = datos.get(s)
        if not d:
            continue
        e_dia = _estado(s, hoy)
        freno = G.puede_operar(e_dia)
        if freno:
            log.info("%s: %s", s, freno)
            continue
        ctx = D.contexto(s, d, ahora)
        sen, intentos = ES.evaluar_todo(s, ctx)
        if not sen.hay:
            log.info("%s: %s", s, sen.motivo)
            continue
        lect = I.leer(ctx)
        div = COR.buscar(s, sen.lado, datos, ahora)
        ev = C.evaluar(sen, ctx, e_dia, CAPITAL, lect, div)
        if not ev.aprobada:
            log.info("%s: senal %s VETADA — %s", s, sen.setup, ev.veto)
            DI.guardar({"simbolo": s, "setup": sen.setup, "lado": sen.lado,
                        "ventana": sen.ventana, "sesgo_htf": sen.sesgo,
                        "estado": "vetada", "nota": ev.veto, "origen": "sistema"})
            continue
        candidatas.append((sen, ev, ctx))

    if not candidatas:
        return

    # ── ¿Cuales caben dentro del riesgo total? (cap. 14.1) ────────────
    elegidas = C.elegir([(s, e) for s, e, _ in candidatas], abiertas)
    tomadas = {id(s) for s, _ in elegidas}
    for sen, ev, ctx in candidatas:
        if id(sen) in tomadas:
            ejecutar(sen, ev, ctx)
        else:
            log.info("%s: senal buena descartada — %s", sen.simbolo, ev.veto)


async def bucle() -> None:
    """El ciclo cada CADA_S segundos, hasta que alguien pare el proceso."""
    while not _parar:
        t0 = datetime.now()
        try:
            await ciclo()
        except Exception:  # noqa: BLE001
            log.exception("el ciclo fallo; se reintenta en el siguiente")
        # Dormir lo que falte, para que el periodo sea el periodo y no
        # el periodo mas lo que tardo el ciclo. Se duerme a tramos de
        # cinco segundos en vez de uno largo para que una senal de
        # parada no tarde cinco minutos en surtir efecto.
        resto = CADA_S - (datetime.now() - t0).total_seconds()
        while resto > 0 and not _parar:
            await asyncio.sleep(min(resto, 5))
            resto -= 5


async def main() -> None:
    def _fin(*_):
        global _parar
        _parar = True
        log.info("parando al terminar el ciclo")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _fin)
        except (ValueError, AttributeError):
            pass

    r = PA.resumen()
    log.info("PAPEL · %d emitidas desde %s: %d cerradas (%+.2fR), "
             "%d abiertas, %d pendientes, %d expiradas",
             r["emitidas"], r["desde"] or "—", r["cerradas"], r["r_total"],
             r["abiertas"], r["pendientes"], r["expiradas"])
    log.info("principal: %s · riesgo %.2f%% · capital %.0f · cada %ds",
             ",".join(ACTIVOS), G.RIESGO_POR_OPERACION, CAPITAL, CADA_S)
    log.info("NO se envian ordenes a ningun broker: se opera en papel "
             "(cap. 20.1, paso 7)")
    log.info("diario en %s", AL.destino())

    # El tablero arranca PRIMERO y en paralelo. Si esperara al ciclo,
    # Render vería el puerto cerrado durante la descarga de datos del
    # primer ciclo y daría el despliegue por fallido.
    from materias import api as API
    await asyncio.gather(API.servir(PUERTO), bucle())


if __name__ == "__main__":
    asyncio.run(main())
