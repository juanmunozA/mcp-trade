"""
El ensemble: de evidencia a probabilidad calibrada, con detector de ambigüedad.

POR QUÉ VARIOS MODELOS
──────────────────────
Los errores de familias distintas están descorrelacionados. Medido en este
proyecto sobre un mercado real:

    Gemini          leyó "gain the most seats" como "creció más"   → 0.02
    DeepSeek-R1     leyó "obtiene el mayor número de escaños"      → 0.90
    llama-3.1-8b    leyó igual que DeepSeek                        → 0.95
    mercado                                                        → 0.805

Un solo modelo habría apostado $20 al lado equivocado. El desacuerdo entre
modelos (0.318) fue la alarma.

LOS TRES VETOS
──────────────
Cualquiera de ellos fuerza PASS, sin importar lo atractivo que parezca el edge:

 1. **Ambigüedad declarada** — el modelo dijo que la pregunta admite dos
    lecturas. Le creemos.
 2. **Desacuerdo alto** — los modelos no convergen. La pregunta es ambigua
    aunque ninguno lo haya dicho.
 3. **Divergencia extrema contra el mercado** — un edge real en mercados de
    predicción es de 5 a 15 puntos. Si te sale 70, lo probable no es que
    hayas encontrado oro: es que entendiste otra pregunta.

Ese tercer veto es contraintuitivo —rechaza justo las apuestas que parecen
más rentables— y es el que más dinero ahorra.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pilot_a_llm.calibration import CalibrationMap, combine, disagreement
from pilot_a_llm.markets import Candidate
from pilot_a_llm.prompts import (
    FORECAST_SCHEMA,
    FORECASTER_SYSTEM,
    build_forecast_prompt,
)
from pilot_a_llm.providers import Provider, ProviderError, QuotaExhausted
from pilot_a_llm.research import Evidence

log = logging.getLogger("forecast")


@dataclass(frozen=True, slots=True)
class VetoConfig:
    max_disagreement: float = 0.20
    """Desv. est. entre modelos. Por encima, la pregunta es ambigua."""

    max_divergence: float = 0.35
    """
    Diferencia máxima contra el precio de mercado. Calibrado sobre el caso
    real que medimos: la lectura errónea produjo 0.785 de divergencia; las
    lecturas correctas, 0.005 y 0.095.
    """

    min_models: int = 2
    """
    Dos opiniones para mover dinero.

    Con una, `disagreement` vale 0 siempre y el veto por desacuerdo -- una
    de las tres defensas -- deja de existir. Medido: 158 de 205
    pronosticos los firmo un solo modelo, y la orden de los Brewers salio
    de ahi, con un dato inventado que nadie contrasto.

    Cuesta volumen: hoy solo el 23% de los ciclos junta dos modelos. Es
    el precio correcto -- no operar le gana a operar a ciegas.
    """

    require_evidence: bool = True

    min_escalation: float = 0.05
    """
    Divergencia mínima para pagar la segunda opinión del modelo caro.
    Por debajo de esto el mercado se descarta igual, así que gastar 175
    neurons en confirmarlo es tirar el cupo del día.
    """


@dataclass(slots=True)
class Forecast:
    candidate: Candidate
    p_raw: float
    p_calibrated: float
    per_model: dict[str, float] = field(default_factory=dict)
    disagreement: float = 0.0

    means_yes: str = ""
    is_ambiguous: bool = False
    ambiguity_note: str = ""
    reference_class: str = ""
    base_rate: float = 0.5
    key_evidence: list[str] = field(default_factory=list)
    steps_required: str = ""
    confidence: str = "low"
    what_would_change_my_mind: str = ""
    resolution_risk: str = ""

    vetoed: bool = False
    veto_reason: str = ""
    cost_usd: float = 0.0
    neurons: float = 0.0
    latency_ms: int = 0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def divergence(self) -> float:
        return abs(self.p_calibrated - self.candidate.price)


class Forecaster:
    """
    `providers` es un dict {nombre: Provider} en orden de preferencia.
    El primero aporta los campos cualitativos (paráfrasis, tasa base); los
    demás solo votan con su probabilidad.
    """

    def __init__(
        self,
        providers: dict[str, Provider],
        *,
        calibration: CalibrationMap | None = None,
        weights: dict[str, float] | None = None,
        vetoes: VetoConfig | None = None,
        vault=None,
        fallback: dict[str, Provider] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("el ensemble necesita al menos un proveedor")
        self.providers = providers
        self.fallback = fallback or {}
        """
        Proveedores de otra nube, para cuando los titulares agoten su cuota.

        Medido: Cloudflare se agota y el agente queda mudo el resto del día
        —85 minutos o 20 horas, según cuándo pase. Con respaldo sigue
        pronosticando con cuota distinta.

        Es respaldo y no titular a propósito: el respaldo (Gemini) también
        hace la investigación, así que investigar y pronosticar con el mismo
        modelo correlaciona sus errores y el ensemble pierde sentido. Mejor
        eso que nada, peor que lo normal.
        """
        self.calibration = calibration
        self.weights = weights or {}
        self.vetoes = vetoes or VetoConfig()
        self.vault = vault
        """Vault de Obsidian. Si está, sus notas entran al prompt."""
        self.quota_exhausted = False
        """Lo enciende un 429. El ciclo lo consulta para cortar a tiempo."""

    async def forecast_cascada(
        self, c: Candidate, ev: Evidence, *, budget=None
    ) -> Forecast | None:
        """
        La cascada completa, en un solo sitio.

        Escalón 1: solo el modelo barato (4.5 neurons) mira el mercado.
        Escalón 2: si ve un desajuste que valdría la pena, se paga la
        segunda opinión (175 neurons, 39× más).

        Vive aquí y no en el orquestador porque es una decisión sobre CÓMO
        pronosticar. El pipeline solo pide un pronóstico; qué modelos se
        consultan y en qué orden es asunto del ensemble.

        El límite: solo se salta la segunda opinión cuando el barato dice
        que no hay nada. Si dice que sí, es obligatoria — sin ella no hay
        señal de desacuerdo, y ese veto evitó la peor apuesta que el
        sistema habría hecho.
        """
        barato = await self.forecast(c, ev, second_opinion=False)
        if barato is None or barato.vetoed:
            return barato

        vale_la_pena = barato.divergence >= self.vetoes.min_escalation
        if not vale_la_pena:
            return barato

        if budget is not None:
            modelos = [p.model for p in self.providers.values()]
            if not budget.can_afford(modelos):
                log.info("sin presupuesto para escalar %s", c.slug[:40])
                return barato

        completo = await self.forecast(c, ev, second_opinion=True)
        return completo if completo is not None else barato

    async def forecast(
        self, c: Candidate, ev: Evidence, *, second_opinion: bool = True
    ) -> Forecast | None:
        """
        Devuelve None solo si ningún modelo respondió algo utilizable.

        `second_opinion=False` consulta solo al modelo barato. Es el primer
        escalón de la cascada: medido, DeepSeek cuesta 175 neurons y llama
        4.5 — 39× más. Gastar el caro en mercados que el barato ya descarta
        agota el cupo diario en un solo ciclo.

        El costo: sin segundo modelo no hay señal de desacuerdo, así que ese
        veto queda ciego. Por eso la cascada solo salta el segundo modelo
        cuando el primero ya dice que no hay nada — si dice que sí, la
        segunda opinión es obligatoria.
        """
        started = datetime.now(UTC)

        if self.vetoes.require_evidence and ev.is_thin:
            return self._empty_veto(
                c, f"evidencia insuficiente ({len(ev.text)} chars, "
                   f"{len(ev.sources)} fuentes)"
            )

        # Las notas del operador (vault de Obsidian) filtradas por categoría.
        notes = self.vault.read_context(category=c.category) if self.vault else ""

        prompt = build_forecast_prompt(
            question=c.question,
            resolution_rules=c.rules,
            evidence=ev.text,
            today=f"{datetime.now(UTC):%Y-%m-%d}",
            days_to_resolution=c.days_to_resolution,
            operator_notes=notes,
        )

        # Si los titulares ya agotaron su cuota hoy, se va directo al
        # respaldo en vez de coleccionar 429s.
        if self.quota_exhausted and self.fallback:
            activos = dict(self.fallback)
            log.info("cuota agotada: usando respaldo %s", list(activos))
        else:
            # El modelo barato va primero en el dict; con second_opinion=False
            # solo se consulta a él.
            activos = dict(self.providers) if second_opinion else dict(
                list(self.providers.items())[:1]
            )

        results = await asyncio.gather(
            *(self._ask(name, p, prompt) for name, p in activos.items()),
            return_exceptions=True,
        )

        parsed: dict[str, dict] = {}
        cost = 0.0
        neurons = 0.0
        for (name, _), out in zip(activos.items(), results, strict=True):
            if isinstance(out, Exception) or out is None:
                log.warning("%s no respondió: %s", name, out)
                continue
            data, c_usd, n = out
            parsed[name] = data
            cost += c_usd
            neurons += n

        # Si los titulares murieron por cuota, se reintenta con el respaldo
        # antes de darse por vencido.
        if not parsed and self.fallback and activos is not self.fallback:
            log.info("reintentando %s con respaldo", c.slug[:40])
            rescate = await asyncio.gather(
                *(self._ask(n, p, prompt) for n, p in self.fallback.items()),
                return_exceptions=True,
            )
            for (name, _), out in zip(self.fallback.items(), rescate, strict=True):
                if isinstance(out, Exception) or out is None:
                    continue
                data, c_usd, n = out
                parsed[name] = data
                cost += c_usd

        if not parsed:
            log.error("ningún modelo respondió para %s", c.slug)
            return None

        probs = {n: float(d["probability"]) for n, d in parsed.items()}
        p_raw = combine(probs, self.weights)
        p_cal = self.calibration(p_raw, c.category) if self.calibration else p_raw
        dis = disagreement(probs)

        # Los campos cualitativos vienen del primer modelo que respondió,
        # siguiendo el orden de preferencia.
        lead = next(d for n, d in parsed.items() if n in parsed)

        f = Forecast(
            candidate=c,
            p_raw=round(p_raw, 6),
            p_calibrated=round(p_cal, 6),
            per_model=probs,
            disagreement=round(dis, 6),
            means_yes=str(lead.get("means_yes", ""))[:500],
            is_ambiguous=bool(lead.get("is_ambiguous", False)),
            ambiguity_note=str(lead.get("ambiguity_note", ""))[:500],
            reference_class=str(lead.get("reference_class", ""))[:500],
            base_rate=_clamp01(lead.get("base_rate", 0.5)),
            key_evidence=[str(x)[:300] for x in (lead.get("key_evidence") or [])][:5],
            steps_required=str(lead.get("steps_required", ""))[:500],
            confidence=str(lead.get("confidence", "low")),
            what_would_change_my_mind=str(lead.get("what_would_change_my_mind", ""))[:500],
            resolution_risk=str(lead.get("resolution_risk", ""))[:500],
            cost_usd=round(cost, 6),
            neurons=round(neurons, 2),
            latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        )
        self._apply_vetoes(f)
        return f

    # ── internos ─────────────────────────────────────────────────────────

    async def _ask(self, name: str, provider: Provider, prompt: str):
        try:
            comp = await provider.complete(
                prompt,
                system=FORECASTER_SYSTEM,
                schema=FORECAST_SCHEMA,
                max_tokens=4096,
            )
        except QuotaExhausted:
            # Se propaga: el ciclo debe detenerse, no seguir gastando la
            # cuota de Gemini en mercados que no va a poder pronosticar.
            self.quota_exhausted = True
            log.warning("%s: cuota agotada", name)
            raise
        except ProviderError as exc:
            log.warning("%s falló: %s", name, exc)
            return None

        data = comp.json()
        if not isinstance(data, dict) or "probability" not in data:
            log.warning("%s devolvió JSON inutilizable: %s", name, comp.text[:120])
            return None
        try:
            data["probability"] = _clamp01(data["probability"])
        except (TypeError, ValueError):
            return None
        return data, comp.cost_usd, comp.neurons

    def _apply_vetoes(self, f: Forecast) -> None:
        v = self.vetoes

        if f.is_ambiguous:
            f.vetoed = True
            f.veto_reason = f"el modelo declaró la pregunta ambigua: {f.ambiguity_note[:150]}"
            return

        if len(f.per_model) >= 2 and f.disagreement > v.max_disagreement:
            spread = ", ".join(f"{n}={p:.2f}" for n, p in f.per_model.items())
            f.vetoed = True
            f.veto_reason = (
                f"desacuerdo {f.disagreement:.3f} > {v.max_disagreement} ({spread})"
            )
            return

        if f.divergence > v.max_divergence:
            # El veto que más plata ahorra, y el que parece más absurdo:
            # rechaza justo lo que se ve más rentable.
            f.vetoed = True
            f.veto_reason = (
                f"divergencia {f.divergence:.3f} contra mercado {f.candidate.price:.3f} "
                f"— más probable haber leído mal la pregunta que haber hallado un edge de "
                f"{f.divergence * 100:.0f} puntos"
            )

    @staticmethod
    def _empty_veto(c: Candidate, reason: str) -> Forecast:
        f = Forecast(candidate=c, p_raw=c.price, p_calibrated=c.price)
        f.vetoed = True
        f.veto_reason = reason
        return f


def _clamp01(x) -> float:
    return min(max(float(x), 0.0), 1.0)
