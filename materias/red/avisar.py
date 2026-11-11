"""
Avisos al usuario: Telegram si esta configurado, y siempre un globo de
Windows, un pitido y una linea en state/red/avisos.log.

Para el telefono hacen falta TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el
.env (el bot se crea con @BotFather). Sin ellos el aviso solo sale en el PC.
Un aviso que falla nunca tumba a quien lo manda.

    python -m materias.red.avisar "titulo" "cuerpo"
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
LOG = RAIZ / "state" / "red" / "avisos.log"
_GLOBO = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
          "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
          "$n.ShowBalloonTip(15000, $env:AVISO_TITULO, $env:AVISO_CUERPO, 'Info'); Start-Sleep 15; $n.Dispose()")


def telegram(texto: str) -> bool:
    load_dotenv(RAIZ / ".env")
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": texto}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"telegram fallo: {type(exc).__name__}")
        return False


def _log(linea: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {linea}\n")


def avisar(titulo: str, cuerpo: str = "", fuerte: bool = True) -> None:
    """`fuerte`: entrada o fallo (telefono + pitido). Si no, solo globo y log."""
    _log(f"{titulo} | {cuerpo}".replace("\n", " · "))
    if fuerte:
        telegram(f"{titulo}\n{cuerpo}".strip())
    if sys.platform != "win32":
        return
    try:
        if fuerte:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", _GLOBO],
                         env=os.environ | {"AVISO_TITULO": titulo[:60], "AVISO_CUERPO": (cuerpo or " ")[:250]},
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as exc:  # noqa: BLE001
        _log(f"globo fallo: {type(exc).__name__}")


if __name__ == "__main__":
    avisar(*(sys.argv[1:3] or ["MCP Trade", "prueba de aviso"]))
