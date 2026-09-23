"""
Que hacer con la operacion ya abierta, y cuanto arriesgar. Capitulos 13 y 14.

LA ADVERTENCIA QUE MANDA SOBRE TODO ESTE ARCHIVO
────────────────────────────────────────────────
«ATENCION: Cerrar parcial y mover a breakeven REDUCE el R promedio
ganador. Si tu estrategia tiene 40 % de acierto y depende de ganadores de
3R, cerrar 50 % en 1R puede volverla NO RENTABLE. Decide la gestion con
BACKTESTING, no por sensacion.» (cap. 13.2)

Por eso aca no hay un esquema "el bueno". Hay cuatro, los cuatro que la
guia tabula, y el backtest los corre todos sobre las mismas senales para
que el numero decida. Elegir uno a mano seria exactamente la sensacion
que la guia prohibe.

Esto importa de verdad en este sistema: el primer backtest de nueve
modelos dio un ganador medio de 2,59R. Cerrar la mitad en 1R lo bajaria
a ~1,8R, y con eso el win rate de equilibrio subiria de 27,9 % a 35,7 %.
El sistema mide 31,2 %. La gestion, sola, puede pasar el sistema de
rentable a no rentable sin tocar una sola entrada.

LO OTRO QUE HAY AQUI
El capitulo 14: tamano de lote, limites diarios, riesgo correlacionado.
Eso no es opcional ni se decide midiendo: son topes duros.
«Es el componente que mas determina la supervivencia a largo plazo. Una
estrategia mediocre con buena gestion de riesgo puede sobrevivir; una
estrategia excelente con mala gestion termina quebrando la cuenta.»
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ══════════════════════════════════════════════════════════════════════
# Capitulo 14.1 — Reglas fundamentales. Topes duros, no preferencias.
# ══════════════════════════════════════════════════════════════════════

RIESGO_POR_OPERACION = 0.5
"""«0,5 %-1 % del capital (maximo 2 %). En cuentas de fondeo, 0,25-0,5 %»."""

RIESGO_MAXIMO = 2.0
"""El techo absoluto que la guia pone al riesgo por operacion."""

LIMITE_DIARIO_R = 2.0
"""«Detener el trading tras perder 2R-3R en el dia, o tras 2 perdidas consecutivas»."""

LIMITE_PERDIDAS_SEGUIDAS = 2

LIMITE_SEMANAL_PCT = 6.0
"""«-6 % semanal, -10 % mensual -> pausa y revision»."""

LIMITE_MENSUAL_PCT = 10.0

RIESGO_TOTAL_ABIERTO = 3.0
"""
«No superar 2 %-3 % sumando todas las posiciones, CONSIDERANDO CORRELACION
(oro y plata largos a la vez = aproximadamente la misma operacion)».

