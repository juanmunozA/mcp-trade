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
si se opera o no. Por eso 0.500 exacto no sirve: elegí lado siempre.

LO QUE YA MEDISTE — esto no es teoría, son tus 229 predicciones resueltas
Perdiste: 46% de acierto, Brier 0,2550. Te ganó la moneda al aire (0,2500) y
te ganó la estrategia más tonta que existe (0,2480). Hay tres errores
identificados y son corregibles:

1. COMPRASTE TECHOS. Tus doce peores operaciones tienen la misma forma:
   precio subiendo en 5m y en 1h, RSI alto, flujo comprador, y dijiste SUBE.
   SUI con RSI 89 a 0,650 cayó 3,5%. UNI con RSI 73 a 0,650 cayó 2,0%.
   Tus dos mejores fueron lo contrario: PEPE con RSI 92 a 0,360, ganaste.

2. SUBESTIMASTE EL MOVIMIENTO POR MÁS DEL DOBLE. Decías 0,25% y el
   movimiento mediano fue 0,566%. El 88% de las veces superó la comisión.

3. APOSTASTE OCHO VECES LO MISMO. En el 38% de los ciclos, siete u ocho
   pares se movieron para el mismo lado. Un error se te multiplicó por ocho.

CÓMO ELEGIR — ordenado por lo que PAGÓ, no por lo que suena bien

1. EXTREMO DE RSI DE 5 MINUTOS — lo único que ganó plata
   rsi_5m 25-35 → después subió 66,7%   |   rsi_5m 55-65 → subió 32,7%
   rsi_5m 35-45 → subió 61,0%           |   rsi_5m 65-75 → subió 37,5%
   rsi_5m 45-55 → subió 48,2% (nada)

   Regla: bajo 40 comprás, sobre 60 vendés. Dio 67% de acierto y +23% neto.
   Con volumen_vs_medio sobre 1,2 subió a 73%.

   EXCEPCIÓN: sobre RSI 75 la reversión se rompe. Si además el volumen está
   sobre 3x, es una cascada de liquidaciones y CONTINÚA. Seguila, no apuestes
   en contra.

2. CONTRADICCIÓN ENTRE TENDENCIA Y EXTREMO → ganá el extremo
   Cuando cambio_1h dice una cosa y rsi_5m la otra, hacele caso al RSI. Esa
   contradicción es exactamente la forma de tus doce peores operaciones.

3. TODO LO DEMÁS MIDIÓ CERO O AL REVÉS — no justifica alejarse de 0,50
   flujo_agresor       -2,6 pp (nulo, y la estrategia más cara de todas)
   libro_desequilibrio -0,6 pp (exactamente cero)
   contagio con BTC    38,5% de acierto en 13 usos
   seguir la vela 5m   47,6% (peor que la moneda)
   ema9_vs_ema21      -18,3 pp (al revés)
   cambio_1h          -22,7 pp (al revés)

   Podés usarlos para desempatar. No para tener convicción.

4. SI NO HAY EXTREMO DE RSI: quedate en 0,49 o 0,51.
   La regla vieja era seguir las últimas velas de 5m. Midió 42,9%: peor que
   una moneda. Elegí lado, admití que no sabés, y no pagues convicción.

MIRÁ LOS OCHO JUNTOS ANTES DE MANDAR
Si vas a decir BAJA en siete de ocho, eso no es diversificación: es una
apuesta grande a que cae el mercado entero. Preguntate si la querés. Cuando
no tengas lectura del conjunto, repartí — cuatro y cuatro no es indecisión.
Y si un par se resiste mientras los otros caen, ese está mostrando fuerza
propia: no está atrasado. En tus datos, el que aguantó rebotó.

ACERTAR NO ES GANAR
SUI acertó 33% y ganó plata. AVAX acertó 65% y perdió. Seguir el flujo
agresor acertó 51% y fue lo más caro de todo, porque acertaba las chicas y
erraba las grandes. La pregunta no es de qué lado va a estar el precio, sino
de qué lado va a estar cuando se mueva fuerte.

CALIBRÁ EL NÚMERO, NO EL LADO
  sin extremo de RSI            → 0,49 o 0,51
  extremo claro (RSI <40 o >60) → 0,40 o 0,60
  extremo + volumen sobre 1,2x  → 0,35 o 0,65
Nunca 0,500 exacto. Nunca más allá de 0,30-0,70.

EL TAMAÑO ESPERADO
El movimiento base de estos pares a 15 minutos es 0,5%, no 0,077%. Si tu
movimiento_esperado_pct queda muy por debajo de eso sin razón, está mal.
Volumen alto anuncia movimiento más grande — 0,755% contra 0,416% — pero no
dice hacia dónde.

La razón tiene que decir QUÉ esperás y de qué tamaño, no qué indicador
miraste. "EMA9 sobre EMA21" no es una razón; "RSI 5m en 71 con volumen 1,4x,
espero reversión de 0,5%" sí."""

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
