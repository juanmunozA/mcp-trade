"""
Collector 24/7. La pieza de la fase 0.

No opera. No decide nada. Solo graba books a Postgres con reconexión y
detección de huecos. Déjalo corriendo **14 días antes de tocar cualquier
estrategia** — sin datos propios no hay backtest, y sin backtest no hay
nada que validar.

    python -m core.polymarket.collector

El gate de fase 0 es: 14 días sin huecos > 60s. Lo verificas con
`python -m scripts.check_gate --phase 0`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from datetime import UTC, datetime

from core import net
from core.config import get_settings
from core.polymarket.client import ReadOnlyClient
from core.polymarket.models import Book
from core.storage import db

log = logging.getLogger("collector")

POLL_INTERVAL_S = float(os.getenv("COLLECTOR_POLL_S", "60"))
FLUSH_EVERY_N = int(os.getenv("COLLECTOR_FLUSH_N", "2000"))
FLUSH_EVERY_S = float(os.getenv("COLLECTOR_FLUSH_S", "900"))
MARKET_REFRESH_S = 300.0

# Los defaults están calibrados para Neon free, y son deliberadamente lentos:
#
#   · POLL cada 60 s   → mercados de eventos se mueven en horas, no en ms.
#   · FLUSH cada 15 min → Neon hace scale-to-zero y cobra CU-horas. Con flush
#     cada 5 s la base nunca duerme: ~182 CU-h/mes contra un límite de 100.
#     Con 15 min despierta unas horas al día y sobra presupuesto.
#
# Para el piloto B (cripto, 1 Hz) pon COLLECTOR_POLL_S=1 y apunta
# DATABASE_URL a una Postgres local: 1 Hz son ~300 MB/día y el free tier
# de Neon son 0.5 GB en total.


class Collector:
    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs
        self.settings = get_settings()
        self.client = ReadOnlyClient(self.settings.clob_host, self.settings.gamma_host)
        self._buffer: list[tuple] = []
        self._tokens: list[str] = []
        self._last_flush = asyncio.get_event_loop().time()
        self._last_tick: datetime | None = None
        self._stop = asyncio.Event()

    # ── ciclo de vida ────────────────────────────────────────────────────

    async def run(self) -> None:
        # Algunas redes bloquean Polymarket por DNS. Esto lo rodea sin tocar
        # la configuración de la máquina; en una red sana no hace nada.
        if rescued := await net.ensure_resolvable(*net.hosts_from_settings(self.settings)):
            log.warning("DNS rescatado por DoH: %s", rescued)

        await db.init_pool(self.settings.database_url)
        await db.log_health("connected", f"slugs={self.slugs or 'TODOS los binarios activos'}")
        log.info("collector arriba | slugs=%s", self.slugs or "todos")

        tasks = [
            asyncio.create_task(self._refresh_markets_loop(), name="markets"),
            asyncio.create_task(self._poll_loop(), name="poll"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._flush()
            await db.log_health("disconnected", "shutdown limpio")
            await self.client.aclose()
            await db.close_pool()
            log.info("collector abajo, buffer vaciado")

    def stop(self) -> None:
        self._stop.set()

    # ── descubrimiento de mercados ───────────────────────────────────────

    async def _refresh_markets_loop(self) -> None:
        while not self._stop.is_set():
            try:
                markets = await self.client.active_markets(self.slugs or None)
                tokens: list[str] = []
                for m in markets:
                    await db.upsert_market(
                        m.condition_id, m.slug, m.question,
                        m.token_up, m.token_down, m.end_date,
                    )
                    tokens.extend((m.token_up, m.token_down))
                self._tokens = tokens
                log.info("siguiendo %d mercados (%d tokens)", len(markets), len(tokens))
            except Exception as exc:  # noqa: BLE001
                log.warning("refresh de mercados falló: %s", exc)
                await db.log_health("error", f"refresh_markets: {exc}")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=MARKET_REFRESH_S)

    # ── polling del book ─────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self._tokens:
                await asyncio.sleep(2.0)
                continue
            try:
                await self._tick()
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001
                # Reconexión con backoff exponencial. Esto es lo que se prueba
                # matando el proceso a mano tres veces en la fase 0.
                log.warning("tick falló (%s), reintento en %.1fs", exc, backoff)
                await db.log_health("error", str(exc)[:500])
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _tick(self) -> None:
        now = datetime.now(UTC)

        # Detección de huecos: el número que define el gate de fase 0.
        if self._last_tick is not None:
            gap = (now - self._last_tick).total_seconds()
            if gap > self.settings.stale_data_seconds:
                log.error("HUECO de %.1fs en los datos", gap)
                await db.log_health("gap", f"{gap:.1f}s sin datos", gap_seconds=gap)
        self._last_tick = now

        # Lotes de 50 para no reventar el rate limit
        for i in range(0, len(self._tokens), 50):
            chunk = self._tokens[i : i + 50]
            books = await self.client.get_books(chunk)
            for token_id, book in books.items():
                self._buffer.append(self._to_row(now, token_id, book))

        loop_now = asyncio.get_event_loop().time()
        if len(self._buffer) >= FLUSH_EVERY_N or loop_now - self._last_flush >= FLUSH_EVERY_S:
            await self._flush()

    @staticmethod
    def _to_row(ts: datetime, token_id: str, book: Book) -> tuple:
        return (
            ts, token_id, book.best_bid, book.best_ask,
            book.bids[0].size if book.bids else None,
            book.asks[0].size if book.asks else None,
            book.mid, book.spread,
            book.depth("bid"), book.depth("ask"),
        )

    async def _flush(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            await db.insert_book_snapshots(rows)
        except Exception as exc:  # noqa: BLE001
            # Perder datos es peor que un crash: reencolar y avisar fuerte.
            log.error("FLUSH FALLÓ, reencolando %d filas: %s", len(rows), exc)
            self._buffer = rows + self._buffer
            await db.log_health("error", f"flush: {exc}")
        finally:
            self._last_flush = asyncio.get_event_loop().time()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    settings = get_settings()
    collector = Collector(settings.market_slugs)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows no tiene SIGTERM
            loop.add_signal_handler(sig, collector.stop)

    await collector.run()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
