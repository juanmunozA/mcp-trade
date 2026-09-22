"""
El modelo mira el mercado y dice si sube.

UNA LLAMADA PARA TODOS LOS PARES
Se le mandan los ocho juntos y devuelve ocho probabilidades. Ocho
llamadas separadas gastarian ocho veces la cuota para medir lo mismo, y
con 250 llamadas al dia eso limita el experimento a media hora.

EL LISTON ES 0.50
"Sube o baja en 15 minutos" es casi una moneda al aire. Si el promedio
de sus probabilidades no le gana a 0.50, no tiene nada -- y la unica
forma de saberlo es que se comprometa con un numero antes, no despues.
"""

from __future__ import annotations

import json
import os
import pathlib

import httpx

URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
       "{m}:generateContent")

PROMPT = """Sos un operador cuantitativo. Para cada par te doy el estado del
mercado AHORA y decís la probabilidad de que el precio esté MÁS ALTO dentro de
15 minutos.

DIRECTRIZ: SIEMPRE SE ENTRA. No existe quedarse afuera. Tu número decide de
qué LADO y con cuánta convicción, nunca si se opera o no. Nunca 0.500 exacto.

TU PUNTO DE PARTIDA NO SOS VOS
Recibís `PROBABILIDAD DE LA REGRESION`: una logística ajustada sobre todas las
operaciones ya resueltas. Medido sobre las mismas 261 operaciones, la
regresión sacó 54,4% de acierto y +17,02% neto; un modelo de lenguaje leyendo
las mismas tablas sacó 47,1% y −62,15%. La cuenta la hace mejor la cuenta.

TU TRABAJO ES AJUSTARLA, NO REEMPLAZARLA. Solo podés moverla ±0,06, y el
recorte se aplica en código aunque mandes otra cosa. Movela cuando veas algo
que sus cinco variables NO codifican:
  - cascada: RSI>75 con volumen sobre 3x. La regresión lee "RSI alto =
    reversión" y ahí se equivoca, porque el movimiento continúa.
  - ruptura de rango con volumen, que no está entre sus variables
  - un par que se resiste mientras los otros siete caen
Si no ves nada de eso, repetí la base tal cual. Repetirla es la respuesta
correcta la mayoría de las veces; desviarse sin motivo es lo que costó la
primera noche.

DE DÓNDE SALEN LAS REGLAS
Todo lo medido está en LO QUE SE SABE DE ESTE MERCADO, arriba: la tabla de
probabilidad condicional, el umbral de rentabilidad, cómo estimar el tamaño
del movimiento, qué señales midieron cero. Esas notas las mantiene el propio
sistema con sus datos y se reescriben cuando la evidencia cambia — son la
fuente, no este texto. Si una nota contradice algo que creías, gana la nota.

Si el bloque dice que algunas notas no entraron por espacio, tenelas por
existentes y medidas: no afirmes que algo "no está medido" porque no lo viste.

CÓMO RESPONDER
Elegí el lado y contraé la convicción hacia 0,50 según lo que diga la tabla.
Nunca más allá de 0,30-0,70. Etiquetá el fundamento por lo que de verdad
usaste, y en `dato` poné el número concreto que lo respalda.

La razón dice QUÉ esperás y de qué tamaño, no qué indicador miraste. "EMA9
sobre EMA21" no es una razón; "RSI 5m en 71 con 1h subiendo, celda 0,35,
m=0,6%, voy 0,44" sí."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "predicciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "simbolo": {"type": "string"},
                    "p_sube": {"type": "number",
                               "description": "Probabilidad de estar más alto en 15 min, 0 a 1"},
                    "fundamento": {
                        "type": "string",
                        "enum": ["reversion_rsi", "reversion_rsi_con_volumen",
                                 "cascada", "flujo_agresor", "contagio_btc",
                                 "libro_con_volumen", "ruptura_rango",
                                 "inercia_corto", "sin_senal"],
                        "description": "Qué justifica el lado. reversion_rsi es la celda "
                                       "de la tabla; cascada es RSI>75 con volumen >3x "
                                       "donde el movimiento CONTINÚA en vez de revertir"},
                    "dato": {"type": "string",
                             "description": "El número que lo respalda, ej: flujo_reciente=0.42"},
                    "movimiento_esperado_pct": {
                        "type": "number",
                        "description": "Cuánto esperás que se mueva en 15 min, en %"},
                    "razon": {"type": "string", "description": "Máximo 90 caracteres"},
                    "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                },
                "required": ["simbolo", "p_sube", "fundamento", "dato",
                             "movimiento_esperado_pct", "razon", "confianza"],
            },
        }
    },
    "required": ["predicciones"],
}


_AQUI = pathlib.Path(__file__).parent
CARPETAS = [_AQUI / "notas", _AQUI.parent / "vault" / "cripto"]
"""
Donde buscar las notas, en orden.