El parentesis es lo importante y es lo que `exposicion()` implementa:
sumar los riesgos sin ponderar por correlacion da un numero que miente.
"""

# Cuanto cuenta cada par como "la misma operacion" (cap. 15.5).
CORRELACION = {
    ("GC=F", "SI=F"): 0.8,   # «Oro vs Plata: positiva; plata mas volatil»
    ("HG=F", "SI=F"): 0.5,   # plata tiene componente industrial
    ("GC=F", "HG=F"): 0.3,
    ("CL=F", "NG=F"): 0.4,   # ambos energia, pero con drivers distintos
    ("CL=F", "HG=F"): 0.4,   # «Cobre vs economia global» / petroleo vs ciclo
}


def correlacion(a: str, b: str) -> float:
    """Cuanto se solapan dos activos, en [0, 1]. El mismo activo es 1."""
    if a == b:
        return 1.0
    return CORRELACION.get((a, b)) or CORRELACION.get((b, a)) or 0.0


def exposicion(abiertas: list[dict], nueva: dict | None = None) -> dict:
    """
    El riesgo total abierto contando correlacion (cap. 14.1).

    Dos largos de oro y plata al 0,5 % cada uno no son 1 % de riesgo:
    con correlacion 0,8 se comportan casi como una sola posicion de
    ~0,9 %. La formula suma el riesgo directo y le agrega el solape de
    cada par, que es la forma simple de no engañarse.

    `abiertas` y `nueva` son dicts con al menos `simbolo`, `lado` y
    `riesgo_pct`.
    """
    todas = list(abiertas) + ([nueva] if nueva else [])
    directo = sum(p.get("riesgo_pct", RIESGO_POR_OPERACION) for p in todas)
    solape = 0.0
    for i in range(len(todas)):
        for j in range(i + 1, len(todas)):
            a, b = todas[i], todas[j]
            c = correlacion(a["simbolo"], b["simbolo"])
            if not c:
                continue
            # Mismo lado en activos correlacionados: se suman. Lados
            # opuestos: se cancelan parcialmente, que es una cobertura.
            signo = 1 if a.get("lado") == b.get("lado") else -1
            ra = a.get("riesgo_pct", RIESGO_POR_OPERACION)
            rb = b.get("riesgo_pct", RIESGO_POR_OPERACION)
            solape += signo * c * min(ra, rb)
    efectivo = directo + solape
    return {"directo": round(directo, 3), "efectivo": round(efectivo, 3),
            "solape": round(solape, 3), "posiciones": len(todas),
            "excede": efectivo > RIESGO_TOTAL_ABIERTO,
            "tope": RIESGO_TOTAL_ABIERTO}


def lotes(capital: float, riesgo_pct: float, dist_precio: float,
          tamano_contrato: float) -> float:
    """
    «Lotes = Riesgo en dinero / (Distancia al SL en precio x Tamano del contrato)».

    El ejemplo de la guia: cuenta de 10.000, riesgo 1 % = 100 USD. Compra
    en XAUUSD a 2.650 con SL en 2.645 -> distancia 5,00. Contrato 100 oz.
    Lotes = 100 / (5,00 x 100) = 0,20.

    Y la consecuencia que la guia subraya: «con el mismo riesgo y un SL
    mas ajustado de 2,50: 100 / (2,50 x 100) = 0,40 lotes. REFINAR LA
    ENTRADA NO AUMENTA EL RIESGO: aumenta el tamano y el R potencial».
    """
    if dist_precio <= 0 or tamano_contrato <= 0:
        return 0.0
    return round((capital * min(riesgo_pct, RIESGO_MAXIMO) / 100)
                 / (dist_precio * tamano_contrato), 2)


def lotes_por_tick(capital: float, riesgo_pct: float, dist_precio: float,
                   tick_size: float, tick_value: float) -> float:
    """
    «Para MT5 generico: Lotes = Riesgo / (Distancia SL / Tick size x Tick value)».

    La variante que hay que usar cuando el broker cotiza en ticks y no en
    tamano de contrato. Da el mismo numero cuando tick_value =
    tick_size x contrato.
    """
    if dist_precio <= 0 or tick_size <= 0 or tick_value <= 0:
        return 0.0
    ticks = dist_precio / tick_size
    return round((capital * min(riesgo_pct, RIESGO_MAXIMO) / 100)
                 / (ticks * tick_value), 2)


@dataclass
class EstadoDia:
    """Lo que el capitulo 14.1 obliga a llevar contado."""
    fecha: str = ""
    operaciones: int = 0
    perdidas_seguidas: int = 0
    r_dia: float = 0.0
    r_semana: float = 0.0
    r_mes: float = 0.0

    def registrar(self, r: float) -> None:
        self.operaciones += 1
        self.r_dia += r
        self.r_semana += r
        self.r_mes += r
        self.perdidas_seguidas = self.perdidas_seguidas + 1 if r <= 0 else 0


def puede_operar(e: EstadoDia, max_operaciones: int = 2,
                 riesgo_pct: float = RIESGO_POR_OPERACION) -> str:
    """
    Los frenos del capitulo 14.1. Devuelve el motivo del freno, o "".

    Es el punto 8 del checklist universal: «¿Ya alcance mi limite de
    perdidas o de operaciones del dia?». Va al final del checklist pero
    se evalua igual que los demas: si es "si", no hay operacion, por
    perfecto que sea el setup.
    """
    if e.operaciones >= max_operaciones:
        return f"ya hay {e.operaciones} operaciones hoy (tope {max_operaciones})"
    if e.perdidas_seguidas >= LIMITE_PERDIDAS_SEGUIDAS:
        return f"{e.perdidas_seguidas} perdidas consecutivas: se para el dia"
    if e.r_dia <= -LIMITE_DIARIO_R:
        return f"{e.r_dia:.1f}R en el dia: se alcanzo el limite de -{LIMITE_DIARIO_R}R"
    if e.r_semana * riesgo_pct <= -LIMITE_SEMANAL_PCT:
        return f"{e.r_semana * riesgo_pct:.1f}% semanal: pausa y revision"
    if e.r_mes * riesgo_pct <= -LIMITE_MENSUAL_PCT:
        return f"{e.r_mes * riesgo_pct:.1f}% mensual: pausa y revision"
    return ""


# ══════════════════════════════════════════════════════════════════════
# Capitulo 13 — Gestion de la posicion
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Esquema:
    """
    Un plan de salida de los cuatro que tabula el capitulo 13.2.

    `parciales` son pares (multiplo de R, fraccion del volumen). `be_en`
    es el multiplo de R a partir del cual el stop va a entrada. `trailing`
    dice si el resto se acompana.
    """
    nombre: str
    parciales: list[tuple[float, float]] = field(default_factory=list)
    be_en: float | None = None
    trailing: str = ""
    nota: str = ""


ESQUEMAS = [
    Esquema("todo_en_tp", [],
            nota="«100 % en el objetivo. Simple, estadisticas limpias, "
                 "mejor para automatizar. Contra: mas operaciones que "
                 "llegan cerca y giran»"),
    Esquema("50_50", [(1.0, 0.5)], be_en=1.0,
            nota="«50 % en 1R-2R (liquidez interna) + stop a breakeven; "
                 "50 % al objetivo final. Contra: reduce la ganancia de "
                 "los grandes ganadores»"),
    Esquema("escalonado", [(1.0, 1 / 3), (2.0, 1 / 3)], be_en=1.0,
            trailing="estructura",
            nota="«1/3 en 1R, 1/3 en 2R, 1/3 con trailing detras de "
                 "estructura. Captura tendencias largas; mas complejo»"),
    Esquema("be_tardio", [], be_en=1.5,
            nota="La regla del capitulo 13.3 sin parciales: «mover a BE "
                 "solo tras alcanzar 1,5R-2R, no antes». Protege sin "
                 "recortar al ganador"),
]
"""
Los cuatro esquemas, para correrlos todos sobre las mismas senales.

