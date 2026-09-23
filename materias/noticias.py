"""
El filtro de noticias del capitulo 15. Horas en ET.

POR QUE ESTO NO ES OPCIONAL
───────────────────────────
«Las noticias son el motor de fondo de las materias primas. Aunque
operes TECNICAMENTE, debes saber que noticias salen cada dia, porque en
segundos pueden invalidar cualquier estructura, ampliar spreads y generar
slippage.» (cap. 15)

Y el punto 5 del checklist universal (cap. 11.9) lo exige de forma
binaria: «¿Estoy dentro de una killzone y FUERA de la ventana de noticias
de alto impacto?». Es una condicion del checklist, igual que la barrida
o el cambio de estructura.

LA REGLA CONCRETA (cap. 15.4)
«Regla por defecto: no abrir posiciones desde 15 minutos ANTES hasta
15-30 minutos DESPUES de una noticia de alto impacto.»
«Si tienes posicion abierta: protegela (BE o cierre parcial) o cierrala
antes del dato.»

LO QUE ESTE MODULO PUEDE Y NO PUEDE HACER
Puede reproducir las noticias con RECURRENCIA FIJA: las semanales
(jobless claims los jueves, inventarios EIA los miercoles, COT los
viernes) y las que tienen regla de calendario (NFP el primer viernes).
Esas son deterministas y se calculan sin pedirle nada a nadie.

NO puede saber la fecha exacta del CPI de un mes concreto, ni si hay
FOMC esta semana: la guia dice que salen «a mitad de mes» y «8 veces al
ano», que no es una regla programable. Para esas se marca una VENTANA DE
SOSPECHA y se informa, en lugar de inventarse una fecha. El capitulo 15.4
manda revisar un calendario real (Forex Factory, Investing, MT5) cada
dia; cuando haya una fuente conectada, `cargar_calendario()` la recibe y
tiene prioridad sobre lo que se deduce aqui.

Ese limite esta escrito a proposito. Un filtro que afirma con seguridad
la hora de un dato que no conoce es peor que no tener filtro: da
confianza falsa justo en el momento de maximo riesgo.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

ANTES_MIN = 15
"""«Desde 15 minutos antes...»"""

DESPUES_MIN = 30
"""«...hasta 15-30 minutos despues». Se toma el extremo prudente."""


@dataclass(frozen=True)
class Evento:
    nombre: str
    hora: time
    impacto: str          # 'muy_alto' | 'alto' | 'medio_alto' | 'medio'
    activos: tuple[str, ...]
    fuente: str           # 'regla' (deterministe) | 'sospecha' (aproximada)
    nota: str = ""


# ══════════════════════════════════════════════════════════════════════
# Capitulo 15.2 — Calendario de EE.UU. Afecta a todos los metales.
# ══════════════════════════════════════════════════════════════════════

TODOS = ("GC=F", "SI=F", "HG=F", "CL=F", "NG=F")
METALES = ("GC=F", "SI=F", "HG=F")


def _dia_habil_n(d: date, n: int) -> date:
    """El n-esimo dia habil del mes de `d`."""
    x = date(d.year, d.month, 1)
    c = 0
    while True:
        if x.weekday() < 5:
            c += 1
            if c == n:
                return x
        x += timedelta(days=1)


def _primer_viernes(d: date) -> date:
    x = date(d.year, d.month, 1)
    while x.weekday() != 4:
        x += timedelta(days=1)
    return x


def _ultimo_habil(d: date) -> date:
    x = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def eventos_del_dia(d: date) -> list[Evento]:
    """
    Que se publica hoy, segun las tablas 15.2 y 15.3.

    Separa lo que sabe de lo que sospecha. `fuente='regla'` son fechas
    que se deducen del calendario sin ambiguedad; `fuente='sospecha'` son
    ventanas donde la guia dice que el dato suele caer, marcadas para
    prudencia pero sin fingir precision.
    """
    out: list[Evento] = []
    dow = d.weekday()   # 0 lunes ... 4 viernes

    # ── Semanales: deterministas ──────────────────────────────────────
    if dow == 3:
        out.append(Evento("Jobless Claims", time(8, 30), "medio", TODOS, "regla"))
        out.append(Evento("Almacenamiento de gas EIA", time(10, 30), "muy_alto",
                          ("NG=F",), "regla",
                          "el dato semanal del gas; mueve NG en segundos"))
    if dow == 2:
        out.append(Evento("Inventarios de crudo EIA", time(10, 30), "muy_alto",
                          ("CL=F",), "regla",
                          "Weekly Petroleum Status Report"))
    if dow == 1:
        out.append(Evento("Inventarios API", time(16, 30), "medio_alto",
                          ("CL=F",), "regla", "privado; anticipa al EIA"))
    if dow == 4:
        out.append(Evento("Baker Hughes (conteo de plataformas)", time(13, 0),
                          "medio", ("CL=F",), "regla"))
        out.append(Evento("Informe COT de la CFTC", time(15, 30), "medio",
                          TODOS, "regla", "posicionamiento; datos al martes"))

    # ── Mensuales con regla de calendario ─────────────────────────────
    if d == _primer_viernes(d):
        out.append(Evento("NFP (nominas no agricolas)", time(8, 30), "muy_alto",
                          TODOS, "regla",
                          "«empleo fuerte -> dolar/tasas suben -> oro suele bajar»"))
    # «ADP: miercoles antes del NFP».
    if dow == 2 and (_primer_viernes(d) - d).days in (1, 2):
        out.append(Evento("ADP empleo privado", time(8, 15), "medio",
                          TODOS, "regla"))
    if d == _dia_habil_n(d, 1) or d == _dia_habil_n(d, 3):
        out.append(Evento("ISM (Manufacturero / Servicios)", time(10, 0),
                          "medio_alto", TODOS, "regla",
                          "«el componente de precios importa para inflacion»"))
    if d == _ultimo_habil(d):
        out.append(Evento("PCE (inflacion preferida por la Fed)", time(8, 30),
                          "alto", TODOS, "sospecha", "«fin de mes»"))

    # ── Mensuales sin dia fijo: ventana de sospecha ───────────────────
    if 10 <= d.day <= 15 and dow < 5:
        out.append(Evento("CPI (inflacion al consumidor)", time(8, 30),
                          "muy_alto", TODOS, "sospecha",
                          "«mitad de mes»; la fecha exacta exige calendario real"))
    if 12 <= d.day <= 17 and dow < 5:
        out.append(Evento("PPI (precios al productor)", time(8, 30),
                          "medio_alto", TODOS, "sospecha"))
        out.append(Evento("Ventas minoristas", time(8, 30), "medio_alto",
                          TODOS, "sospecha"))

    # ── China: cobre y plata (cap. 15.3) ──────────────────────────────
    if d.day <= 3 or d.day >= 29:
        out.append(Evento("PMI de China (oficial y Caixin)", time(21, 45),
                          "medio_alto", ("HG=F", "SI=F"), "sospecha",
                          "«fin/inicio de mes, noche en ET»"))
    return out


# La ventana de FOMC no se deduce: se declara cuando se sabe.
FOMC_CONOCIDOS: set[date] = set()
"""
Fechas de FOMC cargadas a mano o por `cargar_calendario`.

