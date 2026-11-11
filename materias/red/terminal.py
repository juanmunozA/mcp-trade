"""
Velas en vivo de MT5, SOLO LECTURA y sin login.

Se conecta al terminal que ya este abierto con la cuenta que tenga puesta:
primero el portable de fondeo (FONDEO_RUTA), si no el de Tickmill
(MT5_RUTA). No pasa usuario ni clave, asi que nunca cambia la cuenta del
terminal, y este modulo no tiene ninguna funcion que mande ordenes.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]


def conectar():
    import MetaTrader5 as mt5
    load_dotenv(RAIZ / ".env")
    errores = []
    for ruta, portable in ((os.getenv("FONDEO_RUTA"), True), (os.getenv("MT5_RUTA"), False)):
        if not ruta or not Path(ruta).exists():
            continue
        if mt5.initialize(path=ruta, portable=portable, timeout=60000):
            if mt5.account_info() is not None and mt5.symbol_select("XAUUSD", True):
                return mt5
            errores.append(f"{Path(ruta).parent.name}: sin cuenta")
        else:
            errores.append(f"{Path(ruta).parent.name}: {mt5.last_error()}")
        mt5.shutdown()
    raise RuntimeError("ningun MT5 disponible: " + "; ".join(errores))


def velas(mt5, tf: str = "M5", n: int = 1500, simbolo: str = "XAUUSD") -> np.ndarray:
    """Las ultimas n velas YA CERRADAS (la que se esta formando se quita)."""
    r = mt5.copy_rates_from_pos(simbolo, getattr(mt5, f"TIMEFRAME_{tf}"), 0, n + 1)
    if r is None or len(r) < 2:
        raise RuntimeError(f"MT5 no devolvio velas de {tf}: {mt5.last_error()}")
    return r[:-1]
