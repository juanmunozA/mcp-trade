"""
Qué credenciales están puestas y qué te falta.

    python -m scripts.check_env

Nunca imprime un secreto completo: solo los primeros y últimos caracteres,
lo justo para confirmar que pegaste la correcta. Puedes correrlo con
alguien mirando la pantalla.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
)

# (variable, para qué sirve, dónde se saca)
GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "Base de datos — sin esto no corre nada": [
        ("DATABASE_URL", "toda la persistencia", "Neon Console → Connection string"),
    ],
    "Modelos — el estratega y el triaje": [
        ("GEMINI_API_KEY", "estratega (free tier)", "aistudio.google.com → Get API key"),
        ("CLOUDFLARE_ACCOUNT_ID", "triaje, modelos abiertos", "dash.cloudflare.com → Workers AI"),
        ("CLOUDFLARE_API_TOKEN", "triaje, modelos abiertos", "dash.cloudflare.com → API Tokens"),
        ("OPENROUTER_API_KEY", "2º modelo del ensemble", "openrouter.ai/keys"),
        ("ANTHROPIC_API_KEY", "opcional, 3er modelo", "console.anthropic.com"),
    ],
    "Alertas — kill switch y reporte diario": [
        ("TELEGRAM_BOT_TOKEN", "avisos", "@BotFather en Telegram"),
        ("TELEGRAM_CHAT_ID", "a quién avisar", "@userinfobot en Telegram"),
    ],
    "Infra opcional": [
        ("NEON_API_KEY", "gestionar branches (no necesaria para operar)", "Neon → API keys"),
    ],
    "Wallets — SOLO para fase 3 (live). Déjalas para el final": [
        ("PILOT_A_PRIVATE_KEY", "firmar órdenes del piloto A", "wallet dedicada"),
        ("PILOT_A_FUNDER", "dirección con los fondos de A", "wallet dedicada"),
        ("PILOT_B_PRIVATE_KEY", "firmar órdenes del piloto B", "wallet dedicada, SEPARADA"),
        ("PILOT_B_FUNDER", "dirección con los fondos de B", "wallet dedicada, SEPARADA"),
    ],
}

# Qué se desbloquea con qué
CAPABILITIES: list[tuple[str, list[str]]] = [
    ("Grabar datos de mercado (fase 0)", ["DATABASE_URL"]),
    ("Backtest y replay (fase 1)", ["DATABASE_URL"]),
    ("Piloto A en paper (fase 2)", ["DATABASE_URL", "GEMINI_API_KEY"]),
    ("Piloto B en paper (fase 2)", ["DATABASE_URL"]),
    ("Alertas y reporte diario", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    ("Ensemble multi-modelo", ["GEMINI_API_KEY", "OPENROUTER_API_KEY"]),
    ("Triaje masivo de mercados", ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"]),
    ("Piloto A en LIVE (fase 3)", ["DATABASE_URL", "GEMINI_API_KEY",
                                   "PILOT_A_PRIVATE_KEY", "PILOT_A_FUNDER"]),
    ("Piloto B en LIVE (fase 3)", ["DATABASE_URL",
                                   "PILOT_B_PRIVATE_KEY", "PILOT_B_FUNDER"]),
]


def mask(value: str) -> str:
    """Suficiente para reconocerla, insuficiente para usarla."""
    if len(value) <= 12:
        return value[:2] + "…" + value[-2:]
    return f"{value[:6]}…{value[-4:]}  ({len(value)} chars)"


def main() -> int:
    env_path = Path(".env")
    if not env_path.exists():
        print(f"{RED}No existe .env{RESET}   →  Copy-Item .env.example .env")
        return 1
    load_dotenv(env_path, override=True)

    present: set[str] = set()

    for group, entries in GROUPS.items():
        print(f"\n{group}")
        print("─" * 62)
        for var, purpose, where in entries:
            value = (os.getenv(var) or "").strip()
            if value:
                present.add(var)
                print(f"  {GREEN}✓{RESET} {var:<24} {DIM}{mask(value)}{RESET}")
            else:
                print(f"  {RED}✗{RESET} {var:<24} {DIM}{purpose}{RESET}")
                print(f"    {DIM}└─ {where}{RESET}")

    print("\n\nQué puedes correr hoy")
    print("─" * 62)
    for capability, required in CAPABILITIES:
        missing = [v for v in required if v not in present]
        if not missing:
            print(f"  {GREEN}✓{RESET} {capability}")
        else:
            print(f"  {YELLOW}·{RESET} {DIM}{capability} — falta {', '.join(missing)}{RESET}")

    total = sum(len(v) for v in GROUPS.values())
    print(f"\n{len(present)}/{total} credenciales configuradas\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
