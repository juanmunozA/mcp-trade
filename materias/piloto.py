"""
Piloto de entrada forzada: cada 4 horas toma posicion, si o si.

QUE MIDE Y POR QUE ESTA SEPARADO DE TODO LO DEMAS
─────────────────────────────────────────────────
El sistema normal no opera casi nunca, y con razon: la guia lo repite --
«si alguna es "no", no hay operacion» (cap. 11.9), «sobreoperar» entre
los errores del principiante (cap. 20.4). Eso esta bien para operar y es
terrible para medir, porque mezcla dos preguntas distintas:

    ¿sabe ELEGIR cuando entrar?
    ¿sabe ACERTAR la direccion cuando entra?

Un sistema que opera cinco veces al mes tarda meses en responder la
segunda, y cuando responde no se sabe cual de las dos habilidades midio.

La entrada forzada separa las dos. Cada cuatro horas se entra pase lo que
pase, alcista o bajista, sin permitir «no opero». Lo unico que decide el
agente es LA DIRECCION. Si acierta por encima del azar de forma
sostenida, sabe leer el mercado; si no, todo lo demas da igual.

ESTO CONTRADICE LA GUIA, Y ES A PROPOSITO
El capitulo 20.4 pone sobreoperar entre los errores, y esto sobreopera
por definicion. No es una estrategia: es un experimento de medicion, con
su propio origen en el diario (`piloto`) para que nunca se mezcle con las
operaciones reales ni contamine sus metricas. Es la misma forma del banco
de pruebas de cripto, que corrio con entrada forzada por el mismo motivo.

SEIS AL DIA, Y ESO IMPORTA
Cada 4 horas son 6 decisiones diarias. El tier gratis de Gemini da 20 al
dia por modelo: cabe con holgura, cosa que un ciclo de 5 minutos dentro
de killzone (~126 al dia) no hacia. La cadencia no es solo experimental,
es lo que hace que el piloto pueda correr sin factura.

QUE MIRA PARA DECIDIR
Las velas, como siempre -- y ADEMAS las noticias reales del oro, buscadas
en la web en ese momento. El capitulo 15 es explicito: «las noticias son
el motor de fondo de las materias primas. Aunque operes tecnicamente,
debes saber que noticias salen cada dia». Un sistema que entra cada
cuatro horas pase lo que pase se va a comer un NFP tarde o temprano; que
al menos lo sepa.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from materias import agente as AG
from materias import datos as D
from materias import diario as DI
from materias import estructura as E
from materias import gestion as G
from materias import guia as GUIA
from materias import noticias as N

log = logging.getLogger("materias.piloto")
NY = ZoneInfo("America/New_York")

CADA_HORAS = float(os.getenv("PILOTO_CADA_HORAS", "4"))
HORIZONTE_HORAS = float(os.getenv("PILOTO_HORIZONTE_HORAS", "3.8333"))
"""
Cuanto vive una entrada: 3 horas y 50 minutos, no 4.

Los diez minutos que faltan hasta la siguiente entrada no son un hueco:
son la ventana de analisis. La posicion se cierra PRIMERO y el agente
decide DESPUES, con el resultado de la anterior ya cerrado y las velas
mas recientes ya descargadas.

Si el horizonte fuera de cuatro horas exactas, la entrada nueva caeria en
el mismo instante que el cierre de la vieja y el agente estaria decidiendo
sobre un mercado que todavia tiene su propia posicion encima. Diez
minutos separan las dos cosas.

Tambien asegura que nunca hay dos posiciones vivas a la vez, que es lo
que permite tratar cada tramo como una observacion independiente en vez
de como parte de una serie solapada.
"""

ANALISIS_MIN = round((CADA_HORAS - HORIZONTE_HORAS) * 60)
HORIZONTE_MIN = HORIZONTE_HORAS * 60

TARDE_MAX_MIN = float(os.getenv("PILOTO_TARDE_MAX_MIN", "2"))
"""
Cuanto puede retrasarse un cierre antes de que su precio deje de valer.

El ciclo revisa cada `PILOTO_REVISAR_S`, asi que un cierre siempre llega
un poco tarde: con la reticula de 30 minutos, hasta un minuto. Dos da
margen sin dejar pasar un retraso de verdad.
"""

# Cuanto dura una vela de cada temporalidad, en minutos.
_DURACION = {"5M": 5, "15M": 15, "1H": 60, "4H": 240, "diario": 1440}
_CLAVE_CTX = {"5M": "m5", "15M": "m15", "1H": "h1", "4H": "h4", "diario": "diario"}


def atr_del_horizonte(ctx: dict) -> tuple[str, float]:
    """
    El ATR de la temporalidad que corresponde a cuanto dura la posicion.

    POR QUE NO VALE UNO FIJO
    El stop tiene que sobrevivir el horizonte sin dejar de caber en lo que
    el precio puede recorrer en el. Esas dos cosas dependen del TIEMPO, y
    un yardstick fijo se rompe en cuanto cambia la cadencia.

    Ya se rompio dos veces en vivo, en las dos direcciones:
      - midiendo contra el ATR de 15M para una posicion de 4 horas, el
        agente puso el stop a 2,30 con el ATR en 4,8: lo barre el ruido
      - dandole el recorrido de 4 horas se fue a 55,60 y el 2R ya no cabia

    Con la cadencia de 30 minutos el problema vuelve: exigir 1x el ATR de
    1H -- unos 14 dolares -- para una posicion de 25 minutos es pedir un
    stop tres veces mayor que el recorrido esperado.

    LA REGLA
    Se toma la temporalidad cuya vela dura mas o menos un cuarto del
    horizonte. Con 25 minutos sale 5M; con 3h50m sale 1H, que es
    exactamente lo que estaba puesto a mano. Un cuarto porque la posicion
    tiene que poder respirar varias velas antes de que el stop signifique
    algo: con una sola vela de margen, cualquier mecha lo toca.
    """
    objetivo = max(1.0, HORIZONTE_MIN / 4)
    tf = min(_DURACION, key=lambda k: abs(_DURACION[k] - objetivo))
    velas = ctx.get(_CLAVE_CTX[tf]) or []
    return tf, E.atr(E.velas(velas)) if len(velas) > 15 else 0.0

ORIGEN = "piloto"
RIESGO_PCT = float(os.getenv("PILOTO_RIESGO_PCT", "0.5"))

LOTE_FIJO = float(os.getenv("PILOTO_LOTE", "0") or 0)
"""
Lote fijo, en vez de calcularlo por el capitulo 14.2.

