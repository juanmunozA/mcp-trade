"""
Presupuesto de inferencia. El agente tiene que saber cuánto le queda.

POR QUÉ EXISTE ESTE ARCHIVO
───────────────────────────
Medido en producción: una corrida de 40 mercados consumió los 10.000
Neurons diarios de Cloudflare de una sentada. El agente no se enteró —
siguió pidiendo pronósticos hasta que todas las llamadas fallaron, y el
resto del día quedó muerto.

Un presupuesto que solo descubres al agotarlo no es un presupuesto.

CÓMO FUNCIONA
─────────────
Se acumula el consumo de cada llamada y, cuando queda poco, el agente deja
de aceptar mercados nuevos en vez de fallar a mitad de un ciclo.

El consumo se **estima** a partir de los tokens: Cloudflare ya no manda la
cabecera `cf-ai-neurons`. Durante un tiempo el código la leía, no estaba, y
contabilizaba cero — 104 mercados pronosticados con 0 neurons registrados,
y la cuota descubierta al chocar con un 429. Ver `estimar_neurons`.

LO QUE SIGUE SIN RESOLVER
El contador vive en memoria: si el proceso reinicia a media tarde, cree
tener el cupo entero otra vez. El 429 lo corrige, pero tarde.

La reserva existe porque el ciclo ya empezado debe poder terminar: quedarse
sin presupuesto entre la investigación y el pronóstico tira a la basura una
llamada a Gemini que ya se pagó.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

log = logging.getLogger("budget")

# Plan gratuito de Workers AI. Se reinicia a las 00:00 UTC.
CLOUDFLARE_DAILY_NEURONS = 10_000

# Medido en este proyecto, con un prompt de pronóstico realista.
COST_PER_MODEL = {
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": 175.0,
    "@cf/meta/llama-3.1-8b-instruct": 4.5,
}
DEFAULT_COST = 50.0


# ── estimar el consumo cuando Cloudflare no lo dice ──────────────────────
# Cloudflare dejó de mandar la cabecera `cf-ai-neurons`: hoy solo devuelve
# `cf-ai-req-id`. Comprobado en vivo. El resultado fue que este presupuesto
# recibía 0 en cada llamada y creía no gastar nada — 104 mercados
# pronosticados, 0 neurons contabilizados, y la cuota descubierta al chocar
# con un 429.
#
# Neurons por cada 1.000 tokens, (entrada, salida). Calibrado contra las
# DOS medidas que sí tomamos cuando la cabecera existía:
#
#     llama-3.1-8b       4.5 neurons   (~5.5K entrada · ~0.3K salida)
#     deepseek-r1-32b  175   neurons   (~5.5K entrada · ~2.5K salida)
#
# asumiendo que la salida cuesta 3× la entrada, que es lo habitual. R1
# razona mucho antes de responder y por eso su salida pesa tanto.
NEURONS_POR_1K: dict[str, tuple[float, float]] = {
    "@cf/meta/llama-3.1-8b-instruct": (0.70, 2.11),
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": (13.5, 40.4),
}

# Un modelo que no esté en la tabla se cobra al más caro que conocemos.
# Subestimar es gastar a ciegas, que es exactamente el fallo que esto
# viene a arreglar.
NEURONS_DESCONOCIDO = max(NEURONS_POR_1K.values())


def estimar_neurons(model: str, entrada: int, salida: int) -> float:
    """
    Cuántos neurons costó una llamada, a partir de sus tokens.

    Es una ESTIMACIÓN y hay que tratarla como tal: sirve para frenar antes
    de agotar la cuota, no para saber el número exacto. La única verdad
    absoluta sigue siendo el 429, y el agente lo respeta por encima de esto.
    """
    ent, sal = NEURONS_POR_1K.get(model, NEURONS_DESCONOCIDO)
    return round(entrada / 1000 * ent + salida / 1000 * sal, 2)


@dataclass
class Budget:
    """Contabilidad diaria. Se reinicia sola al cambiar el día UTC."""

    daily_neurons: int = CLOUDFLARE_DAILY_NEURONS
    reserve_pct: float = 0.10
    """
    Colchón que no se toca. Permite terminar el ciclo en curso y deja
    margen para reintentos, en vez de morir a mitad de un mercado.
    """

    daily_gemini_requests: int = 250
    """Free tier de gemini-2.5-flash. También diario, también UTC."""

    _neurons_used: float = 0.0
    _gemini_used: int = 0
    _day: date = field(default_factory=lambda: datetime.now(UTC).date())

    # ── estado ───────────────────────────────────────────────────────────

    @property
    def neurons_used(self) -> float:
        self._roll()
        return round(self._neurons_used, 1)

    @property
    def neurons_left(self) -> float:
        self._roll()
        return max(0.0, self.daily_neurons - self._neurons_used)

    @property
    def usable_left(self) -> float:
        """Lo que se puede gastar sin comerse la reserva."""
        return max(0.0, self.neurons_left - self.daily_neurons * self.reserve_pct)

    @property
    def gemini_left(self) -> int:
        self._roll()
        return max(0, self.daily_gemini_requests - self._gemini_used)

    @property
    def exhausted(self) -> bool:
        return self.usable_left <= 0 or self.gemini_left <= 0

    # ── registro ─────────────────────────────────────────────────────────

    def spend_neurons(self, n: float) -> None:
        self._roll()
        self._neurons_used += n

    def spend_gemini(self, n: int = 1) -> None:
        self._roll()
        self._gemini_used += n

    # ── decisión ─────────────────────────────────────────────────────────

    def cost_of(self, models: list[str]) -> float:
        return sum(COST_PER_MODEL.get(m, DEFAULT_COST) for m in models)

    def can_afford(self, models: list[str]) -> bool:
        """¿Alcanza para un mercado más, sin tocar la reserva?"""
        return self.usable_left >= self.cost_of(models) and self.gemini_left > 0

    def markets_affordable(self, models: list[str]) -> int:
        """
        Cuántos mercados más caben hoy. El ciclo usa esto para recortar su
        propia lista en vez de fallar a mitad de camino.
        """
        cost = self.cost_of(models)
        by_neurons = int(self.usable_left / cost) if cost > 0 else 0
        return max(0, min(by_neurons, self.gemini_left))

    def snapshot(self) -> dict:
        return {
            "neurons_usados": self.neurons_used,
            "neurons_disponibles": round(self.usable_left, 1),
            "pct_consumido": round(self._neurons_used / self.daily_neurons * 100, 1),
            "gemini_restantes": self.gemini_left,
            "agotado": self.exhausted,
            "dia_utc": self._day.isoformat(),
        }

    def _roll(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._day:
            log.info(
                "presupuesto reiniciado (%s → %s): se usaron %.0f neurons y %d gemini",
                self._day, today, self._neurons_used, self._gemini_used,
            )
            self._day = today
            self._neurons_used = 0.0
            self._gemini_used = 0
