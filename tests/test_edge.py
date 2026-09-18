"""
Tests de la capa de decisión. Junto a los del risk kernel, son los que
deciden si se opera o no.
"""

from __future__ import annotations

import pytest

from pilot_a_llm.edge import (
    Bet,
    EdgeConfig,
    Quote,
    brier_skill_score,
    decide,
    kelly_binary,
    log_loss,
)

BANKROLL = 100.0


def cfg(**over) -> EdgeConfig:
    base = dict(
        min_price=0.10, max_price=0.90, min_edge=0.05,
        fee_bps=0.0, slippage_buffer=0.01,
        market_weight=0.0,          # sin encogimiento salvo que el test lo pida
        kelly_fraction=0.20, max_position_usd=25.0,
    )
    base.update(over)
    return EdgeConfig(**base)


def quote(yes: float, no: float | None = None) -> Quote:
    """Por defecto un mercado sin vig: ask_no = 1 - ask_yes."""
    return Quote(ask_yes=yes, ask_no=1.0 - yes if no is None else no)


# ─────────────────────────── Kelly ──────────────────────────────────────


def test_kelly_formula():
    # p=0.60 a precio 0.50 → (0.60-0.50)/(1-0.50) = 0.20
    assert kelly_binary(0.60, 0.50) == pytest.approx(0.20)
    # p=0.90 a precio 0.50 → 0.80
    assert kelly_binary(0.90, 0.50) == pytest.approx(0.80)


def test_kelly_sin_ventaja_es_cero():
    assert kelly_binary(0.50, 0.50) == 0.0
    assert kelly_binary(0.40, 0.50) == 0.0   # nunca negativo: el otro lado es la apuesta


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_kelly_precio_invalido(price):
    assert kelly_binary(0.6, price) == 0.0


def test_kelly_crece_con_la_ventaja():
    assert kelly_binary(0.55, 0.5) < kelly_binary(0.65, 0.5) < kelly_binary(0.75, 0.5)


# ─────────────────── la regla: edge, no convicción ──────────────────────


def test_creer_que_si_no_basta():
    """
    p=0.70 suena a apuesta segura. Pero si el mercado cotiza 0.70, no hay
    nada que ganar. Este es EL error que arruina a la gente.
    """
    d = decide(0.70, quote(0.70), BANKROLL, cfg())
    assert d.bet is Bet.PASS
    assert not d


def test_apuesta_cuando_el_mercado_esta_mal():
    d = decide(0.70, quote(0.50), BANKROLL, cfg())
    assert d.bet is Bet.YES
    assert d.edge == pytest.approx(0.70 - 0.50 - 0.01)
    assert d.size_usd > 0


def test_toma_el_lado_NO_cuando_corresponde():
    """Baja probabilidad no es 'no apuestes': es 'apuesta al otro lado'."""
    d = decide(0.20, quote(0.50), BANKROLL, cfg())
    assert d.bet is Bet.NO
    assert d.price_paid == pytest.approx(0.50)
    assert d.edge == pytest.approx(0.80 - 0.50 - 0.01)


# ─────────────────────────── filtros ────────────────────────────────────


@pytest.mark.parametrize("price", [0.05, 0.95])
def test_fuera_de_la_banda_ni_se_mira(price):
    """En los extremos el mercado ya tiene razón y el spread se lo come todo."""
    d = decide(0.50, quote(price), BANKROLL, cfg())
    assert d.bet is Bet.PASS
    assert "fuera de la banda" in d.reason


def test_edge_pequeno_se_descarta():
    d = decide(0.55, quote(0.50), BANKROLL, cfg(min_edge=0.10))
    assert d.bet is Bet.PASS
    assert "umbral" in d.reason


def test_los_costos_pueden_matar_la_apuesta():
    """Un edge de 6 puntos no sobrevive a 5 de slippage más el umbral."""
    sin_costos = decide(0.56, quote(0.50), BANKROLL, cfg(slippage_buffer=0.0))
    con_costos = decide(0.56, quote(0.50), BANKROLL, cfg(slippage_buffer=0.05))
    assert sin_costos.bet is Bet.YES
    assert con_costos.bet is Bet.PASS


def test_se_paga_el_ask_no_el_mid():
    """El mid es un precio que no existe. Un vig ancho reduce el edge real."""
    apretado = decide(0.70, Quote(ask_yes=0.50, ask_no=0.50), BANKROLL, cfg())
    ancho = decide(0.70, Quote(ask_yes=0.56, ask_no=0.56), BANKROLL, cfg())
    assert apretado.edge > ancho.edge


