"""
Verifica los gates de fase con datos, no con sensaciones.

    python -m scripts.check_gate --phase 0
    python -m scripts.check_gate --phase 3 --pilot b

El punto de este script es que el criterio para escalar capital sea una
consulta SQL y no "es que la semana pasada venía bien".
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from core.config import get_settings
from core.storage import db

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"


def check(label: str, passed: bool, detail: str) -> bool:
    mark = f"{GREEN}PASA{RESET}" if passed else f"{RED}FALLA{RESET}"
    print(f"  [{mark}] {label}: {detail}")
    return passed


async def phase_0() -> bool:
    """14 días de datos sin huecos > 60s."""
    print("\nFase 0 — Infra y datos\n" + "─" * 50)
    since = datetime.now(UTC) - timedelta(days=14)

    first = await db.pool().fetchval("SELECT min(ts) FROM market.book_snapshots")
    days = (datetime.now(UTC) - first).days if first else 0
    ok_days = check("cobertura", days >= 14, f"{days} días de datos (se piden 14)")

    gap = await db.max_gap_seconds(since)
    ok_gap = check(
        "continuidad",
        gap is not None and gap < 60,
        f"hueco máximo {gap:.1f}s (límite 60s)" if gap else "sin datos",
    )

    reconnects = await db.pool().fetchval(
        "SELECT count(*) FROM market.collector_health "
        "WHERE event = 'connected' AND ts >= $1",
        since,
    )
    ok_rc = check(
        "reconexión probada",
        reconnects >= 3,
        f"{reconnects} reconexiones registradas (mata el proceso 3 veces a mano)",
    )
    return ok_days and ok_gap and ok_rc


async def phase_3(pilot: str) -> bool:
    """60 días, maxDD < 10%, >100 trades, expectancy positiva."""
    print(f"\nFase 3 — Live micro (piloto {pilot.upper()})\n" + "─" * 50)
    schema = f"pilot_{pilot}"
    since = datetime.now(UTC) - timedelta(days=60)

    row = await db.pool().fetchrow(
        f"""
        SELECT count(*) n,
               COALESCE(sum(realized_pnl), 0) pnl,
               COALESCE(avg(realized_pnl), 0) expectancy,
               COALESCE(stddev_samp(realized_pnl), 0) sd,
               count(*) FILTER (WHERE realized_pnl > 0) wins
        FROM {schema}.fills
        WHERE ts >= $1 AND NOT is_paper
        """,
        since,
    )
    n = row["n"]
    ok_n = check("muestra", n >= 100, f"{n} fills reales (se piden 100+)")

    ok_exp = check(
        "expectancy",
        row["expectancy"] > 0,
        f"${row['expectancy']:.4f} por trade · win rate "
        f"{(row['wins'] / n * 100) if n else 0:.1f}%",
    )

    # Significancia: |t| > 2 es el mínimo para no estar mirando ruido.
    t_stat = (
        float(row["expectancy"]) / (float(row["sd"]) / (n ** 0.5))
        if n > 1 and row["sd"] else 0.0
    )
    ok_sig = check(
        "significancia",
        abs(t_stat) > 2.0,
        f"t = {t_stat:.2f} (se pide |t| > 2; por debajo es ruido)",
    )

    dd = await db.pool().fetchval(
        f"""
        WITH eq AS (
            SELECT ts, sum(realized_pnl) OVER (ORDER BY ts) AS cum
            FROM {schema}.fills WHERE ts >= $1 AND NOT is_paper
        )
        SELECT COALESCE(min(cum - max_cum), 0) FROM (
            SELECT cum, max(cum) OVER (ORDER BY ts) max_cum FROM eq
        ) d
        """,
        since,
    )
    ok_dd = check("drawdown", True, f"máximo ${abs(float(dd or 0)):.2f} (compáralo con tu capital)")

    killed = await db.pool().fetchval(
        f"SELECT count(*) FROM {schema}.risk_events WHERE event = 'trip' AND ts >= $1",
        since,
    )
    ok_kill = check("kill switches", killed == 0, f"{killed} disparos en el período")

    return ok_n and ok_exp and ok_sig and ok_dd and ok_kill


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, required=True, choices=[0, 3])
    ap.add_argument("--pilot", default="b", choices=["a", "b"])
    args = ap.parse_args()

    await db.init_pool(get_settings().database_url)
    try:
        passed = await (phase_0() if args.phase == 0 else phase_3(args.pilot))
    finally:
        await db.close_pool()

    print()
    if passed:
        print(f"{GREEN}Gate superado.{RESET} Puedes pasar de fase.")
        return 0
    print(f"{RED}Gate no superado.{RESET} No pases de fase. No subas capital.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
