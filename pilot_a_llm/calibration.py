"""
Calibración: la diferencia entre "acierta" y "sabe cuánto sabe".

ForecastBench encontró que los LLMs ya alcanzaron **paridad con
superforecasters humanos** en capacidad de pronóstico. La debilidad que
queda documentada no es razonar — es **la calibración**.

PolyBench lo muestra en plata: cinco de siete modelos perdieron dinero
"declarando confianza de 0.8-0.9". Un modelo que dice 0.85 y acierta el
60% de las veces no está equivocado a veces: está **sistemáticamente
sobredimensionando cada posición que toma**, porque el sizing de Kelly
depende directamente de `p`.

Este módulo hace tres cosas, todas contra mercados ya resueltos:
  1. Mide qué tan mal calibrado estás (Brier, log-loss, fiabilidad).
  2. Construye un mapeo que corrige el sesgo hacia adelante.
  3. Pondera el ensemble por track record real, no por reputación.

Sin dependencias externas: esto tiene que poder correr y testearse en
milisegundos, como el risk kernel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Con menos de esto, cualquier "corrección" que apliques es ruido.
MIN_SAMPLES_TO_CALIBRATE = 30
MIN_SAMPLES_TO_WEIGHT = 20


@dataclass(frozen=True, slots=True)
class Resolution:
    """Un pronóstico cuyo mercado ya resolvió. La única verdad disponible."""

    p_forecast: float
    outcome: bool
    model: str = ""
    market_slug: str = ""
    p_market: float | None = None   # precio cuando pronosticaste
    category: str = ""


@dataclass(frozen=True, slots=True)
class Bin:
    lo: float
    hi: float
    n: int
    mean_forecast: float
    actual_rate: float

    @property
    def gap(self) -> float:
        """Positivo = exceso de confianza (prometiste más de lo que entregaste)."""
        return self.mean_forecast - self.actual_rate


@dataclass(frozen=True, slots=True)
class Diagnostics:
    n: int
    brier: float
    log_loss: float
    base_rate: float
    mean_forecast: float
    bins: list[Bin] = field(default_factory=list)
    brier_market: float | None = None

    @property
    def skill_vs_market(self) -> float | None:
        """
        BSS = 1 - brier/brier_mercado.  ≤ 0 significa que el precio te gana:
        no hay negocio, apaga el piloto.
        """
        if self.brier_market is None or self.brier_market <= 0:
            return None
        return 1.0 - self.brier / self.brier_market

    @property
    def overconfidence(self) -> float:
        """
        Promedio ponderado del gap en los bins extremos (p ≤ 0.2 o p ≥ 0.8).
        Positivo = exceso de confianza. Es el número que hay que vigilar.
        """
        extreme = [b for b in self.bins if b.mean_forecast <= 0.2 or b.mean_forecast >= 0.8]
        total_n = sum(b.n for b in extreme)
        if not total_n:
            return 0.0
        return sum(abs(b.gap) * b.n for b in extreme) / total_n

    def report(self) -> str:
        lines = [
            f"n={self.n}  brier={self.brier:.4f}  log_loss={self.log_loss:.4f}",
            f"tasa base={self.base_rate:.3f}  pronóstico medio={self.mean_forecast:.3f}",
            f"exceso de confianza={self.overconfidence:+.4f}",
        ]
        if (bss := self.skill_vs_market) is not None:
            verdict = "HAY EDGE" if bss > 0 else "EL MERCADO TE GANA — apaga el piloto"
            lines.append(f"brier skill vs mercado={bss:+.4f}  → {verdict}")
        lines.append("")
        lines.append("  rango        n    predicho   real    gap")
        for b in self.bins:
            if b.n:
                lines.append(
                    f"  {b.lo:.1f}-{b.hi:.1f}  {b.n:5d}    "
                    f"{b.mean_forecast:.3f}   {b.actual_rate:.3f}  {b.gap:+.3f}"
                )
        return "\n".join(lines)


# ── métricas ─────────────────────────────────────────────────────────────


def brier(records: list[Resolution]) -> float:
    """Error cuadrático medio de la probabilidad. Más bajo es mejor. 0.25 = moneda."""
    if not records:
        return 0.0
    return sum((r.p_forecast - float(r.outcome)) ** 2 for r in records) / len(records)


def mean_log_loss(records: list[Resolution], *, eps: float = 1e-9) -> float:
    """Castiga el exceso de confianza mucho más duro que Brier."""
    if not records:
        return 0.0
    total = 0.0
    for r in records:
        p = min(max(r.p_forecast, eps), 1 - eps)
        total += -math.log(p) if r.outcome else -math.log(1 - p)
    return total / len(records)


def reliability(records: list[Resolution], *, n_bins: int = 10) -> list[Bin]:
    """Diagrama de fiabilidad: ¿de lo que llamaste 70%, pasó el 70%?"""
    buckets: list[list[Resolution]] = [[] for _ in range(n_bins)]
    for r in records:
        idx = min(int(r.p_forecast * n_bins), n_bins - 1)
        buckets[idx].append(r)

    out: list[Bin] = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bucket:
            out.append(Bin(lo, hi, 0, (lo + hi) / 2, 0.0))
            continue
        out.append(
            Bin(
                lo, hi, len(bucket),
                sum(r.p_forecast for r in bucket) / len(bucket),
                sum(float(r.outcome) for r in bucket) / len(bucket),
            )
        )
    return out


def diagnose(records: list[Resolution], *, n_bins: int = 10) -> Diagnostics:
    if not records:
        return Diagnostics(0, 0.0, 0.0, 0.0, 0.0)

    with_market = [r for r in records if r.p_market is not None]
    brier_market = (
        sum((r.p_market - float(r.outcome)) ** 2 for r in with_market) / len(with_market)
        if with_market else None
    )

    return Diagnostics(
        n=len(records),
        brier=brier(records),
        log_loss=mean_log_loss(records),
        base_rate=sum(float(r.outcome) for r in records) / len(records),
        mean_forecast=sum(r.p_forecast for r in records) / len(records),
        bins=reliability(records, n_bins=n_bins),
        brier_market=brier_market,
    )


# ── el mapeo de calibración ──────────────────────────────────────────────


def _pava(values: list[float], weights: list[float]) -> list[float]:
    """
    Pool Adjacent Violators: regresión isotónica en ~20 líneas.

    Impone que el mapeo sea monótono no decreciente — si dijiste 0.7 no
    puedes terminar calibrado por debajo de lo que dijiste 0.6. Sin esto,
    el ruido de bins con pocas muestras produce mapeos no monótonos que
    son estadísticamente absurdos.
    """
    # cada bloque: [valor, peso acumulado, cuántos bins originales absorbió]
    blocks = [[v, w, 1] for v, w in zip(values, weights, strict=True)]

    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] <= blocks[i + 1][0] + 1e-12:
            i += 1
            continue
        v1, w1, c1 = blocks[i]
        v2, w2, c2 = blocks[i + 1]
        total_w = w1 + w2
        merged = (v1 * w1 + v2 * w2) / total_w if total_w > 0 else (v1 + v2) / 2
        blocks[i] = [merged, total_w, c1 + c2]
        blocks.pop(i + 1)
        i = max(0, i - 1)   # el merge puede romper la monotonía hacia atrás

    result: list[float] = []
    for value, _weight, count in blocks:
        result.extend([value] * count)
    return result


class CalibrationMap:
    """
    Corrige tu probabilidad cruda usando tu propio historial resuelto.

    Con pocas muestras **no hace nada** a propósito: devuelve la
    probabilidad tal cual. Una corrección estimada con 8 mercados
    resueltos es superstición con decimales.
    """

    def __init__(
        self,
        records: list[Resolution],
        *,
        n_bins: int = 10,
        prior_strength: float = 10.0,
    ) -> None:
        self.n = len(records)
        self.active = self.n >= MIN_SAMPLES_TO_CALIBRATE
        self._centers: list[float] = []
        self._values: list[float] = []

        if not self.active:
            return

        bins = reliability(records, n_bins=n_bins)
        populated = [b for b in bins if b.n > 0]
        if len(populated) < 2:
            self.active = False
            return

        # Encogimiento hacia la identidad: con n chico, calibrado ≈ crudo.
        shrunk = [
            (b.n * b.actual_rate + prior_strength * b.mean_forecast)
            / (b.n + prior_strength)
            for b in populated
        ]
        monotone = _pava(shrunk, [float(b.n) for b in populated])

        self._centers = [b.mean_forecast for b in populated]
        self._values = monotone

    def __call__(self, p: float) -> float:
        """Interpolación lineal entre centros de bin; constante en los extremos."""
        if not self.active or not self._centers:
            return p
        if p <= self._centers[0]:
            return self._values[0]
        if p >= self._centers[-1]:
            return self._values[-1]
        for i in range(len(self._centers) - 1):
            x0, x1 = self._centers[i], self._centers[i + 1]
            if x0 <= p <= x1:
                if x1 == x0:
                    return self._values[i]
                t = (p - x0) / (x1 - x0)
                return self._values[i] + t * (self._values[i + 1] - self._values[i])
        return p

    def __repr__(self) -> str:
        state = "activo" if self.active else f"inactivo (n={self.n})"
        return f"<CalibrationMap {state}>"


# ── ensemble ─────────────────────────────────────────────────────────────


def ensemble_weights(
    history: dict[str, list[Resolution]],
    *,
    min_samples: int = MIN_SAMPLES_TO_WEIGHT,
) -> dict[str, float]:
    """
    Pondera cada modelo por Brier inverso — track record, no reputación.

    Los modelos sin historial suficiente reciben peso uniforme: no los
    castigas por ser nuevos, pero tampoco les crees por el nombre.
    """
    if not history:
        return {}

    scored = {m: brier(rs) for m, rs in history.items() if len(rs) >= min_samples}
    if not scored:
        uniform = 1.0 / len(history)
        return dict.fromkeys(history, uniform)

    raw = {m: 1.0 / (b + 0.01) for m, b in scored.items()}
    for model in history:
        raw.setdefault(model, sum(raw.values()) / len(raw))

    total = sum(raw.values())
    return {m: w / total for m, w in raw.items()}


def combine(predictions: dict[str, float], weights: dict[str, float]) -> float:
    """
    Media aritmética ponderada en espacio de probabilidad.

    Elegida a propósito sobre el promedio en logit (log opinion pool):
    el promedio aritmético **encoge hacia el centro**, y dado que el modo
    de falla documentado de los LLMs es el exceso de confianza, encoger es
    exactamente lo que quieres. El logit pooling extremiza, y extremizar
    un ensemble ya sobreconfiado es la peor combinación posible.
    """
    if not predictions:
        return 0.5
    total_w = sum(weights.get(m, 0.0) for m in predictions)
    if total_w <= 0:
        return sum(predictions.values()) / len(predictions)
    return sum(p * weights.get(m, 0.0) for m, p in predictions.items()) / total_w


def disagreement(predictions: dict[str, float]) -> float:
    """
    Desviación estándar entre modelos. Es una señal por derecho propio:
    desacuerdo alto = el evento es genuinamente incierto, y la respuesta
    correcta suele ser no apostar aunque la media muestre edge.
    """
    if len(predictions) < 2:
        return 0.0
    vals = list(predictions.values())
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
