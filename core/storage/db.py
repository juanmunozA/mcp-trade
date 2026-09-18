"""Pool de Postgres + repositorios. Nada de ORM: son 10 queries, no un framework."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg

from core.risk import OrderIntent, Verdict

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """
    Pool persistente. Preferir siempre el endpoint DIRECTO de Neon.

    Si alguien apunta al `-pooler` (PgBouncer en modo transacción), asyncpg
    revienta con «prepared statement already exists» porque reutiliza
    statements entre conexiones que el pooler baraja. Desactivar la caché lo
    evita, a costa de re-planificar cada query: por eso es un parche, no el
    camino recomendado.
    """
    global _pool
    if _pool is None:
        is_pgbouncer = "-pooler." in dsn
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
            init=_register_codecs,
            statement_cache_size=0 if is_pgbouncer else 100,
        )
    return _pool


async def _register_codecs(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("init_pool() no ha sido llamado")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def tx():
    async with pool().acquire() as conn, conn.transaction():
        yield conn


async def apply_schema(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        await conn.close()


# ───────────────────────── mercado ──────────────────────────────────────


async def upsert_market(
    condition_id: str,
    slug: str,
    question: str | None,
    token_up: str,
    token_down: str,
    end_date: datetime | None,
    raw: dict[str, Any] | None = None,
) -> None:
    await pool().execute(
        """
        INSERT INTO market.markets
            (condition_id, slug, question, token_up, token_down, end_date, raw)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (condition_id) DO UPDATE
            SET last_seen = now(), active = TRUE, raw = EXCLUDED.raw
        """,
        condition_id, slug, question, token_up, token_down, end_date, raw,
    )


async def insert_book_snapshots(rows: list[tuple]) -> None:
    """COPY en lote. El collector escribe miles de filas por minuto."""
    if not rows:
        return
    async with pool().acquire() as conn:
        await conn.copy_records_to_table(
            "book_snapshots",
            schema_name="market",
            records=rows,
            columns=[
                "ts", "token_id", "best_bid", "best_ask", "bid_size",
                "ask_size", "mid", "spread", "depth_bid_5c", "depth_ask_5c",
            ],
        )


async def log_health(event: str, detail: str = "", gap_seconds: float | None = None) -> None:
    await pool().execute(
        "INSERT INTO market.collector_health (event, detail, gap_seconds) VALUES ($1, $2, $3)",
        event, detail, gap_seconds,
    )


async def max_gap_seconds(since: datetime) -> float | None:
    """Gate de fase 0: el mayor hueco entre snapshots. Debe ser < 60s."""
    return await pool().fetchval(
        """
        SELECT MAX(gap) FROM (
            SELECT EXTRACT(EPOCH FROM (ts - LAG(ts) OVER (ORDER BY ts))) AS gap
            FROM (SELECT DISTINCT ts FROM market.book_snapshots WHERE ts >= $1) t
        ) g
        """,
        since,
    )


# ───────────────────────── piloto (genérico) ────────────────────────────


class PilotRepo:
    """Un repo por piloto. El schema se fija al construir: imposible cruzarlos."""

    def __init__(self, pilot: str) -> None:
        if pilot.lower() not in ("a", "b"):
            raise ValueError(f"piloto desconocido: {pilot}")
        self.pilot = pilot.lower()
        self.schema = f"pilot_{self.pilot}"

    async def record_intent(
        self,
        intent: OrderIntent,
        verdict: Verdict,
        *,
        mid: float | None,
        strategy: str = "",
        policy_id: int | None = None,
    ) -> int:
        """Se graba SIEMPRE, aprobada o rechazada. Los rechazos son el dato valioso."""
        return await pool().fetchval(
            f"""
            INSERT INTO {self.schema}.order_intents
                (token_id, market_slug, side, price, size, notional, mid_at_check,
                 approved, reject_code, reject_detail, reason, strategy, policy_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id
            """,
            intent.token_id, intent.market_slug, str(intent.side), intent.price,
            intent.size, intent.notional, mid, verdict.approved,
            verdict.code or None, verdict.detail or None, intent.reason,
            strategy, policy_id,
        )

    async def record_order(
        self,
        order_id: str,
        intent_id: int,
        intent: OrderIntent,
        *,
        status: str,
        is_paper: bool,
        latency_ms: int | None = None,
        raw: dict | None = None,
    ) -> None:
        await pool().execute(
            f"""
            INSERT INTO {self.schema}.orders
                (order_id, intent_id, token_id, side, price, size, status,
                 is_paper, latency_ms, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (order_id) DO UPDATE SET status = EXCLUDED.status
            """,
            order_id, intent_id, intent.token_id, str(intent.side), intent.price,
            intent.size, status, is_paper, latency_ms, raw,
        )

    async def record_fill(
        self,
        order_id: str | None,
        token_id: str,
        side: str,
        price: float,
        size: float,
        *,
        fee: float = 0.0,
        realized_pnl: float = 0.0,
        slippage_bps: float | None = None,
        is_paper: bool,
    ) -> None:
        await pool().execute(
            f"""
            INSERT INTO {self.schema}.fills
                (order_id, token_id, side, price, size, fee, realized_pnl,
                 slippage_bps, is_paper)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            order_id, token_id, side, price, size, fee, realized_pnl,
            slippage_bps, is_paper,
        )

    async def record_risk_event(self, event: str, reason: str, snapshot: dict) -> None:
        await pool().execute(
            f"INSERT INTO {self.schema}.risk_events (event, reason, snapshot) "
            "VALUES ($1, $2, $3)",
            event, reason, snapshot,
        )

    async def daily_summary(self, day: date) -> dict[str, Any]:
        """Lo que va al reporte de Telegram y a los gates de fase."""
        row = await pool().fetchrow(
            f"""
            WITH i AS (
                SELECT count(*) n, count(*) FILTER (WHERE NOT approved) rej
                FROM {self.schema}.order_intents WHERE ts::date = $1
            ), f AS (
                SELECT count(*) n, COALESCE(sum(realized_pnl), 0) pnl,
                       COALESCE(sum(fee), 0) fees,
                       AVG(slippage_bps) slip
                FROM {self.schema}.fills WHERE ts::date = $1
            ), o AS (
                SELECT count(*) n FROM {self.schema}.orders WHERE ts_sent::date = $1
            )
            SELECT i.n AS n_intents, i.rej AS n_rejected, o.n AS n_orders,
                   f.n AS n_fills, f.pnl AS realized_pnl, f.fees,
                   f.slip AS avg_slippage_bps,
                   CASE WHEN o.n > 0 THEN f.n::numeric / o.n ELSE NULL END AS fill_rate
            FROM i, f, o
            """,
            day,
        )
        return dict(row) if row else {}

    async def rejection_breakdown(self, day: date) -> list[dict]:
        """Si un código domina, tus límites están mal calibrados (o hay un bug)."""
        rows = await pool().fetch(
            f"""
            SELECT reject_code, count(*) n
            FROM {self.schema}.order_intents
            WHERE ts::date = $1 AND NOT approved
            GROUP BY 1 ORDER BY 2 DESC
            """,
            day,
        )
        return [dict(r) for r in rows]

    async def recent_journal(self, limit: int = 30) -> list[dict]:
        """Lo que el estratega lee de sí mismo. Solo piloto A."""
        rows = await pool().fetch(
            f"""
            SELECT j.ts, j.thesis, j.outcome_pnl, j.outcome_note, p.regime, p.bias
            FROM {self.schema}.journal j
            LEFT JOIN {self.schema}.policies p ON p.id = j.policy_id
            ORDER BY j.ts DESC LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
