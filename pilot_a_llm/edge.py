"""
La matemática del dinero: de un pronóstico a una apuesta dimensionada.

LA REGLA QUE LO GOBIERNA TODO
─────────────────────────────
No apuestas porque creas que algo va a pasar. Apuestas cuando **el mercado
lo tiene mal valorado**. Son cosas distintas y confundirlas es la forma más
común de perder plata en mercados de predicción.

PolyBench (38.666 mercados de Polymarket, 7 modelos) lo demuestra con
números incómodos:

    Gemini-3-Flash   75.0% de precisión  →   +6.2% de retorno
    MiMo-V2-Flash    62.1% de precisión  →  +17.6% de retorno

Trece puntos MENOS de precisión, casi 3× más retorno. Porque lo que pagan
no son los aciertos, es la diferencia entre tu probabilidad y el precio.
De los siete modelos, cinco perdieron dinero — declarando confianza de
0.8 a 0.9 mientras perdían.

Por eso este módulo:
  1. Compra al **ask real**, nunca al mid. El mid es un precio que no existe.
  2. Encoge tu estimación hacia el mercado. El precio agrega información
     que tú no tienes; ignorarlo es exceso de confianza con pasos extra.
  3. Exige que el edge supere costos ANTES de considerar la apuesta.
  4. Dimensiona con Kelly fraccional, porque Kelly completo asume que
     conoces `p`. No la conoces: la estimaste.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class Bet(StrEnum):
    YES = "YES"
    NO = "NO"
    PASS = "PASS"


@dataclass(frozen=True, slots=True)
class EdgeConfig:
    # ── filtros de entrada ───────────────────────────────────────────────
    min_price: float = 0.10
    max_price: float = 0.90
    """
    Banda de precio. Fuera de aquí no se opera y ni siquiera se investiga.
    En el estudio de Lacuna, filtrar a la banda de consenso medio subió el
    ROI de +4.0% a +9.1%. En los extremos el mercado ya tiene razón y el
    spread se come cualquier ventaja.
    """

    # ── umbral de edge ───────────────────────────────────────────────────
    min_edge: float = 0.05
    """
    Ventaja mínima en probabilidad (5 puntos) para considerar la apuesta.
    Por debajo de esto no distingues señal de tu propio error de
    calibración.
    """
    longshot_umbral: float = 0.20
    longshot_factor: float = 2.0
    """
    Comprar barato lo improbable pide el DOBLE de ventaja.

    El sesgo de longshot esta documentado y va en una sola direccion: los
    mercados de baja probabilidad estan sobrevalorados, porque la gente
    paga de mas por el billete de loteria. Comprar YES a 0.15 es apostar
    contra esa evidencia, asi que el listado tiene que ser mas alto.

    Apostar al lado favorito -- YES caro, o NO contra un YES barato -- va
    A FAVOR del sesgo y conserva el umbral normal. La asimetria es el
    punto: no es "los extremos son peligrosos", es que un extremo lo es y
    el otro no.

    Medido: Brewers campeon, mercado 0.152, agente 0.250, ventaja 0.0586.
    Pasaba el umbral plano de 0.05 y con esto no pasa -- necesitaria
    0.10, o sea creer que el mercado se equivoca al doble.
    """

    fee_bps: float = 0.0          # verifica la tabla vigente de Polymarket
    slippage_buffer: float = 0.01  # 1 punto, medido de tus propios fills

    # ── encogimiento hacia el mercado ────────────────────────────────────
    market_weight: float = 0.30
    """
    Cuánto peso le das al precio de mercado frente a tu modelo.
    0.30 significa: p_final = 0.70·p_modelo + 0.30·p_mercado.

    Esto REDUCE tu edge medido a propósito. Es el antídoto más barato
    contra el modo de falla documentado —exceso de confianza— y contra
    apostar fuerte sobre una probabilidad que estimaste con un LLM.
    Súbelo si tu Brier skill score vs mercado es negativo.
    """

    # ── dimensionamiento ─────────────────────────────────────────────────
    kelly_fraction: float = 0.20
    """
    Fracción de Kelly. Kelly completo maximiza el crecimiento *si conoces
    p*. Tú la estimaste con un modelo cuya debilidad documentada es
    justamente la calibración. Con p sobreestimada, Kelly completo lleva a
    la ruina; un quinto de Kelly sobrevive al error.
    """
    max_position_usd: float = 25.0

    def __post_init__(self) -> None:
        if not 0 < self.min_price < self.max_price < 1:
            raise ValueError("banda de precio inválida")
        if not 0 <= self.market_weight <= 1:
            raise ValueError("market_weight debe estar en [0, 1]")
        if not 0 < self.kelly_fraction <= 1:
            raise ValueError("kelly_fraction debe estar en (0, 1]")
        if self.min_edge <= 0:
            raise ValueError("min_edge debe ser > 0")


@dataclass(frozen=True, slots=True)
class Quote:
    """Los precios que realmente puedes pagar, no el mid."""

    ask_yes: float
    ask_no: float
    bid_yes: float = 0.0
    bid_no: float = 0.0

    @property
    def implied_prob(self) -> float:
        """
        Probabilidad implícita del mercado, normalizada por el vig.
        ask_yes + ask_no > 1 casi siempre: esa diferencia es el costo de
        cruzar el spread, y hay que quitarla antes de comparar.
        """
        total = self.ask_yes + self.ask_no
        return self.ask_yes / total if total > 0 else 0.5

    @property
    def vig(self) -> float:
        return max(0.0, self.ask_yes + self.ask_no - 1.0)


@dataclass(frozen=True, slots=True)
class Decision:
    bet: Bet
    reason: str
    p_model: float = 0.0
    p_market: float = 0.0
    p_final: float = 0.0
    price_paid: float = 0.0
    edge: float = 0.0
    kelly_full: float = 0.0
    size_usd: float = 0.0
    shares: float = 0.0
    expected_value_usd: float = 0.0

    def __bool__(self) -> bool:
        return self.bet is not Bet.PASS


def _pass(reason: str, **kw) -> Decision:
    return Decision(bet=Bet.PASS, reason=reason, **kw)


def kelly_binary(p: float, price: float) -> float:
    """
    Fracción de Kelly para un contrato binario comprado a `price` que paga $1.

        f* = (p - price) / (1 - price)

    Derivación: arriesgas `price` para ganar `1 - price`. Con
    b = (1-price)/price, Kelly clásico f* = (p·b - (1-p))/b se simplifica
    a la expresión de arriba.

    Devuelve 0 si no hay ventaja. Nunca devuelve negativo: si `p < price`
    la apuesta correcta es el otro lado, no un short.
    """
    if not 0 < price < 1:
        return 0.0
    return max(0.0, (p - price) / (1.0 - price))


def decide(
    p_model: float,
    quote: Quote,
    bankroll_usd: float,
    cfg: EdgeConfig,
) -> Decision:
    """
    Convierte un pronóstico en una apuesta dimensionada, o en un PASS.

    `PASS` es el resultado más frecuente y eso está bien. Un agente que
    encuentra una apuesta en cada mercado que mira no está evaluando,
    está apostando.
    """
    if not 0.0 <= p_model <= 1.0:
        return _pass(f"p_model fuera de rango: {p_model}")
    if bankroll_usd <= 0:
        return _pass("sin capital")
    if not (0 < quote.ask_yes < 1 and 0 < quote.ask_no < 1):
        return _pass(f"quote inválido: yes={quote.ask_yes} no={quote.ask_no}")

    p_market = quote.implied_prob

    # ── 1. banda de precio ───────────────────────────────────────────────
    if not cfg.min_price <= p_market <= cfg.max_price:
        return _pass(
            f"precio {p_market:.3f} fuera de la banda "
            f"[{cfg.min_price}, {cfg.max_price}]: el mercado ya tiene razón",
            p_model=p_model, p_market=p_market,
        )

    # ── 2. encogimiento hacia el mercado ─────────────────────────────────
    p_final = (1 - cfg.market_weight) * p_model + cfg.market_weight * p_market

    # ── 3. edge contra el precio que REALMENTE pagas ─────────────────────
    cost = cfg.slippage_buffer + cfg.fee_bps / 10_000
    edge_yes = p_final - quote.ask_yes - cost
    edge_no = (1.0 - p_final) - quote.ask_no - cost

    if edge_yes >= edge_no:
        bet, edge, price = Bet.YES, edge_yes, quote.ask_yes
        p_win = p_final
    else:
        bet, edge, price = Bet.NO, edge_no, quote.ask_no
        p_win = 1.0 - p_final

    common = dict(p_model=p_model, p_market=p_market, p_final=p_final)

    umbral = cfg.min_edge
    if price < cfg.longshot_umbral:
        umbral *= cfg.longshot_factor
    if edge < umbral:
        extra = " (longshot: el doble)" if umbral > cfg.min_edge else ""
        return _pass(
            f"edge {edge:+.4f} bajo el umbral {umbral:.3f}{extra} "
            f"(tras {cost:.4f} de costos)",
            edge=edge, price_paid=price, **common,
        )

    # ── 4. Kelly fraccional ──────────────────────────────────────────────
    f_full = kelly_binary(p_win, price)
    if f_full <= 0:
        return _pass("Kelly no positivo tras costos", edge=edge, price_paid=price, **common)

    size = min(bankroll_usd * f_full * cfg.kelly_fraction, cfg.max_position_usd)
    # Hacia ABAJO, no al mas cercano: el kernel compara precio x shares
    # contra el tope, y redondear hacia arriba lo cruza por milesimas.
    # Medido: con tope $5.00 y precio 0.35, round() da 14.29 shares =
    # $5.0015, y el kernel rechaza con "$5.00 > $5.00" -- tirando a la
    # basura la investigacion entera del mercado por un centesimo.
    shares = math.floor((size / price) * 100) / 100
    ev = shares * (p_win - price)

    return Decision(
        bet=bet,
        reason=(
            f"{bet}: modelo {p_model:.3f} vs mercado {p_market:.3f} "
            f"→ final {p_final:.3f} · edge {edge:+.4f} · "
            f"Kelly {f_full:.3f}×{cfg.kelly_fraction}"
        ),
        price_paid=price,
        edge=edge,
        kelly_full=f_full,
        size_usd=round(size, 2),
        shares=shares,
        expected_value_usd=round(ev, 4),
        **common,
    )


# ── diagnóstico ──────────────────────────────────────────────────────────


def brier_skill_score(brier_model: float, brier_market: float) -> float:
    """
    LA métrica que decide si el piloto A sigue vivo.

        BSS = 1 - brier_modelo / brier_mercado

    > 0  tu modelo pronostica mejor que el precio de mercado. Hay negocio.
    ≤ 0  el mercado te gana. **No hay edge. Apaga el piloto.**

    No mires el PnL para tomar esta decisión: con 30 apuestas el PnL es
    ruido. El BSS converge mucho más rápido porque usa cada pronóstico,
    no solo los que se convirtieron en apuesta.
    """
    if brier_market <= 0:
        return 0.0
    return 1.0 - brier_model / brier_market


def log_loss(p: float, outcome: bool, *, eps: float = 1e-9) -> float:
    """Penaliza el exceso de confianza mucho más que Brier. Útil como alarma."""
    p = min(max(p, eps), 1 - eps)
    return -math.log(p) if outcome else -math.log(1 - p)
