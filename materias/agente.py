"""
El agente que lee la guia y decide. `python -m materias.agente`

QUE ES Y QUE NO ES
──────────────────
Recibe las 56 paginas de la guia enteras, el estado del mercado ahora
mismo, lo que el mismo ha aprendido de sus operaciones anteriores, y
decide. No ejecuta una regla que yo escribi: razona sobre el documento
delante del mercado.

La diferencia con el motor determinista de `setups.py` es real y las dos
cosas conviven a proposito:

    setups.py    mi lectura del documento, congelada en codigo. Se puede
                 hacer backtest sobre diez anos. No entiende nada.
    agente.py    el documento entero en contexto. Razona sobre matices
                 que no caben en un `if`. No se puede backtestear hacia
                 atras, porque no existia.

El capitulo 18.2 avisa de lo que se pierde: «solo se puede automatizar lo
que se puede definir sin ambiguedad». Un agente que razona no es
reproducible: la misma vela puede darle dos lecturas. Eso significa que
no se puede validar como se valida una regla, y que la unica forma de
saber si sirve es medirlo EN VIVO contra el motor determinista, sobre las
mismas velas, y comparar. Eso es lo que hace `comparar()`.

POR QUE EL DOCUMENTO ENTERO Y NO UN BUSCADOR
Porque los capitulos se apoyan entre si. Decidir si una barrida del
minimo de Asia es trampa o ruptura usa el 6.2, el 5.3, el 10, el 15.5 y
el 2.3 a la vez. Un buscador que devuelva «los tres trozos mas
parecidos» le quita justo las conexiones que SON el analisis.

QUE VE EL AGENTE Y QUE NO
No ve velas: un modelo de lenguaje no lee un grafico. Ve lo que los
modulos deterministas CALCULAN sobre las velas -- estructura, FVG,
order blocks, liquidez sin barrer, sesion, indicadores, fase de Wyckoff.
Esos modulos son sus ojos; la guia es su criterio. Sin los ojos no
podria mirar; sin la guia no sabria que esta mirando.

LO QUE NO PUEDE DECIDIR
El tamano de la posicion y los limites del dia. El capitulo 14 los fija
en numeros -- 0,5 % por operacion, parar tras 2 perdidas, 3 % de riesgo
total contando correlacion -- y no son criterio, son aritmetica. Un
agente que pudiera subirse el riesgo tras una racha estaria haciendo
justo lo que el capitulo 19 llama exceso de confianza. El agente decide
SI y COMO; el codigo decide CUANTO.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from materias import estructura as E
from materias import fases as F
from materias import guia as GUIA
from materias import indicadores as I
from materias import sesiones as S

log = logging.getLogger("materias.agente")

MODELO = os.getenv("MATERIAS_MODELO", "claude-opus-5")
ESFUERZO = os.getenv("MATERIAS_ESFUERZO", "high")
"""
`high` y no `low`.

