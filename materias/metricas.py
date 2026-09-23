"""
Como se juzga si una estrategia sirve. Capitulos 14.3-14.5 y 18.7.

POR QUE ESTO ES UN ARCHIVO Y NO UNAS LINEAS EN EL BACKTEST
──────────────────────────────────────────────────────────
Porque el primer backtest con los nueve modelos imprimio esto:

    win rate 31,2%  ->  "POR DEBAJO del liston"

y era falso. El liston de ~35-38% que la guia menciona en el capitulo
20.2 esta calculado PARA UN R:R DE 2. El sistema medido daba un ganador
medio de 2,59R, y con eso el capitulo 14.3 dice que el equilibrio esta en
1 / (1 + 2,59) = 27,9 %. O sea que 31,2 % estaba por ENCIMA.

Comparar contra una constante en vez de contra la formula es el mismo
error que la guia senala en el capitulo 14.3 cuando dice que «el win rate
solo tiene sentido junto al R:R». El umbral no es un numero fijo: sale de
lo que la propia estrategia consigue.

LO QUE LA GUIA PIDE MEDIR (cap. 18.7)
    Profit Factor        > 1,3   (bueno > 1,5; > 3 sugiere sobreajuste)
    Expectativa          positiva y > costos, expresada en R
    Win rate             solo junto al R:R
    Maximo drawdown      < 15-20 %
    Recovery factor      > 3
    Sharpe ratio         > 1
    Numero de operaciones > 100 (idealmente > 300)
    Racha perdedora maxima
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Resultado:
    n: int = 0
    ganadoras: int = 0
    win_rate: float = 0.0
    r_total: float = 0.0
    expectativa: float = 0.0
    ganancia_media: float = 0.0
    perdida_media: float = 0.0
    rr_efectivo: float = 0.0
    win_rate_equilibrio: float = 0.0
    margen: float = 0.0
    profit_factor: float | None = 0.0
    max_drawdown_r: float = 0.0
    max_drawdown_pct: float = 0.0
    recovery_factor: float = 0.0
    sharpe: float = 0.0
    peor_racha: int = 0
    mejor_racha: int = 0
    muestra_suficiente: bool = False
    veredicto: str = ""
    avisos: list[str] = field(default_factory=list)

    def dict(self) -> dict:
        return self.__dict__.copy()


def calcular(rs: list[float], riesgo_pct: float = 0.5) -> Resultado:
    """
    Todas las metricas del capitulo 18.7 a partir de la lista de R.

    `rs` son los resultados en multiplos de R -- la unidad del capitulo
    13.1: «R es la distancia entre la entrada y el stop. Medir todo en R
    permite comparar operaciones con distinto tamano y activo».

    `riesgo_pct` es cuanto del capital se arriesga por operacion, y sirve
    para traducir el drawdown de R a porcentaje de cuenta, que es la
    forma en que el capitulo 18.7 pone su liston (< 15-20 %).
    """
    m = Resultado(n=len(rs))
    if not rs:
        m.veredicto = "sin operaciones"
        return m

    gan = [r for r in rs if r > 0]
    per = [r for r in rs if r <= 0]
    m.ganadoras = len(gan)
    m.win_rate = 100 * len(gan) / len(rs)
    m.r_total = sum(rs)

    # ── Expectativa (cap. 14.4) ───────────────────────────────────────
    # «Expectativa = (Win rate x Ganancia media) - (Loss rate x Perdida
    # media)». Da lo mismo que la media aritmetica de los R, pero se
    # escribe asi porque es la forma en que la guia la presenta y porque
    # las dos mitades se informan por separado.
    m.ganancia_media = round(statistics.mean(gan), 3) if gan else 0.0
    m.perdida_media = round(abs(statistics.mean(per)), 3) if per else 0.0
    wr, lr = len(gan) / len(rs), len(per) / len(rs)
    m.expectativa = round(wr * m.ganancia_media - lr * m.perdida_media, 4)

    # ── El liston que de verdad aplica (cap. 14.3) ────────────────────
    # «Win rate de equilibrio = 1 / (1 + R:R)». El R:R que se usa no es
    # el planeado sino el CONSEGUIDO: si los objetivos de 2R salen en
    # promedio a 2,59R porque algunos llegan mas lejos, el equilibrio
    # baja. Y si las perdidas salen peores que 1R por slippage, sube.
    if m.perdida_media > 0:
        m.rr_efectivo = round(m.ganancia_media / m.perdida_media, 3)
        m.win_rate_equilibrio = round(100 / (1 + m.rr_efectivo), 2)
        m.margen = round(m.win_rate - m.win_rate_equilibrio, 2)

    # ── Profit factor (cap. 18.7) ─────────────────────────────────────
    bruto_g = sum(gan)
    bruto_p = abs(sum(per))
    # Sin perdidas el cociente es infinito. Se deja en None y no en
    # float("inf") porque `json.dumps` escribe `Infinity` a secas, que NO
    # es JSON valido: el navegador falla al parsearlo y el tablero entero
    # se queda en blanco por un grupo de tres operaciones sin perdedoras.
    m.profit_factor = round(bruto_g / bruto_p, 3) if bruto_p else None

    # ── Drawdown (cap. 14.5) ──────────────────────────────────────────
    # «Caida desde el maximo de equity». Se mide sobre la curva
    # acumulada en R y despues se traduce a % de cuenta multiplicando
    # por el riesgo por operacion, que es la conversion que hace el
    # capitulo 14.5: «con 1 % de riesgo, 8 perdidas seguidas es -8 %».
    pico = acum = 0.0
    for r in rs:
        acum += r
        pico = max(pico, acum)
        m.max_drawdown_r = max(m.max_drawdown_r, pico - acum)
    m.max_drawdown_r = round(m.max_drawdown_r, 2)
    m.max_drawdown_pct = round(m.max_drawdown_r * riesgo_pct, 2)
    if m.max_drawdown_r > 0:
        m.recovery_factor = round(m.r_total / m.max_drawdown_r, 2)

    # ── Sharpe (cap. 18.7) ────────────────────────────────────────────
    # «Rentabilidad ajustada por volatilidad, > 1». Aca es por operacion,
    # no anualizado: media de los R sobre su desviacion tipica. Es la
    # version honesta con esta muestra -- anualizarlo exigiria suponer
    # una frecuencia de operaciones constante que no se ha medido.
    if len(rs) > 1:
        sd = statistics.pstdev(rs)
        m.sharpe = round(statistics.mean(rs) / sd, 3) if sd > 0 else 0.0

    # ── Rachas (cap. 14.5 y 18.7) ─────────────────────────────────────
    racha_p = racha_g = 0
    for r in rs:
        if r <= 0:
            racha_p += 1
            racha_g = 0
        else:
            racha_g += 1
            racha_p = 0
        m.peor_racha = max(m.peor_racha, racha_p)
        m.mejor_racha = max(m.mejor_racha, racha_g)

    # ── Veredicto ─────────────────────────────────────────────────────
    m.muestra_suficiente = m.n >= 100
    if m.n < 100:
        m.avisos.append(f"{m.n} operaciones: la guia pide >100 (idealmente >300) "
                        "para concluir algo (cap. 18.7)")
    elif m.n < 300:
        m.avisos.append(f"{m.n} operaciones: sirve para una lectura, pero la guia "
                        "prefiere >300")
    if m.profit_factor is not None and m.profit_factor > 3:
        m.avisos.append(f"profit factor {m.profit_factor}: «> 3 suele indicar "
                        "SOBREAJUSTE» (cap. 18.7), no excelencia")
    if m.max_drawdown_pct > 20:
        m.avisos.append(f"drawdown {m.max_drawdown_pct}% del capital: por encima "
                        "del 15-20% que la guia marca como tolerable")
    if m.peor_racha >= 8:
        m.avisos.append(f"{m.peor_racha} perdidas seguidas = "
                        f"-{m.peor_racha * riesgo_pct:.1f}% de cuenta seguidos. "
                        "«Saber cuantas puedes soportar sin abandonar el sistema»")
    if m.recovery_factor and m.recovery_factor < 3:
        m.avisos.append(f"recovery factor {m.recovery_factor}: la guia pide > 3")

    if m.profit_factor is None:
        m.veredicto = ("sin operaciones perdedoras: el profit factor no esta "
                       "definido y la muestra no dice nada todavia")
    elif m.expectativa > 0 and m.profit_factor >= 1.3:
        m.veredicto = "expectativa positiva y profit factor sano"
    elif m.expectativa > 0:
        m.veredicto = ("expectativa positiva pero profit factor "
                       f"{m.profit_factor} por debajo de 1,3")
    else:
        m.veredicto = "expectativa negativa: el sistema pierde dinero"
    return m


def informe(m: Resultado, riesgo_pct: float = 0.5) -> list[str]:
    """El resultado en texto, con el liston al lado de cada numero."""
    if not m.n:
        return ["sin operaciones"]
    L = ["RESULTADO  (capitulo 18.7)"]
    L.append(f"  operaciones        {m.n}"
             + ("" if m.muestra_suficiente else "   <- muestra corta"))
    L.append(f"  win rate           {m.win_rate:.1f}%")
    L.append(f"  R total            {m.r_total:+.2f}R")
    L.append(f"  expectativa        {m.expectativa:+.3f}R por operacion"
             f"   (liston: > 0 y > costos)")
    L.append(f"  ganancia media     {m.ganancia_media:.2f}R"
             f"     perdida media {m.perdida_media:.2f}R")
    if m.win_rate_equilibrio:
        L.append("")
        L.append("  EL LISTON QUE APLICA A ESTE SISTEMA (cap. 14.3)")
        L.append(f"    R:R conseguido       {m.rr_efectivo:.2f}")
        L.append(f"    win rate equilibrio  {m.win_rate_equilibrio:.1f}%"
                 f"   = 1 / (1 + {m.rr_efectivo:.2f})")
        L.append(f"    win rate real        {m.win_rate:.1f}%")
        signo = "POR ENCIMA" if m.margen > 0 else "POR DEBAJO"
        L.append(f"    -> {signo} por {abs(m.margen):.1f} puntos")
    L.append("")
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "sin perdidas"
    L.append(f"  profit factor      {pf}    (sano > 1,3; bueno > 1,5)")
    L.append(f"  max drawdown       {m.max_drawdown_r:.2f}R"
             f"  = {m.max_drawdown_pct:.1f}% con riesgo {riesgo_pct}%"
             f"   (liston < 15-20%)")
    L.append(f"  recovery factor    {m.recovery_factor:.2f}    (liston > 3)")
    L.append(f"  sharpe por op.     {m.sharpe:.2f}    (liston > 1)")
    L.append(f"  peor racha         {m.peor_racha} perdidas seguidas"
             f"   (mejor: {m.mejor_racha} ganadoras)")
    L.append("")
    L.append(f"  VEREDICTO: {m.veredicto}")
    for a in m.avisos:
        L.append(f"  AVISO: {a}")
    return L


def monte_carlo(rs: list[float], vueltas: int = 2000,
                semilla: int = 7) -> dict:
    """
    Simulacion de escenarios sobre la misma muestra (cap. 18.6, punto 6).

    «Monte Carlo para estimar peores rachas y drawdown.»

    DOS PREGUNTAS DISTINTAS, DOS METODOS DISTINTOS
    La primera version de esto REORDENABA la muestra, y de ahi salia que
    la probabilidad de terminar en perdida era 0,0 % y que el resultado
    p05 era identico al mediano. No era un hallazgo: era un error. La
    suma de una lista no cambia al barajarla, asi que reordenar no puede
    decir nada sobre el resultado final.

    Reordenar SI dice algo sobre el drawdown y las rachas, porque esos
    dependen del orden: las mismas operaciones agrupando las perdidas al
    principio dan una curva muy distinta.

    Para el resultado final hace falta REMUESTREO CON REEMPLAZO
    (bootstrap): construir series nuevas de la misma longitud sacando
    operaciones al azar de la muestra, repitiendo algunas y omitiendo
    otras. Eso responde «si el sistema sigue comportandose asi, que rango
    de resultados cabe esperar en las proximas N operaciones».

    Asi que se hacen los dos y se etiqueta cual responde a que.
    """
    import random
    if len(rs) < 20:
        return {}
    rnd = random.Random(semilla)
    n = len(rs)

    # ── Reordenamiento: drawdown y rachas ─────────────────────────────
    dds, rachas = [], []
    for _ in range(vueltas):
        mezcla = rs[:]
        rnd.shuffle(mezcla)
        pico = acum = dd = 0.0
        racha = peor = 0
        for r in mezcla:
            acum += r
            pico = max(pico, acum)
            dd = max(dd, pico - acum)
            racha = racha + 1 if r <= 0 else 0
            peor = max(peor, racha)
        dds.append(dd)
        rachas.append(peor)

    # ── Bootstrap: rango de resultados posibles ───────────────────────
    finales, dds_b = [], []
    for _ in range(vueltas):
        muestra = [rs[rnd.randrange(n)] for _ in range(n)]
        pico = acum = dd = 0.0
        for r in muestra:
            acum += r
            pico = max(pico, acum)
            dd = max(dd, pico - acum)
        finales.append(acum)
        dds_b.append(dd)

    dds.sort()
    rachas.sort()
    finales.sort()
    dds_b.sort()

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return {
        "n": n,
        # Del reordenamiento: cuanto pudo doler con estas mismas operaciones.
        "drawdown_mediano_r": round(q(dds, 0.5), 2),
        "drawdown_p95_r": round(q(dds, 0.95), 2),
        "peor_drawdown_r": round(dds[-1], 2),
        "racha_p95": q(rachas, 0.95),
        "peor_racha_simulada": rachas[-1],
        # Del bootstrap: que rango de resultados cabe esperar.
        "resultado_p05_r": round(q(finales, 0.05), 2),
        "resultado_mediano_r": round(q(finales, 0.5), 2),
        "resultado_p95_r": round(q(finales, 0.95), 2),
        "prob_perder": round(sum(1 for f in finales if f < 0) / len(finales), 3),
        "drawdown_p95_bootstrap_r": round(q(dds_b, 0.95), 2),
    }