Primero dentro del paquete y despues en el vault del repo grande. Asi
el modulo funciona igual suelto en su propio repositorio que dentro de
este, sin cambiar una linea.
"""


def _notas() -> str:
    """
    Lo que se sabe del mercado, no lo que el modelo recuerde.

    Son hallazgos con evidencia, no consejos de curso: flujo de ordenes,
    vuelta de vela, contagio entre monedas, financiacion extrema, y por
    que fracasan las estrategias. Van en cada llamada porque el modelo no
    tiene memoria entre una y otra.

    Si la carpeta no esta, devuelve vacio y el resto sigue igual.
    """
    carpeta = next((c for c in CARPETAS if c.is_dir()), None)
    if carpeta is None:
        return ""
    try:
        trozos = []
        for f in sorted(carpeta.glob("*.md")):
            trozos.append(f"### {f.stem}" + chr(10) + f.read_text(encoding="utf-8").strip())
        if not trozos:
            return ""
        return ("LO QUE SE SABE DE ESTE MERCADO" + chr(10) + chr(10)
                + (chr(10) + chr(10)).join(trozos))
    except OSError:
        return ""


MARGEN = 0.06
"""
Cuanto puede apartarse el modelo de lenguaje de la regresion.

No es desconfianza gratuita: medido sobre las mismas 261 operaciones, la
regresion fuera de muestra saco 54,4% de acierto y +17,02% neto, y el
modelo leyendo las mismas tablas saco 47,1% y -62,15%. La cuenta la hace
mejor la cuenta.