Decidir si una barrida es trampa o ruptura con nueve modelos en juego y
56 paginas de criterio no es una clasificacion: es el tipo de problema
donde el esfuerzo se nota. Bajarlo ahorra centimos en la operacion que
decide si se arriesga medio punto de la cuenta.
"""


@dataclass
class Decision:
    """Lo que el agente decidio, y por que."""
    opera: bool = False
    lado: str = ""
    entrada: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    modelo_guia: str = ""
    """Que estrategia de la guia esta aplicando, con su numero de capitulo."""
    confianza: str = "baja"
    razonamiento: str = ""
    citas: list[str] = field(default_factory=list)
    """Los capitulos concretos en que se apoya. Sin esto no es auditable."""
    riesgos: str = ""
    """Que haria que esta lectura fuera equivocada. La guia lo pide en 20.3."""
    descartado: str = ""
    """Si no opera, por que. Vale tanto como la operacion (cap. 19.1)."""
    rr: float = 0.0
    uso: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return self.__dict__.copy()


ESQUEMA = {
    "type": "object",
    "properties": {
        "opera": {"type": "boolean",
                  "description": "true solo si el checklist del cap. 11.9 pasa entero"},
        "lado": {"type": "string", "enum": ["compra", "venta", ""]},
        "entrada": {"type": "number", "description": "0 si no opera"},
        "sl": {"type": "number"},
        "tp": {"type": "number"},
        "modelo_guia": {
            "type": "string",
            "description": "Estrategia aplicada con su capitulo, p.ej. "
                           "'11.4 Judas swing' o '20.2 Barrida de sesion'"},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "razonamiento": {
            "type": "string",
            "description": "Como se llego a la decision, en pasos. "
                           "Menciona lo que NO cuadra, no solo lo que cuadra"},
        "citas": {
            "type": "array", "items": {"type": "string"},
            "description": "Capitulos concretos en que se apoya, p.ej. ['6.2','11.4']"},
        "riesgos": {
            "type": "string",
            "description": "Que tendria que pasar para que esta lectura "
                           "resultara equivocada"},
        "descartado": {
            "type": "string",
            "description": "Si opera=false, el motivo concreto"},
    },
    "required": ["opera", "lado", "entrada", "sl", "tp", "modelo_guia",
                 "confianza", "razonamiento", "citas", "riesgos", "descartado"],
    "additionalProperties": False,
}


SISTEMA = """Eres un operador de materias primas y metales. Tu unico
criterio es la guia que viene a continuacion: 56 paginas que definen como
se lee el mercado, cuando se entra, cuanto se arriesga y como se mide.

La guia manda sobre cualquier cosa que creas saber de otro sitio. Si algo
que recuerdas la contradice, gana la guia. Si la guia no cubre una
situacion, no la operes: el capitulo 11.9 termina en «si alguna es "no",
no hay operacion», y eso incluye «no se».

COMO DECIDES
Sigue el checklist universal del capitulo 11.9, en orden, y para en el
primer "no". Los ocho modelos del capitulo 11 y la estrategia armada del
20.2 son tus plantillas; di cual estas aplicando y por que esa y no otra.

NO OPERAR ES LA RESPUESTA CORRECTA CASI SIEMPRE
El capitulo 20.4 lista sobreoperar entre los errores del principiante, y
el 5.2 avisa de que el rango «es la fase donde mas cuentas se pierden».
Un dia sin setup es un dia bien trabajado. No fuerces una lectura para
tener algo que hacer: si no encaja limpio en un modelo de la guia,
`opera` va en false y explicas que faltaba.

LO QUE TIENES QUE DECIR SIEMPRE
Cita los capitulos en que te apoyas y di que haria que tu lectura fuera
equivocada. Si no puedes nombrar el capitulo, es que no lo sacaste de la
guia.

LO QUE NO DECIDES
El tamano de la posicion y los limites del dia. El capitulo 14 los fija
en numeros y los aplica el sistema. Tu decides SI y COMO; el codigo
decide CUANTO. Tampoco decides el horario: si te llega un estado fuera de
killzone, ya sabes la respuesta.

