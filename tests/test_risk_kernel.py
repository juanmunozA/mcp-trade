"""
Tests del risk kernel. Estos son los tests que importan del repo entero.
Si algo aquí se pone rojo, no se opera en live. Sin excepciones.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from core.risk import OrderIntent, Reject, RiskKernel, RiskLimits, Side

TOK_UP = "token-up"
TOK_DOWN = "token-down"


def make_limits(**over) -> RiskLimits:
    base = dict(
        max_order_usd=5.0,
        max_position_usd=25.0,
        max_gross_exposure_usd=50.0,
        max_daily_loss_usd=10.0,
        max_orders_per_min=6,
        max_open_orders=10,
        max_price_deviation=0.03,
        min_price=0.02,
        max_price=0.98,
        allowed_token_ids=frozenset({TOK_UP, TOK_DOWN}),
    )
    base.update(over)
    return RiskLimits(**base)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


@pytest.fixture
def kernel(tmp_path):
    return RiskKernel("test", make_limits(), state_dir=tmp_path)


def intent(**over) -> OrderIntent:
    base = dict(
        pilot="test",
        token_id=TOK_UP,
        side=Side.BUY,
        price=0.50,
        size=4.0,          # notional 2.00
        market_slug="btc-up-or-down",
        reason="test",
    )
    base.update(over)
    return OrderIntent(**base)


# ─────────────────────────── camino feliz ───────────────────────────────


def test_orden_valida_pasa(kernel):
    assert kernel.check(intent(), mid=0.50).approved


# ─────────────────────────── fail closed ────────────────────────────────


def test_whitelist_vacia_rechaza_todo(tmp_path):
    k = RiskKernel("test", make_limits(allowed_token_ids=frozenset()), state_dir=tmp_path)
    v = k.check(intent(), mid=0.50)
    assert not v.approved and v.code == Reject.NOT_WHITELISTED


def test_token_desconocido_rechazado(kernel):
    v = kernel.check(intent(token_id="token-random"), mid=0.50)
    assert v.code == Reject.NOT_WHITELISTED


def test_sin_mid_no_se_opera(kernel):
    """Operar a ciegas no es una opción."""
    v = kernel.check(intent(), mid=None)
    assert not v.approved and v.code == Reject.NO_REFERENCE_PRICE


# ─────────────────────────── precio y tamaño ────────────────────────────


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_precio_fuera_de_0_1(kernel, price):
    assert kernel.check(intent(price=price), mid=0.5).code == Reject.BAD_PRICE


@pytest.mark.parametrize("price", [0.01, 0.99])
def test_precio_fuera_de_la_banda(kernel, price):
    v = kernel.check(intent(price=price, size=1.0), mid=price)
    assert v.code == Reject.PRICE_OUT_OF_BAND


@pytest.mark.parametrize("size", [0.0, -1.0])
def test_size_invalido(kernel, size):
    assert kernel.check(intent(size=size), mid=0.5).code == Reject.BAD_SIZE


def test_orden_demasiado_grande(kernel):
    # 0.50 * 20 = $10 > max_order_usd=$5
    assert kernel.check(intent(size=20.0), mid=0.5).code == Reject.ORDER_TOO_BIG


def test_desviacion_de_precio_vs_mid(kernel):
    """El fat finger y la alucinación del LLM mueren aquí."""
    v = kernel.check(intent(price=0.60), mid=0.50)
    assert v.code == Reject.PRICE_DEVIATION


def test_desviacion_dentro_de_tolerancia_pasa(kernel):
    assert kernel.check(intent(price=0.52), mid=0.50).approved


# ─────────────────────────── anti-loop ──────────────────────────────────


def test_rate_limit_corta_el_loop_infinito(tmp_path):
    clock = FakeClock()
    k = RiskKernel("test", make_limits(max_orders_per_min=3), state_dir=tmp_path, clock=clock)

    for _ in range(3):
        i = intent()
        assert k.check(i, mid=0.5).approved
        k.on_order_sent(i)
        k.on_order_closed()

    assert k.check(intent(), mid=0.5).code == Reject.RATE_LIMIT

    # la ventana es deslizante de 60s
    clock.advance(61)
    assert k.check(intent(), mid=0.5).approved


def test_tope_de_ordenes_abiertas(tmp_path):
    k = RiskKernel(
        "test",
        make_limits(max_open_orders=2, max_orders_per_min=100),
        state_dir=tmp_path,
    )
    for _ in range(2):
        i = intent()
        assert k.check(i, mid=0.5).approved
        k.on_order_sent(i)  # sin on_order_closed: quedan abiertas

    assert k.check(intent(), mid=0.5).code == Reject.TOO_MANY_OPEN

    k.on_order_closed()
    assert k.check(intent(), mid=0.5).approved


# ─────────────────────────── caps de exposición ─────────────────────────


def test_cap_de_posicion(tmp_path):
    k = RiskKernel("test", make_limits(max_position_usd=5.0), state_dir=tmp_path)
    k.on_fill(TOK_UP, Side.BUY, 0.50, 9.0)           # posición $4.50
    v = k.check(intent(size=4.0), mid=0.50)          # llevaría a $6.50
    assert v.code == Reject.POSITION_CAP


def test_cap_de_posicion_permite_reducir(tmp_path):
    """Vender cuando estás sobre el cap debe poderse: reducir nunca se bloquea por tamaño."""
    k = RiskKernel("test", make_limits(max_position_usd=5.0), state_dir=tmp_path)
    k.on_fill(TOK_UP, Side.BUY, 0.50, 9.0)
    assert k.check(intent(side=Side.SELL, size=4.0), mid=0.50).approved


def test_cap_de_exposicion_bruta(tmp_path):
    k = RiskKernel(
        "test",
        make_limits(max_position_usd=10.0, max_gross_exposure_usd=10.0),
        state_dir=tmp_path,
    )
    k.on_fill(TOK_UP, Side.BUY, 0.50, 18.0)          # $9 bruto en UP
    v = k.check(intent(token_id=TOK_DOWN, size=4.0), mid=0.50)  # +$2 -> $11 bruto
    assert v.code == Reject.GROSS_EXPOSURE_CAP


# ─────────────────────────── kill switch ────────────────────────────────


def test_perdida_diaria_dispara_el_kill(tmp_path):
    k = RiskKernel("test", make_limits(max_daily_loss_usd=2.0), state_dir=tmp_path)
    k.on_fill(TOK_UP, Side.BUY, 0.60, 10.0)
    k.on_fill(TOK_UP, Side.SELL, 0.35, 10.0)   # realiza -$2.50

    assert k.killed
    assert "pérdida diaria" in k.kill_reason
    assert k.check(intent(), mid=0.5).code == Reject.KILLED


def test_el_kill_sobrevive_al_reinicio(tmp_path):
    """El modo de falla clásico: 'se cayó, lo reinicié, siguió perdiendo'."""
    k1 = RiskKernel("test", make_limits(), state_dir=tmp_path)
    k1.trip("prueba manual")
    assert k1.killed

    k2 = RiskKernel("test", make_limits(), state_dir=tmp_path)  # proceso nuevo
    assert k2.killed
    assert "prueba manual" in k2.kill_reason
    assert k2.check(intent(), mid=0.5).code == Reject.KILLED


def test_reset_requiere_confirmacion_explicita(kernel):
    kernel.trip("prueba")
    with pytest.raises(PermissionError):
        kernel.reset_kill("sí")
    with pytest.raises(PermissionError):
        kernel.reset_kill("")
    kernel.reset_kill("reset-test")
    assert not kernel.killed
    assert kernel.check(intent(), mid=0.5).approved


def test_trip_es_idempotente(kernel):
    kernel.trip("primera")
    kernel.trip("segunda")
    assert "primera" in kernel.kill_reason
    assert "segunda" not in kernel.kill_reason


def test_excepcion_interna_mata_el_piloto(kernel, monkeypatch):
    """Fail closed: si el kernel se rompe, nadie opera."""
    monkeypatch.setattr(
        kernel, "_check", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    v = kernel.check(intent(), mid=0.5)
    assert not v.approved and v.code == Reject.INTERNAL
    assert kernel.killed


# ─────────────────────────── contabilidad ───────────────────────────────


def test_pnl_de_cierre_simple(kernel):
    kernel.on_fill(TOK_UP, Side.BUY, 0.40, 10.0)
    realized = kernel.on_fill(TOK_UP, Side.SELL, 0.55, 10.0)
    assert realized == pytest.approx(1.50)
    assert kernel.position(TOK_UP) == pytest.approx(0.0)


def test_costo_medio_al_ampliar(kernel):
    kernel.on_fill(TOK_UP, Side.BUY, 0.40, 10.0)
    kernel.on_fill(TOK_UP, Side.BUY, 0.60, 10.0)      # medio = 0.50
    realized = kernel.on_fill(TOK_UP, Side.SELL, 0.50, 20.0)
    assert realized == pytest.approx(0.0)


def test_cierre_parcial(kernel):
    kernel.on_fill(TOK_UP, Side.BUY, 0.40, 10.0)
    realized = kernel.on_fill(TOK_UP, Side.SELL, 0.50, 4.0)
    assert realized == pytest.approx(0.40)
    assert kernel.position(TOK_UP) == pytest.approx(6.0)


def test_voltear_de_lado(kernel):
    kernel.on_fill(TOK_UP, Side.BUY, 0.40, 10.0)
    realized = kernel.on_fill(TOK_UP, Side.SELL, 0.50, 15.0)
    assert realized == pytest.approx(1.00)            # solo realiza sobre las 10
    assert kernel.position(TOK_UP) == pytest.approx(-5.0)


def test_pnl_diario_se_resetea_pero_el_kill_no(tmp_path):
    current = [date(2026, 1, 1)]
    k = RiskKernel("test", make_limits(), state_dir=tmp_path, today=lambda: current[0])

    k.on_fill(TOK_UP, Side.BUY, 0.60, 5.0)
    k.on_fill(TOK_UP, Side.SELL, 0.50, 5.0)
    assert k.realized_pnl_today == pytest.approx(-0.50)

    k.trip("kill de ayer")
    current[0] = date(2026, 1, 2)

    assert k.realized_pnl_today == pytest.approx(0.0)  # el PnL sí rueda
    assert k.killed                                     # el kill NO


# ─────────────────────────── reconciliación ─────────────────────────────


def test_reconcile_ok(kernel):
    kernel.on_fill(TOK_UP, Side.BUY, 0.5, 10.0)
    assert kernel.reconcile({TOK_UP: 10.0}).approved
    assert not kernel.killed


def test_desincronizacion_mata_el_piloto(kernel):
    """Divergencia = perdimos un fill = doble exposición."""
    kernel.on_fill(TOK_UP, Side.BUY, 0.5, 10.0)
    v = kernel.reconcile({TOK_UP: 14.0})
    assert not v.approved
    assert kernel.killed
    assert "desincronización" in kernel.kill_reason


def test_posicion_fantasma_en_el_exchange_tambien_mata(kernel):
    v = kernel.reconcile({TOK_DOWN: 3.0})
    assert not v.approved and kernel.killed


# ─────────────────────────── validación de límites ──────────────────────


def test_limites_incoherentes_no_se_construyen():
    with pytest.raises(ValueError):
        make_limits(max_order_usd=100.0, max_position_usd=10.0)
    with pytest.raises(ValueError):
        make_limits(max_position_usd=100.0, max_gross_exposure_usd=10.0)
    with pytest.raises(ValueError):
        make_limits(min_price=0.9, max_price=0.1)
    with pytest.raises(ValueError):
        make_limits(max_daily_loss_usd=0.0)


def test_from_env_falla_si_falta_una_variable(monkeypatch):
    for key in [k for k in os.environ if k.startswith("RISK_Z_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RISK_Z_MAX_ORDER_USD", "5")
    with pytest.raises(RuntimeError, match="RISK_Z_MAX_POSITION_USD"):
        RiskLimits.from_env("z")
