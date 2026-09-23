"""
El lector de estructura: swings, BOS, CHoCH, FVG, order blocks y liquidez.

DE DONDE SALE CADA REGLA
────────────────────────
Todo lo de este archivo esta tomado de la Guia integral de trading ·
Materias primas y metales, capitulos 4 (estructura), 6 (liquidez), 7
(FVG) y 8 (order blocks). Cada funcion cita el capitulo del que sale su
definicion, porque el valor de esto no es que sea ingenioso sino que sea
FIEL: si mas adelante los resultados no acompanan, hay que poder
distinguir "la guia dice otra cosa" de "la guia dice esto y no funciona".

POR QUE SE CALCULA Y NO SE LE PREGUNTA AL MODELO
Un FVG es una condicion aritmetica sobre tres velas y un BOS es una
comparacion de cierre contra un nivel. Eso lo hace mejor el codigo, y
ademas deja el resultado medible: si el agente dice "entre por un FVG",
despues se puede verificar que el FVG existia.

LA REGLA QUE MAS SE USA EN TODO EL ARCHIVO
"Ruptura valida = cierre de cuerpo mas alla del nivel. Una mecha que
cruza y vuelve es una barrida, no un BOS." (cap. 4.3). Esa distincion
decide casi todo: confundirlas es el error numero dos de la lista de
errores del principiante.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Vela:
    """Una vela OHLC con su hora. `i` es su posicion en la serie."""
    i: int
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    @property
    def alcista(self) -> bool:
        return self.c > self.o

    @property
    def cuerpo(self) -> float:
        return abs(self.c - self.o)

    @property
    def rango(self) -> float:
        return self.h - self.l


@dataclass
class Swing:
    """Un punto de giro. `tipo` es 'high' o 'low'."""
    i: int
    t: str
    precio: float
    tipo: str


@dataclass
class FVG:
    """
    Fair Value Gap: el hueco de tres velas del capitulo 7.1.

    `ce` es el Consequent Encroachment, el 50% del hueco. La guia lo
    marca como el nivel clave: en un FVG alcista respetado, los cuerpos
    no deberian cerrar por debajo del CE.
    """
    i: int
    t: str
    tipo: str          # 'alcista' | 'bajista'
    tope: float
    piso: float
    tamano: float
    mitigado: bool = False
    invalidado: bool = False

    @property
    def ce(self) -> float:
        return (self.tope + self.piso) / 2


@dataclass
class OrderBlock:
    """
    La ultima vela contraria antes del impulso que rompe estructura (cap. 8.1).

    `mean_threshold` es el 50%, que la guia senala como nivel clave de
    entrada. `con_fvg` y `rompio` registran dos de los cinco criterios de
    validez, porque un OB sin desplazamiento ni ruptura es solo una vela.
    """
    i: int
    t: str
    tipo: str          # 'alcista' | 'bajista'
    tope: float
    piso: float
    con_fvg: bool = False
    rompio: str = ""   # 'BOS' | 'CHoCH' | ''
    mitigado: bool = False

    @property
    def mean_threshold(self) -> float:
        return (self.tope + self.piso) / 2


@dataclass
class Estructura:
    """El estado estructural de una temporalidad."""
    swings: list[Swing] = field(default_factory=list)
    tendencia: str = "rango"      # 'alcista' | 'bajista' | 'rango'
    ultimo_evento: str = ""       # 'BOS' | 'CHoCH' | ''
    evento_en: str = ""
    # El extremo que, si se rompe, invalida la tendencia (cap. 4.1).
    protegido: float | None = None
    ultimo_alto: float | None = None
    ultimo_bajo: float | None = None


def velas(datos: list[dict]) -> list[Vela]:
    """Convierte filas OHLC en velas indexadas."""
    return [Vela(i=i, t=str(d["t"]), o=float(d["o"]), h=float(d["h"]),
                 l=float(d["l"]), c=float(d["c"]), v=float(d.get("v", 0) or 0))
            for i, d in enumerate(datos)]


def atr(vs: list[Vela], n: int = 14) -> float:
    """
    Average True Range. Sirve para distinguir desplazamiento de ruido.

    La guia lo usa como vara en dos lugares: una vela de 2x ATR es
    desplazamiento real y una de 0,3x es ruido (cap. 2.3), y el
    desplazamiento que valida un cambio de estructura pide al menos
    1,2x ATR (cap. 20.2).
    """
    if len(vs) < 2:
        return 0.0
    tr = []
    for a, b in zip(vs[:-1], vs[1:], strict=False):
        tr.append(max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)))
    ult = tr[-n:] if len(tr) >= n else tr
    return sum(ult) / len(ult) if ult else 0.0


def swings(vs: list[Vela], lado: int = 2) -> list[Swing]:
    """
    Puntos de giro por fractal (cap. 4.1).

    `lado=1` es el fractal de tres velas y `lado=2` el de Bill Williams,
    de cinco. Se usa 2 por defecto porque con 1 aparecen demasiados
    giros menores y la estructura se vuelve ilegible.
    """
    out = []
    for k in range(lado, len(vs) - lado):
        v = vs[k]
        vecinos = vs[k - lado:k] + vs[k + 1:k + lado + 1]
        if all(v.h > x.h for x in vecinos):
            out.append(Swing(v.i, v.t, v.h, "high"))
        if all(v.l < x.l for x in vecinos):
            out.append(Swing(v.i, v.t, v.l, "low"))
    return sorted(out, key=lambda s: s.i)


def leer_estructura(vs: list[Vela], lado: int = 2) -> Estructura:
    """
    La secuencia de maximos y minimos, y el ultimo evento (cap. 4.2, 4.3).

    Se recorre en orden marcando BOS cuando un cierre supera el ultimo
    extremo a favor, y CHoCH la primera vez que lo rompe en contra. Se
    exige CIERRE, no mecha: esa es la regla del capitulo 4.3 y es lo que
    separa una ruptura de una barrida.
    """
    e = Estructura(swings=swings(vs, lado))
    if len(e.swings) < 2:
        return e

    altos = [s for s in e.swings if s.tipo == "high"]
    bajos = [s for s in e.swings if s.tipo == "low"]
    if not altos or not bajos:
        return e
    e.ultimo_alto = altos[-1].precio
    e.ultimo_bajo = bajos[-1].precio

    tendencia = "rango"
    protegido = None
    evento = ""
    evento_en = ""
    ref_alto = altos[0].precio
    ref_bajo = bajos[0].precio

    for v in vs:
        prev_altos = [s for s in altos if s.i < v.i]
        prev_bajos = [s for s in bajos if s.i < v.i]
        if prev_altos:
            ref_alto = prev_altos[-1].precio
        if prev_bajos:
            ref_bajo = prev_bajos[-1].precio

        if v.c > ref_alto:
            # Cierre sobre el ultimo maximo: continuacion si ya era
            # alcista, cambio de caracter si venia bajista.
            evento = "BOS" if tendencia == "alcista" else "CHoCH"
            evento_en = v.t
            tendencia = "alcista"
            if prev_bajos:
                protegido = prev_bajos[-1].precio
        elif v.c < ref_bajo:
            evento = "BOS" if tendencia == "bajista" else "CHoCH"
            evento_en = v.t
            tendencia = "bajista"
            if prev_altos:
                protegido = prev_altos[-1].precio

    e.tendencia = tendencia
    e.ultimo_evento = evento
    e.evento_en = evento_en
    e.protegido = protegido
    return e


def fvgs(vs: list[Vela], minimo: float = 0.0) -> list[FVG]:
    """
    Los huecos de tres velas (cap. 7.1).

    Alcista cuando el minimo de la tercera queda por encima del maximo de
    la primera; bajista al reves. `minimo` descarta los insignificantes:
    en temporalidades bajas aparecen decenas por dia y la guia advierte
    que un FVG por si solo no es una senal.

    Se marca `mitigado` si el precio volvio a entrar y `invalidado` si lo
    atraveso con cierre, que es cuando deja de servir como zona.
    """
    out = []
    for k in range(2, len(vs)):
        a, b, c = vs[k - 2], vs[k - 1], vs[k]
        if c.l > a.h and (c.l - a.h) >= minimo:
            out.append(FVG(b.i, b.t, "alcista", tope=c.l, piso=a.h,
                           tamano=c.l - a.h))
        elif c.h < a.l and (a.l - c.h) >= minimo:
            out.append(FVG(b.i, b.t, "bajista", tope=a.l, piso=c.h,
                           tamano=a.l - c.h))
    for f in out:
        post = [v for v in vs if v.i > f.i + 1]
        if f.tipo == "alcista":
            f.mitigado = any(v.l <= f.tope for v in post)
            f.invalidado = any(v.c < f.piso for v in post)
        else:
            f.mitigado = any(v.h >= f.piso for v in post)
            f.invalidado = any(v.c > f.tope for v in post)
    return out


def order_blocks(vs: list[Vela], a: float = 0.0) -> list[OrderBlock]:
    """
    La ultima vela contraria antes de un impulso que rompe estructura
    (cap. 8.1).

    Se exige desplazamiento -- el impulso debe superar 1,2x ATR, la misma
    vara que el capitulo 20.2 pide para validar un cambio de estructura --
    porque un OB sin desplazamiento no cumple el primero de los cinco
    criterios de validez.
    """
    if a <= 0:
        a = atr(vs)
    out = []
    for k in range(1, len(vs) - 1):
        v, sig = vs[k], vs[k + 1]
        if sig.cuerpo < 1.2 * a:
            continue
        if not v.alcista and sig.alcista and sig.c > v.h:
            out.append(OrderBlock(v.i, v.t, "alcista", tope=max(v.o, v.c),
                                  piso=v.l))
        elif v.alcista and not sig.alcista and sig.c < v.l:
            out.append(OrderBlock(v.i, v.t, "bajista", tope=v.h,
                                  piso=min(v.o, v.c)))
    for ob in out:
        post = [x for x in vs if x.i > ob.i + 1]
        if ob.tipo == "alcista":
            ob.mitigado = any(x.l <= ob.tope for x in post)
        else:
            ob.mitigado = any(x.h >= ob.piso for x in post)
    return out


def barrida(vs: list[Vela], nivel: float, lado: str,
            desde: int = 0) -> Vela | None:
    """
    Busca una barrida del nivel: mecha que lo cruza y cuerpo que vuelve
    (cap. 6.2).

    `lado='bajo'` busca toma de liquidez por debajo (sell-side) y
    `'alto'` por encima. Devuelve la vela que barrio, o None.

    Es deliberadamente distinto de una ruptura: si el cuerpo cierra
    afuera esto devuelve None, porque entonces es un BOS y la operacion
    que corresponde es otra.
    """
    for v in vs:
        if v.i < desde:
            continue
        if lado == "bajo" and v.l < nivel and v.c > nivel:
            return v
        if lado == "alto" and v.h > nivel and v.c < nivel:
            return v
    return None


def premium_discount(alto: float, bajo: float, precio: float) -> dict:
    """
    En que mitad del rango esta el precio (cap. 4.4).

    La guia es tajante: en sesgo alcista solo se compra en discount y en
    bajista solo se vende en premium. Comprar en premium por euforia es
    el error numero tres de la lista.

    OTE es la zona de entrada optima, entre el 62% y el 79% de retroceso,
    con 0,705 como centro.
    """
    rango = alto - bajo
    if rango <= 0:
        return {"zona": "?", "equilibrio": precio, "pct": 0.5}
    pct = (precio - bajo) / rango
    return {
        "zona": "premium" if pct > 0.5 else "discount",
        "pct": round(pct, 3),
        "equilibrio": round((alto + bajo) / 2, 4),
        "ote_compra": [round(alto - rango * 0.79, 4),
                       round(alto - rango * 0.62, 4)],
        "ote_venta": [round(bajo + rango * 0.62, 4),
                      round(bajo + rango * 0.79, 4)],
        "en_ote": 0.21 <= pct <= 0.38 or 0.62 <= pct <= 0.79,
    }


def equal_levels(sw: list[Swing], tol: float) -> dict:
    """
    Maximos y minimos iguales (cap. 6.1).

    La guia los llama liquidez "muy limpia": varios traders ven doble
    techo o suelo y dejan sus stops justo detras, asi que el precio
    tiende a ir a buscarlos. `tol` suele ser una fraccion del ATR.
    """
    def _juntar(lista: list[Swing]) -> list[float]:
        out = []
        for s in lista:
            if any(abs(s.precio - x) <= tol for x in out):
                continue
            iguales = [x for x in lista if abs(x.precio - s.precio) <= tol]
            if len(iguales) >= 2:
                out.append(round(sum(x.precio for x in iguales) / len(iguales), 4))
        return out
    return {"eqh": _juntar([s for s in sw if s.tipo == "high"]),
            "eql": _juntar([s for s in sw if s.tipo == "low"])}


# ─────────────────────────────────────────────────────────────────────
# Zonas institucionales del capitulo 8.2 y variantes de FVG del 7.1
# ─────────────────────────────────────────────────────────────────────

def breaker_blocks(vs: list[Vela], obs: list[OrderBlock]) -> list[OrderBlock]:
    """
    Un OB que fallo y cambio de polaridad (cap. 8.2).

    «Un OB alcista roto se convierte en zona de venta». Se usa mucho en
    reversiones: el precio barrio liquidez, rompio el bloque que debia
    defenderlo, y ahora ese bloque actua del lado contrario.
    """
    out = []
    for ob in obs:
        post = [v for v in vs if v.i > ob.i + 1]
        if not post:
            continue
        if ob.tipo == "alcista" and any(v.c < ob.piso for v in post):
            out.append(OrderBlock(ob.i, ob.t, "bajista", tope=ob.tope,
                                  piso=ob.piso, con_fvg=ob.con_fvg,
                                  rompio="breaker"))
        elif ob.tipo == "bajista" and any(v.c > ob.tope for v in post):
            out.append(OrderBlock(ob.i, ob.t, "alcista", tope=ob.tope,
                                  piso=ob.piso, con_fvg=ob.con_fvg,
                                  rompio="breaker"))
    return out


def ifvg(vs: list[Vela], huecos: list[FVG]) -> list[FVG]:
    """
    FVG invertido: el precio lo atraveso con cierre y cambio de polaridad
    (cap. 7.1).

    «un FVG alcista roto pasa a actuar como resistencia». Es la base de
    la estrategia 11.7, que entra en el retroceso al hueco invertido.
    """
    out = []
    for f in huecos:
        if not f.invalidado:
            continue
        out.append(FVG(f.i, f.t, "bajista" if f.tipo == "alcista" else "alcista",
                       tope=f.tope, piso=f.piso, tamano=f.tamano,
                       mitigado=False, invalidado=False))
    return out


def inducement(sw: list[Swing], zona_precio: float, lado: str) -> float | None:
    """
    El extremo menor sin barrer que hay entre el precio y la zona (cap. 6.3).

    «si entre el precio y tu zona hay un minimo/maximo menor obvio sin
    barrer, espera a que se barra». Invita a entrar antes de tiempo, y
    el precio suele ir a buscarlo primero.
    """
    tipo = "low" if lado == "compra" else "high"
    cand = [s for s in sw if s.tipo == tipo]
    if not cand:
        return None
    if lado == "compra":
        entre = [s for s in cand if zona_precio < s.precio]
        return min(entre, key=lambda s: s.precio).precio if entre else None
    entre = [s for s in cand if s.precio < zona_precio]
    return max(entre, key=lambda s: s.precio).precio if entre else None


def bpr(alcistas: list[FVG], bajistas: list[FVG]) -> list[dict]:
    """
    Balanced Price Range: donde se solapan un FVG alcista y uno bajista
    (cap. 7.1). «Suele ser una zona de reaccion fuerte».
    """
    out = []
    for a in alcistas:
        for b in bajistas:
            lo = max(a.piso, b.piso)
            hi = min(a.tope, b.tope)
            if hi > lo:
                out.append({"piso": round(lo, 4), "tope": round(hi, 4),
                            "ce": round((lo + hi) / 2, 4)})
    return out


def nwog(velas_1h: list[dict]) -> dict:
    """
    New Week Opening Gap: cierre del viernes 17:00 contra apertura del
    domingo 18:00 (cap. 7.2).

    «Zona de referencia para toda la semana; el precio suele reaccionar
    en sus bordes y en su 50 %».
    """
    cierre = apertura = None
    for i in range(len(velas_1h) - 1, 0, -1):
        v, prev = velas_1h[i], velas_1h[i - 1]
        # Salto de mas de 20 horas entre velas: hay fin de semana en medio.
        if (v["dt"] - prev["dt"]).total_seconds() > 20 * 3600:
            cierre, apertura = prev["c"], v["o"]
            break
    if cierre is None:
        return {}
    return {"cierre_viernes": round(cierre, 4), "apertura_domingo": round(apertura, 4),
            "tope": round(max(cierre, apertura), 4),
            "piso": round(min(cierre, apertura), 4),
            "ce": round((cierre + apertura) / 2, 4),
            "tamano": round(abs(apertura - cierre), 4)}


def volumen_relativo(vs: list[Vela], n: int = 20) -> float:
    """
    Volumen de la ultima vela contra su media (cap. 9.3).

    La guia lo usa para separar movimiento con participacion de vacio de
    liquidez: «Vela de rango grande + volumen bajo = movimiento poco
    fiable».
    """
    if len(vs) < n + 1 or not vs[-1].v:
        return 1.0
    med = sum(v.v for v in vs[-n - 1:-1]) / n
    return round(vs[-1].v / med, 2) if med else 1.0
