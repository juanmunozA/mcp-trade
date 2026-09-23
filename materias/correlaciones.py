"""
Correlaciones entre activos y SMT divergence. Capitulo 15.5.

LA IDEA CENTRAL
───────────────
«CLAVE — SMT Divergence (Smart Money Technique): comparar activos
correlacionados (XAUUSD vs XAGUSD, o oro vs DXY invertido). Si uno barre
un minimo y el otro NO confirma, suele indicar que esa barrida es una
TRAMPA y precede a un giro. Es un filtro muy util EN KILLZONES.»

Eso encaja exacto con lo que ya hacen los modelos de reversion del
capitulo 11: todos arrancan con una barrida de liquidez y necesitan
decidir si fue trampa o ruptura real. Hasta ahora eso lo resolvia solo el
cambio de estructura posterior. La SMT es una segunda opinion, tomada de
otro activo, disponible en el mismo instante.

POR QUE ESTO NO ES UN INDICADOR MAS
Un indicador mira el mismo precio desde otro angulo, asi que no aporta
informacion nueva -- es el error que el capitulo 16.3 senala («usar
varios que miden lo mismo»). La SMT mira OTRA SERIE. Cuando el oro hace
minimo nuevo y la plata no, eso es un dato que el grafico del oro no
contiene.

LA TABLA DE LA GUIA, EN CODIGO
    Oro vs DXY                inversa       si DXY rompe maximo con fuerza,
                                            cuidado con compras de oro
    Oro vs rendimiento real   inversa fuerte filtro de sesgo semanal
    Oro vs Plata              positiva      la plata es mas volatil
    Oro vs S&P 500            variable      en panico ambos caen primero
    Cobre vs economia china   positiva      «Dr. Copper»
    Petroleo vs CAD           petroleo arriba = CAD fuerte
    Oro vs AUD                positiva
    Petroleo vs inflacion     positiva
"""

from __future__ import annotations

from dataclasses import dataclass

# Con que se compara cada activo para buscar SMT, y con que signo.
# +1 = se mueven juntos, -1 = inversos.
PAREJAS: dict[str, list[tuple[str, int, str]]] = {
    "GC=F": [("SI=F", +1, "«Oro vs Plata: positiva; plata mas volatil»"),
             ("DX-Y.NYB", -1, "«Oro vs DXY: inversa (generalmente)»")],
    "SI=F": [("GC=F", +1, "el par clasico de SMT en metales"),
             ("HG=F", +1, "la plata tiene componente industrial")],
    "HG=F": [("SI=F", +1, "ambos industriales"),
             ("GC=F", +1, "metales, correlacion debil")],
    "CL=F": [("NG=F", +1, "energia, pero con drivers distintos")],
    "NG=F": [("CL=F", +1, "energia")],
}

AUXILIARES = ("DX-Y.NYB",)
"""
El indice dolar, que no se opera pero se descarga para la SMT del oro.

«El oro cotiza en USD; un dolar fuerte suele presionar al oro
(correlacion inversa, no perfecta).» El parentesis importa: por eso la
divergencia con DXY se trata como confluencia y no como veto.

El simbolo es `DX-Y.NYB`, el indice de ICE, y no `DX=F`: el futuro del
dolar no lo sirve Yahoo -- devuelve 404 -- y probarlo antes de escribirlo
evito que la SMT del oro quedara silenciosamente muerta. Da 5M igual que
los futuros, que es lo que necesita la comparacion.
"""


@dataclass
class SMT:
    hay: bool = False
    tipo: str = ""        # 'alcista' | 'bajista'
    pareja: str = ""
    detalle: str = ""
    fuerza: float = 0.0

    def dict(self) -> dict:
        return self.__dict__.copy()