def test_vig_se_normaliza_en_la_prob_implicita():
    q = Quote(ask_yes=0.55, ask_no=0.55)
    assert q.implied_prob == pytest.approx(0.50)
    assert q.vig == pytest.approx(0.10)


# ────────────────────── encogimiento hacia el mercado ───────────────────


def test_encogimiento_reduce_el_edge():
    """A propósito: es el antídoto contra el exceso de confianza."""
    sin = decide(0.80, quote(0.50), BANKROLL, cfg(market_weight=0.0))
    con = decide(0.80, quote(0.50), BANKROLL, cfg(market_weight=0.30))
    assert con.p_final == pytest.approx(0.7 * 0.80 + 0.3 * 0.50)
    assert con.edge < sin.edge
    assert con.size_usd < sin.size_usd


def test_encogimiento_total_elimina_toda_apuesta():
    d = decide(0.95, quote(0.50), BANKROLL, cfg(market_weight=1.0))
    assert d.bet is Bet.PASS   # p_final == p_market, edge cero


# ─────────────────────────── sizing ─────────────────────────────────────


def test_kelly_fraccional_se_aplica():
    # tope alto a propósito: si el cap muerde, no estás midiendo la fracción
    sin_tope = cfg(max_position_usd=1000.0)
    completo = decide(0.70, quote(0.50), BANKROLL, cfg(kelly_fraction=1.0, max_position_usd=1000.0))
    quinto = decide(0.70, quote(0.50), BANKROLL, cfg(kelly_fraction=0.20, max_position_usd=1000.0))
    assert completo.kelly_full == pytest.approx(0.40)      # (0.70-0.50)/(1-0.50)
    assert completo.size_usd == pytest.approx(40.0)
    assert quinto.size_usd == pytest.approx(completo.size_usd * 0.20, rel=0.01)
    assert sin_tope.max_position_usd == 1000.0


def test_tope_de_posicion_manda():
    d = decide(0.95, quote(0.20), 100_000.0, cfg(max_position_usd=25.0))
    assert d.size_usd == pytest.approx(25.0)


def test_mas_ventaja_mas_tamano():
    poca = decide(0.60, quote(0.50), BANKROLL, cfg())
    mucha = decide(0.80, quote(0.50), BANKROLL, cfg())
    assert mucha.size_usd > poca.size_usd


def test_shares_y_ev_coherentes():
    d = decide(0.70, quote(0.50), BANKROLL, cfg())
    assert d.shares == pytest.approx(d.size_usd / d.price_paid, rel=0.01)
    assert d.expected_value_usd > 0


def test_sin_capital_no_hay_apuesta():
    assert decide(0.90, quote(0.50), 0.0, cfg()).bet is Bet.PASS


# ─────────────────────────── entradas basura ────────────────────────────


@pytest.mark.parametrize("p", [-0.1, 1.1])
def test_probabilidad_invalida(p):
    assert decide(p, quote(0.50), BANKROLL, cfg()).bet is Bet.PASS


@pytest.mark.parametrize("q", [Quote(0.0, 1.0), Quote(1.0, 0.0), Quote(-0.5, 0.5)])
def test_quote_invalido(q):
    assert decide(0.60, q, BANKROLL, cfg()).bet is Bet.PASS


def test_config_incoherente_no_se_construye():
    with pytest.raises(ValueError):
        EdgeConfig(min_price=0.9, max_price=0.1)
    with pytest.raises(ValueError):
        EdgeConfig(market_weight=1.5)
    with pytest.raises(ValueError):
        EdgeConfig(kelly_fraction=0.0)
    with pytest.raises(ValueError):
        EdgeConfig(min_edge=0.0)


# ──────────────────── el criterio para matar el piloto ──────────────────


def test_brier_skill_score():
    assert brier_skill_score(0.15, 0.20) > 0     # le ganas al mercado
    assert brier_skill_score(0.25, 0.20) < 0     # el mercado te gana → apagar
    assert brier_skill_score(0.20, 0.20) == 0.0


def test_log_loss_castiga_la_sobreconfianza():
    """Estar muy seguro y equivocarse debe doler mucho más que dudar."""
    seguro_y_mal = log_loss(0.99, outcome=False)
    dudoso_y_mal = log_loss(0.60, outcome=False)
    assert seguro_y_mal > dudoso_y_mal * 4
