"""
Tests del presupuesto de inferencia.

Nacen de un fallo real: una corrida de 40 mercados consumió los 10.000
Neurons diarios de una sentada, el agente no se enteró, y el resto del día
quedó muerto con todas las llamadas fallando.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pilot_a_llm.budget import CLOUDFLARE_DAILY_NEURONS, Budget

DEEPSEEK = "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"
LLAMA = "@cf/meta/llama-3.1-8b-instruct"
ENSEMBLE = [DEEPSEEK, LLAMA]


def test_arranca_completo():
    b = Budget()
    assert b.neurons_used == 0
    assert b.neurons_left == CLOUDFLARE_DAILY_NEURONS
    assert not b.exhausted


def test_costo_del_ensemble_medido():
    """175 + 4.5, medidos contra la API real."""
    assert Budget().cost_of(ENSEMBLE) == pytest.approx(179.5)


def test_reserva_no_se_toca():
    b = Budget(reserve_pct=0.10)
    b.spend_neurons(9_000)
    assert b.neurons_left == pytest.approx(1_000)
    assert b.usable_left == pytest.approx(0)      # los 1.000 son la reserva
    assert b.exhausted


def test_cuantos_mercados_caben():
    """El número que evita quemar el día entero en un ciclo."""
    b = Budget()
    # (10.000 - 1.000 de reserva) / 179.5 ≈ 50
    assert b.markets_affordable(ENSEMBLE) == 50


def test_el_presupuesto_se_agota_y_lo_sabe():
    """La corrida real: 40 mercados quemaron el 72% del día de una sentada."""
    b = Budget()
    b.spend_neurons(40 * 179.5)
    assert b.snapshot()["pct_consumido"] == pytest.approx(71.8)
    assert b.markets_affordable(ENSEMBLE) <= 11   # quedaba para muy poco más
    b.spend_neurons(3_000)
    assert b.exhausted
    assert not b.can_afford(ENSEMBLE)


def test_gemini_tambien_limita():
    """250 req/día. Si se acaban, no hay investigación aunque sobren neurons."""
    b = Budget()
    b.spend_gemini(250)
    assert b.gemini_left == 0
    assert b.exhausted
    assert b.markets_affordable(ENSEMBLE) == 0


def test_limita_por_el_recurso_mas_escaso():
    b = Budget()
    b.spend_gemini(245)
    assert b.markets_affordable(ENSEMBLE) == 5   # gemini manda, no neurons


def test_modelo_desconocido_usa_costo_por_defecto():
    assert Budget().cost_of(["@cf/algo/nuevo"]) == pytest.approx(50.0)


def test_con_modelos_baratos_el_cuello_de_botella_pasa_a_gemini():
    """
    Hallazgo útil: llama solo permitiría ~2.000 mercados por Neurons, pero
    Gemini corta en 250/día. Con el ensemble caro manda Cloudflare; con el
    barato manda Gemini. Optimizar el modelo equivocado no sirve de nada.
    """
    b = Budget()
    assert b.markets_affordable([LLAMA]) == 250           # límite de Gemini
    assert b.markets_affordable(ENSEMBLE) == 50           # límite de Neurons
    assert b.usable_left / b.cost_of([LLAMA]) > 1_000     # neurons de sobra


def test_se_reinicia_al_cambiar_el_dia_utc(monkeypatch):
    b = Budget()
    b.spend_neurons(9_500)
    b.spend_gemini(200)
    assert b.exhausted

    manana = date(2099, 1, 1)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            class D:
                @staticmethod
                def date():
                    return manana
            return D()

    monkeypatch.setattr("pilot_a_llm.budget.datetime", FakeDT)
    assert b.neurons_used == 0
    assert b.gemini_left == 250
    assert not b.exhausted


def test_snapshot_es_legible():
    b = Budget()
    b.spend_neurons(2_500)
    s = b.snapshot()
    assert s["pct_consumido"] == pytest.approx(25.0)
    assert s["agotado"] is False
    assert s["dia_utc"] == datetime.now(UTC).date().isoformat()