Con 0 se usa la formula: `Lotes = Riesgo / (Distancia SL x Contrato)`, que
es lo que manda la guia y lo que mantiene el riesgo en el 0,5 % pase lo
que pase con el stop.

Con un numero se usa ese, y punto. Sirve para cuentas pequenas donde el
lote minimo del broker es mayor que el que sale de la formula: con 160
USD de balance, un stop de 15 dolares en oro y riesgo del 0,5 %, la
formula pide 0,0005 lotes y el broker no baja de 0,01. Veinte veces mas.

LO QUE ESO IMPLICA, ESCRITO PARA QUE NO SE OLVIDE
0,01 lotes de oro con un stop de 15 dolares arriesgan 15 USD. Sobre 160
de balance es el 9,4 %, no el 0,5 %. Dos perdidas seguidas son el 19 % de
la cuenta. El capitulo 14.1 fija el riesgo por operacion en 0,5-1 % y
14.5 explica por que: «con 40 % de acierto, la probabilidad de 8 perdidas
seguidas en 200 operaciones es alta».

LO QUE NO SE ROMPE
La medicion. El R es independiente del lote -- es (salida - entrada)
dividido por (entrada - stop) -- asi que el acierto de direccion y todas
las metricas en R siguen siendo validas. Lo unico que dependia del lote
era traducir R a porcentaje de cuenta, y eso se arregla calculando el
riesgo REAL de cada entrada en vez de suponer el 0,5 %.
"""

USA_BROKER = os.getenv("PILOTO_BROKER", "").strip() == "1"
"""
Con esto a 1 el piloto analiza y opera EL INSTRUMENTO DEL BROKER.

Sin ella lee `GC=F` de Yahoo -- el futuro de COMEX -- y opera en papel
contra esa misma serie. Sirve para medir la direccion sin depender de
nada, y es lo que corre en Render.

Con ella lee XAUUSD de MT5 y manda las ordenes a la cuenta. La diferencia
no es cosmetica: el futuro y el contado cotizan con decenas de dolares de
separacion y ese desfase SE MUEVE. Medido contra el grafico del operador
el mismo dia: 38,42 a las 14:27 y 41,30 a las 21:45. Tres dolares de
deriva en siete horas, sobre stops de cuatro. Un nivel calculado en uno y
puesto en el otro no es una aproximacion: es otro precio.

Solo funciona en Windows, porque el paquete MetaTrader5 solo existe ahi.
Por eso Render se queda con Yahoo y esto corre en la maquina que tenga la
terminal abierta.
"""


@dataclass
class Entrada:
    lado: str = ""
    entrada: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    confianza: str = "baja"
    razonamiento: str = ""
    citas: list[str] = field(default_factory=list)
    riesgos: str = ""
    peso_noticias: str = ""
    """Cuanto peso tuvieron las noticias frente a las velas, segun el agente."""
    noticias: str = ""
    uso: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return self.__dict__.copy()


ESQUEMA = {
    "type": "object",
    "properties": {
        # Sin "ninguno": eso es la entrada forzada. El agente no puede
        # abstenerse, solo elegir lado.
        "lado": {"type": "string", "enum": ["compra", "venta"],
                 "description": "Obligatorio. No existe la opcion de no entrar"},
        "sl": {"type": "number", "description": "Precio del stop loss"},
        "tp": {"type": "number", "description": "Precio del objetivo"},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"],
                      "description": "Baja es una respuesta legitima y frecuente"},
        "razonamiento": {
            "type": "string",
            "description": "Por que ese lado y no el otro. Di tambien que "
                           "apunta al lado contrario"},
        "citas": {"type": "array", "items": {"type": "string"},
                  "description": "Capitulos de la guia en que te apoyas"},
        "riesgos": {"type": "string",
                    "description": "Que haria que esta direccion fuera la equivocada"},
        "peso_noticias": {
            "type": "string", "enum": ["decisivo", "apoya", "irrelevante", "en_contra"],
            "description": "Que papel tuvieron las noticias en esta direccion"},
    },
    "required": ["lado", "sl", "tp", "confianza", "razonamiento", "citas",
                 "riesgos", "peso_noticias"],
    "additionalProperties": False,
}


SISTEMA = """Eres un operador de oro. Tu criterio es la guia que viene a
continuacion: 56 paginas sobre como se lee el mercado.

ESTO NO ES OPERATIVA NORMAL. ES UN EXPERIMENTO DE MEDICION.
Cada cuatro horas tienes que tomar posicion, si o si, alcista o bajista.
NO existe la opcion de no operar.

Sabes que la guia dice lo contrario -- «si alguna es "no", no hay
operacion» (cap. 11.9), y el capitulo 20.4 pone sobreoperar entre los
errores del principiante. Tienes razon y aun asi tienes que elegir lado.
El experimento mide UNA sola cosa: si sabes leer hacia donde va el precio.
Permitir «no se» haria imposible medirlo, porque no se podria distinguir
entre prudencia y no tener ni idea.

COMO ELEGIR CUANDO NADA ES CLARO
Casi siempre no habra un setup limpio. Entonces usa, en este orden:
  1. el sesgo estructural de las temporalidades altas (caps. 4.2, 4.3)
  2. hacia que liquidez apunta el precio -- el "draw on liquidity" del
     capitulo 6.1: que extremo sin barrer tiene mas cerca y mas a mano
  3. en que fase esta el mercado (cap. 5): en markup se compran
     retrocesos, en markdown se venden
  4. las noticias, si hay algo que mueva al oro (cap. 15.1)
Y pones confianza "baja". Decir "baja" cuando no ves nada es lo mas util
que puedes hacer: despues se mide si tus "alta" aciertan mas que tus
"baja", y eso vale tanto como el acierto global.

LO QUE SI DECIDES TU
La direccion, el stop y el objetivo.

DONDE VA EL STOP: ENTRE DOS PAREDES
La posicion vive lo que diga el retrato -- ahi tienes el horizonte exacto
y contra que ATR se mide -- o hasta que toque stop u objetivo. Eso pone el
stop entre dos limites, y los dos importan:

  - demasiado cerca (menos de 1x ese ATR) y lo barre el ruido de los
    primeros minutos, pase lo que pase con la direccion
  - demasiado lejos (mas de 3x) y ya no cabe un objetivo alcanzable en el
    tiempo que dura la posicion

El punto dulce suele estar entre 1x y 2x, y SIEMPRE detras de una
estructura: el minimo del rango, el extremo de la sesion, el borde del
Order Block. Nunca a una distancia redonda ni a media vela.

El objetivo, en liquidez identificable (cap. 6.1) y alcanzable en ese
tiempo. Si con un stop estructural el 2R no cabe, pon el objetivo donde
de verdad esta la liquidez y deja el R:R que salga: es preferible un
objetivo honesto a 1,5R que uno inventado a 2R.