SOBRE LOS PRECIOS
Los niveles que des tienen que ser precios reales del instrumento, no
aproximaciones. La entrada tiene que quedar al otro lado del precio
actual -- es una orden limite en un retroceso, no una entrada a mercado
(cap. 20.2, 12.1). El objetivo tiene que estar en liquidez identificable
y a 2R o mas del stop, o no hay operacion (cap. 11.9, punto 6)."""


def _fmt(x, d=4):
    return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, (int, float)) else str(x))


def retrato(simbolo: str, ctx: dict) -> str:
    """
    Lo que el agente puede ver del mercado. Sus ojos.

    Un modelo de lenguaje no lee un grafico. Esto le da, en texto, lo que
    los modulos deterministas calcularon sobre las velas: la estructura,
    los huecos, los bloques, la liquidez que sigue sin barrer, la sesion,
    los indicadores y la fase.

    Se le dan HECHOS, no conclusiones. No dice «hay una barrida alcista»
    sino «el minimo de Asia esta en 4.312,40 y el precio hizo 4.309,80 y
    cerro en 4.318,20». Interpretar eso es su trabajo, y si le damos la
    interpretacion hecha estamos otra vez ejecutando mi lectura del
    documento en vez de la suya.
    """
    ahora: datetime = ctx["ahora"]
    est = S.estado(ahora)
    v5 = E.velas(ctx["m5"])
    v15 = E.velas(ctx.get("m15", []))
    v4 = E.velas(ctx.get("h4", []))
    vd = E.velas(ctx.get("diario", []))
    a5 = E.atr(v5)

    L = [f"# {simbolo} — {ahora:%Y-%m-%d %H:%M} hora de Nueva York", ""]

    L.append("## Sesion")
    L.append(f"- sesiones activas: {', '.join(est['sesiones']) or 'ninguna'}")
    L.append(f"- killzones activas: {', '.join(est['killzones']) or 'ninguna'}")
    L.append(f"- ventana Silver Bullet: {', '.join(est['silver_bullet']) or 'no'}")
    L.append(f"- ¿el sistema permite operar ahora?: "
             f"{'si, ' + est['ventana_operable'] if est['operable'] else 'no'}")
    if not est["operable"]:
        L.append(f"- proxima ventana: {S.proxima_operable(ahora)}")

    L.append("")
    L.append("## Precio")
    precio = v5[-1].c if v5 else 0
    L.append(f"- ultimo cierre de 5M: {_fmt(precio)}")
    L.append(f"- ATR(14) de 5M: {_fmt(a5)}   de 15M: {_fmt(E.atr(v15))}   "
             f"diario: {_fmt(E.atr(vd))}")
    if len(v5) >= 12:
        L.append(f"- ultima hora: maximo {_fmt(max(v.h for v in v5[-12:]))}, "
                 f"minimo {_fmt(min(v.l for v in v5[-12:]))}")
    L.append("- ultimas 6 velas de 5M (apertura, maximo, minimo, cierre):")
    for v in v5[-6:]:
        L.append(f"    {ctx['dt5'].get(v.i, ''):%H:%M}  {_fmt(v.o)} {_fmt(v.h)} "
                 f"{_fmt(v.l)} {_fmt(v.c)}   cuerpo {_fmt(v.cuerpo)} "
                 f"({v.cuerpo / a5:.1f}x ATR)" if a5 else "")

    L.append("")
    L.append("## Estructura")
    for nombre, vs in (("diario", vd), ("4H", v4), ("15M", v15), ("5M", v5)):
        if len(vs) < 20:
            continue
        e = E.leer_estructura(vs)
        L.append(f"- {nombre}: {e.tendencia}"
                 + (f", ultimo evento {e.ultimo_evento}" if e.ultimo_evento else "")
                 + (f", ultimo maximo {_fmt(e.ultimo_alto)}" if e.ultimo_alto else "")
                 + (f", ultimo minimo {_fmt(e.ultimo_bajo)}" if e.ultimo_bajo else ""))
    if v4:
        e4 = E.leer_estructura(v4)
        if e4.ultimo_alto and e4.ultimo_bajo:
            pd = E.premium_discount(e4.ultimo_alto, e4.ultimo_bajo, precio)
            L.append(f"- posicion en el rango de 4H: {pd['zona']} "
                     f"({pd['pct']:.0%} del rango; 50% es el equilibrio)")

    L.append("")
    L.append("## Liquidez (niveles, sin interpretar)")
    a, lon, n = ctx.get("asia", {}), ctx.get("londres", {}), ctx.get("niveles", {})
    if a.get("alto"):
        L.append(f"- rango de Asia: {_fmt(a['bajo'])} a {_fmt(a['alto'])} "
                 f"(ancho {_fmt(a['alto'] - a['bajo'])})")
    if lon.get("alto"):
        L.append(f"- rango de Londres hoy: {_fmt(lon['bajo'])} a {_fmt(lon['alto'])}")
    for k, etq in (("pdh", "maximo de ayer"), ("pdl", "minimo de ayer"),
                   ("pwh", "maximo de la semana pasada"),
                   ("pwl", "minimo de la semana pasada")):
        if n.get(k):
            L.append(f"- {etq} ({k.upper()}): {_fmt(n[k])}")
    if ctx.get("tdo"):
        L.append(f"- apertura de medianoche (True Day Open): {_fmt(ctx['tdo'])}")
    if ctx.get("eqh"):
        L.append(f"- maximos iguales (EQH): {', '.join(_fmt(x) for x in ctx['eqh'][:4])}")
    if ctx.get("eql"):
        L.append(f"- minimos iguales (EQL): {', '.join(_fmt(x) for x in ctx['eql'][:4])}")
    g = ctx.get("nwog") or {}
    if g:
        L.append(f"- gap de apertura semanal: {_fmt(g['piso'])} a {_fmt(g['tope'])}, "
                 f"50% en {_fmt(g['ce'])}")

    L.append("")
    L.append("## Desequilibrios y zonas")
    huecos = [f for f in E.fvgs(v5[-60:], a5 * 0.2) if not f.invalidado]
    if huecos:
        for f in huecos[-5:]:
            L.append(f"- FVG {f.tipo} de 5M: {_fmt(f.piso)} a {_fmt(f.tope)}, "
                     f"50% en {_fmt(f.ce)}"
                     + ("  (ya mitigado)" if f.mitigado else ""))
    else:
        L.append("- sin FVG de 5M vigentes")
    if v4:
        a4 = E.atr(v4)
        obs = [o for o in E.order_blocks(v4, a4) if not o.mitigado]
        for o in obs[-3:]:
            L.append(f"- Order Block {o.tipo} de 4H sin mitigar: {_fmt(o.piso)} a "
                     f"{_fmt(o.tope)}, mean threshold {_fmt(o.mean_threshold)}")

    L.append("")
    L.append("## Fase y volumen")
    h1 = ctx.get("h1", [])
    if len(h1) >= 40:
        fase = F.leer(E.velas(h1), [v.get("v", 0) or 0 for v in h1])
        L.append(f"- fase de mercado (1H): {fase.nombre} — {fase.detalle}")
        for ev in fase.eventos:
            L.append(f"  · {ev}")
    amd = F.power_of_3(ctx["m5"], a, ahora)
    L.append(f"- Power of 3 / AMD: {amd.fase} — {amd.detalle}")
    if v5:
        L.append(f"- volumen de la ultima vela contra su media de 20: "
                 f"{E.volumen_relativo(v5):.2f}x")

    L.append("")
    L.append("## Indicadores")
    lect = I.leer(ctx)
    if lect.ema200_1h:
        L.append(f"- EMA 200 de 1H: {_fmt(lect.ema200_1h)} "
                 f"(precio {'por encima' if lect.precio_sobre_ema200 else 'por debajo'})")
    L.append(f"- RSI(14) de 15M: {lect.rsi14_15m}")
    if lect.adx:
        L.append(f"- ADX: {lect.adx['adx']} → {lect.adx['regimen']}, "
                 f"+DI {lect.adx['pdi']} / -DI {lect.adx['ndi']}")
    if lect.bollinger:
        L.append(f"- Bollinger de 15M: ancho en el percentil "
                 f"{lect.bollinger['percentil_ancho']:.0%} de su historia"
                 + ("  (squeeze)" if lect.bollinger.get("squeeze") else ""))
    if lect.vwap:
        L.append(f"- VWAP de la sesion: {_fmt(lect.vwap['vwap'])} "
                 f"(precio {'encima' if lect.vwap['precio_sobre'] else 'debajo'})")
    if lect.divergencia:
        L.append(f"- divergencia precio/RSI: {lect.divergencia}")
    if lect.pivotes:
        p = lect.pivotes
        L.append(f"- pivotes: S2 {_fmt(p['s2'])}  S1 {_fmt(p['s1'])}  "
                 f"P {_fmt(p['p'])}  R1 {_fmt(p['r1'])}  R2 {_fmt(p['r2'])}")

    L.append("")
    L.append("## Coste de operar")
    L.append(f"- spread: {_fmt(ctx.get('spread', 0))}")
    L.append(f"- coste total de una operacion (spread + comision): "
             f"{_fmt(ctx.get('costo', 0))}")
    L.append(f"- colchon minimo para el stop: {_fmt(ctx.get('margen_sl', 0))}")
    L.append(f"- tamano de contrato: {ctx.get('contrato', 1)}")
    return "\n".join(L)


class ErrorIA(Exception):
    """Un proveedor no pudo responder. El siguiente lo intenta."""


PROVEEDORES = [p.strip() for p in os.getenv(
    "MATERIAS_PROVEEDORES", "anthropic,gemini,mistral").split(",") if p.strip()]
"""
El orden en que se intenta, y por que hay mas de uno.

