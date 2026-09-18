"""Aplica el schema. Idempotente.   python -m scripts.init_db"""

from __future__ import annotations

import asyncio
import sys

from core.config import get_settings
from core.storage import db


async def main() -> int:
    settings = get_settings()
    print(f"aplicando schema en {settings.database_url.split('@')[-1]} ...")
    try:
        await db.apply_schema(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        print(f"FALLÓ: {exc}", file=sys.stderr)
        print("¿corriste `docker compose up -d`?", file=sys.stderr)
        return 1
    print("schema aplicado: market, pilot_a, pilot_b")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