LO QUE NO DECIDES
El tamano. El capitulo 14 lo fija en numeros y lo aplica el sistema.

SE HONESTO CON LA CONFIANZA
No la subas para que la respuesta parezca mejor. Un experimento donde
todo es "alta" no mide nada."""


# ══════════════════════════════════════════════════════════════════════
# Noticias reales
# ══════════════════════════════════════════════════════════════════════

PREGUNTA_NOTICIAS = """Busca que esta moviendo al oro (XAUUSD) AHORA MISMO.

Necesito, de las ultimas 24 horas:
- que datos macro de EE.UU. salieron y como quedaron frente al consenso
- donde estan el dolar (DXY) y los rendimientos reales a 10 anos, y en
  que direccion se movieron
- si hay algo de la Fed: declaraciones, actas, expectativas de tasas
- riesgo geopolitico o de mercado que empuje al oro como refugio
- que hay en el calendario de HOY y MANANA que pueda moverlo

Responde en 10 lineas como maximo, en espanol, con la fecha de cada cosa.
Si algo no lo encuentras, dilo: es mejor un hueco que un dato inventado.
No des opinion sobre si subira o bajara, solo los hechos."""


RELEVANTES = (
    "gold", "oro", "bullion", "xau", "fed", "fomc", "powell", "rate",
    "tasa", "inflation", "inflac", "cpi", "pce", "yield", "treasury",
    "dollar", "dxy", "jobs", "payroll", "nfp", "safe haven", "refugio",
    "geopolit", "war", "guerra", "tariff", "arancel", "central bank",
    "etf", "silver", "plata", "recession", "precious",
)
"""
Que titular cuenta como noticia del oro.