Le queda el trabajo donde si puede aportar -- ver algo que las cinco
variables no codifican -- y 0.06 alcanza para eso sin permitirle deshacer
lo unico que midio ganancia. Se recorta en codigo: pedirlo en el prompt
seria confiar en que obedezca.
"""


class ErrorIA(Exception):
    pass


def _boletin(h: dict | None) -> str:
    """
    Su propio historial, en la cara.

    Sin esto el modelo no tiene forma de saber que le esta yendo mal: cada
    llamada arranca sin memoria y con la misma confianza de siempre.

    Van tres cosas y cada una responde una pregunta distinta. El total dice
    CUANTO va perdiendo. El desglose por fundamento dice POR DONDE, que es
    lo unico accionable -- la primera noche `contagio_btc` acerto 5 de 13 y
    el promedio global no lo mostraba. Y la calibracion del tamano dice si
    sigue subestimando el movimiento, que fue el tercer error medido.
    """
    if not h or not h.get("n"):
        return ""
    l = [f"TU HISTORIAL EN ESTE EXPERIMENTO ({int(h['n'])} predicciones resueltas):"]
    if h.get("acierto_pct") is not None:
        l.append(f"- acertaste la dirección el {h['acierto_pct']}% de las veces")
    if h.get("brier_agente") is not None:
        l.append(f"- Brier: vos {h['brier_agente']:.4f} | azar 0.2500 | "
                 f"inercia {h['brier_inercia']:.4f}"
                 + ("  ← te están ganando los dos"
                    if h["brier_agente"] > min(0.25, h["brier_inercia"]) else ""))
    if h.get("pnl_bruto_pct") is not None:
        l.append(f"- antes de comisiones: {h['pnl_bruto_pct']:+.3f}%")
        l.append(f"- DESPUÉS de comisiones: {h['pnl_neto_pct']:+.3f}%")

    porf = h.get("por_fundamento") or []
    if porf:
        l.append("")
        l.append("CUÁL DE TUS RAZONES SIRVE (n>=5, de mejor a peor):")
        for f in porf:
            l.append(f"  {f['fundamento']:22} n={int(f['n']):3}  "
                     f"acierto {float(f['acierto_pct']):5.1f}%  neto {float(f['neto_pct']):+7.2f}%")
        peor = porf[-1]
        if float(peor["neto_pct"]) < 0:
            # `sin_senal` no es una razon para apostar: es la ausencia de una.
            # Si es el peor, lo que falla es el desempate, no el fundamento.
            l.append("  → tu regla de desempate cuando no ves nada está perdiendo. "
                     "Acercate más a 0.50 en esos casos, no elijas con ganas."
                     if peor["fundamento"] == "sin_senal" else
                     f"  → dejá de usar '{peor['fundamento']}' como razón para "
                     "alejarte de 0.50. Te está costando plata.")

    esp, real = h.get("mov_esperado_medio"), h.get("mov_real_mediano")
    if esp and real:
        l.append("")
        l.append(f"TAMAÑO: decís que se moverá {esp:.3f}% y se mueve {real:.3f}%.")
        if real > esp * 1.5:
            l.append(f"  Estás subestimando por {real / esp:.1f}x. Subí tus "
                     "estimaciones y con ellas tu convicción cuando la señal sea buena.")

    if (h.get("pnl_neto_pct") or 0) < 0:
        l.append("")
        l.append("Estás PERDIENDO. Pero ojo con la lección equivocada: el problema "
                 "no fue apostar fuerte, fue apostar del lado del movimiento que ya "
                 "pasó. Cuando no veas un extremo de RSI, 0.49 o 0.51. Cuando lo "
                 "veas, apostá contra el extremo y con convicción.")
    return chr(10).join(l)


async def predecir(fotos: list[dict], modelo: str = "",
                   historial: dict | None = None,
                   referencia: dict | None = None,
                   base: dict[str, float] | None = None,
                   nota_base: str = "",
                   saber_texto: str = "") -> list[dict]:
    # Quien razona este ciclo. El primero que tenga llave; el experimento
    # mide igual con cualquiera, y `modelo` queda grabado en cada fila para
    # poder comparar despues proveedor contra proveedor.
    pedido = modelo or os.getenv("CRIPTO_MODELO", "").strip()
    texto = _partes(fotos, historial, referencia, base, nota_base, saber_texto)

    if k := os.getenv("ANTHROPIC_API_KEY", "").strip():
        return await _por_anthropic(fotos, k, pedido or "claude-opus-5",
                                    base, texto)
    if k := os.getenv("DEEPSEEK_API_KEY", "").strip():
        return await _por_anthropic(fotos, k, pedido or "deepseek-flash",
                                    base, texto, DEEPSEEK)
    # Groq antes que Mistral: su tier gratuito sirve modelos de 120B, y
    # el de Mistral solo los chicos, que leen mal la tabla condicional.
    if k := os.getenv("GROQ_API_KEY", "").strip():
        return await _por_openai(fotos, k, pedido or "openai/gpt-oss-120b",
                                 base, texto, GROQ)
    if k := os.getenv("MISTRAL_API_KEY", "").strip():
        return await _por_openai(fotos, k, pedido or "ministral-14b-latest",
                                 base, texto, MISTRAL)

    llave = os.getenv("GEMINI_API_KEY", "").strip()
    if not llave:
        raise ErrorIA("no hay llave de ningún proveedor")
    modelo = pedido or "gemini-2.5-flash"

    cuerpo = {
        "contents": [{"parts": [
            {"text": PROMPT},
            # Lo aprendido gana sobre lo sembrado: `saber_texto` viene de la
            # base, donde el propio agente reescribe sus notas. `_notas()`
            # queda para correr suelto, sin base de conocimiento todavia.
            *([{"text": notas}] if (notas := saber_texto or _notas()) else []),
            *([{"text": boletin}] if (boletin := _boletin(historial)) else []),
            *([{"text": "BTC COMO REFERENCIA (no se opera, es para el contagio):"
                + chr(10) + json.dumps(referencia, ensure_ascii=False)}]
              if referencia else []),
            *([{"text": "PROBABILIDAD DE LA REGRESION (tu punto de partida):"
                + chr(10) + json.dumps(base, ensure_ascii=False)
                + chr(10) + nota_base}] if base else []),
            {"text": "MERCADO AHORA:" + chr(10) + json.dumps(fotos, ensure_ascii=False)},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": ESQUEMA,
            "temperature": 0.2,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=120) as cli:
            r = await cli.post(URL.format(m=modelo), params={"key": llave}, json=cuerpo)
    except httpx.HTTPError as exc:
        raise ErrorIA(f"red: {exc}") from exc
    if r.status_code != 200:
        raise ErrorIA(f"HTTP {r.status_code}: {r.text[:400]}")

    try:
        texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(texto)["predicciones"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ErrorIA("respuesta ilegible") from exc
    return _validar(items, fotos, base, modelo)


DEEPSEEK = "https://api.deepseek.com/anthropic"
MISTRAL = "https://api.mistral.ai/v1/chat/completions"
GROQ = "https://api.groq.com/openai/v1/chat/completions"


async def _por_openai(fotos, llave, modelo, base, cuerpo, url) -> list[dict]:
    """
    El formato de OpenAI, que hablan Mistral y Groq.

    `response_format` en `json_object` obliga a que la respuesta sea JSON
    valido -- lo mas cerca del `responseSchema` de Gemini que hay aca.
    No garantiza los CAMPOS, solo que parsee, asi que `_validar` sigue
    descartando lo que no cierre.
    """
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(url,
            headers={"Authorization": f"Bearer {llave}"},
            # `max_tokens` se SUMA al input para el limite por minuto del
            # proveedor -- Groq lo cobra antes de generar nada. Ocho
            # predicciones con su razon entran en 1.800 de sobra; pedir mas
            # solo hace que la llamada se rechace sin haber empezado.
            json={"model": modelo, "max_tokens": 2600, "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  # Los gpt-oss razonan antes de contestar y ese razonamiento
                  # sale del mismo `max_tokens`. En "high" se lo gasta entero
                  # pensando y devuelve vacio, que llega como un 400 de JSON
                  # invalido y cuesta el ciclo. La decision ya viene calculada
                  # por la regresion: aca alcanza con ajustar.
                  "reasoning_effort": "low",
                  "messages": [{"role": "system", "content": PROMPT},
                               {"role": "user", "content": cuerpo}]})
    if r.status_code != 200:
        raise ErrorIA(f"HTTP {r.status_code}: {r.text[:400]}")
    try:
        txt = r.json()["choices"][0]["message"]["content"]
        items = json.loads(txt)["predicciones"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ErrorIA("respuesta ilegible") from exc
    return _validar(items, fotos, base, modelo)


def _partes(fotos, historial, referencia, base, nota_base, saber_texto) -> str:
    """Todo lo que el modelo tiene que ver, en un solo texto."""
    t = []
    if notas := (saber_texto or _notas()):
        t.append(notas)
    if boletin := _boletin(historial):
        t.append(boletin)
    if base:
        t.append("PROBABILIDAD DE LA REGRESION (tu punto de partida):"
                 + chr(10) + json.dumps(base, ensure_ascii=False)
                 + chr(10) + nota_base)
    if referencia:
        t.append("BTC COMO REFERENCIA (no se opera, es para el contagio):"
                 + chr(10) + json.dumps(referencia, ensure_ascii=False))
    t.append("MERCADO AHORA:" + chr(10) + json.dumps(fotos, ensure_ascii=False))
    t.append("Devolvé SOLO un objeto JSON con esta forma, sin texto alrededor: "
             + json.dumps({"predicciones": [{k: "…" for k in ESQUEMA["properties"]
                 ["predicciones"]["items"]["required"]}]}, ensure_ascii=False))
    return (chr(10) + chr(10)).join(t)


async def _por_anthropic(fotos, llave, modelo, base, cuerpo, url="") -> list[dict]:
    """
    El formato de Anthropic, que DeepSeek tambien habla en `/anthropic`.

    A diferencia de Gemini no hay `responseSchema` que garantice la forma,
    asi que el esquema va pedido en el texto y la respuesta se recorta a
    las llaves extremas. Es menos estricto y por eso `_validar` descarta
    cualquier fila que no cierre, igual que con Gemini.
    """
    import anthropic

    cli = anthropic.AsyncAnthropic(api_key=llave,
                                   **({"base_url": url} if url else {}))
    extra = {} if url else {"thinking": {"type": "adaptive"},
                            "output_config": {"effort": "medium"}}
    try:
        msg = await cli.messages.create(
            model=modelo, max_tokens=8000, system=PROMPT,
            messages=[{"role": "user", "content": cuerpo}], **extra)
    except Exception as exc:  # noqa: BLE001
        raise ErrorIA(f"{type(exc).__name__}: {str(exc)[:140]}") from exc
    if msg.stop_reason == "refusal":
        raise ErrorIA(f"declinó: {msg.stop_details}")
    txt = "".join(b.text for b in msg.content if b.type == "text")
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        raise ErrorIA("respuesta sin JSON")
    try:
        items = json.loads(txt[i:j + 1])["predicciones"]
    except (KeyError, ValueError) as exc:
        raise ErrorIA("respuesta ilegible") from exc
    return _validar(items, fotos, base, modelo)


def _validar(items, fotos, base, modelo) -> list[dict]:
    """Lo que sobrevive de lo que dijo. Igual para todos los proveedores."""

    validas = []
    conocidos = {f["simbolo"] for f in fotos}
    for it in items:
        s = it.get("simbolo")
        if s not in conocidos:
            continue
        try:
            p = float(it.get("p_sube"))
        except (TypeError, ValueError):
            continue
        # Fuera de [0,1] es basura; pegarlo al borde disfrazaria el error.
        if not 0.0 <= p <= 1.0:
            continue
        # Con entrada forzada, 0.500 exacto no define lado y el motor lo
        # leeria como "no opera". Se desempata con el ultimo movimiento,
        # que es la regla que el prompt ya le da para cuando no ve nada.
        if abs(p - 0.5) < 1e-9:
            f = next((x for x in fotos if x["simbolo"] == s), None)
            p = 0.52 if (f or {}).get("cambio_5m", 0) >= 0 else 0.48
        try:
            esp = float(it.get("movimiento_esperado_pct") or 0)
        except (TypeError, ValueError):
            esp = 0.0
        fund = it.get("fundamento") or "sin_senal"
        # Si dice tener fundamento pero espera un movimiento que no paga la
        # comision, el fundamento no existe. Se marca como tal en vez de
        # creerle: la etiqueta es para medir despues cual sirve de verdad.
        if fund != "sin_senal" and abs(esp) < 0.10:
            fund = "sin_senal"
        # El margen se recorta aca y no se le pide de favor al prompt. Un
        # modelo que ignora la instruccion no deberia poder tirar por la
        # borda la unica parte del sistema que midio ganancia.
        if base and s in base:
            b = base[s]
            p = min(b + MARGEN, max(b - MARGEN, p))
        validas.append({
            "simbolo": s, "p_sube": round(p, 4),
            "razon": str(it.get("razon", ""))[:160],
            # Solo Gemini tiene `responseSchema` que fuerza el enum. Los
            # proveedores por formato OpenAI/Anthropic no lo garantizan, y
            # un gpt-oss ya devolvio 0.58 en vez de "media" -- rompia el
            # INSERT entero, 8 filas perdidas por un campo cosmetico.
            "confianza": (c if (c := it.get("confianza")) in
                          ("alta", "media", "baja") else "media"),
            "fundamento": fund,
            "dato": str(it.get("dato", ""))[:120],
            "mov_esperado": round(esp, 3),
            "modelo": modelo,
        })
    if not validas:
        raise ErrorIA("ninguna predicción utilizable")
    return validas
