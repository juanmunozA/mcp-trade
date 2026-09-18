"""
Tests del lector de capital.

Lo que protegen: que el agente nunca dimensione sobre un saldo inventado,
y que las posiciones abiertas no se cuenten como capital disponible —
eso sería apalancarse sin darse cuenta.
"""

from __future__ import annotations

import pytest

from pilot_a_llm.bankroll import BankrollReader


async def test_paper_arranca_con_el_saldo_configurado():
    b = await BankrollReader(paper_starting_usd=150.0).read()
    assert b.cash_usd == pytest.approx(150.0)
    assert b.positions_usd == pytest.approx(0.0)
    assert b.source == "paper"


async def test_solo_el_efectivo_es_desplegable():
    """Contar posiciones abiertas como capital libre es apalancarse solo."""
    r = BankrollReader(paper_starting_usd=100.0)
    r.on_paper_fill(30.0)
    b = await r.read()
    assert b.cash_usd == pytest.approx(70.0)
    assert b.positions_usd == pytest.approx(30.0)
    assert b.total_usd == pytest.approx(100.0)
    assert b.deployable_usd == pytest.approx(70.0)


async def test_resolucion_favorable_aumenta_el_capital():
    r = BankrollReader(paper_starting_usd=100.0)
    r.on_paper_fill(20.0)          # compra 40 shares a 0.50
    r.on_paper_resolution(40.0, 20.0)   # ganan: pagan $1 cada una
    b = await r.read()
    assert b.cash_usd == pytest.approx(120.0)
    assert b.positions_usd == pytest.approx(0.0)


async def test_resolucion_adversa_reduce_el_capital():
    r = BankrollReader(paper_starting_usd=100.0)
    r.on_paper_fill(20.0)
    r.on_paper_resolution(0.0, 20.0)
    b = await r.read()
    assert b.cash_usd == pytest.approx(80.0)


async def test_deployable_nunca_negativo():
    r = BankrollReader(paper_starting_usd=10.0)
    r.on_paper_fill(50.0)
    b = await r.read()
    assert b.deployable_usd == 0.0


async def test_live_sin_funder_falla_ruidoso():
    """Mejor reventar que dimensionar sobre una dirección vacía."""
    r = BankrollReader(funder_address=None, is_paper=False)
    with pytest.raises(RuntimeError, match="PILOT_A_FUNDER"):
        await r.read()


async def test_error_onchain_devuelve_cero_no_un_numero_inventado(monkeypatch):
    """
    Fail closed: sin saldo confirmado no se opera. Devolver un estimado
    sería peor que quedarse quieto.
    """
    async def boom(address):
        raise ConnectionError("red caída")

    r = BankrollReader(funder_address="0xabc", is_paper=False)
    monkeypatch.setattr(r, "_read_onchain", boom)
    b = await r.read()
    assert b.source == "fallback"
    assert b.deployable_usd == 0.0


async def test_lectura_onchain_ok(monkeypatch):
    async def fake(address):
        return 42.5, 17.5

    r = BankrollReader(funder_address="0xabc", is_paper=False)
    monkeypatch.setattr(r, "_read_onchain", fake)
    b = await r.read()
    assert b.source == "onchain"
    assert b.cash_usd == pytest.approx(42.5)
    assert b.total_usd == pytest.approx(60.0)