«8 veces al ano, 14:00 ET (rueda de prensa 14:30). Volatilidad extrema
en ambos sentidos.» No hay regla de calendario que las genere: las fija
la Fed con un ano de antelacion y se publican. Mientras esto este vacio,
el filtro NO protege de FOMC, y eso se informa en vez de disimularse.
"""

_EXTERNO: dict[date, list[Evento]] = {}


def cargar_calendario(eventos: list[dict]) -> int:
    """
    Recibe un calendario real y le da prioridad sobre lo deducido.

    Es el punto 1 del capitulo 15.4: «Revisa el calendario (Forex
    Factory, Investing.com, calendario de MT5) cada dia antes de empezar;
    filtra por USD e impacto alto». Cuando haya una fuente conectada,
    entra por aqui. Cada entrada necesita `fecha` (ISO), `hora` (HH:MM),
    `nombre` e `impacto`.
    """
    n = 0
    for e in eventos:
        try:
            d = date.fromisoformat(e["fecha"])
            h, mi = (int(x) for x in e["hora"].split(":")[:2])
        except Exception:  # noqa: BLE001
            continue
        ev = Evento(e["nombre"], time(h, mi), e.get("impacto", "alto"),
                    tuple(e.get("activos", TODOS)), "calendario",
                    e.get("nota", ""))
        _EXTERNO.setdefault(d, []).append(ev)
        if "FOMC" in e["nombre"].upper():
            FOMC_CONOCIDOS.add(d)
        n += 1
    return n


def _del_dia(d: date) -> list[Evento]:
    if d in _EXTERNO:
        # El calendario real reemplaza lo deducido para ese dia.
        return _EXTERNO[d]
    ev = eventos_del_dia(d)
    if d in FOMC_CONOCIDOS:
        ev.append(Evento("FOMC (decision de tasas)", time(14, 0), "muy_alto",
                         TODOS, "regla", "rueda de prensa 14:30"))
    return ev


def bloqueo(ahora: datetime, simbolo: str,
            solo_muy_alto: bool = False) -> str:
    """
    Si hay noticia dentro de la ventana prohibida, dice cual.

    «No abrir posiciones desde 15 minutos antes hasta 15-30 minutos
    despues de una noticia de alto impacto» (cap. 15.4). Devuelve el
    motivo del bloqueo, o "" si se puede operar.

    `solo_muy_alto` restringe el filtro a NFP, CPI, FOMC e inventarios:
    util para medir cuanto cuesta el filtro completo contra el minimo.
    """
    niveles = ("muy_alto",) if solo_muy_alto else ("muy_alto", "alto", "medio_alto")
    for d in (ahora.date() - timedelta(days=1), ahora.date()):
        for ev in _del_dia(d):
            if ev.impacto not in niveles:
                continue
            if simbolo not in ev.activos:
                continue
            t = datetime.combine(d, ev.hora, tzinfo=ahora.tzinfo)
            desde = t - timedelta(minutes=ANTES_MIN)
            hasta = t + timedelta(minutes=DESPUES_MIN)
            if desde <= ahora <= hasta:
                cual = "confirmada" if ev.fuente in ("regla", "calendario") else "probable"
                return (f"{ev.nombre} a las {ev.hora:%H:%M} ET ({cual}): "
                        f"ventana de {ANTES_MIN} min antes a {DESPUES_MIN} despues")
    return ""


def proteger(ahora: datetime, simbolo: str) -> str:
    """
    Aviso para posiciones YA abiertas (cap. 15.4, punto 3).

    «Si tienes posicion abierta: protegela (BE o cierre parcial) o
    cierrala antes del dato.» Se avisa con una hora de antelacion, que da
    margen para mover el stop sin hacerlo en el minuto del dato -- cuando
    «spreads y slippage se multiplican».
    """
    for ev in _del_dia(ahora.date()):
        if ev.impacto not in ("muy_alto", "alto") or simbolo not in ev.activos:
            continue
        t = datetime.combine(ahora.date(), ev.hora, tzinfo=ahora.tzinfo)
        faltan = (t - ahora).total_seconds() / 60
        if 0 < faltan <= 60:
            return (f"{ev.nombre} en {int(faltan)} min: mover a BE o cerrar "
                    "parcial antes del dato")
    return ""


def agenda(d: date) -> list[str]:
    """La lista del dia, para el log de la manana."""
    ev = sorted(_del_dia(d), key=lambda e: e.hora)
    if not ev:
        return ["sin noticias de impacto conocidas para hoy"]
    out = []
    for e in ev:
        marca = "" if e.fuente in ("regla", "calendario") else "  (probable)"
        act = "todos" if set(e.activos) == set(TODOS) else ",".join(e.activos)
        out.append(f"  {e.hora:%H:%M}  {e.impacto:11} {e.nombre}  [{act}]{marca}")
    if d not in FOMC_CONOCIDOS and not _EXTERNO.get(d):
        out.append("  AVISO: las fechas de FOMC no se deducen del calendario. "
                   "Sin cargarlas, este filtro no protege de FOMC.")
    return out


def filtro_202(ahora: datetime, simbolo: str) -> str:
    """
    El filtro de noticias EXACTO de la estrategia del capitulo 20.2.

    «Sin nuevas entradas de 08:15 a 09:00 ET en dias de NFP/CPI; no
    operar dia de FOMC despues de 13:30 ET.»

    Es mas estricto y mas concreto que la regla general de 15/30 minutos:
    cubre una ventana entera de 45 minutos en los dos datos que mas mueven
    a los metales, y el dia entero de FOMC a partir de la tarde. Se aplica
    SOLO al modelo 20.2, porque es su regla, no la del capitulo 15.
    """
    d = ahora.date()
    nombres = {e.nombre.split()[0] for e in _del_dia(d) if simbolo in e.activos}
    if ("NFP" in nombres or "CPI" in nombres) and time(8, 15) <= ahora.time() < time(9, 0):
        cual = "NFP" if "NFP" in nombres else "CPI"
        return f"dia de {cual}: sin entradas nuevas de 08:15 a 09:00 ET"
    if d in FOMC_CONOCIDOS and ahora.time() >= time(13, 30):
        return "dia de FOMC despues de 13:30 ET: no se opera"
    return ""
