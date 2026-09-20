"""
El pipeline de un mercado, como secuencia explícita de pasos.

POR QUÉ ASÍ
───────────
Antes esto vivía en un método de 80 líneas con seis salidas tempranas:
investigar, pronosticar, escalar la cascada, pedir el book, revisar vetos,
decidir, validar riesgo, ordenar, guardar y escribir el vault. Cada cambio
obligaba a releerlo entero para saber qué pasaba antes y qué después.

Ahora cada paso es una función que recibe el mismo contexto y devuelve
`True` para seguir o `False` para parar. El orden está en una lista y se
lee de un vistazo:

    PASOS = [investigar, pronosticar, cotizar, decidir, validar, ejecutar]

Tres cosas se ganan con eso:

 1. **Se lee linealmente.** El flujo es la lista, no el control de flujo.
 2. **Cada paso se prueba solo.** Recibe un contexto, devuelve un bool.
 3. **Observabilidad gratis.** `parado_en` dice exactamente qué paso
    descartó cada mercado, sin instrumentar nada.

Añadir un paso —un filtro de correlación, un segundo pase de evidencia—
es insertar una función en la lista, no cirugía en un método grande.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from core.events import emit
from core.risk import OrderIntent, RiskKernel, Side
from pilot_a_llm.edge import Bet, Decision, EdgeConfig, Quote, decide
from pilot_a_llm.markets import Candidate

log = logging.getLogger("pipeline")


class _Servicios(Protocol):
    """Lo que un paso necesita. Lo provee PilotA."""

    researcher: object
    forecaster: object
    market_data: object
    exec_client: object
    kernel: RiskKernel | None
    budget: object
    bankroll: object
    repo: object
    vault: object
    # El tope del kernel. Cruza la frontera entre PilotA y los pasos, asi
    # que es parte del contrato y no un atributo privado al que se llega
    # por casualidad: sin declararlo aqui, escribirlo mal no da error de
    # tipos y revienta recien en produccion.
    max_order_usd: float


@dataclass(slots=True)
class MarketRun:
    """
    El estado de UN mercado atravesando el pipeline. Cada paso lo llena un
    poco más; si para, queda registrado dónde y por qué.
    """

    candidate: Candidate
    bankroll_usd: float

    evidence: object | None = None
    forecast: object | None = None
    quote: Quote | None = None
    decision: Decision | None = None
    order: object | None = None

    # Estado que un paso deja para el siguiente. Son campos declarados y no
    # atributos sueltos porque el dataclass usa `slots=True`: asignarle algo
    # no declarado lanza AttributeError. Paso desapercibido hasta produccion,
    # donde ningun mercado lograba pasar de `cotizar`.
    books: tuple | None = None
    book: object | None = None
    intent: object | None = None
    intent_id: int | None = None

    parado_en: str = ""
    motivo: str = ""
    neurons: float = 0.0
    cost_usd: float = 0.0
    escalado: bool = False
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def parar(self, paso: str, motivo: str) -> bool:
        self.parado_en = paso
        self.motivo = motivo
        return False

    @property
    def accion(self) -> str:
        if self.decision and self.decision:
            return str(self.decision.bet)
        return "PASS"

    @property
    def opero(self) -> bool:
        return self.order is not None


# ── los pasos ────────────────────────────────────────────────────────────


async def investigar(r: MarketRun, s: _Servicios) -> bool:
    """Hechos con fecha. Sin evidencia el modelo rellena con su prior."""
    r.evidence = await s.researcher.investigate(r.candidate)
    s.budget.spend_gemini()
    r.cost_usd += r.evidence.cost_usd
    if r.evidence.is_thin:
        return r.parar("investigar", "evidencia insuficiente")
    return True


async def pronosticar(r: MarketRun, s: _Servicios) -> bool:
    """
    Ensemble en cascada: el barato mira todo, el caro solo donde hay algo.
    La cascada vive dentro del Forecaster, no aquí — es una decisión sobre
    cómo pronosticar, no sobre cómo orquestar.
    """
    f = await s.forecaster.forecast_cascada(
        r.candidate, r.evidence, budget=s.budget
    )
    if f is None:
        return r.parar("pronosticar", "ningún modelo respondió")

    r.forecast = f
    r.neurons = f.neurons
    r.cost_usd += f.cost_usd
    r.escalado = len(f.per_model) > 1
    s.budget.spend_neurons(f.neurons)

    if f.vetoed:
        return r.parar("veto", f.veto_reason)
    return True


async def cotizar(r: MarketRun, s: _Servicios) -> bool:
    """Precios reales del book. Se paga el ask, nunca el mid."""
    c = r.candidate
    books = await s.market_data.get_books([c.token_yes, c.token_no])
    by, bn = books.get(c.token_yes), books.get(c.token_no)
    if by is None or bn is None or by.best_ask is None or bn.best_ask is None:
        return r.parar("cotizar", "sin book utilizable")

    r.quote = Quote(by.best_ask, bn.best_ask, by.best_bid or 0, bn.best_bid or 0)
    r.books = (by, bn)
    return True


async def decidir(r: MarketRun, s: _Servicios) -> bool:
    """
    Kelly fraccional. El tope NUNCA debe superar el del kernel: si pide más
    de lo que el riesgo acepta, la orden se rechaza y se pierde todo el
    trabajo del mercado.
    """
    tope = min(s.max_order_usd, r.bankroll_usd * 0.2)
    r.decision = decide(
        r.forecast.p_calibrated, r.quote, r.bankroll_usd,
        EdgeConfig(max_position_usd=tope),
    )
    if not r.decision:
        return r.parar("decidir", r.decision.reason)
    return True


async def validar(r: MarketRun, s: _Servicios) -> bool:
    """El risk kernel. Última palabra, y no negocia con la oportunidad."""
    c, d = r.candidate, r.decision
    token = c.token_yes if d.bet is Bet.YES else c.token_no
    by, bn = r.books
    book = by if d.bet is Bet.YES else bn

    r.intent = OrderIntent(
        pilot="a", token_id=token, side=Side.BUY,
        price=d.price_paid, size=d.shares,
        market_slug=c.slug, reason=r.forecast.means_yes[:200],
    )
    r.book = book

    verdict = s.kernel.check(r.intent, mid=book.mid)
    r.intent_id = await s.repo.record_intent(
        r.intent, verdict, mid=book.mid, strategy="forecast"
    )
    if not verdict.approved:
        return r.parar("kernel", verdict.detail)
    return True


async def ejecutar(r: MarketRun, s: _Servicios) -> bool:
    """Coloca la orden. La sección crítica del kernel va bajo lock."""
    intent = r.intent    
    book = r.book        

    placed = await s.exec_client.place(intent, book=book)
    s.kernel.on_order_sent(intent)
    r.order = placed

    realized = 0.0
    if placed.status == "filled":
        s.kernel.on_order_closed()
        realized = s.kernel.on_fill(
            intent.token_id, Side.BUY, placed.price, placed.size
        )
        if placed.is_paper:
            s.bankroll.on_paper_fill(placed.price * placed.size)

    await s.repo.record_order(
        placed.order_id, r.intent_id, intent,
        status=placed.status, is_paper=placed.is_paper,
        latency_ms=placed.latency_ms, raw=placed.raw,
    )
    if placed.status == "filled":
        await s.repo.record_fill(
            placed.order_id, intent.token_id, "BUY", placed.price, placed.size,
            realized_pnl=realized, is_paper=placed.is_paper,
        )
    return True


ANALISIS = [investigar, pronosticar]
"""
Mirar, sin tocar el mercado. No coloca órdenes ni consume riesgo: se puede
correr sobre cualquier candidato y descartarlo después sin consecuencia.
"""

OPERACION = [cotizar, decidir, validar, ejecutar]
"""
Actuar. Indivisible: `validar` y `ejecutar` comparten el estado en memoria
del kernel bajo un lock, y entre `check()` y `on_order_sent()` hay awaits.
Partirlo devuelve la carrera que ese lock existe para evitar.
"""

PASOS = ANALISIS + OPERACION
"""
El flujo completo. El corte entre las dos fases es donde el orquestador
puede meterse: ver el pronóstico y decidir si vale la pena operarlo.
"""

# Pasos que tocan el risk kernel y no pueden solaparse: entre `check()` y
# `on_order_sent()` hay awaits, y dos mercados concurrentes podrían pasar
# el mismo límite antes de que ninguno lo consuma.
BAJO_LOCK = {"validar", "ejecutar"}


async def correr(
    r: MarketRun, s: _Servicios, *, lock: asyncio.Lock, pasos: list | None = None
) -> MarketRun:
    """
    Atraviesa el pipeline. Nunca lanza: un mercado que falla no puede
    tumbar el ciclo, y queda registrado dónde se rompió.

    `pasos` permite correr solo una fase — ANALISIS u OPERACION — para que
    el orquestador decida en medio.
    """
    slug = r.candidate.slug
    for paso in pasos or PASOS:
        t0 = time.monotonic()
        emit("paso", f"{paso.__name__}: {slug[:44]}",
             slug=slug, paso=paso.__name__)
        try:
            if paso.__name__ in BAJO_LOCK:
                async with lock:
                    seguir = await paso(r, s)
            else:
                seguir = await paso(r, s)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s falló en %s: %s", slug[:40], paso.__name__, exc)
            r.parar(paso.__name__, f"excepción: {exc!r}"[:300])
            # Una excepcion NO es un PASS prudente. Marcarla distinto es
            # lo que hubiera hecho visible que 105 de 178 PASS eran
            # caidas y no decisiones.
            emit("error", f"{paso.__name__} se rompió en {slug[:36]}",
                 slug=slug, paso=paso.__name__, error=repr(exc)[:200])
            break
        ms = round((time.monotonic() - t0) * 1000)
        if not seguir:
            emit("parada", f"{slug[:36]} paró en {paso.__name__}: {r.motivo[:90]}",
                 slug=slug, paso=paso.__name__, motivo=r.motivo, ms=ms)
            break
        emit("ok", f"{paso.__name__} ok: {slug[:40]}",
             slug=slug, paso=paso.__name__, ms=ms, **_detalle(r, paso.__name__))

    _log(r)
    return r


def _detalle(r: MarketRun, paso: str) -> dict:
    """Lo que vale la pena ver del paso recien terminado, y nada mas."""
    if paso == "pronosticar" and r.forecast:
        return {"p": round(r.forecast.p_calibrated, 4),
                "precio": round(r.candidate.price, 4),
                "modelos": list(getattr(r.forecast, "per_model", {}) or {})}
    if paso == "cotizar" and r.quote:
        return {"ask_yes": r.quote.ask_yes, "ask_no": r.quote.ask_no}
    if paso == "decidir" and r.decision:
        return {"apuesta": str(r.decision.bet), "usd": round(r.decision.size_usd, 2),
                "shares": round(r.decision.shares, 1)}
    if paso == "ejecutar" and r.order:
        return {"orden": r.order.order_id, "estado": r.order.status,
                "precio": r.order.price, "size": r.order.size}
    return {}


def _log(r: MarketRun) -> None:
    slug = r.candidate.slug[:38]
    if r.opero:
        d = r.decision
        log.info(
            "ORDEN %s %s | p=%.3f vs %.3f | edge %+.3f | $%.2f",
            d.bet, slug, r.forecast.p_calibrated, r.candidate.price,
            d.edge, d.size_usd,
        )
    elif r.parado_en == "veto":
        log.info("VETO %s — %s", slug, r.motivo[:100])
    else:
        log.info("%s %s — %s", r.parado_en.upper(), slug, r.motivo[:90])


def resumen(runs: list[MarketRun]) -> dict:
    """
    Estadísticas del ciclo. `parado_en` da el desglose gratis: sin
    instrumentar nada se sabe qué paso descartó cada mercado.
    """
    por_paso: dict[str, int] = {}
    for r in runs:
        if r.parado_en:
            por_paso[r.parado_en] = por_paso.get(r.parado_en, 0) + 1
    return {
        "vistos": len(runs),
        "ordenes": sum(1 for r in runs if r.opero),
        "escalados": sum(1 for r in runs if r.escalado),
        "descartes": por_paso,
        "neurons": round(sum(r.neurons for r in runs), 1),
        "costo": round(sum(r.cost_usd for r in runs), 6),
    }
