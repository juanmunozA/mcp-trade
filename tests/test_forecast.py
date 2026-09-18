"""
Tests del ensemble y sus tres vetos.

Estos son los tests que impiden que el agente apueste por haber entendido
mal la pregunta. El caso base está tomado de un fallo real medido en este
proyecto: un modelo leyó "gain the most seats" como "creció más" y produjo
0.02 contra un mercado en 0.805.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pilot_a_llm.forecast import Forecaster, VetoConfig
from pilot_a_llm.markets import Candidate
from pilot_a_llm.research import Evidence

NOW = datetime(2026, 9, 18, tzinfo=UTC)

GOOD = {
    "means_yes": "El partido obtiene el mayor número de escaños.",
    "is_ambiguous": False,
    "ambiguity_note": "",
    "reference_class": "partidos gobernantes: ~70%",
    "base_rate": 0.7,
    "key_evidence": ["encuestas 45-52%", "controla 325 de 450 escaños"],
    "steps_required": "Publicación de resultados oficiales.",
    "probability": 0.80,
    "confidence": "medium",
    "what_would_change_my_mind": "Caída en encuestas.",
    "resolution_risk": "",
}


class FakeProvider:
    """Devuelve lo que le digan. Cero red."""

    def __init__(self, payload: dict | None, *, fail: bool = False) -> None:
        self.name = "fake"
        self.model = "fake"
        self._payload = payload
        self._fail = fail
        self.calls = 0

    async def complete(self, prompt, **kw):
        from pilot_a_llm.providers import Completion, ProviderError

        self.calls += 1
        if self._fail:
            raise ProviderError("simulado")
        import json
        return Completion(
            text=json.dumps(self._payload), provider="fake", model="fake"
        )


def candidate(price: float = 0.805) -> Candidate:
    return Candidate(
        condition_id="0x1", slug="ru-duma",
        question="Will United Russia gain the most seats?",
        rules="Resuelve según el partido con más escaños.",
        token_yes="y", token_no="n", price=price, liquidity=80_000,
        volume=400_000, days_to_resolution=11, category="politica", end_date=NOW,
    )


def evidence(text: str = "x" * 400) -> Evidence:
    return Evidence(text=text, sources=["https://ejemplo.com/a"])


def payload(**over) -> dict:
    d = dict(GOOD)
    d.update(over)
    return d


# ─────────────────────────── camino feliz ───────────────────────────────


async def test_pronostico_valido_no_se_veta():
    f = Forecaster({"m": FakeProvider(payload())})
    out = await f.forecast(candidate(), evidence())
    assert out is not None
    assert not out.vetoed
    assert out.p_calibrated == pytest.approx(0.80)
    assert out.means_yes.startswith("El partido")


async def test_sin_calibracion_p_raw_igual_a_calibrada():
    f = Forecaster({"m": FakeProvider(payload(probability=0.42))})
    out = await f.forecast(candidate(), evidence())
    assert out.p_raw == pytest.approx(out.p_calibrated)


# ─────────────────────── VETO 1 · ambigüedad ────────────────────────────


async def test_veto_por_ambiguedad_declarada():
    """Si el modelo dice que la pregunta admite dos lecturas, le creemos."""
    f = Forecaster({"m": FakeProvider(payload(
        is_ambiguous=True, ambiguity_note="puede significar A o B"))})
    out = await f.forecast(candidate(), evidence())
    assert out.vetoed
    assert "ambigua" in out.veto_reason


# ─────────────────────── VETO 2 · desacuerdo ────────────────────────────


async def test_veto_por_desacuerdo_entre_modelos():
    """El caso real: deepseek 0.20 vs llama 0.50 -> no se apuesta."""
    f = Forecaster({
        "a": FakeProvider(payload(probability=0.20)),
        "b": FakeProvider(payload(probability=0.50)),
    })
    out = await f.forecast(candidate(price=0.30), evidence())
    assert out.vetoed
    assert "desacuerdo" in out.veto_reason
    assert out.disagreement > 0.20


async def test_acuerdo_entre_modelos_no_veta():
    f = Forecaster({
        "a": FakeProvider(payload(probability=0.78)),
        "b": FakeProvider(payload(probability=0.82)),
    })
    out = await f.forecast(candidate(), evidence())
    assert not out.vetoed
    assert out.p_raw == pytest.approx(0.80)


async def test_un_solo_modelo_no_dispara_veto_de_desacuerdo():
    """Con un modelo la desviación estándar es 0; no debe vetar por eso."""
    f = Forecaster({"a": FakeProvider(payload(probability=0.80))})
    out = await f.forecast(candidate(), evidence())
    assert not out.vetoed


# ──────────────────── VETO 3 · divergencia extrema ──────────────────────


async def test_veto_por_divergencia_extrema():
    """
    EL caso que motivó todo: p=0.02 contra un mercado en 0.805.
    Un edge de 78 puntos no existe; lo que existe es haber leído mal.
    """
    f = Forecaster({"m": FakeProvider(payload(probability=0.02))})
    out = await f.forecast(candidate(price=0.805), evidence())
    assert out.vetoed
    assert "divergencia" in out.veto_reason
    assert out.divergence > 0.7


async def test_divergencia_moderada_si_pasa():
    """5 a 15 puntos es el rango donde vive el edge real."""
    f = Forecaster({"m": FakeProvider(payload(probability=0.68))})
    out = await f.forecast(candidate(price=0.55), evidence())
    assert not out.vetoed
    assert 0.10 < out.divergence < 0.20


async def test_umbral_de_divergencia_configurable():
    f = Forecaster({"m": FakeProvider(payload(probability=0.60))},
                   vetoes=VetoConfig(max_divergence=0.10))
    out = await f.forecast(candidate(price=0.30), evidence())
    assert out.vetoed


# ─────────────────────── evidencia insuficiente ─────────────────────────


async def test_veto_por_evidencia_pobre():
    """
    Sin evidencia el modelo rellena con su prior, y ahí aparecen los
    pronósticos sobreconfiados.
    """
    f = Forecaster({"m": FakeProvider(payload())})
    out = await f.forecast(candidate(), Evidence(text="poco", sources=[]))
    assert out.vetoed
    assert "evidencia" in out.veto_reason


async def test_evidencia_sin_fuentes_es_pobre():
    out = Evidence(text="x" * 500, sources=[])
    assert out.is_thin


async def test_se_puede_desactivar_el_requisito_de_evidencia():
    f = Forecaster({"m": FakeProvider(payload())},
                   vetoes=VetoConfig(require_evidence=False))
    out = await f.forecast(candidate(), Evidence(text="poco", sources=[]))
    assert not out.vetoed


# ─────────────────────── fallos de proveedor ────────────────────────────


async def test_un_modelo_caido_no_tumba_el_ensemble():
    f = Forecaster({
        "vivo": FakeProvider(payload(probability=0.80)),
        "muerto": FakeProvider(None, fail=True),
    })
    out = await f.forecast(candidate(), evidence())
    assert out is not None
    assert len(out.per_model) == 1


async def test_todos_caidos_devuelve_none():
    f = Forecaster({
        "a": FakeProvider(None, fail=True),
        "b": FakeProvider(None, fail=True),
    })
    assert await f.forecast(candidate(), evidence()) is None


async def test_json_sin_probability_se_descarta():
    f = Forecaster({"m": FakeProvider({"means_yes": "x"})})
    assert await f.forecast(candidate(), evidence()) is None


async def test_probabilidad_fuera_de_rango_se_acota():
    f = Forecaster({"m": FakeProvider(payload(probability=1.7))})
    out = await f.forecast(candidate(price=0.9), evidence())
    assert 0.0 <= out.p_raw <= 1.0


def test_ensemble_vacio_no_se_construye():
    with pytest.raises(ValueError):
        Forecaster({})
