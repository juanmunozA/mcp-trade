"""
La forma que tiene una operacion propuesta, o el motivo por el que no la hay.

POR QUE ESTA SUELTA EN SU PROPIO ARCHIVO
────────────────────────────────────────
Los nueve modelos -- los ocho del capitulo 11 mas la estrategia armada
del 20.2 -- tienen que devolver exactamente lo mismo para que el
backtest, el motor en vivo y el registro del capitulo 19 puedan tratarlos
igual sin preguntar de cual vinieron. Si cada uno trajera su propio tipo,
comparar el rendimiento entre modelos -- que es justo lo que el capitulo
19.1 pide registrar -- obligaria a traducir entre formas.

EL CAMPO `motivo` NO ES DECORATIVO
El checklist del capitulo 11.9 termina en "si alguna es no -> no hay
operacion". Cada NO deja escrito cual fue, y contar esos motivos es lo
que revela si el sistema rechaza por donde deberia. Un embudo que muere
95% en el filtro de stop no tiene un problema de entrada: tiene un
problema de stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Senal:
    simbolo: str
    setup: str = ""
    """Que modelo la produjo: '20.2', '11.1', '11.5'... Para medir cual sirve."""
    hay: bool = False
    motivo: str = ""
    lado: str = ""             # 'compra' | 'venta'
    entrada: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    rr: float = 0.0
    ventana: str = ""
    sesgo: str = ""
    nivel_barrido: float = 0.0
    nombre_nivel: str = ""
    zona: dict = field(default_factory=dict)
    """La zona de entrada: FVG, OB, IFVG o breaker, con sus bordes."""
    fvg: dict = field(default_factory=dict)
    """Alias historico de `zona` cuando la zona es un FVG."""
    confluencias: list[str] = field(default_factory=list)
    calidad: float = 0.0
    """Puntaje de confluencia (cap. 11.9); se calcula en `calidad.py`."""
    expira: str = ""
    pasos: list[str] = field(default_factory=list)

    def dict(self) -> dict:
        return self.__dict__.copy()
