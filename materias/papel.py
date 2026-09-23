"""
Operativa en papel: emitir, seguir y resolver. Capitulo 20.1, paso 7.

QUE ES ESTO Y POR QUE VA AHORA
──────────────────────────────
«7. Forward test en demo o micro-riesgo durante 1-3 meses. Comparar
resultados reales vs backtest.» (cap. 20.1)

El backtest dice que el sistema tiene expectativa positiva sobre 152
operaciones de historico. Eso es una hipotesis, no un resultado: el
historico ya paso, y un backtest siempre puede estar midiendo una
casualidad -- sobre todo este, donde una sola operacion vale el 54 % del
total. El papel es la unica forma de saberlo sin arriesgar dinero,
porque las senales se emiten ANTES de saber que hace el precio.

LA REGLA QUE HACE QUE ESTO VALGA ALGO
Una operacion en papel se abre con el precio que habia cuando se emitio,
no con el que hubo despues. Suena obvio y es lo unico que separa un
forward test de un backtest disfrazado: si la senal se registra con
retraso, el sistema esta viendo el futuro y el resultado no significa
nada. Por eso `abrir()` guarda la fila en el momento de emitirla, con su
timestamp, y `resolver()` nunca toca entrada, SL ni TP.

COMO SE RESUELVE
Cada ciclo se vuelve a bajar la serie de 5M y se REPRODUCE la operacion
desde su vela de emision con `gestion.simular_salida` -- exactamente la
misma funcion que usa el backtest. Que sea la misma importa: si el papel
usara otra logica de salida, comparar papel contra backtest compararia
dos cosas distintas y la comparacion que pide el paso 7 no diria nada.

Reproducir en vez de ir acumulando estado tiene otra ventaja: es
idempotente. Si el proceso se reinicia -- y en Render se reinicia en cada
despliegue -- no se pierde ni se duplica nada, porque el estado vive en
el diario y se recalcula entero desde el precio.

QUE ESQUEMA DE SALIDA
`todo_en_tp`, sin parciales. Es lo que manda el capitulo 20.2 para esta
fase: «Sin parciales en la fase de prueba (ESTADISTICA LIMPIA)». Y ademas
es el que gano la comparacion de los cuatro en la ultima corrida. Los
otros tres se siguen calculando en el backtest; aca no, porque medir
cuatro salidas sobre la misma senal en papel infla la muestra sin aportar
operaciones nuevas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from materias import datos as D
from materias import diario as DI
from materias import estrategia as ES
from materias import gestion as G

log = logging.getLogger("materias.papel")

ESQUEMA = next(e for e in G.ESQUEMAS if e.nombre == "todo_en_tp")
"""«Sin parciales en la fase de prueba (estadistica limpia)» (cap. 20.2)."""

PENDIENTE = "pendiente"
ABIERTA = "abierta"
FINALES = ("ganancia", "perdida", "be", "expirada")


def abrir(sen, ev, capital: float) -> int:
    """
    Registra la senal en el momento de emitirla. No espera a nada.

    Los niveles quedan congelados aqui: `resolver()` los lee pero no los
    escribe nunca. Es la unica garantia de que el resultado no se ajusto
    despues de ver el precio.
    """
    costo = D.costo(sen.simbolo)
    dist = abs(sen.entrada - sen.sl)
    return DI.guardar({
        "ts": str(ev.get("ahora") if isinstance(ev, dict) else datetime.now()),
        "simbolo": sen.simbolo,
        "nombre": D.ACTIVOS.get(sen.simbolo, {}).get("nombre", sen.simbolo),
        "ventana": sen.ventana, "sesgo_htf": sen.sesgo, "setup": sen.setup,
        "zona": sen.zona, "lado": sen.lado,
        "entrada": sen.entrada, "sl": sen.sl, "tp": sen.tp,
        "rr_objetivo": sen.rr,
        "lotes": ev.lotes if not isinstance(ev, dict) else 0,
        "riesgo_pct": G.RIESGO_POR_OPERACION,
        "confluencias": ev.confluencias if not isinstance(ev, dict) else [],
        "calidad": int(ev.puntos) if not isinstance(ev, dict) else 0,
        "estado": PENDIENTE,
        "costo_r": round(costo / dist, 4) if dist > 0 else 0.0,
        "esquema": ESQUEMA.nombre,
        "expira": sen.expira,
        "origen": "papel",
        "nota": " · ".join(sen.pasos),
    })


def _vivas(simbolo: str) -> list[dict]:
    """Las que todavia pueden cambiar de estado."""
    return [o for o in DI.leer_estado(("pendiente", "abierta"), origen="papel")
            if o["simbolo"] == simbolo]


def resolver(simbolo: str, m5: list[dict]) -> list[dict]:
    """
    Reproduce cada operacion viva contra el precio y actualiza su estado.

    Devuelve las que cambiaron, para poder anunciarlas en el log. Las que
    siguen igual no se tocan: escribir en la base cada cinco minutos una
    fila que no cambio solo ensucia el historial.
    """
    if not m5:
        return []
    cambios = []
    for o in _vivas(simbolo):
        try:
            nuevo = _estado_de(o, m5)
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo resolver la operacion %s: %r", o["id"], exc)
            continue
        if not nuevo or nuevo.get("estado") == o["estado"]:
            continue
        DI.actualizar(o["id"], nuevo)
        cambios.append({**o, **nuevo})
    return cambios


def _indice(m5: list[dict], ts: str) -> int | None:
    """La vela en la que se emitio la senal."""
    t = _fecha(ts)
    if t is None:
        return None
    for i, v in enumerate(m5):
        if v["dt"] >= t:
            return i
    return None


def _fecha(s) -> datetime | None:
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def _estado_de(o: dict, m5: list[dict]) -> dict | None:
    """
    Donde esta esta operacion ahora, reproduciendola desde su emision.

    Tres desenlaces: la orden limite no se lleno y expiro, se lleno y
    sigue abierta, o se lleno y cerro en SL o TP.
    """
    i = _indice(m5, o["ts"])
    if i is None:
        # La serie todavia no llega a esa vela, o ya no la incluye.
        return None

    expira = _fecha(o.get("expira")) or (m5[i]["dt"] + timedelta(minutes=ES.MINUTOS_VIGENCIA))
    entrada, sl, tp = o["entrada"], o["sl"], o["tp"]

    # ── ¿se lleno la limite? ──────────────────────────────────────────
    k = None
    for j in range(i + 1, len(m5)):
        if m5[j]["dt"] > expira:
            break
        if m5[j]["l"] <= entrada <= m5[j]["h"]:
            k = j
            break
    if k is None:
        # Solo se declara expirada cuando el precio YA paso de la
        # vigencia. Mientras no llegue, sigue pendiente: darla por
        # expirada antes de tiempo perderia entradas validas.
        if m5[-1]["dt"] > expira:
            # La salida es cuando EXPIRO, no cuando el ciclo se dio
            # cuenta. Si el proceso estuvo caido dos horas, anotar la
            # hora actual falsearia el historial (cap. 19.1).
            return {"estado": "expirada", "r": 0.0, "salida": str(expira)}
        return None

    # ── ya dentro: se sigue hasta SL o TP ─────────────────────────────
    r = G.simular_salida(m5, k - 1, o["lado"], entrada, sl, tp, ESQUEMA)
    if r["estado"] == "abierta":
        return {"estado": ABIERTA, "salida": None}
    bruto = r["r"]
    neto = round(bruto - (o.get("costo_r") or 0.0), 4)
    return {"estado": r["estado"], "r_bruto": bruto, "r": neto,
            "salida": r.get("salida")}


def resumen() -> dict:
    """Como va el forward test, para el log y el tablero."""
    todas = DI.leer_estado(None, origen="papel")
    cerradas = [o for o in todas if o.get("r") is not None
                and o["estado"] in ("ganancia", "perdida", "be")]
    return {
        "emitidas": len(todas),
        "pendientes": sum(1 for o in todas if o["estado"] == PENDIENTE),
        "abiertas": sum(1 for o in todas if o["estado"] == ABIERTA),
        "expiradas": sum(1 for o in todas if o["estado"] == "expirada"),
        "cerradas": len(cerradas),
        "r_total": round(sum(o["r"] for o in cerradas), 3),
        "desde": min((o["fecha"] for o in todas), default=None),
    }
