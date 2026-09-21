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
mercado AHORA y tenés que decir la probabilidad de que el precio esté MÁS ALTO
dentro de 15 minutos.

LO QUE CUESTA EQUIVOCARSE — leelo antes de responder
Cada operación paga 0,10% de comisión entre entrar y salir. El movimiento
típico de estos pares en 15 minutos es 0,08%.

Es decir: **la comisión es MÁS GRANDE que el movimiento normal**. Acertar la
dirección no alcanza para ganar plata. Si acertás el 60% de las veces, igual
perdés, porque lo que ganás cuando acertás no paga el peaje de todas.

Solo vale la pena alejarse de 0.50 si esperás un movimiento de al menos 0,25%
en 15 minutos — el triple de lo normal. Eso pasa poco: en rupturas con volumen
real, en liquidaciones en cadena, en noticias. No en "el RSI está sobrecomprado".

DATOS QUE RECIBÍS
- velas_5m: últimas 12 velas de 5 min como [máximo%, mínimo%, cierre%] contra
  la apertura de cada vela
- cambio_5m/15m/1h/4h/24h: variación porcentual en esos plazos
- rsi_5m, rsi_15m: RSI (sobre 70 sobrecomprado, bajo 30 sobrevendido)
- ema9_vs_ema21 y precio_vs_ema50: distancia a las medias, en %
- volatilidad_pct: rango medio de las últimas 20 velas
- volumen_vs_medio: volumen de la última vela contra su promedio
- libro_desequilibrio: -1 a 1, positivo = más dinero comprando
- pos_en_rango_24h: 0 = mínimo del día, 1 = máximo del día

REGLAS
1. 0.50 es la respuesta correcta casi siempre. No es rendirse: es reconocer
   que el movimiento esperado no paga la comisión.
2. Los indicadores clásicos —EMA cruzada, RSI sobrecomprado, tendencia en
   varios plazos— ya están en el precio. Que el RSI esté alto NO es razón
   suficiente: todos lo ven.
3. Alejate de 0.50 solo si podés nombrar algo que justifique un movimiento
   TRES VECES más grande de lo normal en los próximos 15 minutos.
4. Nadie te premia por opinar. Se mide la plata, y quedarse quieto no cuesta
   nada mientras que equivocarse sí.
5. Si te alejás de 0.50, tu razón tiene que decir QUÉ esperás que pase y de
   qué tamaño — no qué indicador miraste.

Devolvé una entrada por par."""

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
                    "razon": {"type": "string", "description": "Máximo 90 caracteres"},
                    "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                },
                "required": ["simbolo", "p_sube", "razon", "confianza"],
            },
        }
    },
    "required": ["predicciones"],
}


NOTAS = pathlib.Path(__file__).parent.parent / "vault" / "cripto"


def _notas() -> str:
    """
    Lo que se sabe del mercado, no lo que el modelo recuerde.

    Son hallazgos con evidencia, no consejos de curso: flujo de ordenes,
    vuelta de vela, contagio entre monedas, financiacion extrema, y por
    que fracasan las estrategias. Van en cada llamada porque el modelo no
    tiene memoria entre una y otra.

    Si la carpeta no esta, devuelve vacio y el resto sigue igual.
    """
    try:
        trozos = []
        for f in sorted(NOTAS.glob("*.md")):
            trozos.append(f"### {f.stem}" + chr(10) + f.read_text(encoding="utf-8").strip())
        if not trozos:
            return ""
        return ("LO QUE SE SABE DE ESTE MERCADO" + chr(10) + chr(10)
                + (chr(10) + chr(10)).join(trozos))
    except OSError:
        return ""


class ErrorIA(Exception):
    pass


def _boletin(h: dict | None) -> str:
    """
    Su propio historial, en la cara.

    Sin esto el modelo no tiene forma de saber que le esta yendo mal: cada
    llamada arranca sin memoria y con la misma confianza de siempre. Con
    esto ve cuanta plata costo su ultima tanda de decisiones.
    """
    if not h or not h.get("n"):
        return ""
    l = [f"TU HISTORIAL EN ESTE EXPERIMENTO ({int(h['n'])} predicciones resueltas):"]
    if h.get("acierto_pct") is not None:
        l.append(f"- acertaste la dirección el {h['acierto_pct']}% de las veces")
    if h.get("pnl_bruto_pct") is not None:
        l.append(f"- antes de comisiones: {h['pnl_bruto_pct']:+.3f}%")
        l.append(f"- DESPUÉS de comisiones: {h['pnl_neto_pct']:+.3f}%")
        if h.get("operadas"):
            l.append(f"- te alejaste de 0.50 en {int(h['operadas'])} de {int(h['n'])}")
    if (h.get("pnl_neto_pct") or 0) < 0:
        l.append("")
        l.append("Estás PERDIENDO plata. Las veces que te alejaste de 0.50 costaron "
                 "más en comisiones de lo que ganaron. Sé mucho más exigente: "
                 "0.50 salvo que veas algo grande de verdad.")
    return chr(10).join(l)


async def predecir(fotos: list[dict], modelo: str = "",
                   historial: dict | None = None) -> list[dict]:
    llave = os.getenv("GEMINI_API_KEY", "").strip()
    if not llave:
        raise ErrorIA("falta GEMINI_API_KEY")
    modelo = modelo or os.getenv("CRIPTO_MODELO", "gemini-2.5-flash")

    cuerpo = {
        "contents": [{"parts": [
            {"text": PROMPT},
            *([{"text": notas}] if (notas := _notas()) else []),
            *([{"text": boletin}] if (boletin := _boletin(historial)) else []),
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
        validas.append({
            "simbolo": s, "p_sube": round(p, 4),
            "razon": str(it.get("razon", ""))[:160],
            "confianza": it.get("confianza", "media"),
            "modelo": modelo,
        })
    if not validas:
        raise ErrorIA("ninguna predicción utilizable")
    return validas
