"""
Las velas con las que se entrena la red, bajadas del broker y guardadas.

POR QUE DEL BROKER Y NO DE YAHOO
El instrumento que se opera es el XAUUSD de Tickmill. El futuro de COMEX
cotiza 38-56 dolares por encima y con otro horario; entrenar con el y
operar el otro es aprender un mercado y apostar en otro.

POR QUE EN BLOQUES
MT5 rechaza una peticion que pase de su tope de barras por grafico
(Herramientas > Opciones > Graficos > Max. barras en la ventana; por
defecto 100.000) con un «Invalid params» sin mas explicacion. Se pide en
tramos hacia atras hasta que deja de devolver velas.

POR QUE .npz Y NO PARQUET
pyarrow exige numpy 2, y en Windows con Smart App Control numpy 2 no
carga. El formato propio de numpy no necesita nada mas.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import numpy as np

CARPETA = Path(__file__).resolve().parents[2] / "state" / "red"

PIP = 0.10
"""
Un pip del oro, en dolares de precio.

En XAUUSD el punto de MT5 vale 0,01 y la convencion del mercado es que un
pip son diez puntos. Con 0,01 lotes (una onza) un pip son 0,10 USD de
ganancia o perdida; con 0,10 lotes, un dolar.
"""

_TF = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def _conectar():
    import MetaTrader5 as mt5
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    # «IPC send failed» sale cuando el terminal esta arrancando o colgado: se reintenta
    # antes de rendirse (el 08-11-2026 un solo intento dejo la semana sin medir).
    import time
    for intento in range(4):
        ok = mt5.initialize(path=os.getenv("MT5_RUTA") or None,
                            login=int(os.getenv("MT5_LOGIN") or 0),
                            password=os.getenv("MT5_CLAVE", ""),
                            server=os.getenv("MT5_SERVIDOR", ""), timeout=60000)
        if ok:
            break
        error = mt5.last_error()
        mt5.shutdown()
        if intento < 3:
            time.sleep(30)
    else:
        raise RuntimeError(f"MT5 no conecta tras 4 intentos: {error}")
    if mt5.account_info().trade_mode != 0:
        # Para bajar datos no importa, pero si esto sale es que el .env
        # apunta a otra cuenta de la que se cree.
        print("AVISO: la terminal no esta en una cuenta demo")
    return mt5


def descargar(tf: str = "M5", simbolo: str = "XAUUSD",
              dias_bloque: int | None = None) -> np.ndarray:
    """Todas las velas que el terminal tenga, de la mas vieja a la mas nueva."""
    mt5 = _conectar()
    marco = getattr(mt5, f"TIMEFRAME_{tf}")
    mt5.symbol_select(simbolo, True)
    # Bloques de unas 15.000 velas, holgados respecto al tope de 100.000.
    dias = dias_bloque or max(1, int(15_000 * _TF[tf] / (60 * 24)))
    fin = dt.datetime.now() + dt.timedelta(days=1)
    partes, vacios = [], 0
    # Suelo de fechas: el MT5 de Python revienta con SystemError si la
    # fecha es anterior a 1970, y en H1 los bloques llegan hasta ahi antes
    # de encontrar tres vacios seguidos.
    suelo = dt.datetime(2005, 1, 1)
    while vacios < 3 and fin > suelo:
        ini = max(suelo, fin - dt.timedelta(days=dias))
        r = mt5.copy_rates_range(simbolo, marco, ini, fin)
        if r is None or len(r) == 0:
            vacios += 1          # fines de semana y festivos dejan tramos vacios
        else:
            vacios = 0
            partes.append(r)
        fin = ini
    mt5.shutdown()
    if not partes:
        raise RuntimeError(f"MT5 no devolvio velas de {simbolo} {tf}")
    todo = np.concatenate(partes[::-1])
    # Los bloques se solapan en los bordes: una vela por instante.
    _, unicos = np.unique(todo["time"], return_index=True)
    return todo[np.sort(unicos)]


def guardar(velas: np.ndarray, tf: str, simbolo: str = "XAUUSD") -> Path:
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"{simbolo.lower()}_{tf.lower()}.npz"
    np.savez_compressed(ruta, velas=velas)
    return ruta


COMPLETO_DESDE = dt.datetime(2019, 1, 1, tzinfo=dt.UTC)
"""
Desde cuando el historico de 5M y 1M de Tickmill esta entero.

Medido en la demo con el tope de barras subido a 5.000.000: de 2010 a 2018
hay entre 126 y 486 velas de 5M por ano -- un 0,4 % de lo que deberia --,
y de 2019 en adelante entre el 98,5 y el 99,2 %. Entrenar con los anos
huecos haria que una «ventana de una hora» abarcase semanas.
"""


def cargar(tf: str = "M5", simbolo: str = "XAUUSD", completo: bool = True) -> np.ndarray:
    v = np.load(CARPETA / f"{simbolo.lower()}_{tf.lower()}.npz")["velas"]
    if completo and tf in ("M1", "M5", "M15"):
        v = v[v["time"] >= int(COMPLETO_DESDE.timestamp())]
    return v


if __name__ == "__main__":
    for marco in ("M5", "M1", "H1"):
        v = descargar(marco)
        ruta = guardar(v, marco)
        ini = dt.datetime.fromtimestamp(int(v["time"][0]))
        print(f"{marco}: {len(v):,} velas desde {ini:%Y-%m-%d} -> {ruta.name}")
