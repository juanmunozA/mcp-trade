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

import httpx

URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
       "{m}:generateContent")

PROMPT = """Sos un operador cuantitativo. Para cada par te doy el estado del
mercado AHORA y tenés que decir la probabilidad de que el precio esté MÁS ALTO
dentro de 15 minutos.

Los datos que recibís:
- velas_5m: últimas 12 velas de 5 minutos como [máximo%, mínimo%, cierre%]
  medidos contra la apertura de cada vela
- cambio_5m/15m/1h/4h/24h: variación porcentual en esos plazos
- rsi_5m, rsi_15m: RSI (sobre 70 = sobrecomprado, bajo 30 = sobrevendido)
- ema9_vs_ema21: cuánto está la media rápida sobre la lenta, en %
- precio_vs_ema50: distancia a la media de 50 velas de 5 minutos
- volatilidad_pct: rango medio de las últimas 20 velas
- volumen_vs_medio: volumen de la última vela contra el promedio
- libro_desequilibrio: de -1 a 1, positivo = más dinero comprando
- pos_en_rango_24h: 0 = mínimo del día, 1 = máximo del día

REGLAS
1. Quince minutos en cripto es casi ruido. Si no ves algo concreto, tu
   respuesta honesta es 0.50, y decirlo no es fallar: es lo correcto.
2. Solo alejate de 0.50 cuando puedas nombrar QUÉ lo justifica.
3. No inventes narrativa. "Está en tendencia alcista" no es una razón si
   la tendencia ya está en el precio.
4. Un número entre 0.35 y 0.65 es lo esperable. Fuera de ahí necesitás
   una señal muy fuerte.
5. Nadie te penaliza por decir 0.50 en los ocho. Te penaliza equivocarte.

Devolvé una entrada por par, con la probabilidad y en una línea corta qué
miraste."""

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


class ErrorIA(Exception):
    pass


async def predecir(fotos: list[dict], modelo: str = "") -> list[dict]:
    llave = os.getenv("GEMINI_API_KEY", "").strip()
    if not llave:
        raise ErrorIA("falta GEMINI_API_KEY")
    modelo = modelo or os.getenv("CRIPTO_MODELO", "gemini-2.5-flash")

    cuerpo = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"text": "MERCADO AHORA:\n" + json.dumps(fotos, ensure_ascii=False)},
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