La fuente devuelve todo lo que toca al simbolo, y eso incluye una minera
junior australiana que perforo un granito. Eso no mueve el XAUUSD. Lo que
lo mueve esta en el capitulo 15.1: tasas reales, dolar, Fed, inflacion,
refugio, bancos centrales, flujos de ETF. El filtro es esa lista, en los
dos idiomas porque la fuente mezcla.
"""


def _noticias_yahoo(maximo: int = 8) -> list[str]:
    """
    Titulares reales del oro. Sin clave y sin cuota.

    Es la fuente que SIEMPRE esta: la busqueda con grounding de Gemini
    tiene una cuota propia mucho mas estrecha que la de generar texto
    -- medido: con la misma clave, generar responde 200 y buscar devuelve
    429 -- asi que no se puede depender de ella para un piloto que corre
    seis veces al dia.
    """
    import warnings
    import yfinance as yf
    warnings.filterwarnings("ignore")
    vistos: set[str] = set()
    out: list[tuple[str, str]] = []
    for simbolo in ("GC=F", "GLD"):
        try:
            crudas = yf.Ticker(simbolo).news or []
        except Exception as exc:  # noqa: BLE001
            log.warning("sin noticias de %s: %s", simbolo, exc)
            continue
        for x in crudas:
            c = x.get("content", x)
            titulo = str(c.get("title") or "").strip()
            if not titulo or titulo in vistos:
                continue
            if not any(p in titulo.lower() for p in RELEVANTES):
                continue
            vistos.add(titulo)
            prov = c.get("provider")
            prov = prov.get("displayName", "?") if isinstance(prov, dict) else "?"
            fecha = str(c.get("pubDate") or "")[:16].replace("T", " ")
            resumen = " ".join(str(c.get("summary") or "").split())[:260]
            out.append((fecha, f"[{fecha}] {prov}: {titulo}"
                              + (f"\n    {resumen}" if resumen else "")))
    out.sort(key=lambda t: t[0], reverse=True)
    return [t[1] for t in out[:maximo]]


def noticias_web() -> str:
    """
    Lo que esta pasando con el oro ahora mismo, de tres fuentes.

    El capitulo 15.1 lista lo que mueve al oro -- tasas reales, dolar,
    Fed, inflacion, refugio, bancos centrales -- y nada de eso esta en
    las velas. El calendario de `noticias.py` sabe QUE dias salen los
    datos, pero no que dijeron, y para elegir una direccion lo segundo
    pesa mas.

    Orden deliberado: primero los titulares de Yahoo, que no gastan cuota
    y por tanto estan siempre; despues el calendario, que es determinista;
    y por ultimo la busqueda de Gemini, que da el mejor resumen pero se
    queda sin cuota a las pocas llamadas. Si la ultima falla, el piloto
    sigue con las dos primeras en vez de entrar a ciegas.
    """
    partes = []
    titulares = _noticias_yahoo()
    if titulares:
        partes.append("### Titulares (Yahoo Finance)\n" + "\n".join(titulares))

    hoy = datetime.now(NY).date()
    partes.append("### Calendario de hoy (deducido)\n" + "\n".join(N.agenda(hoy)))

    for n, llave in enumerate(AG.llaves_gemini(), 1):
        try:
            partes.append("### Busqueda en la web\n" + _buscar_gemini(llave))
            break
        except Exception as exc:  # noqa: BLE001
            log.info("clave %d sin cuota de busqueda: %s", n, str(exc)[:90])
    return "\n\n".join(partes)


def _buscar_gemini(llave: str) -> str:
    """Gemini con busqueda de Google. Devuelve el texto y sus fuentes."""
    import httpx
    modelo = os.getenv("PILOTO_MODELO_NOTICIAS", "gemini-3.5-flash-lite")
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{modelo}:generateContent",
        headers={"x-goog-api-key": llave},
        json={"contents": [{"role": "user",
                            "parts": [{"text": PREGUNTA_NOTICIAS}]}],
              "tools": [{"google_search": {}}],
              "generationConfig": {"maxOutputTokens": 1200}},
        timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    d = r.json()
    c = d["candidates"][0]
    txt = "".join(p.get("text", "") for p in c["content"]["parts"])
    gm = c.get("groundingMetadata", {}) or {}
    fuentes = [x.get("web", {}).get("title", "")
               for x in gm.get("groundingChunks", [])][:6]
    if fuentes:
        txt += "\n\nFuentes: " + ", ".join(f for f in fuentes if f)
    return txt.strip()


# ══════════════════════════════════════════════════════════════════════
# La decision
# ══════════════════════════════════════════════════════════════════════

def decidir(simbolo: str, ctx: dict, noticias: str, notas: str = "") -> Entrada:
    """Una direccion, obligatoria. La guia entera y las noticias delante."""
    sistema = [
        {"type": "text", "text": SISTEMA},
        {"type": "text",
         "text": "# LA GUIA (integra, 56 paginas)\n\n" + GUIA.texto(),
         "cache_control": {"type": "ephemeral"}},
    ]
    if notas:
        sistema.append({"type": "text",
                        "text": "# LO QUE HAS APRENDIDO EN ESTE PILOTO\n\n" + notas})

    mensaje = (AG.retrato(simbolo, ctx)
               + "\n\n## Noticias del oro ahora mismo\n\n" + (noticias or "sin datos")
               + "\n\nElige lado. No puedes abstenerte.")

    # Se reusa la cadena de proveedores del agente, pero con ESTE esquema.
    original = AG.ESQUEMA
    try:
        AG.ESQUEMA = ESQUEMA
        d, uso = AG._preguntar(sistema, mensaje)
    except AG.ErrorIA as exc:
        log.warning("el piloto no pudo decidir: %s", exc)
        return Entrada(razonamiento=f"sin decision: {exc}")
    finally:
        AG.ESQUEMA = original

    precio = ctx.get("precio") or 0
    return Entrada(
        lado=d.get("lado", ""), entrada=precio,
        sl=float(d.get("sl") or 0), tp=float(d.get("tp") or 0),
        confianza=d.get("confianza", "baja"),
        razonamiento=d.get("razonamiento", ""),
        citas=list(d.get("citas") or []), riesgos=d.get("riesgos", ""),
        peso_noticias=d.get("peso_noticias", ""), noticias=noticias, uso=uso)


def revisar(e: Entrada, ctx: dict) -> str:
    """
    Lo que el codigo comprueba. Menos que en el sistema normal.

    Aqui la entrada es a MERCADO -- se entra ya, al precio que haya -- asi
    que no aplica la comprobacion de que la limite este en un retroceso.
    Lo que si se comprueba es que el stop y el objetivo esten del lado
    correcto y que el stop no caiga dentro del ruido, porque un stop de un
    tick convierte la medicion en una moneda al aire con comisiones.
    """
    if e.lado not in ("compra", "venta"):
        return f"lado ilegible: {e.lado!r}"
    if e.sl <= 0 or e.tp <= 0:
        return "faltan niveles"
    p = e.entrada
    if e.lado == "compra" and not (e.sl < p < e.tp):
        return f"compra con SL {e.sl} y TP {e.tp} sobre un precio de {p}"
    if e.lado == "venta" and not (e.tp < p < e.sl):
        return f"venta con SL {e.sl} y TP {e.tp} sobre un precio de {p}"
    # El stop se mide contra el ATR de 1H, no de 15M, porque la posicion
    # vive hasta cuatro horas. Medido en la primera ejecucion real: el
    # agente puso el stop a 2,30 con el ATR de 15M en 4,8 -- paso el
    # filtro de 15M y aun asi lo habria barrido el ruido de la primera
    # media hora. El yardstick tiene que ser el horizonte, no la vela.
    dist = abs(p - e.sl)
    tf, atr = atr_del_horizonte(ctx)
    if atr > 0:
        if dist < 1.0 * atr:
            return (f"stop de {dist:.4f} por debajo del ATR de {tf} ({atr:.4f}): "
                    f"lo barre el ruido antes de los {HORIZONTE_MIN:.0f} min")
        if dist > 3 * atr:
            return (f"stop de {dist:.4f} sobre 3x ATR de {tf} ({atr:.4f}): "
                    f"no deja sitio para un objetivo alcanzable en ese tiempo")
    return ""


# ══════════════════════════════════════════════════════════════════════
# El ciclo
# ══════════════════════════════════════════════════════════════════════

def simbolo() -> str:
    """Sobre que se opera: el del broker o el futuro de Yahoo."""
    if USA_BROKER:
        from materias import broker as B
        return B.SIMBOLO
    return D.PRINCIPAL


async def cargar_datos() -> dict:
    """
    Las velas, de donde toque.

    Con broker vienen de MT5 y son del MISMO instrumento que se opera.
    Sin el, de Yahoo. La forma que devuelven es identica, asi que todo lo
    que hay aguas abajo -- estructura, retrato, agente -- funciona igual
    sin saber de donde salieron.
    """
    if USA_BROKER:
        from materias import broker as B
        return await asyncio_to_thread(B.datos_completos)
    datos = await D.cargar([D.PRINCIPAL])
    return datos.get(D.PRINCIPAL) or {}


async def asyncio_to_thread(fn, *a, **k):
    """`MetaTrader5` es sincrono y bloqueante: va a un hilo."""
    import asyncio
    return await asyncio.to_thread(fn, *a, **k)


def _suyas(filas: list[dict], sim: str = "") -> list[dict]:
    """
    Solo las entradas de ESTE instrumento.

    POR QUE HACE FALTA
    Pueden correr dos pilotos a la vez contra la misma base: el de Render
    sobre GC=F en papel, y el de la maquina con MT5 sobre XAUUSD contra el
    broker. Los dos escriben con origen `piloto`.

    Sin separar por simbolo pasan dos cosas, las dos silenciosas:
      - el temporizador de las cuatro horas se comparte, asi que una
        entrada de Render bloquea la siguiente del broker
      - `resolver` intenta cerrar una posicion de XAUUSD reproduciendola
        contra velas del futuro de COMEX, que cotiza 40 dolares mas
        arriba: todas saldrian tocando el stop

    El simbolo ya estaba guardado en cada fila. Solo faltaba usarlo.
    """
    sim = sim or simbolo()
    return [o for o in filas if o.get("simbolo") == sim]


def vivas(sim: str = "") -> list[dict]:
    return _suyas(DI.leer_estado(("abierta",), origen=ORIGEN), sim)


def _cuando(o: dict) -> datetime | None:
    """
    La hora de una entrada, como fecha y no como texto.

    POR QUE NO VALE COMPARAR EL TEXTO
    Conviven dos formatos. Postgres devuelve la suya con un espacio y
    con zona -- `2026-09-24 09:27:25.069431-04:00` -- y una fila escrita
    sin `ts` explicito se guarda con `isoformat()`, que pone una T y no
    lleva zona: `2026-09-24T08:27:24`.

    Ordenados como texto, la T gana: vale 84 y el espacio 32. Asi que la
    fila mas vieja salia elegida como la mas reciente.

    Lo que eso provoco: el piloto creyo que la ultima entrada era de una
    hora antes, dio por cumplida la espera de 15 minutos y abrio una
    segunda posicion 50 segundos despues de la primera, en direccion
    contraria. Dos posiciones opuestas a la vez, que no miden nada y
    pagan el spread dos veces.
    """
    try:
        t = datetime.fromisoformat(str(o.get("ts")))
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=NY) if t.tzinfo is None else t


def toca_entrar(ahora: datetime, sim: str = "") -> bool:
    """¿Han pasado ya las cuatro horas desde la ultima entrada de este activo?"""
    todas = _suyas(DI.leer_estado(None, origen=ORIGEN), sim)
    fechas = [f for f in map(_cuando, todas) if f]
    if not fechas:
        return True
    return (ahora - max(fechas)) >= timedelta(hours=CADA_HORAS)


def resolver(m5: list[dict], sim: str = "") -> list[dict]:
    """
    Cierra las entradas vivas contra el precio: por SL, por TP o por tiempo.

    Se reproduce desde la vela de entrada con la misma funcion que usa el
    backtest, para que el resultado del piloto sea comparable con todo lo
    demas. Si ni SL ni TP se tocaron al cumplirse el horizonte, se cierra
    a mercado y el R sale del recorrido real -- un empate tambien es un
    dato, y forzarlo a ser ganancia o perdida seria inventarselo.
    """
    if not m5:
        return []
    cambios = []
    esq = next(e for e in G.ESQUEMAS if e.nombre == "todo_en_tp")
    for o in vivas(sim):
        try:
            t0 = datetime.fromisoformat(str(o["ts"]))
        except (ValueError, TypeError):
            continue
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=NY)
        i = next((k for k, v in enumerate(m5) if v["dt"] >= t0), None)
        if i is None:
            continue
        limite = t0 + timedelta(hours=HORIZONTE_HORAS)
        tramo = [v for v in m5 if v["dt"] <= limite]
        if len(tramo) <= i + 1:
            continue
        r = G.simular_salida(tramo, i, o["lado"], o["entrada"], o["sl"],
                             o["tp"], esq)
        dist = abs(o["entrada"] - o["sl"]) or 1
        if r["estado"] == "abierta":
            if m5[-1]["dt"] < limite:
                continue          # todavia le queda tiempo
            # ── Se acabo el horizonte sin tocar nada ──────────────────
            #
            # Con broker hay que cerrarla DE VERDAD, no solo anotarlo. La
            # primera version escribia la fila y dejaba la posicion viva
            # en MT5 con su SL y su TP. Con entradas cada cuatro horas
            # casi no se notaba, porque el stop o el objetivo acababan
            # tocando; con entradas cada 30 minutos se acumulan seis
            # posiciones abiertas en tres horas, cada una arriesgando lo
            # suyo a la vez. Y el diario diria que estan cerradas.
            cierre = tramo[-1]["c"]
            tarde = 0.0
            if USA_BROKER:
                from materias import broker as B
                tk = _ticket_broker(o)
                if tk:
                    res = B.cerrar_posicion(tk, "fin de horizonte")
                    # Cuanto se ha pasado del horizonte al cerrar. En
                    # marcha normal son segundos.
                    #
                    # Contra el RELOJ y no contra la ultima vela: si el
                    # feed viene atrasado -- y viene, justo al despertar
                    # de una suspension -- la ultima vela sigue siendo la
                    # del horizonte y el retraso saldria cero. Paso: una
                    # posicion estuvo 52 minutos de mas y se anoto como
                    # cerrada a tiempo.
                    tarde = max(0.0, (datetime.now(NY) - limite).total_seconds() / 60)
                    if res.get("precio") and tarde <= TARDE_MAX_MIN:
                        # El precio al que se cerro DE VERDAD, no el de la
                        # ultima vela: entre uno y otro hay spread.
                        cierre = res["precio"]
                    elif res.get("precio"):
                        # Se cerro, pero tarde. El precio de ese cierre no
                        # sirve para medir: el experimento pregunta a donde
                        # fue el precio EN EL HORIZONTE, y entre el minuto
                        # 25 y el cierre real hay un trozo de mercado que no
                        # formaba parte de la pregunta.
                        #
                        # Paso de verdad: la maquina se suspendio con una
                        # posicion de 25 minutos abierta y volvio hora y
                        # media despues. Tomar ese precio habria apuntado
                        # como resultado del agente un movimiento que ocurrio
                        # cuando ya no habia nadie midiendo.
                        #
                        # Se mide contra la vela del horizonte, que es la
                        # respuesta correcta, y se deja anotado el retraso
                        # para poder descartar la operacion despues.
                        log.warning("la posicion %s se cerro %.0f min tarde "
                                    "(@%s); se mide contra la vela del "
                                    "horizonte, no contra ese precio",
                                    tk, tarde, res["precio"])
                    elif not res.get("cerrada"):
                        log.warning("no se cerro la posicion %s: %s", tk,
                                    res.get("motivo"))
            signo = 1 if o["lado"] == "compra" else -1
            bruto = round(signo * (cierre - o["entrada"]) / dist, 4)
            estado = ("ganancia" if bruto > 0.02 else
                      "perdida" if bruto < -0.02 else "be")
            salida = str(tramo[-1]["dt"])
        else:
            # Salio por SL o por TP dentro del horizonte: el broker la
            # cerro solo, a su precio y a su hora. No hay retraso posible.
            tarde = 0.0
            bruto, estado, salida = r["r"], r["estado"], r.get("salida")
        neto = round(bruto - (o.get("costo_r") or 0), 4)

        # ── La medicion limpia: ¿acerto la DIRECCION a 4 horas? ───────
        #
        # Es otra pregunta que el R y hay que responderla aparte. El R
        # depende de donde puso el stop: una direccion correcta con el
        # stop mal puesto pierde dinero, y una direccion equivocada con
        # el stop lejos puede cerrar en verde por azar. El experimento
        # mide si SABE HACIA DONDE VA EL PRECIO, y eso solo se ve
        # comparando el precio de entrada con el del horizonte, sin que
        # el stop tenga nada que decir.
        #
        # El liston es 50 %: la moneda al aire. Es el mismo que uso el
        # banco de pruebas de cripto, y por el mismo motivo.
        cierre_h = tramo[-1]["c"]
        signo = 1 if o["lado"] == "compra" else -1
        mov = signo * (cierre_h - o["entrada"])
        nota = _con_medicion(o.get("nota"), {
            "direccion_ok": mov > 0,
            "movimiento": round(mov, 4),
            "precio_horizonte": round(cierre_h, 4),
            "horas": HORIZONTE_HORAS,
            "tarde_min": round(tarde, 1),
            "limpia": tarde <= TARDE_MAX_MIN,
        })
        DI.actualizar(o["id"], {"estado": estado, "r_bruto": bruto,
                                "r": neto, "salida": salida, "nota": nota})
        cambios.append({**o, "estado": estado, "r": neto,
                        "direccion_ok": mov > 0})
        log.info("PILOTO cierra %s %s -> %s  %+.2fR  ·  direccion %s (%+.2f)",
                 o["simbolo"], o["lado"], estado, neto,
                 "OK" if mov > 0 else "fallo", mov)
    return cambios


def _con_medicion(nota, medicion: dict) -> str:
    """Anade la medicion a la nota sin perder lo que el agente escribio."""
    try:
        d = json.loads(nota or "{}")
        if not isinstance(d, dict):
            d = {"texto": str(nota)}
    except (ValueError, TypeError):
        d = {"texto": str(nota or "")}
    d["medicion"] = medicion
    return json.dumps(d, ensure_ascii=False)[:8000]


def entrar(simbolo: str, ctx: dict, capital: float, notas: str = "") -> dict:
    """Una entrada del piloto, de principio a fin."""
    noticias = noticias_web()
    e = decidir(simbolo, ctx, noticias, notas)
    if not e.lado:
        return {"entro": False, "motivo": e.razonamiento}
    veto = revisar(e, ctx)
    if veto:
        log.warning("la direccion no era usable: %s", veto)
        DI.guardar({"ts": str(ctx["ahora"]),
                    "simbolo": simbolo, "setup": "piloto_4h", "lado": e.lado,
                    "estado": "no_enviada", "origen": ORIGEN,
                    "nota": f"rechazada: {veto} · {e.razonamiento}"[:900]})
        return {"entro": False, "motivo": veto}

    dist = abs(e.entrada - e.sl)
    contrato = ctx.get("contrato", 1)
    lotes = LOTE_FIJO or G.lotes(capital, RIESGO_PCT, dist, contrato)
    costo_r = round((ctx.get("costo") or 0) / dist, 4) if dist else 0.0

    # El riesgo REAL de esta entrada, no el que se pidio. Con lote fijo
    # los dos se separan, y guardar el supuesto convertiria el drawdown
    # en porcentaje del tablero en una cifra inventada. El R no cambia --
    # va normalizado por la distancia al stop -- pero el % de cuenta si.
    riesgo_usd = dist * lotes * contrato
    riesgo_real = round(100 * riesgo_usd / capital, 3) if capital else 0.0
    if LOTE_FIJO and riesgo_real > RIESGO_PCT * 2:
        log.warning("lote fijo de %s: esta entrada arriesga %.2f USD = %.1f%% "
                    "de %.0f, no el %.1f%% del cap. 14.1",
                    lotes, riesgo_usd, riesgo_real, capital, RIESGO_PCT)

    # ── Al broker, si esta conectado ──────────────────────────────────
    #
    # A mercado y no con una limite: el piloto entra pase lo que pase, y
    # una limite que a veces no se llena rompe la cadencia que hace
    # medible el experimento.
    envio = {}
    if USA_BROKER:
        from materias import broker as B
        envio = B.enviar_mercado(e.lado, lotes, e.sl, e.tp, simbolo,
                                 comentario="piloto 4h")
        if envio.get("precio"):
            # El precio real de llenado manda sobre el que se calculo:
            # entre decidir y ejecutar el mercado se movio, y el R tiene
            # que salir de donde se entro de verdad.
            e.entrada = round(envio["precio"], 4)
            dist = abs(e.entrada - e.sl)
        if not envio.get("enviada"):
            log.warning("el broker no acepto la orden: %s", envio.get("motivo"))

    nid = DI.guardar({
        "ts": str(ctx["ahora"]), "simbolo": simbolo,
        "nombre": D.ACTIVOS.get(simbolo, {}).get("nombre", simbolo),
        "setup": "piloto_4h", "lado": e.lado, "ventana": "forzada",
        "entrada": e.entrada, "sl": e.sl, "tp": e.tp,
        "rr_objetivo": round(abs(e.tp - e.entrada) / dist, 2) if dist else 0,
        "lotes": lotes, "riesgo_pct": riesgo_real, "costo_r": costo_r,
        "confluencias": e.citas,
        # Si el broker la rechazo no se marca como abierta: no existe.
        "estado": ("abierta" if (not USA_BROKER or envio.get("enviada"))
                   else "no_enviada"),
        "esquema": "todo_en_tp", "origen": ORIGEN,
        "nota": json.dumps({"confianza": e.confianza,
                            "razonamiento": e.razonamiento,
                            "riesgos": e.riesgos,
                            "peso_noticias": e.peso_noticias,
                            "noticias": e.noticias[:1800],
                            "uso": e.uso,
                            "broker": {k: v for k, v in envio.items()
                                       if k != "peticion"} or None,
                            }, ensure_ascii=False)[:8000],
    })
    log.info("PILOTO %s %s @ %.2f  SL %.2f  TP %.2f  %s lotes  confianza %s  "
             "noticias %s", simbolo, e.lado.upper(), e.entrada, e.sl, e.tp,
             lotes, e.confianza, e.peso_noticias)
    return {"entro": True, "id": nid, "entrada": e.dict()}


def resumen(sim: str = "") -> dict:
    """Como va el piloto de un instrumento. Es lo que pinta el tablero."""
    from materias import metricas as M
    todas = _suyas(DI.leer_estado(None, origen=ORIGEN), sim)
    cerradas = [o for o in todas
                if o.get("r") is not None and o["estado"] in
                ("ganancia", "perdida", "be")]
    d = {"entradas": len(todas),
         "abiertas": sum(1 for o in todas if o["estado"] == "abierta"),
         "cerradas": len(cerradas),
         "desde": min((str(o.get("ts") or "") for o in todas), default=None)}
    if not cerradas:
        return d
    rs = [o["r"] for o in cerradas]
    # El riesgo medio REAL de las operaciones, no el configurado: con
    # lote fijo son distintos, y `metricas` lo usa para pasar el drawdown
    # de R a porcentaje de cuenta.
    reales = [o["riesgo_pct"] for o in cerradas if o.get("riesgo_pct")]
    riesgo = round(sum(reales) / len(reales), 3) if reales else RIESGO_PCT
    m = M.calcular(rs, riesgo)
    d["riesgo_medio_pct"] = riesgo
    d["metricas"] = m.dict()

    # Lo que de verdad mide el experimento, y va separado del R a
    # proposito: si el agente sabe hacia donde va el precio. El liston es
    # 50 %, la moneda al aire.
    def _nota(o) -> dict:
        try:
            x = json.loads(o.get("nota") or "{}")
            return x if isinstance(x, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _limpia(n: dict) -> bool:
        """
        ¿Se cerro a tiempo esta operacion?

        Una que se cerro tarde tiene su direccion bien medida -- se mide
        contra la vela del horizonte -- pero estuvo expuesta un rato que
        no formaba parte de la pregunta, y su R no es comparable con el
        de las demas. `limpia` lo dice desde `resolver`. Las de antes de
        que existiera el flag no lo traen, y se dan por buenas: no se
        media el retraso entonces, asi que no hay motivo para sospechar.
        """
        return n.get("medicion", {}).get("limpia", True)

    medidas = [(_nota(o), o) for o in cerradas]
    con_dir = [(n["medicion"]["direccion_ok"], o)
               for n, o in medidas if "medicion" in n and _limpia(n)]
    sucias = sum(1 for n, _ in medidas if "medicion" in n and not _limpia(n))
    if con_dir:
        ok = sum(1 for v, _ in con_dir if v)
        d["direccion"] = {
            "n": len(con_dir),
            "acierto_pct": round(100 * ok / len(con_dir), 1),
            "liston_azar": 50.0,
            "horizonte_horas": HORIZONTE_HORAS,
            # Cuantas se dejaron fuera por cerrarse tarde. Va a la vista
            # porque si son muchas el porcentaje de arriba se calculo
            # sobre una muestra mas pequena de lo que parece.
            "descartadas_por_retraso": sucias,
        }
    elif sucias:
        d["direccion"] = {"n": 0, "descartadas_por_retraso": sucias}

    # Por confianza declarada. Si las "alta" no aciertan mas que las
    # "baja", el agente no sabe cuando sabe -- y eso vale tanto como el
    # acierto global, porque un sistema que reconoce su propia
    # incertidumbre se puede filtrar y uno que no, no.
    porc: dict[str, dict] = {}
    for n, o in medidas:
        c = n.get("confianza", "?")
        e = porc.setdefault(c, {"rs": [], "dir": []})
        e["rs"].append(o["r"])
        if "medicion" in n and _limpia(n):
            e["dir"].append(n["medicion"]["direccion_ok"])
    d["por_confianza"] = {
        k: {"n": len(v["rs"]), "r_total": round(sum(v["rs"]), 2),
            "direccion_pct": (round(100 * sum(v["dir"]) / len(v["dir"]), 1)
                              if v["dir"] else None)}
        for k, v in sorted(porc.items())}

    # Y por el peso que el agente le dio a las noticias: si las entradas
    # donde dijo "decisivo" no van mejor, las noticias no estan
    # aportando y el capitulo 15 no se esta aprovechando.
    porn: dict[str, list[float]] = {}
    for n, o in medidas:
        porn.setdefault(n.get("peso_noticias", "?"), []).append(o["r"])
    d["por_noticias"] = {
        k: {"n": len(v), "r_total": round(sum(v), 2)}
        for k, v in sorted(porn.items())}
    return d


# ══════════════════════════════════════════════════════════════════════
# El bucle
# ══════════════════════════════════════════════════════════════════════

def _registrar_broker() -> dict:
    """
    Mete el simbolo del broker en `datos.ACTIVOS` con SUS numeros.

    `datos.contexto` y `gestion.lotes` buscan ahi el tamano de contrato y
    el colchon del stop. Para un futuro de Yahoo son constantes escritas;
    para el simbolo de un broker no: los define la cuenta y cambian entre
    brokers y entre tipos de cuenta del mismo broker.
    """
    from materias import broker as B
    esp = B.especificacion()
    D.ACTIVOS[esp["simbolo"]] = {
        "nombre": esp["nombre"], "contrato": esp["contrato"],
        "tick": esp["tick"],
        "margen_sl": max(esp["spread"] * 2, esp["stops_min"], esp["tick"] * 3),
        "spread": esp["spread"],
        "comision": float(os.getenv("MT5_COMISION", "0") or 0),
    }
    return esp


VELAS_RANCIAS_MIN = float(os.getenv("PILOTO_VELAS_RANCIAS_MIN", "12"))
"""
Cuanto puede atrasarse el feed antes de que no se pueda operar con el.

