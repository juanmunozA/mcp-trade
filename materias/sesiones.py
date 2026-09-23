"""
Cuando se puede operar y que niveles hay que tener marcados.

DE DONDE SALE
─────────────
Capitulo 10 de la guia: sesiones, killzones y niveles de referencia
diarios. Las horas estan en hora de Nueva York (ET) porque asi las da la
guia y porque es la referencia del mercado; la conversion a Colombia se
calcula sola a partir del horario de verano.

POR QUE ESTO ES UN FILTRO Y NO UN ADORNO
"Fuera de ellas el precio suele lateralizar" (cap. 10). La estrategia
del capitulo 20.2 no dice "preferentemente en killzone": dice SOLO
London Killzone y NY AM. Un setup perfecto a las 13:00 ET no se opera.
Por eso esto va antes que el analisis: si no estamos en ventana, no hay
nada que analizar.

LA TRAMPA DE LA ZONA HORARIA
La guia advierte explicitamente que verifiques en que zona muestra el
grafico tu broker, porque marcar las killzones corridas una hora las
vuelve inutiles. Aca los datos llegan con zona `America/New_York` desde
el origen, asi que no hay conversion manual que equivocar -- pero se
deja el chequeo escrito para que nadie lo "simplifique" despues.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class Ventana:
    """Una franja horaria en hora de Nueva York."""
    nombre: str
    desde: time
    hasta: time
    que_esperar: str

    def contiene(self, t: datetime) -> bool:
        h = t.time()
        if self.desde <= self.hasta:
            return self.desde <= h < self.hasta
        # Cruza medianoche (el rango de Asia).
        return h >= self.desde or h < self.hasta


SESIONES = [
    Ventana("asia", time(19, 0), time(4, 0),
            "Rango estrecho; forma la liquidez que se barrera despues"),
    Ventana("londres", time(3, 0), time(12, 0),
            "Alta liquidez; frecuente Judas swing"),
    Ventana("nueva_york", time(8, 0), time(17, 0),
            "Noticias 08:30 y 10:00; apertura COMEX 08:20"),
    Ventana("solape_londres_ny", time(8, 0), time(12, 0),
            "La franja de mayor volumen del dia en oro y petroleo"),
]

KILLZONES = [
    Ventana("asian_kz", time(20, 0), time(0, 0),
            "Formacion del rango de Asia"),
    Ventana("london_kz", time(2, 0), time(5, 0),
            "Barrida del rango asiatico; a menudo el maximo o minimo del dia"),
    Ventana("ny_am_kz", time(7, 0), time(10, 0),
            "Continuacion o reversion de Londres; la mas operada en oro"),
    Ventana("london_close", time(10, 0), time(12, 0),
            "Toma de beneficios; retrocesos del movimiento del dia"),
    Ventana("ny_pm", time(13, 30), time(16, 0),
            "Movimientos secundarios; reacciones a FOMC"),
]

SILVER_BULLET = [
    Ventana("sb_londres", time(3, 0), time(4, 0), "Silver Bullet de Londres"),
    Ventana("sb_ny_am", time(10, 0), time(11, 0), "Silver Bullet principal"),
    Ventana("sb_ny_pm", time(14, 0), time(15, 0), "Silver Bullet de la tarde"),
]

OPERABLES = ("london_kz", "ny_am_kz", "london_close", "ny_pm")
"""
Las ventanas donde se permite entrar.

Empezo siendo solo dos -- London Killzone y NY AM -- porque son las dos
que la estrategia del capitulo 20.2 autoriza. Pero el capitulo 11 tiene
ocho modelos y no todos viven ahi: el Silver Bullet opera 10:00-11:00 y
14:00-15:00 (cap. 11.5), que caen en London Close y NY PM. Dejar esas
dos fuera hacia imposible implementar el modelo mas mecanico de la guia.

Lo que sigue fuera es `asian_kz`. No es un olvido: durante Asia el precio
esta FORMANDO el rango que despues se barre (cap. 5.2), no reaccionando
a el. Operar ahi es apostar dentro de la acumulacion, que es justo lo que
los otros modelos esperan a que termine.

