"""
Arma la entrega: la carpeta que se le pasa a quien va a correr el agente.

    python -m scripts.empaquetar [--zip]

`entrega/` es derivada y está en .gitignore. Se regenera, no se edita.

El .env de desarrollo tiene claves reales. Por eso al final se escanea lo
que quedó y si aparece algo con forma de credencial se aborta.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "entrega"

DIRS = ["core", "pilot_a_llm", "trade", "scripts", "searxng", "vault/contexto"]
ARCHIVOS = ["Dockerfile", "docker-compose.yml"]
GLOBS = ["n8n/*.json"]

EXCLUIR_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git",
    ".venv", "venv", "node_modules", "pgdata", "ollama_data", "n8n_data",
    "state", "entrega",
}
EXCLUIR_SUFIJOS = {".pyc", ".pyo", ".log", ".db", ".sqlite"}
EXCLUIR_ARCHIVOS = {
    "empaquetar.py", "_entrega_plantillas.py", "_entrega_vault.py",
    "check_env.py",      # pregunta por credenciales de nube que aquí no van
    "strategist.py",     # código muerto: nadie lo importa
    # Existía para el disco efímero de Render. En local el vault es un bind
    # mount y no se pierde — y su write_graph_config() pisaría la paleta del
    # vault de la entrega, que tiene tres capas más.
    "backfill_vault.py",
}

PATRONES = [
    (r"AIza[0-9A-Za-z_\-]{30,}", "Google / Gemini"),
    (r"gsk_[A-Za-z0-9]{40,}", "Groq"),
    (r"cfut_[A-Za-z0-9_\-]{30,}", "Cloudflare"),
    (r"napi_[a-z0-9]{40,}", "Neon"),
    (r"rnd_[A-Za-z0-9]{20,}", "Render"),
    (r"github_pat_[A-Za-z0-9_]{50,}", "GitHub PAT"),
    (r"ghp_[A-Za-z0-9]{36,}", "GitHub clásico"),
    (r"sk-ant-[A-Za-z0-9_\-]{40,}", "Anthropic"),
    (r"sk-or-v1-[A-Za-z0-9]{40,}", "OpenRouter"),
    (r"xoxb-[A-Za-z0-9\-]{20,}", "Slack"),
    (r"\b\d{9,10}:AA[A-Za-z0-9_\-]{30,}", "Telegram bot"),
    # Los authtoken de ngrok no tienen prefijo reconocible, así que se
    # atrapan por la variable que los contiene.
    (r"NGROK_AUTHTOKEN\s*=\s*\S{16,}", "ngrok"),
    (r"MISTRAL_API_KEY\s*=\s*\S{16,}", "Mistral"),
    (r"\b2[A-Za-z0-9]{25,}_[A-Za-z0-9]{20,}\b", "ngrok (formato v2)"),
    (r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{64}(?![A-Za-z0-9])", "llave privada"),
    (r"postgres(?:ql)?://[^\s:/@]+:[^\s@]+@[^\s/]+", "URL de base con contraseña"),
]

# Credenciales de juguete del compose local: solo valen contra un postgres
# de la misma máquina.
# Se permiten por cadena EXACTA, nunca aflojando el patrón: así una URL de
# Neon de verdad sigue disparando la alarma.
PERMITIDO = [
    "postgresql://trade:trade@postgres:5432/mcptrade",
    "postgresql://trade:trade@localhost:5432/mcptrade",
    # el ejemplo que sale en .env.example y en el LEEME
    "postgresql://usuario:clave@ep-algo.region.aws.neon.tech/neondb?sslmode=require",
]

BINARIOS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff2"}


def vaciar(d: Path) -> None:
    """
    Borra el contenido, no la carpeta. En Windows, una terminal parada
    dentro de `entrega/` la bloquea y `rmtree` de la raíz revienta.
    """
    for hijo in d.iterdir():
        shutil.rmtree(hijo) if hijo.is_dir() else hijo.unlink()


def copiar(src: Path, dst: Path) -> int:
    n = 0
    for origen in src.rglob("*"):
        if origen.is_dir():
            continue
        if any(p in EXCLUIR_DIRS for p in origen.relative_to(RAIZ).parts):
            continue
        if origen.suffix in EXCLUIR_SUFIJOS or origen.name in EXCLUIR_ARCHIVOS:
            continue
        destino = dst / origen.relative_to(src)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        n += 1
    return n


def escanear(raiz: Path) -> list[str]:
    hallazgos = []
    for f in sorted(raiz.rglob("*")):
        if not f.is_file() or f.suffix.lower() in BINARIOS:
            continue
        texto = f.read_text(encoding="utf-8", errors="ignore")
        for permitido in PERMITIDO:
            texto = texto.replace(permitido, "")
        for patron, quien in PATRONES:
            for m in re.finditer(patron, texto):
                linea = texto[: m.start()].count("\n") + 1
                hallazgos.append(
                    f"{f.relative_to(raiz)}:{linea} — {quien} ({m.group()[:12]}…)"
                )
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(description="Arma la carpeta de entrega.")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    DESTINO.mkdir(parents=True, exist_ok=True)
    vaciar(DESTINO)

    total = 0
    for d in DIRS:
        n = copiar(RAIZ / d, DESTINO / d)
        total += n
        print(f"  {d + '/':20} {n:3}")

    for a in ARCHIVOS:
        shutil.copy2(RAIZ / a, DESTINO / a)
    total += len(ARCHIVOS)

    for patron in GLOBS:
        for f in sorted(RAIZ.glob(patron)):
            destino = DESTINO / Path(patron).parent / f.name
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, destino)
            total += 1

    # Docker crea como root lo que no exista, y después el contenedor no
    # puede escribir dentro.
    # `3-mercados` y `post-mortems` son los nombres que usa Vault.__init__.
    for vacia in ("state", "vault/3-mercados", "vault/post-mortems"):
        (DESTINO / vacia).mkdir(parents=True, exist_ok=True)
        (DESTINO / vacia / ".gitkeep").touch()

    for nombre, contenido in PLANTILLAS.items():
        p = DESTINO / nombre
        p.write_text(contenido, encoding="utf-8")
        if nombre.endswith(".sh"):
            os.chmod(p, 0o755)
        total += 1

    notas = construir_vault(DESTINO / "vault")
    total += notas
    print(f"  {'(generados)':20} {len(PLANTILLAS):3}")
    print(f"  {'vault obsidian':20} {notas:3}")
    print(f"\n  {total} archivos en {DESTINO}")

    if hallazgos := escanear(DESTINO):
        print(f"\n  ABORTADO — {len(hallazgos)} posibles secretos:\n")
        for h in hallazgos:
            print(f"    {h}")
        vaciar(DESTINO)
        return 1
    print("  escaneado: ninguna credencial viaja")

    if args.zip:
        z = RAIZ / "entrega.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(DESTINO.rglob("*")):
                if f.is_file():
                    zf.write(f, Path("mcp-trade") / f.relative_to(DESTINO))
        print(f"  entrega.zip: {z.stat().st_size / 2**20:.1f} MB")

    return 0


from scripts._entrega_plantillas import PLANTILLAS  # noqa: E402
from scripts._entrega_vault import construir as construir_vault  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
