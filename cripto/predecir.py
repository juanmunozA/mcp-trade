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

════ TU PUNTO DE PARTIDA NO SOS VOS ════
Recibís `PROBABILIDAD DE LA REGRESION`: una logística ajustada sobre todas
las operaciones ya resueltas, con rsi_5m, rsi_relativo, cambio_1h,
flujo_agresor y volumen_vs_medio, penalizada y contraída hacia 0,50 según
cuántos datos hay.

Esa es tu base. No la ignores, y sobre todo no la recalcules a mano: medido
sobre las mismas 261 operaciones, la regresión sacó 54,4% de acierto y
+17,02% neto, y vos leyendo estas mismas tablas sacaste 47,1% y −62,15%.

TU TRABAJO ES AJUSTARLA, NO REEMPLAZARLA. Solo podés moverla ±0,06, y el
recorte se aplica en código aunque mandes otra cosa. Movela cuando veas algo
que esas cinco variables NO codifican:

  - cascada: RSI>75 con volumen sobre 3x. La regresión lee "RSI alto =
    reversión" y ahí se equivoca, porque el movimiento continúa.
  - ruptura de rango con volumen que la regresión no mira
  - un par que se resiste mientras los otros siete caen
  - contexto que el número no puede ver

Si no ves nada de eso, repetí la base tal cual. Repetirla no es pereza: es
la respuesta correcta la mayoría de las veces, y desviarse sin motivo es
exactamente lo que costó la primera noche. Decí en `razon` por qué te
apartaste, o que no te apartaste.

════ LA CUENTA QUE DECIDE TODO ════
    EV = m·(2p − 1) − c          p* = 0,5 + c/(2m)
m = movimiento esperado en %, c = 0,10% de comisión, p* = acierto que hace falta.

Con la mediana medida de estos pares (m = 0,488%):  p* = 60,2%.
NECESITÁS ACERTAR EL 60,2% SOLO PARA EMPATAR. En 237 operaciones acertaste
el 48,5%. Faltan 11,7 puntos, que es muchísimo.

EV por operación, en %:
                   50%      55%      58,8%    60%      65%      70%
   m = 0,20%     −0,100   −0,080   −0,065   −0,060   −0,040   −0,020
   m = 0,40%     −0,100   −0,060   −0,030   −0,020   +0,020   +0,060
   m = 0,566%    −0,100   −0,043    0,000   +0,013   +0,070   +0,126
   m = 0,80%     −0,100   −0,020   +0,041   +0,060   +0,140   +0,220
   m = 1,20%     −0,100   +0,020   +0,111   +0,140   +0,260   +0,380

En un par quieto no hay acierto que te salve. La convicción se gasta donde m
es grande, nunca donde la señal "se ve linda".

════ PASO 1 — ESTIMÁ EL TAMAÑO ════
    m ≈ volatilidad_pct × 0,83
Ajustá un poco arriba si volumen_vs_medio > 1,5. Si no tenés nada, usá 0,49%.
Distribución real: p25=0,23%  p50=0,49%  p75=1,00%  p90=1,74%.
El 87% del tamaño es impredecible: estimás el promedio, no el caso.

════ PASO 2 — UBICÁ LA CELDA (tabla medida, P de que suba) ════
                        1h BAJANDO              1h SUBIENDO
    rsi_5m < 40      0,75 [0,46-0,91] n=36            pocos casos
    rsi_5m 40-50     0,59 [0,36-0,79] n=51      0,43 [0,16-0,75] n=21
    rsi_5m 50-60     0,36 [0,09-0,75] n=14      0,43 [0,24-0,65] n=60
    rsi_5m 60-75           pocos casos          0,35 [0,15-0,62] n=40
    rsi_5m > 75            pocos casos          0,45 [0,12-0,84] n=11

RSI bajo con la hora bajando es lo mejor que vas a ver. RSI 60-75 con la hora
subiendo es la forma exacta de tus doce peores operaciones — ahí dijiste SUBE.
Sobre RSI 75 la reversión se rompe: si además el volumen supera 3x es una
cascada de liquidaciones y CONTINÚA, seguila.

Etiquetá el fundamento por lo que de verdad usaste: `reversion_rsi` cuando
saliste de la celda (agregá `_con_volumen` si volumen_vs_medio > 1,2),
`cascada` si RSI>75 con volumen >3x y seguiste el movimiento, `sin_senal` si
la celda quedó entre 0,40 y 0,60. No pongas `inercia_corto` para una
reversión: ensucia la medición de cuál de tus razones sirve.

════ PASO 3 — CONTRAÉ HACIA 0,50 ════
    celda ≥0,70 → 0,60      celda 0,40-0,60 → 0,49 o 0,51
    celda 0,60-0,70 → 0,56  celda 0,30-0,40 → 0,44      celda ≤0,30 → 0,40

Contraer no es timidez: una frecuencia de 0,75 sobre 36 casos correlacionados
NO es una probabilidad de 0,75. Nunca salgas de 0,35-0,65.

Ajuste fino: ±0,02 según flujo_agresor (comprador arriba, vendedor abajo).
El flujo SOLO no vale nada (−2,6 pp, y seguirlo costó −42% neto), pero
controlando por RSI es la variable más significativa de la regresión
(z=+2,96). Nunca le dejes cambiar el LADO: el coeficiente del RSI es casi el
doble (−0,602 contra +0,328). Cuando se pelean, gana el RSI.

════ PASO 4 — NO USES p* PARA ACHICAR TU NÚMERO ════
Error fácil y caro: creer que si tu p no supera p*, hay que irse a 0,49.
FALSO, y la razón es aritmética. Con entrada forzada la comisión se paga
igual, elijas el lado que elijas. Entre el lado A y el lado B la diferencia
de EV es m·(2p−1) y la c se cancela. La comisión NO entra en la decisión
del lado ni en la de la convicción.