Cada modelo declara ademas su propia restriccion horaria: el Judas solo
corre en London KZ y el Silver Bullet solo dentro de su hora exacta. Esta
tupla es el permiso general; el modelo pone el permiso fino.
"""


def en_ventanas(t: datetime, cuales: list[Ventana]) -> list[str]:
    return [v.nombre for v in cuales if v.contiene(t)]


def estado(t: datetime) -> dict:
    """Donde estamos parados: sesion, killzone y si se puede operar."""
    kz = en_ventanas(t, KILLZONES)
    return {
        "hora_ny": t.strftime("%H:%M"),
        "sesiones": en_ventanas(t, SESIONES),
        "killzones": kz,
        "silver_bullet": en_ventanas(t, SILVER_BULLET),
        "operable": any(k in OPERABLES for k in kz),
        "ventana_operable": next((k for k in kz if k in OPERABLES), ""),
    }


def _entre(velas: list[dict], desde: time, hasta: time,
           dia: datetime) -> list[dict]:
    """Las velas de un dia dentro de una franja horaria."""
    out = []
    for v in velas:
        t = v["dt"]
        if t.date() != dia.date():
            # El rango de Asia empieza el dia anterior por la noche.
            if not (desde > hasta and t.date() == dia.date()
                    and t.time() >= desde):
                if not (desde > hasta
                        and (t.date() - dia.date()).days == -1
                        and t.time() >= desde):
                    continue
        h = t.time()
        dentro = (desde <= h < hasta) if desde <= hasta else (h >= desde or h < hasta)
        if dentro:
            out.append(v)
    return out


def rango_asia(velas: list[dict], ahora: datetime) -> dict:
    """
    Maximo y minimo del rango asiatico (cap. 5.2).

    La guia lo marca como obligatorio: "en oro, la sesion asiatica suele
    formar un rango estrecho que Londres o Nueva York barren despues.
    Marcar su maximo y minimo es obligatorio."

    Se toma 19:00-04:00 ET, que es la sesion de Asia completa.
    """
    sel = [v for v in velas
           if v["dt"].time() >= time(19, 0) or v["dt"].time() < time(4, 0)]
    # Solo el bloque mas reciente: el de la noche que acaba de pasar.
    if not sel:
        return {}
    corte = ahora.replace(hour=4, minute=0, second=0, microsecond=0)
    if ahora.time() < time(4, 0):
        corte = corte.replace(day=ahora.day)
    recientes = [v for v in sel if v["dt"] <= corte
                 and (corte - v["dt"]).total_seconds() <= 10 * 3600]
    if not recientes:
        recientes = sel[-100:]
    return {"alto": max(v["h"] for v in recientes),
            "bajo": min(v["l"] for v in recientes),
            "velas": len(recientes),
            "desde": str(recientes[0]["dt"]), "hasta": str(recientes[-1]["dt"])}


def niveles_diarios(diario: list[dict], semanal: list[dict] | None = None) -> dict:
    """
    Los niveles que la guia obliga a marcar (cap. 10.3 y 6.1).

    PDH/PDL son "nivel de referencia universal" y PWH/PWL "de mayor
    peso". Son los objetivos tipicos de barrida y los destinos del
    "draw on liquidity" que hay que preguntarse cada dia.
    """
    out = {}
    if len(diario) >= 2:
        ayer = diario[-2]
        out["pdh"] = round(float(ayer["h"]), 4)
        out["pdl"] = round(float(ayer["l"]), 4)
        out["pd_equilibrio"] = round((out["pdh"] + out["pdl"]) / 2, 4)
    if semanal and len(semanal) >= 2:
        sem = semanal[-2]
        out["pwh"] = round(float(sem["h"]), 4)
        out["pwl"] = round(float(sem["l"]), 4)
    return out


def true_day_open(velas: list[dict], ahora: datetime) -> float | None:
    """
    La apertura de medianoche de Nueva York (cap. 10.3).

    ICT la llama "True Day Open": por encima es premium del dia y por
    debajo discount. Es distinta de la apertura de las 18:00 y de la de
    las 00:00 de otros husos, y esa distincion es justamente el punto.
    """
    hoy = ahora.date()
    for v in velas:
        t = v["dt"]
        if t.date() == hoy and t.time() >= time(0, 0):
            return round(float(v["o"]), 4)
    return None


def proxima_operable(t: datetime) -> str:
    """Cuanto falta para la proxima ventana habilitada, para el log."""
    h = t.time()
    for v in KILLZONES:
        if v.nombre in OPERABLES and h < v.desde:
            faltan = (v.desde.hour * 60 + v.desde.minute) - (h.hour * 60 + h.minute)
            return f"{v.nombre} en {faltan // 60}h{faltan % 60:02d}m"
    return "london_kz manana"
