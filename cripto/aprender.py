"""
El ciclo que se cierra solo: medir, leer lo medido, reescribir lo que se cree.

QUE PROBLEMA RESUELVE
─────────────────────
Hasta ahora el aprendizaje tenia un humano en el medio. Alguien corria
`cripto.analizar`, leia el informe y traducia los numeros a notas. Sin ese
alguien, el modelo lee las mismas notas dentro de seis meses aunque el
mercado haya cambiado: acumula lo que le escriban, no lo que le pasa.

Aca ese eslabon se automatiza. Cada tanto -- cuando hay suficientes
operaciones resueltas nuevas -- se corre el analisis, se le pasa al modelo
junto con lo que hoy cree saber, y se le pide que reescriba las notas
conservando solo lo que los numeros sostienen.

POR QUE ES EL MODELO Y NO UNA PLANTILLA
Una plantilla podria rellenar cifras, pero no puede decidir que una regla
dejo de valer, que dos notas ahora se contradicen, o que un hallazgo se
evaporo al llegar mas datos. Eso es juicio sobre evidencia, y es
exactamente donde un modelo de lenguaje aporta algo que la regresion no.

DOS FRENOS CONTRA LA DERIVA
    - se le exige citar el numero que respalda cada afirmacion, y el
      informe va en la misma llamada para que pueda hacerlo
    - cada version queda guardada con cuantas operaciones la respaldaban,
      asi que si empieza a inventar se ve en el historial
"""

from __future__ import annotations

import json
import logging
import os

from cripto import saber

log = logging.getLogger("cripto.aprender")

CADA = int(os.getenv("CRIPTO_APRENDER_CADA", "200"))
"""
Cuantas operaciones resueltas nuevas antes de volver a pensar.

Doscientas es un compromiso: con menos, reescribe sobre ruido y cada
version contradice a la anterior; con muchas mas, tarda dias en corregir
una regla que ya dejo de andar. A 8 predicciones cada 5 minutos son unas
dos horas de mercado.
"""

MINIMO = 120
"""Por debajo de esto no hay nada que aprender, solo ruido que memorizar."""

INSTRUCCION = """Sos el agente de un banco de pruebas que predice la dirección
de precios de cripto a 15 minutos. Ya llevás un tiempo operando y acaban de
medir tus resultados.

Tu tarea ahora NO es predecir. Es reescribir lo que creés saber.

Te doy dos cosas:
1. EL INFORME: el análisis estadístico de tus propias operaciones resueltas,
   con intervalos de confianza y corrección por agrupamiento.
2. TUS NOTAS ACTUALES: lo que venías creyendo hasta ahora.

Devolvé las notas reescritas para que reflejen lo que el informe sostiene.

REGLAS, en orden de importancia:

1. CADA AFIRMACIÓN LLEVA SU NÚMERO. Si no podés citar una cifra del informe
   que la respalde, la afirmación no va. "El RSI predice reversión" no sirve;
   "RSI 5m bajo 40 acertó 66,7% con IC [53,4 ; 78,5] sobre n=91" sí.

2. BORRÁ LO QUE YA NO SE SOSTIENE. Si una nota afirma algo que los datos
   nuevos contradicen, corregila o eliminá la afirmación. No la suavices.
   Dejar una regla muerta es peor que no tener la nota.

3. ESCRIBÍ TAMBIÉN LO QUE NO FUNCIONÓ. Que una señal midiera cero es un
   hallazgo y evita volver a probarla. Vale tanto como lo que sí funciona.

4. DISTINGUÍ RÉGIMEN DE LEY. Lo medido en unas horas de una sesión es un
   régimen y puede darse vuelta. Lo estructural -- que los pares
   correlacionados son una apuesta y no ocho, que acertar y perder plata es
   posible -- no depende del régimen. Marcá cuál es cuál.

5. SI UN HALLAZGO SE EVAPORÓ AL LLEGAR MÁS DATOS, CONTALO. Ese episodio
   enseña más que el hallazgo, porque es la forma en que se ve el
   sobreajuste desde adentro.

6. NO INFLES. Si el informe no trae nada nuevo respecto de lo que ya decían
   las notas, devolvé esa nota igual que estaba. Reescribir por reescribir
   ensucia el historial y simula un aprendizaje que no ocurrió.

Escribí en español rioplatense, directo, sin adornos. Cada nota empieza con
una frase en negrita que diga lo principal. Usá bloques de código para las
tablas de números.

Devolvé SOLO un objeto JSON, sin texto alrededor, con esta forma:

{"notas": [{"nombre": "<el mismo nombre que recibiste>",
            "cuerpo": "<el markdown completo de la nota>",
            "cambio": "<una línea: qué cambiaste y por qué, o 'sin cambios'>"}]}

Podés crear una nota nueva si el informe muestra algo que ninguna cubre;
usá un nombre-en-kebab-case corto. No borres notas: si una ya no sirve,
devolvela diciendo explícitamente que se midió y no funcionó."""


class SinLlave(Exception):
    """No hay con que pensar. No es un error: es una configuracion."""


