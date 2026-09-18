"""
El contrato entre el LLM y el resto del sistema.

Una política **no es una orden**. Es un conjunto de preferencias que el
ejecutor traduce a intenciones, que el risk kernel después valida. Tres
capas entre el modelo y la plata.

Todo campo numérico está acotado en el esquema *y* vuelve a recortarse
contra los límites reales en `clamp()`. La redundancia es a propósito:
un esquema Pydantic protege contra una alucinación de formato, no contra
un modelo que razonó su camino hasta un número grande.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Regime(StrEnum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    CHOP = "chop"
    HIGH_VOL = "high_vol"
    UNCLEAR = "unclear"


class Bias(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
    FLAT = "flat"       # cerrar todo y no abrir nada


class Policy(BaseModel):
    """Lo que el estratega puede pedir. Nada más."""

    regime: Regime = Field(description="Régimen de mercado observado")
    bias: Bias = Field(description="Sesgo direccional deseado")

    max_position_usd: float = Field(
        ge=0.0, le=1000.0,
        description="Tamaño máximo por posición en USD. El sistema lo recorta a su propio límite.",
    )
    max_concurrent_markets: int = Field(
        ge=0, le=20, description="Cuántos mercados tocar a la vez",
    )
    target_markets: list[str] = Field(
        default_factory=list, max_length=20,
        description="Slugs de mercado a operar. Deben venir de la lista provista.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Qué tan seguro estás. 'low' debe ser lo normal.",
    )
    thesis: str = Field(
        min_length=20, max_length=2000,
        description="Por qué. Va al journal y lo vas a leer en el próximo ciclo.",
    )
    risk_note: str = Field(
        default="", max_length=1000,
        description="Qué te haría estar equivocado",
    )

    @field_validator("target_markets")
    @classmethod
    def _clean_slugs(cls, v: list[str]) -> list[str]:
        # Un slug con caracteres raros es señal de alucinación o de inyección.
        return [s.strip() for s in v if s.strip() and s.replace("-", "").isalnum()]

    # ── el recorte que sí importa ────────────────────────────────────────

    def clamp(self, *, hard_max_position_usd: float, allowed_slugs: set[str]) -> tuple[Policy, list[str]]:
        """
        Recorta la política contra la realidad y devuelve qué se tocó.

        Los ajustes se loguean porque son señal: un modelo que pide 10× el
        límite en cada ciclo no está "siendo agresivo", está ignorando su
        contexto, y eso te dice que el prompt está mal.
        """
        notes: list[str] = []
        data = self.model_dump()

        if self.max_position_usd > hard_max_position_usd:
            notes.append(
                f"max_position_usd {self.max_position_usd:.2f} → {hard_max_position_usd:.2f}"
            )
            data["max_position_usd"] = hard_max_position_usd

        unknown = [s for s in self.target_markets if s not in allowed_slugs]
        if unknown:
            notes.append(f"slugs descartados (no existen o no están en whitelist): {unknown}")
            data["target_markets"] = [s for s in self.target_markets if s in allowed_slugs]

        if self.bias is Bias.FLAT:
            data["max_position_usd"] = 0.0
            data["target_markets"] = []

        return Policy(**data), notes

    @property
    def is_flat(self) -> bool:
        return self.bias is Bias.FLAT or self.max_position_usd <= 0


# Política de emergencia: lo que se usa si el LLM falla, responde basura,
# o se pasa del timeout. Nunca se opera "con la última política" a ciegas.
SAFE_DEFAULT = Policy(
    regime=Regime.UNCLEAR,
    bias=Bias.FLAT,
    max_position_usd=0.0,
    max_concurrent_markets=0,
    target_markets=[],
    confidence="low",
    thesis="Política de emergencia: el estratega no produjo una salida válida. "
           "Sin exposición hasta el próximo ciclo.",
    risk_note="Fallback automático, no es una decisión del modelo.",
)