Claude primero: esto no es una clasificacion sino leer 56 paginas y
decidir con ellas delante, y ahi el modelo importa.

Pero un agente que se cae cuando su proveedor devuelve 503 no es
autonomo. Ya paso en el experimento de cripto -- «gemini-3.5-flash estuvo
caido 6 de 6» -- y la respuesta fue una cadena. Misma idea aqui.

EL LIMITE QUE DECIDE QUIEN ENTRA
La guia son ~39.000 tokens y NO se recorta: el capitulo 11.9 exige el
checklist entero y los modelos del 11 se apoyan unos en otros. Un
proveedor que no la sostenga no sirve, y darle media guia seria peor que
no llamarlo: decidiria con un criterio incompleto sin que se notara en la
respuesta.
"""


def _plano(sistema: list) -> str:
    """El sistema como un texto, para proveedores sin bloques ni cache."""
    return "\n\n".join(b["text"] for b in sistema)


def _anthropic(sistema: list, mensaje: str) -> tuple[dict, dict]:
    """Claude. El unico que recibe la guia con cache de prompt."""
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ErrorIA("sin ANTHROPIC_API_KEY")
    try:
        msg = anthropic.Anthropic().messages.create(
            model=MODELO,
            max_tokens=16000,
            system=sistema,
            thinking={"type": "adaptive"},
            output_config={"effort": ESFUERZO,
                           "format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{"role": "user", "content": mensaje}],
        )
    except anthropic.APIStatusError as exc:
        raise ErrorIA(f"anthropic HTTP {exc.status_code}: "
                      f"{str(exc.message)[:160]}") from exc
    except anthropic.APIConnectionError as exc:
        raise ErrorIA(f"anthropic red: {exc}") from exc
    if msg.stop_reason == "refusal":
        raise ErrorIA(f"anthropic declino: {msg.stop_details}")
    crudo = next((b.text for b in msg.content if b.type == "text"), "")
    if not crudo:
        raise ErrorIA("anthropic respondio sin texto")
    return json.loads(crudo), {
        "proveedor": "anthropic", "modelo": MODELO,
        "entrada": msg.usage.input_tokens,
        "cache_lectura": msg.usage.cache_read_input_tokens or 0,
        "cache_escritura": msg.usage.cache_creation_input_tokens or 0,
        "salida": msg.usage.output_tokens,
    }


def _gemini(sistema: list, mensaje: str) -> tuple[dict, dict]:
    llave = os.getenv("GEMINI_API_KEY")
    if not llave:
        raise ErrorIA("sin GEMINI_API_KEY")
    import httpx
    modelo = os.getenv("MATERIAS_MODELO_GEMINI", "gemini-3.5-flash")
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent",
        headers={"x-goog-api-key": llave},
        json={"systemInstruction": {"parts": [{"text": _plano(sistema)}]},
              "contents": [{"role": "user", "parts": [{"text": mensaje}]}],
              "generationConfig": {"responseMimeType": "application/json",
                                   "maxOutputTokens": 8000}},
        timeout=180)
    # 429 es cuota agotada y 503 es saturacion. Los dos pasan al
    # siguiente, pero solo el segundo valdria la pena reintentar aqui --
    # reintentar un 429 gasta cuota que ya no hay, que es como se acelero
    # el agotamiento en el experimento de cripto.
    if r.status_code != 200:
        raise ErrorIA(f"gemini HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    try:
        txt = d["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ErrorIA(f"gemini respondio raro: {str(d)[:160]}") from exc
    u = d.get("usageMetadata", {})
    return json.loads(txt), {
        "proveedor": "gemini", "modelo": modelo,
        "entrada": u.get("promptTokenCount", 0), "cache_lectura": 0,
        "cache_escritura": 0, "salida": u.get("candidatesTokenCount", 0)}


def _mistral(sistema: list, mensaje: str) -> tuple[dict, dict]:
    llave = os.getenv("MISTRAL_API_KEY")
    if not llave:
        raise ErrorIA("sin MISTRAL_API_KEY")
    import httpx
    modelo = os.getenv("MATERIAS_MODELO_MISTRAL", "mistral-large-latest")
    r = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {llave}"},
        json={"model": modelo, "max_tokens": 8000,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": _plano(sistema)},
                           {"role": "user", "content": mensaje}]},
        timeout=240)
    if r.status_code != 200:
        raise ErrorIA(f"mistral HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    txt = d["choices"][0]["message"]["content"]
    u = d.get("usage", {})
    return json.loads(txt), {
        "proveedor": "mistral", "modelo": modelo,
        "entrada": u.get("prompt_tokens", 0), "cache_lectura": 0,
        "cache_escritura": 0, "salida": u.get("completion_tokens", 0)}


LLAMAR = {"anthropic": _anthropic, "gemini": _gemini, "mistral": _mistral}


def _preguntar(sistema: list, mensaje: str) -> tuple[dict, dict]:
    """
    Recorre la cadena hasta que uno responda.

    Se pasa al siguiente ante cualquier fallo del proveedor, pero NO ante
    un JSON ilegible: eso no es que este caido, es que entendio mal, y el
    siguiente entenderia igual de mal. Distinguirlos evita gastar tres
    llamadas en el mismo malentendido.
    """
    ultimo: Exception | None = None
    for prov in PROVEEDORES:
        fn = LLAMAR.get(prov)
        if fn is None:
            continue
        try:
            return fn(sistema, mensaje)
        except ErrorIA as exc:
            log.warning("%s no pudo: %s", prov, exc)
            ultimo = exc
        except json.JSONDecodeError as exc:
            raise ErrorIA(f"{prov} devolvio algo que no es JSON") from exc
    raise ultimo or ErrorIA(
        "ningun proveedor disponible. Pone ANTHROPIC_API_KEY, "
        "GEMINI_API_KEY o MISTRAL_API_KEY")


def decidir(simbolo: str, ctx: dict, notas: str = "",
            historial: str = "") -> Decision:
    """
    Una decision. La guia entera + el mercado + lo que ha aprendido.

    La guia va en el `system` con `cache_control` porque no cambia nunca:
    son 39.000 tokens identicos en cada llamada, que es justo el caso que
    el cache de prompt abarata. Sin el, cada decision costaria lo mismo
    que procesar el documento de cero.

    El orden importa: lo estable primero (guia, notas), lo volatil al
    final (el mercado). El cache es un prefijo, y cualquier byte que
    cambie antes del corte invalida todo lo que viene despues.
    """
    sistema = [
        {"type": "text", "text": SISTEMA},
        {"type": "text",
         "text": "# LA GUIA (integra, 56 paginas)\n\n" + GUIA.texto(),
         "cache_control": {"type": "ephemeral"}},
    ]
    if notas:
        sistema.append({
            "type": "text",
            "text": ("# LO QUE HAS APRENDIDO OPERANDO\n\n"
                     "Sale de tus propias operaciones cerradas, medidas. No "
                     "contradice a la guia: la afina para este activo y este "
                     "horario, que es lo que el capitulo 20.1 llama "
                     "especializarse.\n\n" + notas)})

    partes = [retrato(simbolo, ctx)]
    if historial:
        partes.append("\n# TUS ULTIMAS DECISIONES\n\n" + historial)
    partes.append(
        "\nDecide. Sigue el checklist del capitulo 11.9 en orden y para en "
        "el primer «no». Si no hay un setup limpio de la guia, `opera` va "
        "en false y explicas que faltaba.")

    try:
        d, uso = _preguntar(sistema, "\n".join(partes))
    except ErrorIA as exc:
        log.warning("el agente no pudo decidir: %s", exc)
        return Decision(descartado=f"sin decision: {exc}")

    dec = Decision(
        opera=bool(d.get("opera")), lado=d.get("lado", ""),
        entrada=float(d.get("entrada") or 0), sl=float(d.get("sl") or 0),
        tp=float(d.get("tp") or 0), modelo_guia=d.get("modelo_guia", ""),
        confianza=d.get("confianza", "baja"),
        razonamiento=d.get("razonamiento", ""),
        citas=list(d.get("citas") or []), riesgos=d.get("riesgos", ""),
        descartado=d.get("descartado", ""),
        uso=uso,
    )
    if dec.opera and abs(dec.entrada - dec.sl) > 0:
        dec.rr = round(abs(dec.tp - dec.entrada) / abs(dec.entrada - dec.sl), 2)
    return dec


def revisar(dec: Decision, ctx: dict) -> str:
    """
    Lo que el codigo comprueba de la decision del agente. Devuelve el veto.

    El agente decide SI y COMO. Estas son las cosas que no son criterio
    sino aritmetica, y donde una alucinacion cuesta dinero: que los
    niveles esten del lado correcto del precio, que el objetivo llegue a
    los 2R que exige el capitulo 11.9, que el stop no este dentro del
    ruido.

    No es desconfianza del modelo: es que el capitulo 14 da estos numeros
    como numeros, y comprobarlos es gratis.
    """
    if not dec.opera:
        return ""
    precio = ctx.get("precio") or 0
    if dec.entrada <= 0 or dec.sl <= 0 or dec.tp <= 0:
        return "niveles incompletos"
    if dec.lado not in ("compra", "venta"):
        return f"lado ilegible: {dec.lado!r}"

    if dec.lado == "compra":
        if dec.sl >= dec.entrada:
            return f"compra con el stop ({dec.sl}) por encima de la entrada ({dec.entrada})"
        if dec.tp <= dec.entrada:
            return f"compra con el objetivo ({dec.tp}) por debajo de la entrada"
        if precio and dec.entrada > precio:
            return (f"la entrada ({dec.entrada}) esta por encima del precio "
                    f"({precio}): una Buy Limit va en un retroceso (cap. 12.1)")
    else:
        if dec.sl <= dec.entrada:
            return f"venta con el stop ({dec.sl}) por debajo de la entrada"
        if dec.tp >= dec.entrada:
            return f"venta con el objetivo ({dec.tp}) por encima de la entrada"
        if precio and dec.entrada < precio:
            return (f"la entrada ({dec.entrada}) esta por debajo del precio "
                    f"({precio}): una Sell Limit va en un retroceso (cap. 12.1)")

    if dec.rr < 2.0:
        return (f"objetivo a {dec.rr}R: el capitulo 11.9 pide 2R minimo")

    dist = abs(dec.entrada - dec.sl)
    a15 = E.atr(E.velas(ctx.get("m15", [])))
    if a15 > 0:
        if dist > 1.5 * a15:
            return (f"stop de {dist:.4f} sobre {1.5 * a15:.4f} "
                    f"(1,5x ATR de 15M, cap. 20.2)")
        if dist < 0.25 * a15:
            return (f"stop de {dist:.4f} dentro del ruido "
                    f"(menos de 0,25x ATR de 15M)")
    # Que no se invente un precio de otro planeta.
    if precio and abs(dec.entrada - precio) > 20 * (a15 or 1):
        return f"la entrada ({dec.entrada}) esta lejisimos del precio ({precio})"
    return ""


def texto(dec: Decision) -> str:
    """La decision como se lee en el log."""
    if not dec.opera:
        return (f"NO OPERA · {dec.descartado}"
                + (f"  [{', '.join(dec.citas)}]" if dec.citas else ""))
    return (f"{dec.lado.upper()} {dec.modelo_guia} · entrada {dec.entrada} "
            f"SL {dec.sl} TP {dec.tp} ({dec.rr}R) · confianza {dec.confianza} "
            f"· caps. {', '.join(dec.citas)}")
