"""
El paso semanal que hace que la parte numerica aprenda sola.

1. Baja de MT5 las velas de 5M que hayan salido desde la ultima vez.
2. Rehace la tabla de pips (ultimos 365 dias), las lecciones de entrada,
   las reacciones en vacios y extremos de serie y cuanto llega un rebote
   (desde 2022). La tabla del rebote se copia al MT5 y se recompila el UT Bot.
3. Si cambiaron, las sube a GitHub; Render las recoge en el siguiente
   despliegue y el agente las lee en cada decision.

Lo que NO hace: cambiar reglas a mano ni tocar operaciones. Solo vuelve a
medir. Si una leccion se da la vuelta con mas datos, el agente vera la
nueva, con su n al lado.

Pensado para el Programador de tareas de Windows, el sabado con el
mercado cerrado:  python -m materias.red.evolucionar
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
LOG = RAIZ / "state" / "red" / "evolucionar.log"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")
    print(msg, flush=True)


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def subir(archivos: list[str]) -> None:
    if not _git("status", "--porcelain", *archivos):
        _log("sin cambios que subir")
        return
    _git("add", *archivos)
    _git("-c", "user.name=Juan José Muñoz", "-c", "user.email=juanjosemunoazagudelo@gmail.com",
         "commit", "-m", f"Remedicion semanal de pips y lecciones ({datetime.now():%Y-%m-%d})\n\n"
         "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        _log("commit hecho; sin GITHUB_TOKEN, no se sube")
        return
    url = f"https://x-access-token:{token}@github.com/juanmunozA/mcp-trade.git"
    salida = _git("-c", "credential.helper=", "push", url, "HEAD:main")
    _log("push: " + salida.replace(token, "***")[-200:])


def instalar_mt5() -> None:
    """Copia el UT Bot y su tabla de rebotes a la carpeta de indicadores del MT5 y lo recompila."""
    editor = Path(os.getenv("MT5_RUTA", "")).with_name("MetaEditor64.exe")
    for carpeta in Path(os.getenv("APPDATA", "")).glob("MetaQuotes/Terminal/*/MQL5/Indicators"):
        if not (carpeta / "UT_Bot_Filtrado.mq5").exists():
            continue
        for nombre in ("UT_Bot_Filtrado.mq5", "Rebote_tabla.mqh"):
            shutil.copy2(RAIZ / "materias" / "mt5" / nombre, carpeta / nombre)
        if editor.exists():
            subprocess.run([str(editor), f"/compile:{carpeta / 'UT_Bot_Filtrado.mq5'}",
                            f"/log:{carpeta / 'UT_Bot_Filtrado.log'}"], capture_output=True)
        _log(f"UT Bot con la tabla nueva en {carpeta}")


def main() -> None:
    load_dotenv(RAIZ / ".env")
    from materias.red import consejo, largo_rebote, lecciones, retroceso, zonas
    from materias.red import datos as D
    v = D.descargar("M5")
    D.guardar(v, "M5")
    _log(f"velas 5M: {len(v):,}")
    consejo.exportar()
    lecciones.exportar()
    zonas.ARCHIVO.write_text(__import__("json").dumps(zonas.medir(), indent=1, ensure_ascii=False),
                             encoding="utf-8")
    retroceso.ARCHIVO.write_text(__import__("json").dumps(retroceso.medir(), indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    reb = largo_rebote.medir()
    largo_rebote.ARCHIVO.write_text(__import__("json").dumps(reb, indent=1, ensure_ascii=False), encoding="utf-8")
    largo_rebote.exportar_mqh(reb)
    instalar_mt5()
    g = lecciones.datos()["grupos"]
    for cl in ("corto|todas", "largo|todas"):
        if cl in g:
            m, li = g[cl]["mercado"], g[cl]["limitada"]
            _log(f"{cl}: n={m['n']} mercado {m['r_medio']:+.3f} R · limitada {li['r_medio']:+.3f} R → {g[cl]['mejor']}")
    subir(["materias/red/tabla_pips.json", "materias/red/lecciones.json", "materias/red/zonas.json", "materias/red/retroceso.json",
           "materias/red/largo_rebote.json", "materias/mt5/Rebote_tabla.mqh"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:          # que el log diga por que no aprendio esta semana
        _log(f"ERROR {type(exc).__name__}: {exc}")
        raise