Una vela de 5M recien cerrada ya tiene hasta 5 minutos; con el retardo
de la descarga y algun hueco, 12 deja margen sin dejar pasar un feed
parado de verdad.
"""


def _rancias(m5: list[dict]) -> bool:
    """
    ¿Son las velas demasiado viejas para decidir con ellas?

    POR QUE HACE FALTA
    Al volver de una suspension, MT5 tarda en resincronizar con el
    servidor y `copy_rates` devuelve un rato lo ultimo que tenia en
    cache, sin avisar. El ciclo lo daba por bueno.

    Lo que pasa entonces no es que se pierda una operacion: es que se
    manda una mala. El agente razono sobre velas de 52 minutos antes,
    con el oro en 4268.64, y decidio una VENTA con el stop en 4279.00.
    Para cuando la orden salio, el bid real era 4279.58 -- el stop
    quedaba DEBAJO del precio, al otro lado. La orden se rechazo, que es
    el final bueno; el malo habria sido que entrara.

    Mas vale saltarse el ciclo. El siguiente llega en un minuto y para
    entonces MT5 ya se ha puesto al dia.
    """
    if not m5:
        return True
    edad = (datetime.now(NY) - m5[-1]["dt"]).total_seconds() / 60
    if edad <= VELAS_RANCIAS_MIN:
        return False
    log.warning("velas de %.0f min de antiguedad; el feed aun no se ha "
                "puesto al dia. Se salta el ciclo", edad)
    return True


def _dur_seg(seg: float) -> str:
    """
    Una cuenta atras legible, que no miente cuando ya ha pasado.

    `timedelta.seconds` ignora el signo: un retraso de 59 minutos se
    guarda como «-1 dia, 23:01:00» y ese campo vale 23 horas. El tablero
    llego a anunciar «proxima en 23h02m» cuando faltaban dos minutos.
    Por eso se trabaja en segundos totales y se mira el signo.
    """
    if seg <= 0:
        return "ya"
    m = int(seg // 60)
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}m"


async def ciclo(capital: float) -> None:
    """
    Un paso: resolver lo abierto y, si toca, entrar.

    Se revisa cada pocos minutos aunque solo se entre cada cuatro horas,
    porque una posicion puede tocar su stop u objetivo en cualquier
    momento y anotarlo tarde falsearia la hora de salida en el diario
    (cap. 19.1).
    """
    from materias import saber

    s = simbolo()
    d = await cargar_datos()
    if not d or not d.get("m5"):
        log.warning("sin velas de %s", s)
        return
    if _rancias(d["m5"]):
        return
    resolver(d["m5"], s)

    ahora = datetime.now(NY)
    if not toca_entrar(ahora, s):
        r = resumen(s)
        # Con `_cuando` y no ordenando texto, por lo mismo que
        # `toca_entrar`: aqui habia quedado una copia del mismo fallo y
        # elegia una fila rechazada de una hora antes.
        prox = ""
        fechas = [f for f in
                  map(_cuando, _suyas(DI.leer_estado(None, origen=ORIGEN), s))
                  if f]
        if fechas:
            falta = max(fechas) + timedelta(hours=CADA_HORAS) - ahora
            prox = f" · proxima en {_dur_seg(falta.total_seconds())}"
        log.info("%s ET · %d entradas, %d abiertas, %d cerradas (%+.2fR)%s",
                 ahora.strftime("%H:%M"), r["entradas"], r["abiertas"],
                 r["cerradas"], (r.get("metricas") or {}).get("r_total", 0), prox)
        return

    ctx = D.contexto(s, d, ahora)
    entrar(s, ctx, capital, saber.leer())

    # Cada tanto el agente relee sus propios resultados y reescribe sus
    # notas (cap. 20.1, paso 8). Va despues de entrar y no antes: si
    # fallara, la entrada ya esta hecha.
    try:
        r = saber.aprender(origen=ORIGEN)
        if r.get("cambio"):
            log.info("notas reescritas: version %s sobre %s entradas",
                     r["version"], r["apoyo"])
    except Exception:  # noqa: BLE001
        log.exception("no se pudieron reescribir las notas")


def _no_dormir() -> None:
    """
    Pide a Windows que no suspenda la maquina mientras el piloto corre.

    POR QUE HACE FALTA
    El piloto mide direccion a un horizonte fijo, y eso solo significa
    algo si la posicion se cierra CUANDO toca. Con el portatil delante
    esto se dio por hecho; la primera tanda contra el broker lo
    desmintio: la maquina se suspendio a las 05:12 y volvio a las 06:44,
    con una posicion de 25 minutos viva hora y media y sin nadie
    mirandola. El dato de esa operacion no vale, y el dinero estuvo
    expuesto todo ese rato.

    ES_SYSTEM_REQUIRED impide la suspension por inactividad; no impide
    que alguien cierre la tapa ni que se acabe la bateria, asi que no
    sustituye a dejar el equipo enchufado. ES_CONTINUOUS hace que valga
    hasta que el proceso muera, en vez de un solo aplazamiento.

    No se toca la pantalla (ES_DISPLAY_REQUIRED): apagarla no interrumpe
    nada y mantenerla encendida toda la noche no hace falta.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        if ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
            log.info("la maquina no se suspendera mientras el piloto corra")
        else:
            log.warning("no se pudo evitar la suspension; si el equipo se "
                        "duerme, las posiciones se quedan abiertas")
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo evitar la suspension (%s)", exc)


