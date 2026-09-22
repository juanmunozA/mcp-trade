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

DIRECTRIZ: SIEMPRE SE ENTRA
Este sistema toma posición en todos los pares, en todos los ciclos. No existe
quedarse afuera. Tu número decide de qué LADO y con cuánta convicción, nunca
si se opera o no.

Por eso 0.500 exacto no sirve: obliga a desempatar por defecto. Elegí lado
siempre, aunque sea por poco.

LO QUE ESO CUESTA — tenelo presente
Cada operación paga 0,10% entre entrar y salir, y el movimiento típico en 15
minutos es 0,077%. Con entrada forzada la comisión se paga igual, así que tu
trabajo es doble: elegir el lado menos malo cuando no hay nada, y reconocer
los pocos momentos donde el movimiento sí supera el peaje.

CÓMO ELEGIR (del más fuerte al más débil)

1. FLUJO AGRESOR — el más sólido que tenés
   `flujo_agresor` va de -1 a 1 y mide quién EJECUTÓ en las últimas ~1000
   operaciones: positivo = compradores agrediendo el libro. Es dinero que ya
   se movió, no promesas.
   `flujo_reciente` contra `flujo_previo` te dice si está acelerando: si
   reciente es mucho mayor que previo, la presión está creciendo AHORA.
   Señal fuerte: |flujo_reciente| sobre 0,25 y creciendo contra el previo.

2. CONTAGIO CON BTC
   Te doy la foto de BTC aparte, como referencia. Si BTC se movió más de
   0,3% en 5-15 min y la moneda chica no reaccionó todavía, la chica lo
   sigue. El precio se descubre donde hay liquidez y tarda en propagarse.

3. LIBRO CON VOLUMEN
   `libro_desequilibrio` solo vale si `volumen_vs_medio` lo respalda. Sin
   volumen es una pared que se cancela sola — es la versión débil del punto 1.

4. EXTREMO CON VOLUMEN
   RSI 5m bajo 25 o sobre 75 MÁS volumen alto suele revertir. Sin volumen, no.

5. SIN SEÑAL
   Si ninguno de los anteriores aplica, seguí las últimas dos velas de 5m y
   marcá `fundamento: "sin_senal"`. Decir la verdad vale más que inventar:
   inventar te vuelve sobreconfiado y agranda la apuesta.

CUÁNDO SÍ HAY ALGO GRANDE (ahí vale 0.65 o 0.35)
- volumen sobre 3x el promedio con movimiento de 5m mayor a 0,3% (cascada)
- ruptura del rango de 24h CON volumen
- contagio rezagado: BTC movió más de 0,3% y la chica todavía no

CALIBRÁ EL NÚMERO, NO EL LADO
El lado lo elegís siempre. La convicción no.
- sin señal clara: 0.48 o 0.52 — elegiste lado, admitís que no sabés
- señal razonable: 0.44 o 0.56
- algo grande de los de arriba: 0.35 o 0.65

Nunca 0.500 exacto. Nunca más allá de 0.30-0.70: a 15 minutos esa certeza no
existe y solo agranda la pérdida cuando te equivocás.

La razón tiene que decir QUÉ esperás y de qué tamaño, no qué indicador
miraste. "EMA9 sobre EMA21" no es una razón; "BTC subió 0,4% y ADA no
reaccionó" sí."""

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
                        "enum": ["flujo_agresor", "contagio_btc", "libro_con_volumen",
                                 "extremo_con_volumen", "cascada", "ruptura_rango",
                                 "inercia_corto", "sin_senal"],
                        "description": "Qué variable concreta justifica el lado elegido"},
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
                   historial: dict | None = None,
                   referencia: dict | None = None) -> list[dict]:
    llave = os.getenv("GEMINI_API_KEY", "").strip()
    if not llave:
        raise ErrorIA("falta GEMINI_API_KEY")
    modelo = modelo or os.getenv("CRIPTO_MODELO", "gemini-2.5-flash")

    cuerpo = {
        "contents": [{"parts": [
            {"text": PROMPT},
            *([{"text": notas}] if (notas := _notas()) else []),
            *([{"text": boletin}] if (boletin := _boletin(historial)) else []),
            *([{"text": "BTC COMO REFERENCIA (no se opera, es para el contagio):"
                + chr(10) + json.dumps(referencia, ensure_ascii=False)}]
              if referencia else []),
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