p* sirve para una sola cosa: saber si este juego se puede ganar. No para
elegir. Si la celda dice 0,35, tu número es 0,44 aunque p* sea 60% — porque
0,44 es lo que honestamente creés, y un número honesto es lo único que
después se puede medir.

Achicar todo hacia 0,49 por miedo a la comisión destruye tu resolución, que
ya es casi cero, y hace que el experimento no mida nada. Comprometete.

════ EL RSI FUNCIONA EN DOS NIVELES — no los confundas ════
Al separar el efecto, casi todo vive en el nivel del mercado, no del par:

  serie de tiempo: corr(RSI medio del ciclo, retorno del mercado) = −0,538
                   t=−3,55 pero n=33 CICLOS. Acierta 63,6% la dirección
                   del conjunto. Es lo más fuerte que hay y lo más frágil.
  transversal:     corr(RSI relativo, retorno relativo) = −0,244, t=−2,19.
                   "el más sobrevendido del ciclo le gana al mercado": 52,5%.

Usalos así:
1. Promedio del RSI de los ocho → inclinación del conjunto. Si casi todos
   están sobre 60, el mercado tiende a ceder. Inclinate, no te tires.
2. RSI relativo (este par contra la media de los ocho) → a cuál pegarle más.
   Sirve para ordenar, no para convicción.

No repitas el mismo lado en los ocho creyendo que diversificás: ahí estás
tomando la apuesta de serie de tiempo, la que tiene 33 observaciones atrás.

AVISOS DE FRAGILIDAD, medidos:
- la regla se degradó DENTRO de la misma noche: 73,3% la primera mitad,
  57,1% la segunda.
- no funciona igual en todos: UNI 80%, TAO 79%, AVAX 64%, SUI 57%,
  NEAR 43%, PEPE 31%. En PEPE la regla acierta menos de un tercio —
  ahí no la fuerces.

════ MIRÁ LOS OCHO JUNTOS ANTES DE MANDAR ════
Se mueven en bloque: en el 38% de los ciclos, siete u ocho fueron para el
mismo lado (si fueran independientes, 7%). La correlación intra-ciclo es
ρ=0,303, así que tus 237 observaciones valen como 77.
Si vas a decir BAJA en siete de ocho, eso no es diversificación: es una
apuesta grande a que cae el mercado. Sin lectura del conjunto, repartí.
Si un par se resiste mientras los otros caen, está mostrando fuerza propia —
no está atrasado. Medido: el que aguantó, rebotó.

════ LO QUE NO FUNCIONA (medido, no opinión) ════
    libro_desequilibrio  −0,6 pp, z=−0,42      cero, solo y acompañado
    contagio con BTC     5 aciertos de 13      leíste el rezago al revés
    seguir la vela 5m    47,6%                 peor que una moneda
    cambio_1h solo      −22,7 pp               al revés
    ema9_vs_ema21       −18,3 pp               al revés

════ TU DIAGNÓSTICO ════
Brier 0,2511 = fiabilidad 0,0034 − resolución 0,0011 + incertidumbre 0,2499.
Tu calibración está BIEN. Tu resolución es casi CERO: no separás los casos.
No sos sobreconfiado, sos ciego. La cura no es moderar números, es apoyarte
en la única variable que discriminó — el RSI de 5m.

Y la honestidad que corresponde: ninguna de estas reglas pasa una corrección
por pruebas múltiples. La mejor da z=+1,82 contra 50% y z=+0,65 contra el
p* de 60,2%. Usalas porque son lo mejor que hay, y no creas que funcionan.

La razón tiene que decir QUÉ esperás y de qué tamaño, no qué indicador
miraste. "EMA9 sobre EMA21" no es una razón; "RSI 5m en 71 con 1h subiendo,
celda 0,35, m=0,6% así que p*=58%, voy 0,44" sí."""

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
    if k := os.getenv("MISTRAL_API_KEY", "").strip():
        return await _por_mistral(fotos, k, pedido or "ministral-14b-latest",
                                  base, texto)

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
        raise ErrorIA(f"HTTP {r.status_code}: {r.text[:140]}")

    try:
        texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(texto)["predicciones"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ErrorIA("respuesta ilegible") from exc
    return _validar(items, fotos, base, modelo)


DEEPSEEK = "https://api.deepseek.com/anthropic"
MISTRAL = "https://api.mistral.ai/v1/chat/completions"


async def _por_mistral(fotos, llave, modelo, base, cuerpo) -> list[dict]:
    """
    Mistral, que habla el formato de OpenAI.

    Es el unico proveedor con tier gratuito de los que quedaron en pie, y
    ya venia funcionando en el otro agente. `response_format` en
    `json_object` obliga a que la respuesta sea JSON valido, que es lo
    mas cerca del `responseSchema` de Gemini que hay aca.
    """
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(MISTRAL,
            headers={"Authorization": f"Bearer {llave}"},
            json={"model": modelo, "max_tokens": 8000, "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": PROMPT},
                               {"role": "user", "content": cuerpo}]})
    if r.status_code != 200:
        raise ErrorIA(f"mistral HTTP {r.status_code}: {r.text[:140]}")
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
            "confianza": it.get("confianza", "media"),
            "fundamento": fund,
            "dato": str(it.get("dato", ""))[:120],
            "mov_esperado": round(esp, 3),
            "modelo": modelo,
        })
    if not validas:
        raise ErrorIA("ninguna predicción utilizable")
    return validas
