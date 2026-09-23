"""
Los indicadores del capitulo 16, con las formulas tal como las escribe la guia.

QUE SON Y QUE NO SON
────────────────────
«Un indicador tecnico es una formula matematica aplicada a los datos de
precio (OHLC) y/o volumen que produce una nueva serie para facilitar la
lectura del mercado. No predicen: resumen lo que ya paso. Por eso todos
tienen retraso (lag). Se usan como FILTROS Y CONFIRMACIONES, no como
senal unica.»

Esa frase gobierna todo este archivo. Nada de aca abre una operacion
por si mismo. El setup lo dan los modelos del capitulo 11; estos numeros
solo pueden vetar o respaldar.

EL LIMITE QUE LA GUIA PONE EXPLICITO
«Errores comunes: usar varios que miden lo mismo (RSI + Estocastico +
CCI = una sola senal repetida); optimizar parametros hasta que el pasado
funcione perfecto (sobreajuste). Regla practica: MAXIMO 2-3
INDICADORES, de categorias distintas, cada uno con una funcion concreta
en tu plan.»

Por eso estan todos implementados pero el filtro real usa solo tres, y
cada uno de una categoria distinta -- los que la propia guia sugiere en
la tabla 16.3:

    EMA 200 de 1H/4H   tendencia    filtro de sesgo
    ATR 14             volatilidad  stops y tamano (ya vive en estructura.py)
    ADX 14             tendencia    separar rango de tendencia

RSI, MACD, Bollinger, Estocastico, VWAP, OBV y pivotes se calculan y se
guardan para poder medir despues si aportan, pero no filtran. Agregarlos
al filtro sin medirlos primero seria exactamente el sobreajuste que la
guia advierte.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


def sma(cierres: list[float], n: int) -> float:
    """«SMA(n) = (C1 + C2 + ... + Cn) / n»."""
    if len(cierres) < n or n <= 0:
        return 0.0
    return sum(cierres[-n:]) / n


def ema(cierres: list[float], n: int) -> float:
    """
    «EMA_hoy = Precio x k + EMA_ayer x (1 - k), con k = 2 / (n + 1)».

    Se siembra con la SMA de las primeras n velas, que es la convencion
    estandar y la que usa MetaTrader. Da mas peso a lo reciente, asi que
    reacciona antes que la SMA.
    """
    if len(cierres) < n or n <= 0:
        return 0.0
    k = 2 / (n + 1)
    e = sum(cierres[:n]) / n
    for c in cierres[n:]:
        e = c * k + e * (1 - k)
    return e


def serie_ema(cierres: list[float], n: int) -> list[float]:
    """La EMA completa, no solo el ultimo valor. La necesita el MACD."""
    if len(cierres) < n or n <= 0:
        return []
    k = 2 / (n + 1)
    e = sum(cierres[:n]) / n
    out = [e]
    for c in cierres[n:]:
        e = c * k + e * (1 - k)
        out.append(e)
    return out


def rsi(cierres: list[float], n: int = 14) -> float:
    """
    RSI de Wilder (cap. 16.2).

    «RSI = 100 - 100 / (1 + RS), con RS = promedio de ganancias /
    promedio de perdidas de los ultimos 14 periodos (promedios suavizados
    de Wilder: Prom_hoy = (Prom_ayer x 13 + valor_actual) / 14)».

    El suavizado de Wilder no es la media simple: por eso el RSI de una
    media movil normal de 14 da otro numero. La guia especifica cual.
    """
    if len(cierres) < n + 1:
        return 50.0
    dif = [cierres[i] - cierres[i - 1] for i in range(1, len(cierres))]
    gan = [max(d, 0.0) for d in dif]
    per = [max(-d, 0.0) for d in dif]
    pg, pp = sum(gan[:n]) / n, sum(per[:n]) / n
    for i in range(n, len(dif)):
        pg = (pg * (n - 1) + gan[i]) / n
        pp = (pp * (n - 1) + per[i]) / n
    if pp == 0:
        return 100.0
    return 100 - 100 / (1 + pg / pp)


def macd(cierres: list[float], rapida: int = 12, lenta: int = 26,
         senal: int = 9) -> dict:
    """
    «MACD = EMA(12) - EMA(26). Senal = EMA(9) del MACD. Histograma = MACD - senal».

    «Que se busca: cruces MACD/senal, cruce de la linea cero (cambio de
    tendencia), divergencias, y el histograma decreciente como perdida de
    impulso.»
    """
    if len(cierres) < lenta + senal:
        return {}
    er, el = serie_ema(cierres, rapida), serie_ema(cierres, lenta)
    # Alinear: la EMA lenta arranca mas tarde.
    er = er[len(er) - len(el):]
    linea = [a - b for a, b in zip(er, el, strict=False)]
    sig = serie_ema(linea, senal)
    if not sig:
        return {}
    hist = [linea[len(linea) - len(sig) + i] - sig[i] for i in range(len(sig))]
    return {"macd": round(linea[-1], 6), "senal": round(sig[-1], 6),
            "hist": round(hist[-1], 6),
            "cruce_alcista": len(hist) > 1 and hist[-2] <= 0 < hist[-1],
            "cruce_bajista": len(hist) > 1 and hist[-2] >= 0 > hist[-1],
            "sobre_cero": linea[-1] > 0,
            # «histograma decreciente como perdida de impulso».
            "impulso_cae": len(hist) > 2 and abs(hist[-1]) < abs(hist[-2]) < abs(hist[-3])}


def bollinger(cierres: list[float], n: int = 20, k: float = 2.0) -> dict:
    """
    «Banda media = SMA(20). Bandas = SMA(20) +- 2 x desviacion estandar
    de los ultimos 20 cierres. Ancho = (sup - inf) / media».

    «Que se busca: squeeze (bandas muy estrechas -> expansion proxima);
    caminar sobre la banda en tendencias fuertes; reversion a la media en
    rangos.»
    """
    if len(cierres) < n:
        return {}
    ven = cierres[-n:]
    m = sum(ven) / n
    sd = statistics.pstdev(ven)
    sup, inf = m + k * sd, m - k * sd
    ancho = (sup - inf) / m if m else 0.0
    # El squeeze es relativo a la propia historia del activo, no a un
    # numero fijo: 0,02 en gas natural y en oro no significan lo mismo.
    anchos = []
    for i in range(n, len(cierres) + 1):
        w = cierres[i - n:i]
        mm = sum(w) / n
        if mm:
            anchos.append(2 * k * statistics.pstdev(w) / mm)
    pct = (sum(1 for a in anchos if a < ancho) / len(anchos)) if anchos else 0.5
    return {"media": round(m, 4), "sup": round(sup, 4), "inf": round(inf, 4),
            "ancho": round(ancho, 5), "percentil_ancho": round(pct, 3),
            "squeeze": pct < 0.15,
            "sobre_banda_sup": cierres[-1] > sup, "bajo_banda_inf": cierres[-1] < inf}


def estocastico(altos: list[float], bajos: list[float], cierres: list[float],
                n: int = 14, d: int = 3) -> dict:
    """
    «%K = (Cierre - Minimo de 14) / (Maximo de 14 - Minimo de 14) x 100.
    %D = SMA(3) de %K».

    «Util en rangos, enganoso en tendencias» -- por eso no filtra.
    """
    if len(cierres) < n + d:
        return {}
    ks = []
    for i in range(n - 1, len(cierres)):
        hi, lo = max(altos[i - n + 1:i + 1]), min(bajos[i - n + 1:i + 1])
        ks.append(100 * (cierres[i] - lo) / (hi - lo) if hi > lo else 50.0)
    return {"k": round(ks[-1], 2), "d": round(sum(ks[-d:]) / d, 2)}


def adx(altos: list[float], bajos: list[float], cierres: list[float],
        n: int = 14) -> dict:
    """
    ADX / DMI de Wilder (cap. 16.2).

    «+DM = maximo actual - maximo anterior (si es mayor que la caida del
    minimo y positivo); -DM = minimo anterior - minimo actual. +DI = 100 x
    suavizado(+DM) / ATR; -DI analogo. DX = |+DI - -DI| / (+DI + -DI) x
    100. ADX = promedio suavizado de DX.»

    «Que se busca: FUERZA de la tendencia, no direccion. ADX < 20 = rango
    (evitar estrategias de tendencia); ADX > 25 = tendencia; +DI sobre
    -DI = dominio comprador.»

    Este si filtra: es el que decide si el dia sirve para modelos de
    continuacion o solo para los de reversion.
    """
    if len(cierres) < 2 * n + 1:
        return {}
    pdm, ndm, tr = [], [], []
    for i in range(1, len(cierres)):
        up = altos[i] - altos[i - 1]
        dn = bajos[i - 1] - bajos[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(altos[i] - bajos[i], abs(altos[i] - cierres[i - 1]),
                      abs(bajos[i] - cierres[i - 1])))

    def _wilder(xs: list[float]) -> list[float]:
        s = sum(xs[:n])
        out = [s]
        for x in xs[n:]:
            s = s - s / n + x
            out.append(s)
        return out

    str_, spdm, sndm = _wilder(tr), _wilder(pdm), _wilder(ndm)
    dxs = []
    for i in range(len(str_)):
        if str_[i] <= 0:
            continue
        pdi = 100 * spdm[i] / str_[i]
        ndi = 100 * sndm[i] / str_[i]
        s = pdi + ndi
        dxs.append(100 * abs(pdi - ndi) / s if s else 0.0)
    if len(dxs) < n:
        return {}
    a = sum(dxs[:n]) / n
    for x in dxs[n:]:
        a = (a * (n - 1) + x) / n
    pdi = 100 * spdm[-1] / str_[-1] if str_[-1] else 0.0
    ndi = 100 * sndm[-1] / str_[-1] if str_[-1] else 0.0
    return {"adx": round(a, 2), "pdi": round(pdi, 2), "ndi": round(ndi, 2),
            "regimen": "rango" if a < 20 else ("tendencia" if a > 25 else "indefinido"),
            "dominio": "compradores" if pdi > ndi else "vendedores"}


def vwap(velas: list[dict], desde_hora: int | None = None) -> dict:
    """
    «VWAP = SUM(Precio tipico x Volumen) / SUM(Volumen), acumulado desde
    el inicio de la sesion; Precio tipico = (Maximo + Minimo + Cierre)/3».

    «Precio sobre VWAP = control comprador intradia; retrocesos al VWAP
    como entradas; VWAP anclado a un evento.»

    Aca se ancla al dia en curso en hora de Nueva York, que es la sesion
    de referencia de estos futuros. `desde_hora` permite anclarlo a un
    evento -- una noticia, la apertura de Londres -- como la guia sugiere.
    """
    if not velas:
        return {}
    hoy = velas[-1]["dt"].date()
    sel = [v for v in velas if v["dt"].date() == hoy
           and (desde_hora is None or v["dt"].hour >= desde_hora)]
    if not sel:
        return {}
    num = den = 0.0
    tipicos = []
    for v in sel:
        tp = (v["h"] + v["l"] + v["c"]) / 3
        vol = v.get("v", 0) or 0
        tipicos.append(tp)
        num += tp * vol
        den += vol
    # Sin volumen la formula degenera; se cae a la media del tipico, que
    # es lo mismo que un VWAP con volumen constante.
    val = (num / den) if den > 0 else (sum(tipicos) / len(tipicos))
    return {"vwap": round(val, 4), "precio_sobre": velas[-1]["c"] > val,
            "velas": len(sel), "con_volumen": den > 0}


def obv(velas: list[dict]) -> dict:
    """
    «Si Cierre > Cierre anterior: OBV += Volumen; si menor: OBV -= Volumen».

    «Confirmacion de tendencia y divergencias con el precio.» La
    divergencia se mide contra el precio en la misma ventana: precio
    haciendo maximo nuevo con OBV que no lo hace es distribucion.
    """
    if len(velas) < 20:
        return {}
    o = 0.0
    serie = [0.0]
    for i in range(1, len(velas)):
        vol = velas[i].get("v", 0) or 0
        if velas[i]["c"] > velas[i - 1]["c"]:
            o += vol
        elif velas[i]["c"] < velas[i - 1]["c"]:
            o -= vol
        serie.append(o)
    n = min(20, len(serie) - 1)
    precio_sube = velas[-1]["c"] > velas[-1 - n]["c"]
    obv_sube = serie[-1] > serie[-1 - n]
    return {"obv": round(o, 1), "divergencia": precio_sube != obv_sube,
            "precio_sube": precio_sube, "obv_sube": obv_sube}


def pivotes(alto: float, bajo: float, cierre: float) -> dict:
    """
    Pivotes clasicos del dia anterior (cap. 16.2).

    «P = (Max + Min + Cierre del dia anterior) / 3. R1 = 2P - Min;
    S1 = 2P - Max; R2 = P + (Max - Min); S2 = P - (Max - Min);
    R3 = Max + 2(P - Min); S3 = Min - 2(Max - P).»

    «El pivote central como filtro de sesgo.» Son ademas liquidez: mucha
    gente los mira, y donde mira mucha gente hay ordenes.
    """
    p = (alto + bajo + cierre) / 3
    r = alto - bajo
    return {"p": round(p, 4),
            "r1": round(2 * p - bajo, 4), "s1": round(2 * p - alto, 4),
            "r2": round(p + r, 4), "s2": round(p - r, 4),
            "r3": round(alto + 2 * (p - bajo), 4),
            "s3": round(bajo - 2 * (alto - p), 4)}


def divergencia_rsi(cierres: list[float], altos: list[float],
                    bajos: list[float], n: int = 30) -> str:
    """
    Divergencia precio/RSI (cap. 16.2 y 16.3).

    «Precio hace nuevo maximo y RSI no -> debilidad». La guia la pone en
    la tabla de combinaciones utiles con una condicion: «Divergencia sola
    no basta; con barrida tiene contexto». Por eso esto devuelve una
    etiqueta para sumar como confluencia, nunca una senal.
    """
    if len(cierres) < n + 20:
        return ""
    mitad = n // 2
    a, b = cierres[:-mitad], cierres
    r_ant, r_act = rsi(a), rsi(b)
    max_ant, max_act = max(altos[-n:-mitad]), max(altos[-mitad:])
    min_ant, min_act = min(bajos[-n:-mitad]), min(bajos[-mitad:])
    if max_act > max_ant and r_act < r_ant:
        return "bajista"
    if min_act < min_ant and r_act > r_ant:
        return "alcista"
    return ""


@dataclass
class Lectura:
    """Todos los indicadores de un activo en un instante."""
    ema200_1h: float = 0.0
    ema50_1h: float = 0.0
    ema20_15m: float = 0.0
    precio_sobre_ema200: bool = False
    rsi14_15m: float = 50.0
    adx: dict = field(default_factory=dict)
    macd: dict = field(default_factory=dict)
    bollinger: dict = field(default_factory=dict)
    estocastico: dict = field(default_factory=dict)
    vwap: dict = field(default_factory=dict)
    obv: dict = field(default_factory=dict)
    pivotes: dict = field(default_factory=dict)
    divergencia: str = ""

    def dict(self) -> dict:
        return self.__dict__.copy()


def leer(ctx: dict) -> Lectura:
    """
    Calcula todo de una vez sobre el contexto ya cargado.

    Las temporalidades de cada indicador salen de la tabla 16.3: la EMA
    200 se mira en 1H/4H («evita operar contra la tendencia mayor»), el
    resto en la temporalidad de ejecucion.
    """
    L = Lectura()
    h1 = ctx.get("h1", [])
    m15 = ctx.get("m15", [])
    m5 = ctx.get("m5", [])
    diario = ctx.get("diario", [])

    if len(h1) >= 200:
        c1 = [v["c"] for v in h1]
        L.ema200_1h = round(ema(c1, 200), 4)
        L.ema50_1h = round(ema(c1, 50), 4)
        L.precio_sobre_ema200 = (m5[-1]["c"] > L.ema200_1h) if m5 else False
    if len(m15) >= 40:
        c15 = [v["c"] for v in m15]
        h15 = [v["h"] for v in m15]
        b15 = [v["l"] for v in m15]
        L.ema20_15m = round(ema(c15, 20), 4)
        L.rsi14_15m = round(rsi(c15), 2)
        L.adx = adx(h15, b15, c15)
        L.macd = macd(c15)
        L.bollinger = bollinger(c15)
        L.estocastico = estocastico(h15, b15, c15)
        L.divergencia = divergencia_rsi(c15, h15, b15)
    if m5:
        L.vwap = vwap(m5)
        L.obv = obv(m5)
    if len(diario) >= 2:
        a = diario[-2]
        L.pivotes = pivotes(a["h"], a["l"], a["c"])
    return L


def veto(L: Lectura, lado: str, modelo: str) -> str:
    """
    El unico punto donde los indicadores pueden bloquear una operacion.

    Dos filtros, de dos categorias distintas, como manda la regla de
    «maximo 2-3 indicadores»:

    1. EMA 200 de 1H (tendencia): «evita operar contra la tendencia
       mayor» (tabla 16.3). Solo se aplica a los modelos de CONTINUACION.
       A los de reversion no: por definicion buscan el giro, y exigirles
       ir a favor de la EMA 200 los anularia.

    2. ADX (tendencia, fuerza): «ADX < 20 = rango, evitar estrategias de
       tendencia» (cap. 16.2). Tambien solo para continuacion. Con ADX
       bajo los modelos de reversion son justamente los que aplican.

    Devuelve el motivo del veto, o cadena vacia si pasa.
    """
    continuacion = modelo in ("11.2", "11.3", "11.8")
    if not continuacion:
        return ""
    if L.ema200_1h > 0:
        if lado == "compra" and not L.precio_sobre_ema200:
            return "continuacion alcista con el precio bajo la EMA 200 de 1H"
        if lado == "venta" and L.precio_sobre_ema200:
            return "continuacion bajista con el precio sobre la EMA 200 de 1H"
    if L.adx and L.adx.get("regimen") == "rango":
        return f"ADX {L.adx['adx']} < 20: rango, no es dia de continuacion"
    return ""
