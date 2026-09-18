"""
Tests del vault.

Lo que protegen: que las notas del operador lleguen al agente, que el
agente nunca las sobrescriba, y que un vault enorme no reviente el prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pilot_a_llm.forecast import Forecast
from pilot_a_llm.markets import Candidate
from pilot_a_llm.vault import MAX_CONTEXT_CHARS, Vault

NOW = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault")


def forecast(**over) -> Forecast:
    c = Candidate(
        condition_id="0x1", slug="lula-segundo", question="¿Lula queda segundo?",
        rules="", token_yes="y", token_no="n", price=0.30, liquidity=80_000,
        volume=400_000, days_to_resolution=15, category="politica", end_date=NOW,
    )
    base = dict(
        candidate=c, p_raw=0.42, p_calibrated=0.40,
        per_model={"deepseek": 0.40, "llama": 0.44}, disagreement=0.028,
        means_yes="Lula termina en segundo lugar en primera vuelta.",
        reference_class="incumbentes en primera vuelta: ~60%", base_rate=0.6,
        key_evidence=["encuestas lo dan primero", "margen de 8 puntos"],
        steps_required="Que se celebre la elección.",
        confidence="medium",
        what_would_change_my_mind="Un giro de 10 puntos en encuestas.",
        resolution_risk="",
    )
    base.update(over)
    return Forecast(**base)


# ─────────────────────── estructura ─────────────────────────────────────


def test_crea_las_tres_carpetas(vault):
    assert vault.forecasts.is_dir()
    assert vault.post_mortems.is_dir()
    assert vault.context.is_dir()


def test_deja_instrucciones_en_contexto(vault):
    readme = vault.context / "_LEEME.md"
    assert readme.exists()
    assert "lecciones, no noticias" in readme.read_text(encoding="utf-8")


# ─────────────────── lo que el agente escribe ───────────────────────────


def test_escribe_nota_de_pronostico(vault):
    path = vault.write_forecast(forecast(), "YES", 4.50)
    text = path.read_text(encoding="utf-8")
    assert "¿Lula queda segundo?" in text
    assert "Lula termina en segundo lugar" in text
    assert "incumbentes en primera vuelta" in text
    assert "deepseek" in text and "llama" in text
    assert "$4.50" in text


def test_frontmatter_para_obsidian(vault):
    text = vault.write_forecast(forecast(), "YES", 4.50).read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "categoria: politica" in text
    assert "tags: [pronostico, politica]" in text


def test_nota_de_veto_guarda_el_motivo(vault):
    f = forecast(vetoed=True, veto_reason="desacuerdo 0.31 > 0.2")
    text = vault.write_forecast(f, "VETO", None).read_text(encoding="utf-8")
    assert "desacuerdo 0.31" in text
    assert "sin posición" in text


# ─────────────── lo que el agente lee (lo importante) ───────────────────


def test_lee_las_notas_del_operador(vault):
    (vault.context / "resolucion.md").write_text(
        "Polymarket resuelve por la fuente del reglamento, no por la verdad.",
        encoding="utf-8",
    )
    ctx = vault.read_context()
    assert "fuente del reglamento" in ctx


def test_quita_el_frontmatter_al_inyectar(vault):
    (vault.context / "nota.md").write_text(
        "---\ncategoria: politica\n---\n\nLa lección de verdad.",
        encoding="utf-8",
    )
    ctx = vault.read_context(category="politica")
    assert "La lección de verdad" in ctx
    assert "categoria:" not in ctx


def test_nota_con_categoria_no_contamina_otras(vault):
    (vault.context / "geo.md").write_text(
        "---\ncategoria: geopolitica\n---\nSolo para geopolítica.", encoding="utf-8"
    )
    assert "Solo para geopolítica" in vault.read_context(category="geopolitica")
    assert "Solo para geopolítica" not in vault.read_context(category="economia")


def test_nota_sin_categoria_aplica_a_todo(vault):
    (vault.context / "general.md").write_text("Vale para todo.", encoding="utf-8")
    for cat in ("politica", "economia", "deportes"):
        assert "Vale para todo" in vault.read_context(category=cat)


@pytest.mark.parametrize("marca", ["todas", "todos", "all", "*", "TODAS"])
def test_categoria_todas_aplica_a_todo(vault, marca):
    """
    Bug real: el filtro comparaba `category not in tags` y descartaba
    `categoria: todas` para cualquier categoría concreta. Los tests no lo
    vieron porque usaban notas sin frontmatter; se descubrió corriendo.
    """
    (vault.context / "global.md").write_text(
        f"---\ncategoria: {marca}\n---\nLección universal.", encoding="utf-8"
    )
    for cat in ("politica", "geopolitica", "economia"):
        assert "Lección universal" in vault.read_context(category=cat)


def test_varias_categorias_en_una_nota(vault):
    (vault.context / "multi.md").write_text(
        "---\ncategoria: politica, economia\n---\nPara ambas.", encoding="utf-8"
    )
    assert "Para ambas" in vault.read_context(category="politica")
    assert "Para ambas" in vault.read_context(category="economia")
    assert "Para ambas" not in vault.read_context(category="deportes")


def test_respeta_el_tope_de_caracteres(vault):
    """Un vault de meses no puede enterrar la evidencia del mercado."""
    for i in range(40):
        (vault.context / f"nota{i:02d}.md").write_text("x" * 500, encoding="utf-8")
    assert len(vault.read_context()) <= MAX_CONTEXT_CHARS + 200


def test_las_recientes_entran_primero(vault, monkeypatch):
    import os
    import time

    vieja = vault.context / "vieja.md"
    vieja.write_text("LECCION VIEJA", encoding="utf-8")
    os.utime(vieja, (time.time() - 90_000, time.time() - 90_000))

    nueva = vault.context / "nueva.md"
    nueva.write_text("LECCION NUEVA", encoding="utf-8")

    ctx = vault.read_context()
    assert ctx.index("LECCION NUEVA") < ctx.index("LECCION VIEJA")


def test_contexto_vacio_no_revienta(vault):
    assert vault.read_context() == ""


def test_el_readme_no_entra_al_prompt(vault):
    """
    Bug real: el _LEEME.md se inyectaba al prompt. Contiene ejemplos de
    MALAS notas ("Hoy Lula subió en las encuestas") que el modelo leería
    como lecciones, y gasta ~1.400 tokens en instrucciones para el humano.
    Convención: los archivos con "_" al inicio son tuyos, no suyos.
    """
    assert (vault.context / "_LEEME.md").exists()
    assert vault.read_context() == ""

    (vault.context / "real.md").write_text("Lección de verdad.", encoding="utf-8")
    ctx = vault.read_context()
    assert ctx == "### real\nLección de verdad."


# ─────────────────── separación de propiedad ────────────────────────────


def test_el_agente_no_escribe_en_contexto(vault):
    antes = {p.name for p in vault.context.iterdir()}
    vault.write_forecast(forecast(), "YES", 1.0)
    assert {p.name for p in vault.context.iterdir()} == antes


def test_el_prompt_marca_las_notas_como_confiables():
    """
    Van FUERA de <untrusted_market_data> a propósito: las escribió el
    operador, no internet.
    """
    from pilot_a_llm.prompts import build_forecast_prompt

    p = build_forecast_prompt(
        question="q", resolution_rules="r", evidence="e",
        today="2026-09-18", days_to_resolution=10,
        operator_notes="Revisa siempre la fuente del reglamento.",
    )
    assert "<lecciones_del_operador>" in p
    i_notes = p.index("Revisa siempre")
    i_untrusted = p.index("<untrusted_market_data>")
    assert i_notes < i_untrusted


def test_sin_notas_no_aparece_el_bloque():
    from pilot_a_llm.prompts import build_forecast_prompt

    p = build_forecast_prompt(
        question="q", resolution_rules="r", evidence="e",
        today="2026-09-18", days_to_resolution=10,
    )
    assert "lecciones_del_operador" not in p