def _extremo(velas: list[dict], n: int, alto: bool) -> float | None:
    sel = velas[-n:]
    if len(sel) < max(4, n // 3):
        return None
    return max(v["h"] for v in sel) if alto else min(v["l"] for v in sel)


def smt(velas_a: list[dict], velas_b: list[dict], signo: int,
        lado: str, n: int = 24) -> SMT:
    """
    Busca la divergencia sobre las ultimas `n` velas de ambas series.

    Para una COMPRA (se espera giro al alza) interesa la divergencia
    alcista: el activo hizo un minimo mas bajo que el tramo anterior, y
    el correlacionado NO. Para una venta, lo simetrico en maximos.

    Con `signo = -1` -- oro contra DXY -- se invierte lo que se busca en
    el segundo activo: la confirmacion de un minimo nuevo en oro seria un
    maximo nuevo en DXY, y la divergencia es que el DXY no lo haga.

    `n` de 24 velas de 5M son dos horas: el tramo que cubre una killzone
    entera, que es donde la guia dice que este filtro sirve.
    """
    s = SMT()
    if len(velas_a) < n * 2 or len(velas_b) < n * 2:
        return s
    mitad = n
    a_prev, a_act = velas_a[-2 * mitad:-mitad], velas_a[-mitad:]
    b_prev, b_act = velas_b[-2 * mitad:-mitad], velas_b[-mitad:]

    if lado == "compra":
        ea_prev, ea_act = _extremo(a_prev, mitad, False), _extremo(a_act, mitad, False)
        if ea_prev is None or ea_act is None or ea_act >= ea_prev:
            return s   # el activo no hizo minimo nuevo: no hay nada que divergir
        # El correlacionado: con signo +1 deberia hacer minimo nuevo
        # tambien; con signo -1, maximo nuevo.
        busca_bajo = signo > 0
        eb_prev = _extremo(b_prev, mitad, not busca_bajo)
        eb_act = _extremo(b_act, mitad, not busca_bajo)
        if eb_prev is None or eb_act is None:
            return s
        confirmo = (eb_act < eb_prev) if busca_bajo else (eb_act > eb_prev)
        if confirmo:
            return s
        s.hay, s.tipo = True, "alcista"
        s.fuerza = round(abs(eb_act - eb_prev) / max(abs(eb_prev), 1e-9), 5)
        s.detalle = (f"minimo nuevo en el activo ({ea_act:.4f} < {ea_prev:.4f}) "
                     f"sin confirmacion del correlacionado: barrida probablemente trampa")
        return s

    ea_prev, ea_act = _extremo(a_prev, mitad, True), _extremo(a_act, mitad, True)
    if ea_prev is None or ea_act is None or ea_act <= ea_prev:
        return s
    busca_alto = signo > 0
    eb_prev = _extremo(b_prev, mitad, busca_alto)
    eb_act = _extremo(b_act, mitad, busca_alto)
    if eb_prev is None or eb_act is None:
        return s
    confirmo = (eb_act > eb_prev) if busca_alto else (eb_act < eb_prev)
    if confirmo:
        return s
    s.hay, s.tipo = True, "bajista"
    s.fuerza = round(abs(eb_act - eb_prev) / max(abs(eb_prev), 1e-9), 5)
    s.detalle = (f"maximo nuevo en el activo ({ea_act:.4f} > {ea_prev:.4f}) "
                 f"sin confirmacion del correlacionado: barrida probablemente trampa")
    return s


def buscar(simbolo: str, lado: str, todos: dict[str, dict],
           hasta) -> SMT:
    """
    Prueba las parejas del activo y devuelve la primera divergencia.

    `todos` es el diccionario de datos de todos los activos descargados;
    `hasta` recorta cada serie al instante evaluado, para no mirar velas
    que en ese momento no existian.
    """
    mias = todos.get(simbolo, {}).get("m5", [])
    if not mias:
        return SMT()
    a = [v for v in mias if v["dt"] <= hasta]
    for otro, signo, nota in PAREJAS.get(simbolo, []):
        serie = todos.get(otro, {}).get("m5", [])
        if not serie:
            continue
        b = [v for v in serie if v["dt"] <= hasta]
        r = smt(a, b, signo, lado)
        if r.hay:
            r.pareja = otro
            r.detalle = f"{r.detalle} [{nota}]"
            return r
    return SMT()


def ratio_oro_plata(diario_oro: list[dict], diario_plata: list[dict],
                    n: int = 60) -> dict:
    """
    El ratio oro/plata y donde esta contra su propia historia (cap. 15.5).

    «Ratio alto = plata "barata" relativa.» Es un dato de contexto, no
    una senal de entrada: dice cual de los dos metales tiene mas recorrido
    relativo, no cuando entrar.
    """
    if len(diario_oro) < 10 or len(diario_plata) < 10:
        return {}
    por_fecha = {v["dt"].date(): v["c"] for v in diario_plata}
    serie = []
    for v in diario_oro[-n:]:
        p = por_fecha.get(v["dt"].date())
        if p:
            serie.append(v["c"] / p)
    if len(serie) < 10:
        return {}
    act = serie[-1]
    pct = sum(1 for x in serie if x < act) / len(serie)
    return {"ratio": round(act, 2), "percentil": round(pct, 3),
            "lectura": ("plata barata en relativo" if pct > 0.75 else
                        "oro barato en relativo" if pct < 0.25 else "neutro")}
