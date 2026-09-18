"""
Tests del parseo de respuestas. Sin red: solo la lógica que convierte el
texto del modelo en un dict utilizable.

Importan porque los modelos desobedecen el formato. Medido contra la API
real: `gemini-2.5-flash-lite` envuelve en ```json aunque el prompt lo
prohíba explícitamente.
"""

from __future__ import annotations

import pytest

from pilot_a_llm.providers import Completion


def comp(text: str) -> Completion:
    return Completion(text=text, provider="test", model="test")


def test_json_limpio():
    assert comp('{"p": 0.7}').json() == {"p": 0.7}


def test_fence_markdown_se_quita():
    c = comp('```json\n{"p": 0.7, "razon": "x"}\n```')
    assert c.json() == {"p": 0.7, "razon": "x"}
    assert c.used_fence is True


def test_fence_sin_lenguaje():
    assert comp('```\n{"p": 0.3}\n```').json() == {"p": 0.3}


def test_json_limpio_no_marca_fence():
    assert comp('{"p": 0.7}').used_fence is False


def test_texto_antes_del_json():
    """Algunos modelos anteponen prosa pese a las instrucciones."""
    c = comp('Claro, aquí tienes:\n{"p": 0.42, "razon": "porque sí"}')
    assert c.json() == {"p": 0.42, "razon": "porque sí"}


def test_texto_despues_del_json():
    c = comp('{"p": 0.42}\n\nEspero que ayude.')
    assert c.json() == {"p": 0.42}


def test_objetos_anidados():
    c = comp('ruido {"a": {"b": {"c": 1}}, "d": 2} más ruido')
    assert c.json() == {"a": {"b": {"c": 1}}, "d": 2}


def test_llaves_dentro_de_strings():
    """El contador de llaves no debe confundirse con texto que las contiene."""
    c = comp('{"razon": "usa {llaves} en el texto", "p": 0.5}')
    assert c.json() == {"razon": "usa {llaves} en el texto", "p": 0.5}


def test_json_truncado_devuelve_none():
    """
    Lo que pasaba de verdad: Gemini cortaba a media frase por presupuesto de
    thinking. Debe devolver None, nunca un dict a medias que parezca válido.
    """
    c = comp('{"p": 0.62, "reasoning": "Bogotá tiene un clima de alta monta')
    assert c.json() is None


def test_texto_sin_json():
    assert comp("No puedo responder eso.").json() is None


def test_vacio():
    assert comp("").json() is None


def test_json_invalido_devuelve_none():
    assert comp("{esto no es json}").json() is None


@pytest.mark.parametrize(
    "text",
    [
        '  {"p": 1}  ',
        '\n\n{"p": 1}\n',
        '```json{"p": 1}```',
    ],
)
def test_espacios_y_variantes(text):
    assert comp(text).json() == {"p": 1}
