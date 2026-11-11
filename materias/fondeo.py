"""Cuenta de fondeo (FundedNext) en su propio MT5 portable: SOLO LECTURA.

Nunca abre ni cierra operaciones. Usa la segunda copia de MT5
(`FONDEO_RUTA`, arrancada con /portable) para no tocar el terminal de
Tickmill. Credenciales en el .env: FONDEO_LOGIN, FONDEO_CLAVE, FONDEO_SERVIDOR.
Reglas en el .env (las de tu panel de FundedNext):
FONDEO_SALDO_INICIAL, FONDEO_PERDIDA_DIARIA_PCT (5), FONDEO_PERDIDA_TOTAL_PCT (10).

    .venv/Scripts/python.exe -m materias.fondeo
"""
import datetime as dt
import os
import sys
from pathlib import Path

import MetaTrader5 as mt5
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
RAPIDA = 30          # Quick Strike: operaciones de menos de 30 s
TOPE_RAPIDA = 0.30   # ... no pueden pasar del 30 % del beneficio total


def conectar() -> dict:
    load_dotenv(RAIZ / ".env")
    ruta = os.getenv("FONDEO_RUTA", "")
    if not ruta or not Path(ruta).exists():
        sys.exit(f"FONDEO_RUTA no apunta a un terminal64.exe: {ruta!r}")
    args: dict = {"path": ruta, "portable": True}
    login, clave, servidor = os.getenv("FONDEO_LOGIN"), os.getenv("FONDEO_CLAVE"), os.getenv("FONDEO_SERVIDOR")
    if login and clave and servidor:
        args |= {"login": int(login), "password": clave, "server": servidor}
    if not mt5.initialize(**args):
        sys.exit(f"no se pudo conectar al MT5 de fondeo: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None:
        sys.exit("el MT5 de fondeo abrio pero no hay cuenta: entra con Archivo -> Conectarse a una cuenta comercial")
    return info._asdict()


def resumen() -> None:
    cu = conectar()
    try:
        tk = mt5.symbol_info_tick("XAUUSD")
        # el dia del servidor empieza a las 00:00 de su reloj (17:00 NY)
        t_srv = tk.time if tk else int(dt.datetime.now().timestamp())
        # se filtra por d.time (reloj del servidor): pedir el rango con fechas sin zona lo desplaza la hora del PC
        todos = mt5.history_deals_get(dt.datetime(2020, 1, 1), dt.datetime(2030, 1, 1)) or []
        hoy = [d for d in todos if d.time >= t_srv - t_srv % 86400]
        pos = mt5.positions_get() or []
    finally:
        mt5.shutdown()

    def neto(d) -> float:
        return d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)

    cerrado_hoy = sum(neto(d) for d in hoy if d.entry in (1, 2, 3))
    flotante = sum(p.profit + p.swap for p in pos)
    saldo_dia = cu["balance"] - cerrado_hoy
    inicial = float(os.getenv("FONDEO_SALDO_INICIAL") or saldo_dia)
    tope_dia = inicial * float(os.getenv("FONDEO_PERDIDA_DIARIA_PCT") or 5) / 100
    tope_total = inicial * float(os.getenv("FONDEO_PERDIDA_TOTAL_PCT") or 10) / 100
    hoy_pl = cerrado_hoy + flotante

    print(f"cuenta {cu['login']} · {cu['server']} · {'DEMO' if cu['trade_mode'] == 0 else 'REAL/FONDEO'} · "
          f"balance {cu['balance']:.2f} · equity {cu['equity']:.2f} {cu['currency']} · 1:{cu['leverage']}")
    print(f"hoy: cerrado {cerrado_hoy:+.2f} · flotante {flotante:+.2f} · total {hoy_pl:+.2f}")
    # FundedNext: suelo del dia = balance al empezar el dia - % del inicial; suelo total fijo
    suelo_dia = saldo_dia - tope_dia
    print(f"perdida diaria: suelo {suelo_dia:.2f} · margen {cu['equity'] - suelo_dia:.2f}")
    print(f"perdida total: suelo {inicial - tope_total:.2f} · margen {cu['equity'] - (inicial - tope_total):.2f}")
    for p in pos:
        print(f"  abierta #{p.ticket} {'BUY' if p.type == 0 else 'SELL'} {p.volume} {p.symbol} en {p.price_open:.2f} "
              f"SL {p.sl:.2f} TP {p.tp:.2f} · {p.profit:+.2f}")

    # Quick Strike: duracion de cada posicion cerrada (entrada -> salida)
    entradas = {d.position_id: d.time for d in todos if d.entry == 0}
    rapida = total = 0.0
    for d in todos:
        if d.entry in (1, 3) and d.position_id in entradas:
            g = neto(d)
            total += g
            if d.time - entradas[d.position_id] < RAPIDA and g > 0:
                rapida += g
    if total > 0:
        r = rapida / total
        print(f"Quick Strike: {rapida:.2f} de {total:.2f} de beneficio en operaciones < {RAPIDA} s = {100 * r:.0f} % "
              f"({'OK' if r <= TOPE_RAPIDA else 'PASADO DEL 30 %'})")


if __name__ == "__main__":
    resumen()