async def main() -> None:
    import asyncio
    import signal

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    # httpx registra cada peticion en INFO y ensucia el log del piloto,
    # que hace seis cosas al dia y se lee entero.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from materias import almacen as AL
    from materias import agente as AG
    from materias import api as API

    parar = {"si": False}

    def _fin(*_):
        parar["si"] = True
        log.info("parando al terminar el ciclo")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _fin)
        except (ValueError, AttributeError):
            pass

    _no_dormir()

    capital = float(os.getenv("MATERIAS_CAPITAL", "10000"))
    if USA_BROKER:
        from materias import broker as B
        try:
            cta = B.conectar()
        except Exception as exc:  # noqa: BLE001
            log.error("%s", exc)
            log.error("  Abri MetaTrader 5, entra a la cuenta, y en")
            log.error("  Herramientas > Opciones > Asesores Expertos marca")
            log.error("  «Permitir trading algoritmico».")
            log.error("  Comprueba todo con:  python -m materias.broker")
            return
        if not cta["es_demo"] and os.getenv("MATERIAS_ACEPTO_REAL") != "1":
            log.error("la cuenta %s es REAL y falta MATERIAS_ACEPTO_REAL=1. "
                      "El paso 7 del capitulo 20.1 es DEMO.", cta.get("login"))
            return
        capital = float(cta.get("balance") or capital)
        esp = _registrar_broker()
        log.info("operando %s en la cuenta %s · contrato %s · spread %.2f · "
                 "stop minimo %.2f", esp["simbolo"], cta.get("login"),
                 esp["contrato"], esp["spread"], esp["stops_min"])
        if not B.ENVIAR_DE_VERDAD:
            log.warning("MODO SECO: se decide y se registra, pero NO se manda "
                        "ninguna orden. Pone MATERIAS_ENVIAR=1 para operar.")
    revisar_s = int(os.getenv("PILOTO_REVISAR_S", "300"))
    r = resumen()
    def _dur(min_: float) -> str:
        """En minutos si es corto, en horas y minutos si no."""
        m = round(min_)
        return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}m"

    log.info("PILOTO de entrada forzada · entra cada %s · la posicion vive %s "
             "· %d min de analisis entre una y otra",
             _dur(CADA_HORAS * 60), _dur(HORIZONTE_MIN), ANALISIS_MIN)
    if LOTE_FIJO:
        log.info("%s · LOTE FIJO %s (el riesgo por operacion lo fija el stop, "
                 "no el %.2f%% del cap. 14.1)", simbolo(), LOTE_FIJO, RIESGO_PCT)
    else:
        log.info("%s · riesgo %.2f%% por operacion (cap. 14.2)",
                 simbolo(), RIESGO_PCT)
    log.info("proveedores: %s · claves de gemini: %d",
             ",".join(AG.PROVEEDORES), len(AG.llaves_gemini()))
    log.info("diario en %s", AL.destino())
    log.info("%d entradas registradas, %d cerradas", r["entradas"], r["cerradas"])

    puerto = int(os.getenv("PORT", os.getenv("MATERIAS_PUERTO", "8790")))

    async def bucle():
        while not parar["si"]:
            t0 = datetime.now()
            try:
                await ciclo(capital)
            except Exception:  # noqa: BLE001
                log.exception("el ciclo fallo; se reintenta en el siguiente")
            resto = revisar_s - (datetime.now() - t0).total_seconds()
            while resto > 0 and not parar["si"]:
                await asyncio.sleep(min(resto, 5))
                resto -= 5

    await asyncio.gather(API.servir(puerto), bucle())


def _ticket_broker(o: dict) -> int | None:
    """El ticket de MT5 que quedo guardado en la nota al abrir."""
    try:
        n = json.loads(o.get("nota") or "{}")
        return (n.get("broker") or {}).get("ticket")
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