Falta el quinto de la tabla, «parcial en liquidez» (cerrar en cada EQH o
maximo de sesion intermedio). La guia misma lo marca: «requiere juicio».
Es el unico de los cinco que no se puede definir sin ambiguedad, y el
capitulo 18.2 dice que «solo se puede automatizar lo que se puede definir
sin ambiguedad». Se deja fuera a proposito, no por olvido.
"""


def simular_salida(velas: list[dict], desde_i: int, lado: str, entrada: float,
                   sl: float, tp: float, esq: Esquema,
                   atr: float = 0.0) -> dict:
    """
    Recorre la operacion vela a vela aplicando un esquema, y devuelve su R.

    EL SESGO QUE HAY QUE DECLARAR
    Cuando una vela toca SL y TP dentro de la misma vela no se sabe cual
    ocurrio primero sin datos de 1M. Se cuenta como PERDIDA. Es el
    supuesto conservador y hay que dejarlo escrito: contarlo como
    ganancia inflaria el resultado justo en las operaciones mas ajustadas.

    El trailing por estructura (cap. 13.4) se aproxima con los minimos o
    maximos de las ultimas tres velas, que es el fractal mas corto que
    `estructura.swings` reconoce. El de ATR usa el Chandelier de la guia:
    «SL = maximo mas alto desde la entrada - k x ATR (k tipico 2-3)».
    """
    dist = abs(entrada - sl)
    if dist <= 0:
        return {"estado": "invalida", "r": 0.0}
    signo = 1 if lado == "compra" else -1
    restante = 1.0
    r_acum = 0.0
    sl_vivo = sl
    pendientes = sorted(esq.parciales, key=lambda x: x[0])
    hecho: set[float] = set()
    extremo = entrada

    for k in range(desde_i + 1, len(velas)):
        v = velas[k]
        # Cuanto R llevaria si cerrara en el extremo favorable de la vela.
        mejor = (v["h"] - entrada) / dist if lado == "compra" else (entrada - v["l"]) / dist
        peor = (v["l"] - entrada) / dist if lado == "compra" else (entrada - v["h"]) / dist

        # 1. El stop primero: si en la misma vela hay SL y algo mas, gana
        #    el SL. Es la mitad conservadora del supuesto.
        r_sl = (sl_vivo - entrada) / dist * signo
        if peor <= r_sl:
            r_acum += restante * r_sl
            return {"estado": "perdida" if r_acum < 0 else ("be" if abs(r_acum) < 0.01
                    else "ganancia"),
                    "r": round(r_acum, 3), "salida": str(v["dt"]),
                    "esquema": esq.nombre, "parciales": len(hecho)}

        # 2. Parciales alcanzados en esta vela.
        for mult, frac in pendientes:
            if mult in hecho or mejor < mult:
                continue
            frac = min(frac, restante)
            r_acum += frac * mult
            restante -= frac
            hecho.add(mult)

        # 3. Breakeven (cap. 13.3): «no antes» del umbral.
        if esq.be_en is not None and mejor >= esq.be_en:
            be = entrada
            if (lado == "compra" and be > sl_vivo) or (lado == "venta" and be < sl_vivo):
                sl_vivo = be

        # 4. Trailing (cap. 13.4).
        extremo = max(extremo, v["h"]) if lado == "compra" else min(extremo, v["l"])
        if esq.trailing == "estructura" and k >= desde_i + 4:
            ult = velas[k - 2:k + 1]
            nivel = (min(x["l"] for x in ult) if lado == "compra"
                     else max(x["h"] for x in ult))
            if (lado == "compra" and nivel > sl_vivo) or (lado == "venta" and nivel < sl_vivo):
                sl_vivo = nivel
        elif esq.trailing == "atr" and atr > 0:
            nivel = extremo - 2.5 * atr if lado == "compra" else extremo + 2.5 * atr
            if (lado == "compra" and nivel > sl_vivo) or (lado == "venta" and nivel < sl_vivo):
                sl_vivo = nivel

        # 5. Objetivo final.
        r_tp = abs(tp - entrada) / dist
        if mejor >= r_tp and restante > 0:
            r_acum += restante * r_tp
            return {"estado": "ganancia", "r": round(r_acum, 3),
                    "salida": str(v["dt"]), "esquema": esq.nombre,
                    "parciales": len(hecho)}

        if restante <= 0.001:
            return {"estado": "ganancia", "r": round(r_acum, 3),
                    "salida": str(v["dt"]), "esquema": esq.nombre,
                    "parciales": len(hecho)}

    return {"estado": "abierta", "r": round(r_acum, 3), "esquema": esq.nombre,
            "parciales": len(hecho)}


def anadir(riesgo_inicial: float, posiciones: list[dict], nuevo_sl: float,
           entrada_nueva: float, dist_nueva: float) -> str:
    """
    Piramidar: la regla del capitulo 13.5 y su prohibicion.

    «Anadir volumen a una posicion GANADORA tras un nuevo BOS a favor,
    moviendo el stop total para que el riesgo combinado NUNCA supere el
    riesgo inicial. NUNCA promediar a la baja (anadir a una posicion
    perdedora): es la via mas rapida para destruir una cuenta.»

    Devuelve el motivo del rechazo, o "" si se permite.
    """
    if not posiciones:
        return "no hay posicion a la que anadir"
    if any(p.get("r_actual", 0) <= 0 for p in posiciones):
        return ("hay posicion en perdida: anadir seria promediar a la baja, "
                "que la guia prohibe explicitamente")
    riesgo_total = sum(p.get("riesgo_pct", 0) for p in posiciones)
    # Con el stop movido, la parte vieja puede ya no arriesgar nada.
    riesgo_viejo = sum(p.get("riesgo_pct", 0) for p in posiciones
                       if not p.get("en_be"))
    if riesgo_viejo + dist_nueva / max(riesgo_inicial, 1e-9) > riesgo_inicial:
        return (f"el riesgo combinado ({riesgo_total:.2f}%) superaria el "
                f"riesgo inicial ({riesgo_inicial:.2f}%)")
    return ""
