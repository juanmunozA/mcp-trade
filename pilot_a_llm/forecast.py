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
from pilot_a_llm.providers import Provider, ProviderError
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

    min_models: int = 1
    require_evidence: bool = True


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
    ) -> None:
        if not providers:
            raise ValueError("el ensemble necesita al menos un proveedor")
        self.providers = providers
        self.calibration = calibration
        self.weights = weights or {}
        self.vetoes = vetoes or VetoConfig()
        self.vault = vault
        """Vault de Obsidian. Si está, sus notas entran al prompt."""

    async def forecast(self, c: Candidate, ev: Evidence) -> Forecast | None:
        """Devuelve None solo si ningún modelo respondió algo utilizable."""
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

        results = await asyncio.gather(
            *(self._ask(name, p, prompt) for name, p in self.providers.items()),
            return_exceptions=True,
        )

        parsed: dict[str, dict] = {}
        cost = 0.0
        neurons = 0.0
        for (name, _), out in zip(self.providers.items(), results, strict=True):
            if isinstance(out, Exception) or out is None:
                log.warning("%s no respondió: %s", name, out)
                continue
            data, c_usd, n = out
            parsed[name] = data
            cost += c_usd
            neurons += n

        if not parsed:
            log.error("ningún modelo respondió para %s", c.slug)
            return None

        probs = {n: float(d["probability"]) for n, d in parsed.items()}
        p_raw = combine(probs, self.weights)
        p_cal = self.calibration(p_raw) if self.calibration else p_raw
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
