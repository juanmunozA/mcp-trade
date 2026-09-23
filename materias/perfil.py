"""
Perfil de volumen: POC, Value Area, HVN y LVN. Capitulo 9.2.

QUE ES
──────
«Perfil de volumen (Volume Profile): volumen negociado POR NIVEL DE
PRECIO (horizontal). Que se busca: POC (Point of Control, nivel con mas
volumen), Value Area (70 % del volumen: VAH y VAL), HVN (nodos de alto
volumen = soportes/resistencias), LVN (nodos bajos = el precio los
atraviesa rapido).»

POR QUE ESTO SE PUEDE HACER AQUI Y EN UN CFD NO
El capitulo 9.1 separa volumen real de volumen de ticks, y el 9.2 pone
el perfil entre las herramientas que lo necesitan. Estos futuros traen
volumen real de contratos negociados en CME/COMEX/NYMEX, que es
exactamente lo que el perfil requiere. Con un CFD el perfil dibujaria
ticks, que es otra cosa con el mismo nombre.

LA APROXIMACION QUE SE HACE Y HAY QUE DECLARAR
El perfil exacto necesita saber a que precio se negocio cada contrato
dentro de la vela. Con velas OHLC eso no se sabe, asi que el volumen de
cada vela se reparte UNIFORMEMENTE entre su minimo y su maximo. Es la
aproximacion estandar y es buena con velas de 5M -- donde el rango es
pequeno -- y cada vez peor cuanto mayor la temporalidad. Por eso se
calcula sobre 5M y no sobre diario.

PARA QUE SIRVE EN ESTE SISTEMA
El capitulo 16.3 lo pone junto al VWAP como «referencia institucional
intradia: zonas de valor donde reaccionan grandes participantes». Los
LVN son lo mas util operativamente: son huecos de volumen, y el precio
los cruza rapido. Un objetivo al otro lado de un LVN es mas alcanzable
que uno que tenga un HVN en medio.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Perfil:
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    hvn: list[float] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)
    niveles: int = 0
    volumen_total: float = 0.0
    con_volumen_real: bool = False

    def dict(self) -> dict:
        return self.__dict__.copy()


def construir(velas: list[dict], bins: int = 60,
              area: float = 0.70) -> Perfil:
    """
    Reparte el volumen por nivel de precio y saca POC, VA, HVN y LVN.

    `bins` es en cuantos escalones se divide el rango. 60 sobre una
    sesion de 5M da escalones de alrededor de un tick en oro, que es la
    resolucion util: mas finos solo agregan ruido de la aproximacion
    uniforme.

    `area` es el 70 % que la guia define como Value Area. El algoritmo es
    el clasico: desde el POC se va anexando el vecino mas voluminoso de
    arriba o de abajo hasta cubrir el 70 % del total.
    """
    p = Perfil()
    if len(velas) < 10 or bins < 5:
        return p
    alto = max(v["h"] for v in velas)
    bajo = min(v["l"] for v in velas)
    if alto <= bajo:
        return p
    paso = (alto - bajo) / bins
    cubos = [0.0] * bins
    total_vol = 0.0

    for v in velas:
        vol = v.get("v", 0) or 0
        total_vol += vol
        # Sin volumen la vela aporta su presencia: el perfil degenera en
        # un histograma de tiempo, que sigue marcando donde estuvo el
        # precio. Se informa con `con_volumen_real`.
        peso = vol if vol > 0 else 1.0
        i0 = max(0, min(bins - 1, int((v["l"] - bajo) / paso)))
        i1 = max(0, min(bins - 1, int((v["h"] - bajo) / paso)))
        n = i1 - i0 + 1
        for i in range(i0, i1 + 1):
            cubos[i] += peso / n

    p.con_volumen_real = total_vol > 0
    p.volumen_total = round(total_vol, 1)
    p.niveles = bins
    centro = lambda i: bajo + paso * (i + 0.5)  # noqa: E731

    i_poc = max(range(bins), key=lambda i: cubos[i])
    p.poc = round(centro(i_poc), 4)

    # Value Area: crecer desde el POC hacia el vecino mas voluminoso.
    objetivo = sum(cubos) * area
    acum = cubos[i_poc]
    lo = hi = i_poc
    while acum < objetivo and (lo > 0 or hi < bins - 1):
        abajo_v = cubos[lo - 1] if lo > 0 else -1
        arriba_v = cubos[hi + 1] if hi < bins - 1 else -1
        if arriba_v >= abajo_v:
            hi += 1
            acum += arriba_v
        else:
            lo -= 1
            acum += abajo_v
    p.val = round(centro(lo), 4)
    p.vah = round(centro(hi), 4)

    # HVN y LVN: picos y valles locales del histograma.
    media = sum(cubos) / bins
    for i in range(1, bins - 1):
        if cubos[i] > cubos[i - 1] and cubos[i] > cubos[i + 1] and cubos[i] > media * 1.5:
            p.hvn.append(round(centro(i), 4))
        elif cubos[i] < cubos[i - 1] and cubos[i] < cubos[i + 1] and cubos[i] < media * 0.5:
            p.lvn.append(round(centro(i), 4))
    return p


def camino_libre(p: Perfil, desde: float, hasta: float) -> dict:
    """
    Que hay entre la entrada y el objetivo.

    «HVN = soportes/resistencias; LVN = el precio los atraviesa rapido»
    (cap. 9.2). Un objetivo con dos HVN en medio es menos alcanzable que
    uno separado solo por LVN, aunque los dos esten a la misma distancia
    en R. Esto no veta nada: informa, y se suma como confluencia.
    """
    lo, hi = min(desde, hasta), max(desde, hasta)
    hvn = [x for x in p.hvn if lo < x < hi]
    lvn = [x for x in p.lvn if lo < x < hi]
    poc_medio = lo < p.poc < hi
    return {"hvn_en_medio": len(hvn), "lvn_en_medio": len(lvn),
            "poc_en_medio": poc_medio,
            "libre": len(hvn) == 0 and not poc_medio,
            "detalle": (f"{len(hvn)} HVN y {len(lvn)} LVN entre entrada y objetivo"
                        + (", con el POC en medio" if poc_medio else ""))}


def sesgo_value_area(p: Perfil, precio: float) -> str:
    """
    Donde esta el precio respecto a la zona de valor.

    Por encima de la VAH el mercado esta caro contra lo que se negocio;
    por debajo de la VAL, barato. Es la misma idea de premium/discount
    del capitulo 4.4 pero medida con volumen en vez de con el punto medio
    del rango, y por eso vale como segunda opinion y no como repeticion.
    """
    if not p.vah or not p.val:
        return ""
    if precio > p.vah:
        return "sobre_valor"
    if precio < p.val:
        return "bajo_valor"
    return "en_valor"
