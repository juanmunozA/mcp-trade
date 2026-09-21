"""
Tests del scout. Sin red: solo la lógica de filtrado y puntuación.

Lo que protegen: que las compuertas duras descarten antes de gastar tokens,
y que el puntaje premie donde los datos dicen que está el edge — no donde
la intuición dice que está el evento más probable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from pilot_a_llm.markets import (
    Candidate,
    MarketFilter,
    categorize,
    opportunity_score,
    passes,
    select,
)

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def raw(**over) -> dict:
    base = {
        "conditionId": "0xabc",
        "slug": "will-the-senate-confirm-the-nominee",
        "question": "Will the Senate confirm the nominee?",
        "description": "Resolves YES if the Senate votes to confirm. Poll margin counts.",
        "outcomePrices": json.dumps(["0.45", "0.55"]),
        "clobTokenIds": json.dumps(["tok-yes", "tok-no"]),
        "liquidityNum": 80_000,
        "volumeNum": 400_000,
        "endDate": (NOW + timedelta(days=21)).isoformat().replace("+00:00", "Z"),
    }
    base.update(over)
    return base


def cand(**over) -> Candidate:
    base = dict(
        condition_id="0xabc", slug="s", question="Will X happen?", rules="",
        token_yes="y", token_no="n", price=0.5, liquidity=80_000, volume=400_000,
        days_to_resolution=21, category="politica", end_date=NOW,
    )
    base.update(over)
    return Candidate(**base)


# ─────────────────────────── categorización ─────────────────────────────


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Will the Senate confirm the nominee?", "politica"),
        ("Russia x Ukraine ceasefire by December?", "geopolitica"),
        ("Will the Fed cut rates?", "economia"),
        ("Will Bitcoin hit 200k?", "cripto"),
        ("Who wins the NBA championship?", "deportes"),
        ("Will it rain tomorrow?", "otros"),
    ],
)
def test_categoriza(question, expected):
    assert categorize("", question) == expected


# ─────────────────────── compuertas duras ───────────────────────────────


def test_mercado_bueno_pasa():
    assert passes(raw(), MarketFilter(), NOW) is not None


@pytest.mark.parametrize("price", ["0.05", "0.95"])
def test_precios_extremos_se_descartan(price):
    """En los extremos el mercado ya tiene razón; el spread se come todo."""
    m = raw(outcomePrices=json.dumps([price, str(1 - float(price))]))
    assert passes(m, MarketFilter(), NOW) is None


def test_liquidez_insuficiente_se_descarta():
    assert passes(raw(liquidityNum=500), MarketFilter(), NOW) is None


def test_liquidez_excesiva_tambien_se_descarta():
    """El techo es deliberado: los mercados gigantes están saturados de sharps."""
    assert passes(raw(liquidityNum=50_000_000), MarketFilter(), NOW) is None


@pytest.mark.parametrize("days", [0, 1, 400])
def test_horizonte_fuera_de_rango(days):
    m = raw(endDate=(NOW + timedelta(days=days)).isoformat().replace("+00:00", "Z"))
    assert passes(m, MarketFilter(), NOW) is None


def test_cripto_ya_no_se_excluye():
    """
    Antes se excluía por la miscalibración que mide PolyBench. Eso hacía
    falta cuando la calibración era UNA curva para todo: el sesgo de cripto
    se comía la corrección de las demás.

    Ahora `CalibrationMap` segmenta por categoría, así que cada una aprende
    su propio sesgo y cripto puede entrar sin contaminar a nadie.
    """
    m = raw(question="Will Bitcoin reach 200k by December?", slug="btc-200k")
    c = passes(m, MarketFilter(), NOW)
    assert c is not None
    assert c.category == "cripto"


def test_excluir_sigue_funcionando_si_se_pide():
    """El mecanismo no murió: abrir cripto fue una decisión, no un olvido."""
    f = MarketFilter(exclude_categories=frozenset({"cripto"}))
    m = raw(question="Will Bitcoin reach 200k by December?", slug="btc-200k")
    assert passes(m, f, NOW) is None


def test_deportes_de_partido_permitido():
    """
    `sports_narrative_only` pasó a False: un partido es difícil de
    pronosticar, pero su categoría calibra aparte y el dato sirve.
    """
    m = raw(question="Who wins the NBA championship game tonight?", slug="nba-final")
    c = passes(m, MarketFilter(), NOW)
    assert c is not None
    assert c.category == "deportes"


def test_deportes_de_narrativa_permitido():
    """Un despido se decide leyendo noticias, no modelando rendimiento."""
    f = MarketFilter(allowed_categories=frozenset({"politica", "deportes"}))
    m = raw(question="Will the NBA coach be fired before January?", slug="coach-fired")
    assert passes(m, f, NOW) is not None


# ─────────────────────── datos malformados ──────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"outcomePrices": "no es json"},
        {"outcomePrices": json.dumps(["0.5"])},          # falta un lado
        {"clobTokenIds": "roto"},
        {"endDate": None},
        {"endDate": "fecha invalida"},
        {"liquidityNum": "x"},
    ],
)
def test_datos_malformados_no_revientan(bad):
    """La Gamma API devuelve basura de vez en cuando. Descartar, no crashear."""
    assert passes(raw(**bad), MarketFilter(), NOW) is None


# ─────────────────────── puntaje de oportunidad ─────────────────────────


def test_precio_central_puntua_mas_que_extremo():
    centro, _ = opportunity_score(cand(price=0.50))
    extremo, _ = opportunity_score(cand(price=0.88))
    assert centro > extremo


def test_liquidez_optima_es_campana_no_rampa():
    """Lo contraintuitivo: más liquidez NO es mejor."""
    _, baja = opportunity_score(cand(liquidity=15_000))
    _, optima = opportunity_score(cand(liquidity=80_000))
    _, enorme = opportunity_score(cand(liquidity=1_500_000))
    assert optima["liquidez"] > baja["liquidez"]
    assert optima["liquidez"] > enorme["liquidez"]


def test_fecha_limite_suma():
    """Sesgo de optimismo: los humanos sobrevaloran que las cosas sí pasen."""
    con = cand(question="Will a peace deal be signed before 2027?")
    sin = cand(question="Who will be the next prime minister?")
    assert con.is_deadline_shaped
    assert not sin.is_deadline_shaped
    assert opportunity_score(con)[0] > opportunity_score(sin)[0]


def test_politica_puntua_mas_que_otros():
    _, pol = opportunity_score(cand(category="politica"))
    _, otr = opportunity_score(cand(category="otros"))
    assert pol["categoria"] > otr["categoria"]


def test_datos_parseables_suman():
    """El alfa medido en PolyBench venía de 'datos de encuestas parseables'."""
    _, con = opportunity_score(cand(rules="Resolves by the official poll margin."))
    _, sin = opportunity_score(cand(rules="Resolves at the discretion of the oracle."))
    assert con["datos_parseables"] > sin["datos_parseables"]


def test_horizonte_ideal_tres_semanas():
    _, ideal = opportunity_score(cand(days_to_resolution=21))
    _, corto = opportunity_score(cand(days_to_resolution=3))
    _, largo = opportunity_score(cand(days_to_resolution=140))
    assert ideal["horizonte"] > corto["horizonte"]
    assert ideal["horizonte"] > largo["horizonte"]


def test_score_acotado():
    for c in (cand(price=0.5, liquidity=80_000), cand(price=0.13, liquidity=15_001)):
        s, _ = opportunity_score(c)
        assert 0.0 <= s <= 1.0


# ─────────────────────────── selección ──────────────────────────────────


def test_select_ordena_por_score():
    # Slugs distintos a proposito: `select` deduplica por slug, y dos
    # mercados que comparten uno son el mismo mercado listado dos veces.
    markets = [
        raw(slug="mercado-centrado", outcomePrices=json.dumps(["0.50", "0.50"]),
            liquidityNum=80_000),
        raw(slug="mercado-extremo", outcomePrices=json.dumps(["0.85", "0.15"]),
            liquidityNum=1_900_000),
    ]
    picks = select(markets, MarketFilter(), limit=10, now=NOW)
    assert len(picks) == 2
    assert picks[0].score > picks[1].score


def test_select_deduplica_por_slug():
    """Gamma lista el mismo mercado en varios eventos: no se investiga dos veces."""
    uno = raw(slug="repetido", outcomePrices=json.dumps(["0.50", "0.50"]))
    picks = select([uno, dict(uno), dict(uno)], MarketFilter(), limit=10, now=NOW)
    assert len(picks) == 1


def test_limit_es_presupuesto_no_meta():
    """Si solo 1 vale la pena, se investiga 1. Forzar N gasta en los peores."""
    picks = select([raw()], MarketFilter(), limit=20, now=NOW)
    assert len(picks) == 1


def test_lista_vacia():
    assert select([], MarketFilter(), now=NOW) == []


def test_ninguno_pasa():
    assert select([raw(liquidityNum=10)], MarketFilter(), now=NOW) == []
