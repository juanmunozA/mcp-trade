"""
El vigilante: mira el oro en cada cierre de vela de 5M y avisa. SOLO AVISA.

No abre, no cierra ni modifica operaciones: usa `terminal.py`, que no tiene
ninguna funcion de ordenes. Esta para que la entrada llegue ANTES de que el
movimiento se haya ido, sin que nadie tenga que estar preguntando.

Avisa, con las velas ya cerradas:

  ENTRADA   señal del UT Bot elegido (15M, a=2, ATR 10, Heikin Ashi) que
            pasa el filtro de la EMA 200. Es lo unico con ventaja medida
            (ver `utbot.ELEGIDO`), y su salida es la señal contraria.
  SALIDA    ese mismo bot se da la vuelta: el sistema saldria. Es un
            aviso; cerrar lo decide y lo hace el usuario.
  REBOTE    una vela toca el extremo de 2 h y cumple una regla que el
            investigador adopto (validada en datos no vistos). Si no hay
            reglas adoptadas, de esto no avisa: no inventa.
  5M/15M    señal del UT de 5M a favor del de 15M: aviso suave, con su
            medida al lado (con stop y objetivo fijos no gana).
  En el log quedan ademas las señales de 5M en contra de 15M.

Avisos por `avisar.py`: Telegram si esta configurado, globo de Windows,
pitido y state/red/avisos.log. Una sola copia a la vez (vigilante.pid).

    python -m materias.red.vigilante            # se queda corriendo
    python -m materias.red.vigilante --una      # una pasada y sale (prueba)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np

from materias.red import investigador as I
from materias.red import terminal
from materias.red import utbot as U
from materias.red.avisar import _log, avisar

PID = Path(__file__).resolve().parents[2] / "state" / "red" / "vigilante.pid"
NOM = {1: "COMPRA", -1: "VENTA"}


def _ut(v: np.ndarray) -> dict:
    b = {"o": v["open"], "h": v["high"], "l": v["low"], "c": v["close"]}
    a = U.atr(b["h"], b["l"], b["c"], 14)
    src, st, co, ve = U.utbot(b, 1.0, 14, False)
    return {"lado": 1 if src[-1] > st[-1] else -1, "nueva": 1 if co[-1] else -1 if ve[-1] else 0,
            "atr": float(a[-1]), "c": float(b["c"][-1])}


def mirar(m5: np.ndarray, m15: np.ndarray, m15_nueva: bool) -> list[tuple[str, str, bool | None]]:
    """Avisos (titulo, cuerpo, fuerte) de la ultima vela cerrada de 5M. fuerte None = solo log."""
    out = []
    u5, u15 = _ut(m5), _ut(m15)
    c, a = u5["c"], u5["atr"]
    d = I.datos()
    if m15_nueva:
        e = U.estado({"t": m15["time"], "o": m15["open"], "h": m15["high"], "l": m15["low"], "c": m15["close"]})
        u = e and e["ultima"]
        if u and u["velas_atras"] == 0:
            E = U.ELEGIDO
            if u["pasa_filtro"]:
                out.append((f"ENTRADA {NOM[u['lado']]} XAUUSD {u['precio']:.2f}",
                            f"UT 15M elegido, a favor de la EMA 200. Linea {e['linea']:.2f} (ahi se gira). Salida: "
                            f"señal contraria. Medido: {E['prueba']}.", True))
            else:
                out.append((f"SALIDA / giro a {NOM[u['lado']]} XAUUSD {u['precio']:.2f}",
                            f"UT 15M elegido se da la vuelta, contra la EMA 200: el sistema sale de "
                            f"{NOM[-u['lado']]} y no entra. Linea {e['linea']:.2f}.", True))
    s = u5["nueva"]
    if s and s == u15["lado"]:
        f = d.get("regla_fija", {}).get("a_favor", {})
        out.append((f"XAUUSD: 5M da {NOM[s]} a favor de 15M ({c:.2f})",
                    f"Stop {c - s * 2 * a:.2f} · niveles {c + s * 2 * a:.2f} y {c + s * 3 * a:.2f} (ATR {a:.1f}). "
                    f"Medida con salida fija: {f.get('r', 0):+.2f} R (n={f.get('n', 0)}): momento, no setup.", False))
    elif s:
        t = d.get("tamano_rebote", {}).get("M5", {})
        out.append((f"XAUUSD: 5M da {NOM[s]} en contra de 15M ({c:.2f})",
                    f"mediana del movimiento contrario {t.get('p50', 3.1)} ATR = {c + s * t.get('p50', 3.1) * a:.2f}", None))
    R = I.rasgos(m5)
    if len(R["idx"]) and R["idx"][-1] == len(m5) - 1:
        k, lado = len(R["idx"]) - 1, int(R["lado"][-1])
        for x in I.cumple(R["F"], k)[:1]:
            out.append((f"REBOTE {NOM[lado]} XAUUSD {c:.2f}",
                        f"{I.nombre(x)}. Validada: {x['r_val']:+.2f} R, {x['p_val']:.0f} % ganan (n={x['n_val']}). "
                        f"Stop {c - lado * x['stop_atr'] * a:.2f} · objetivo {c + lado * x['obj_atr'] * a:.2f}", True))
    return out


def _unico() -> bool:
    PID.parent.mkdir(parents=True, exist_ok=True)
    try:
        viejo = int(PID.read_text())
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, viejo)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return False
    except (OSError, ValueError):
        pass
    PID.write_text(str(os.getpid()))
    return True


def correr(una: bool = False) -> None:
    if not una and not _unico():
        print("ya hay un vigilante corriendo")
        return
    mt5, ult5, ult15, fallos = None, 0, 0, 0
    _log("vigilante: arranca")
    while True:
        try:
            if mt5 is None:
                mt5 = terminal.conectar()
            m5, m15 = terminal.velas(mt5, "M5", 900), terminal.velas(mt5, "M15", 600)
            t5, t15 = int(m5["time"][-1]), int(m15["time"][-1])
            if t5 != ult5:
                # la primera pasada solo toma referencia: no avisa de una vela vieja
                if ult5 or una:
                    for titulo, cuerpo, fuerte in mirar(m5, m15, t15 != ult15):
                        if fuerte is None:
                            _log(f"{titulo} | {cuerpo}")
                        else:
                            avisar(titulo, cuerpo, fuerte)
                        print(titulo, "|", cuerpo, flush=True)
                ult5, ult15 = t5, t15
            fallos = 0
        except Exception as exc:  # noqa: BLE001
            fallos += 1
            _log(f"vigilante: {type(exc).__name__}: {exc}")
            if fallos == 5:
                avisar("MCP Trade: el vigilante no ve el mercado", f"{type(exc).__name__}: {exc}")
            try:
                import MetaTrader5
                MetaTrader5.shutdown()
            except Exception:  # noqa: BLE001
                pass
            mt5 = None
            time.sleep(60)
        if una:
            return
        # hasta 4 s despues del proximo cierre de 5M; con el mercado cerrado, la vela no cambia y no avisa
        time.sleep(max(5.0, 300 - time.time() % 300 + 4))


if __name__ == "__main__":
    correr(una="--una" in sys.argv)
