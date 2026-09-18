"""Tests del calibrador. El fallo que persiguen es el exceso de confianza."""

from __future__ import annotations

import random

import pytest

from pilot_a_llm.calibration import (
    MIN_SAMPLES_TO_CALIBRATE,
    CalibrationMap,
    Resolution,
    brier,
    combine,
    diagnose,
    disagreement,
    ensemble_weights,
    mean_log_loss,
    reliability,
)


def perfecto(n: int = 200, seed: int = 0) -> list[Resolution]:
    """Un pronosticador perfectamente calibrado: de lo que llama 70%, pasa el 70%."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p = rng.choice([0.1, 0.3, 0.5, 0.7, 0.9])
        out.append(Resolution(p_forecast=p, outcome=rng.random() < p))
    return out


def sobreconfiado(n: int = 200, seed: int = 1) -> list[Resolution]:
    """Dice 0.9 pero acierta 0.6. El modo de falla documentado en PolyBench."""
    rng = random.Random(seed)
    real = {0.1: 0.35, 0.3: 0.40, 0.5: 0.50, 0.7: 0.55, 0.9: 0.60}
    out = []
    for _ in range(n):
        p = rng.choice(list(real))
        out.append(Resolution(p_forecast=p, outcome=rng.random() < real[p]))
    return out


# ─────────────────────────── métricas ───────────────────────────────────


def test_brier_caso_perfecto_y_pesimo():
    assert brier([Resolution(1.0, True), Resolution(0.0, False)]) == pytest.approx(0.0)
    assert brier([Resolution(0.0, True), Resolution(1.0, False)]) == pytest.approx(1.0)
    assert brier([Resolution(0.5, True), Resolution(0.5, False)]) == pytest.approx(0.25)


def test_brier_vacio_no_revienta():
    assert brier([]) == 0.0
    assert mean_log_loss([]) == 0.0
    assert diagnose([]).n == 0


def test_log_loss_no_explota_en_los_extremos():
    """p=0 con outcome=True es -log(0) = infinito. El eps lo evita."""
    valor = mean_log_loss([Resolution(0.0, True)])
    assert valor > 0 and valor < float("inf")


def test_reliability_agrupa_bien():
    records = [Resolution(0.75, True)] * 8 + [Resolution(0.75, False)] * 2
    bins = reliability(records, n_bins=10)
    bin_07 = next(b for b in bins if b.lo == pytest.approx(0.7))
    assert bin_07.n == 10
    assert bin_07.actual_rate == pytest.approx(0.8)
    assert bin_07.gap == pytest.approx(0.75 - 0.80)   # negativo = conservador


# ─────────────────── detección de exceso de confianza ───────────────────


def test_detecta_el_exceso_de_confianza():
    bueno = diagnose(perfecto())
    malo = diagnose(sobreconfiado())
    assert malo.overconfidence > bueno.overconfidence
    assert malo.brier > bueno.brier


def test_skill_vs_mercado_decide_si_el_piloto_vive():
    # el modelo acierta, el mercado no
    gana = [Resolution(0.9, True, p_market=0.5) for _ in range(50)]
    assert diagnose(gana).skill_vs_market > 0

    # el mercado acierta, el modelo no
    pierde = [Resolution(0.2, True, p_market=0.9) for _ in range(50)]
    assert diagnose(pierde).skill_vs_market < 0


def test_skill_es_none_sin_precios_de_mercado():
    assert diagnose([Resolution(0.5, True)]).skill_vs_market is None


def test_report_es_legible():
    texto = diagnose(sobreconfiado()).report()
    assert "brier=" in texto and "exceso de confianza=" in texto


# ─────────────────────── el mapeo de calibración ────────────────────────


def test_no_calibra_con_pocas_muestras():
    """Una corrección estimada con 8 mercados es superstición con decimales."""
    m = CalibrationMap(perfecto(n=MIN_SAMPLES_TO_CALIBRATE - 1))
    assert not m.active
    for p in (0.1, 0.5, 0.9):
        assert m(p) == p      # identidad


def test_calibra_hacia_abajo_al_sobreconfiado():
    m = CalibrationMap(sobreconfiado(n=500))
    assert m.active
    assert m(0.9) < 0.9       # 0.9 declarado vale bastante menos


def test_no_toca_al_ya_calibrado():
    m = CalibrationMap(perfecto(n=500))
    for p in (0.3, 0.5, 0.7):
        assert m(p) == pytest.approx(p, abs=0.15)


def test_el_mapeo_es_monotono():
    """Si dijiste 0.7 no puedes calibrar por debajo de lo que dijiste 0.6."""
    m = CalibrationMap(sobreconfiado(n=500))
    xs = [i / 100 for i in range(1, 100)]
    ys = [m(x) for x in xs]
    assert all(a <= b + 1e-9 for a, b in zip(ys, ys[1:], strict=False))


def test_salida_siempre_en_rango():
    m = CalibrationMap(sobreconfiado(n=500))
    for x in (0.0, 0.01, 0.5, 0.99, 1.0):
        assert 0.0 <= m(x) <= 1.0


def test_calibrar_baja_el_brier():
    """La prueba que importa: ¿corregir mejora el pronóstico de verdad?"""
    entreno = sobreconfiado(n=500, seed=1)
    prueba = sobreconfiado(n=500, seed=99)

    m = CalibrationMap(entreno)
    antes = brier(prueba)
    despues = brier([Resolution(m(r.p_forecast), r.outcome) for r in prueba])
    assert despues < antes


# ─────────────────────────── ensemble ───────────────────────────────────


def test_pondera_por_track_record_no_por_marca():
    hist = {
        "bueno": [Resolution(0.9, True, p_market=0.5) for _ in range(50)],
        "malo": [Resolution(0.9, False, p_market=0.5) for _ in range(50)],
    }
    w = ensemble_weights(hist)
    assert w["bueno"] > w["malo"]
    assert sum(w.values()) == pytest.approx(1.0)


def test_modelo_nuevo_no_se_castiga_ni_se_premia():
    hist = {
        "veterano": [Resolution(0.8, True) for _ in range(50)],
        "nuevo": [Resolution(0.8, True) for _ in range(3)],
    }
    w = ensemble_weights(hist)
    assert w["nuevo"] > 0
    assert sum(w.values()) == pytest.approx(1.0)


def test_sin_historial_pesos_uniformes():
    w = ensemble_weights({"a": [], "b": []})
    assert w["a"] == pytest.approx(0.5) and w["b"] == pytest.approx(0.5)


def test_ensemble_vacio():
    assert ensemble_weights({}) == {}


def test_combine_pondera():
    r = combine({"a": 0.8, "b": 0.4}, {"a": 0.75, "b": 0.25})
    assert r == pytest.approx(0.75 * 0.8 + 0.25 * 0.4)


def test_combine_encoge_hacia_el_centro():
    """El promedio aritmético nunca extremiza. Eso es deliberado."""
    r = combine({"a": 0.9, "b": 0.5}, {"a": 0.5, "b": 0.5})
    assert 0.5 < r < 0.9


def test_combine_sin_pesos_usa_media_simple():
    assert combine({"a": 0.8, "b": 0.4}, {}) == pytest.approx(0.6)


def test_combine_vacio_es_ignorancia():
    assert combine({}, {}) == 0.5


def test_desacuerdo_es_senal():
    assert disagreement({"a": 0.5, "b": 0.5}) == pytest.approx(0.0)
    assert disagreement({"a": 0.1, "b": 0.9}) > 0.5
    assert disagreement({"a": 0.5}) == 0.0