async def _con_claude(instruccion: str, cuerpo: str) -> str:
    """
    El SDK oficial de Anthropic.

    Se usa streaming porque reescribir varias notas son decenas de miles
    de tokens de salida y una llamada sin stream se come el tiempo de
    espera del cliente.
    """
    import anthropic

    cli = anthropic.AsyncAnthropic()
    async with cli.messages.stream(
        model=os.getenv("CRIPTO_MODELO_APRENDER", "claude-opus-5"),
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=instruccion,
        messages=[{"role": "user", "content": cuerpo}],
    ) as flujo:
        msg = await flujo.get_final_message()
    if msg.stop_reason == "refusal":
        raise SinLlave(f"el modelo declinó: {msg.stop_details}")
    return "".join(b.text for b in msg.content if b.type == "text")


async def _con_gemini(instruccion: str, cuerpo: str) -> str:
    """El camino alternativo, por HTTP como el resto del paquete."""
    import httpx

    llave = os.getenv("GEMINI_API_KEY", "").strip()
    modelo = os.getenv("CRIPTO_MODELO_APRENDER", "gemini-2.5-flash")
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{modelo}:generateContent")
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, params={"key": llave}, json={
            "contents": [{"parts": [{"text": instruccion}, {"text": cuerpo}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "temperature": 0.3},
        })
    if r.status_code != 200:
        raise SinLlave(f"gemini HTTP {r.status_code}: {r.text[:160]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


async def _pensar(instruccion: str, cuerpo: str) -> str:
    """
    El que este disponible. Anthropic primero porque razona mejor sobre
    evidencia estadistica, que es todo lo que hace esta tarea.
    """
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return await _con_claude(instruccion, cuerpo)
    if os.getenv("GEMINI_API_KEY", "").strip():
        return await _con_gemini(instruccion, cuerpo)
    raise SinLlave("no hay ANTHROPIC_API_KEY ni GEMINI_API_KEY")


def _json(texto: str) -> dict:
    """
    Recorta la respuesta al objeto JSON.

    Un modelo puede envolver el JSON en un bloque de codigo o meter una
    linea de cortesia antes. Buscar las llaves extremas es mas robusto que
    exigir obediencia perfecta al formato.
    """
    i, j = texto.find("{"), texto.rfind("}")
    if i < 0 or j <= i:
        raise SinLlave("la respuesta no trae JSON")
    return json.loads(texto[i:j + 1])


async def toca(pool) -> bool:
    """
    Si hay bastante material nuevo desde la ultima reescritura.

    Se cuenta contra el `apoyo` de la ultima version guardada, no contra
    un reloj: lo que habilita a aprender son datos, no que pase el tiempo.
    """
    n = await pool.fetchval(
        "SELECT count(*) FROM cripto.predicciones WHERE subio IS NOT NULL")
    if n < MINIMO:
        return False
    ultimo = await pool.fetchval(
        "SELECT max(apoyo) FROM cripto.notas WHERE apoyo IS NOT NULL")
    return n - (ultimo or 0) >= CADA


async def aprender(pool, motivo: str = "ciclo automático") -> dict:
    """
    Una vuelta completa: medir, pensar, guardar.

    Devuelve que cambio. Si no hay llave devuelve `{"saltado": ...}` en
    vez de romper: quedarse sin modelo de lenguaje no deberia tumbar el
    experimento, que sigue prediciendo con la regresion.
    """
    from cripto.analizar import informe, traer

    datos = await traer()
    if len(datos) < MINIMO:
        return {"saltado": f"solo {len(datos)} resueltas"}

    inf = informe(datos)
    notas = await saber.vigentes(pool)
    cuerpo = ("EL INFORME" + chr(10) + chr(10) + inf + chr(10) + chr(10)
              + "TUS NOTAS ACTUALES" + chr(10) + chr(10)
              + json.dumps([{"nombre": k, "cuerpo": v}
                            for k, v in sorted(notas.items())],
                           ensure_ascii=False))
    try:
        crudo = await _pensar(INSTRUCCION, cuerpo)
    except SinLlave as exc:
        log.info("no se pudo aprender: %s", exc)
        return {"saltado": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("la reescritura falló: %s", exc)
        return {"saltado": repr(exc)[:200]}

    try:
        items = _json(crudo)["notas"]
    except (SinLlave, KeyError, ValueError) as exc:
        log.warning("respuesta ilegible: %s", exc)
        return {"saltado": "respuesta ilegible"}

    cambios = []
    for it in items:
        nombre = str(it.get("nombre", "")).strip()
        texto = str(it.get("cuerpo", "")).strip()
        # Una nota vacia o casi vacia borraria conocimiento sin decirlo.
        if not nombre or len(texto) < 120:
            continue
        v = await saber.guardar(pool, nombre, texto, len(datos),
                                f"{motivo}: {str(it.get('cambio',''))[:200]}")
        if v:
            cambios.append({"nombre": nombre, "version": v,
                            "cambio": str(it.get("cambio", ""))[:200]})
    log.info("aprendió sobre %d operaciones: %d notas reescritas",
             len(datos), len(cambios))
    return {"apoyo": len(datos), "reescritas": cambios,
            "sin_cambios": len(items) - len(cambios)}
