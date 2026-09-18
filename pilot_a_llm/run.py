"""
Piloto A — el "Arena".  `python -m pilot_a_llm.run`

Dos relojes muy distintos, y esa separación ES la arquitectura:

  estratega   LLM → Policy            cada 4 h    (lento, caro, razona)
  ejecutor    Policy → OrderIntent    cada 30 s   (rápido, determinista)
  reconcile   verdad del exchange     cada 10 s
  watchdog    datos congelados        cada 5 s

El ejecutor nunca llama al LLM. El LLM nunca llama al exchange.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from core import net
from core.alerts import Level, Telegram
from core.config import Mode, get_settings
from core.polymarket.client import ReadOnlyClient, build_execution_client
from core.polymarket.models import Book, Market
from core.risk import OrderIntent, RiskKernel, RiskLimits, Side
from core.storage import db
from pilot_a_llm.policy import SAFE_DEFAULT, Bias, Policy
from pilot_a_llm.strategist import Strategist

log = logging.getLogger("pilot_a")

PILOT = "a"
EXECUTE_INTERVAL_S = 30.0
RECONCILE_INTERVAL_S = 10.0
WATCHDOG_INTERVAL_S = 5.0


class PilotA:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.repo = db.PilotRepo(PILOT)
        self.alerts = Telegram(
            self.settings.telegram_bot_token.get_secret_value()
            if self.settings.telegram_bot_token else None,
            self.settings.telegram_chat_id,
        )
        self.market_data = ReadOnlyClient(self.settings.clob_host, self.settings.gamma_host)
        self.exec_client = build_execution_client(self.settings, PILOT)

        self.strategist: Strategist | None = None
        self.kernel: RiskKernel | None = None
        self.markets: dict[str, Market] = {}
        self.books: dict[str, Book] = {}

        # Arrancamos flat, siempre. Nunca se hereda exposición de una corrida previa.
        self.policy: Policy = SAFE_DEFAULT
        self.policy_id: int | None = None
        self._hard_max_position = 0.0
        self._last_data: datetime | None = None
        self._stop = asyncio.Event()

    # ── arranque ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.settings.mode is Mode.COLLECT:
            raise SystemExit("MODE=collect: este piloto no opera. Usa paper o live.")
        if self.settings.gemini_api_key is None:
            raise SystemExit("falta GEMINI_API_KEY: el piloto A no corre sin estratega")

        # Rodea bloqueos de DNS sin tocar la configuración de la máquina.
        if rescued := await net.ensure_resolvable(*net.hosts_from_settings(self.settings)):
            log.warning("DNS rescatado por DoH: %s", rescued)

        await db.init_pool(self.settings.database_url)

        markets = await self.market_data.active_markets(self.settings.market_slugs or None)
        if not markets:
            raise SystemExit("ningún mercado coincide con SETS_MARKET_SLUGS")
        self.markets = {m.slug: m for m in markets}

        tokens = frozenset(t for m in markets for t in (m.token_up, m.token_down))
        limits = RiskLimits.from_env(PILOT, allowed_token_ids=tokens)
        self.kernel = RiskKernel(PILOT, limits)
        # El techo real. El LLM no lo ve; solo se usa para recortar su política.
        self._hard_max_position = limits.max_position_usd

        if self.kernel.killed:
            raise SystemExit(
                f"El piloto A está detenido desde antes: {self.kernel.kill_reason}\n"
                f"Investiga, y solo entonces borra state/.KILLED_{PILOT}"
            )

        self.strategist = Strategist(
            self.settings.anthropic_api_key.get_secret_value(),
            self.settings.strategist_model,
        )

        await self.alerts.send(
            Level.INFO,
            f"Piloto A arriba ({self.settings.mode})",
            f"modelo={self.settings.strategist_model} · {len(markets)} mercados",
        )

    # ── el estratega ─────────────────────────────────────────────────────

    async def strategist_loop(self) -> None:
        while not self._stop.is_set():
            if not self.kernel.killed:
                with contextlib.suppress(Exception):
                    await self._think()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.strategist_interval_min * 60,
                )

    async def _think(self) -> None:
        context = await self._build_context()
        policy, meta = await self.strategist.decide(context)

        clamped, notes = policy.clamp(
            hard_max_position_usd=self._hard_max_position,
            allowed_slugs=set(self.markets),
        )
        if notes:
            # Señal de que el prompt necesita trabajo, no ruido.
            log.warning("política recortada: %s", "; ".join(notes))

        self.policy_id = await db.pool().fetchval(
            """
            INSERT INTO pilot_a.policies
                (model, regime, bias, max_position_usd, strategies_on, notes,
                 prompt, raw_response, input_tokens, output_tokens, cost_usd,
                 valid, validation_error)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id
            """,
            meta["model"], str(clamped.regime), str(clamped.bias),
            clamped.max_position_usd, clamped.target_markets,
            "; ".join(notes) or clamped.risk_note,
            meta["prompt"], meta["raw_response"],
            meta["input_tokens"], meta["output_tokens"], meta["cost_usd"],
            meta["valid"], meta["validation_error"],
        )

        await db.pool().execute(
            "INSERT INTO pilot_a.journal (policy_id, thesis) VALUES ($1, $2)",
            self.policy_id, clamped.thesis,
        )

        self.policy = clamped
        log.info(
            "nueva política | régimen=%s sesgo=%s max=$%.2f conf=%s costo=$%s",
            clamped.regime, clamped.bias, clamped.max_position_usd,
            clamped.confidence, meta["cost_usd"],
        )

        if not meta["valid"]:
            await self.alerts.send(
                Level.WARN,
                "Estratega falló, quedamos flat",
                str(meta["validation_error"]),
            )

    async def _build_context(self) -> dict:
        today = datetime.now(UTC).date()
        markets_view = []
        for slug, m in self.markets.items():
            bu, bd = self.books.get(m.token_up), self.books.get(m.token_down)
            if bu is None or bd is None:
                continue
            markets_view.append({
                "slug": slug,
                "question": m.question,
                "up_bid": bu.best_bid, "up_ask": bu.best_ask,
                "down_bid": bd.best_bid, "down_ask": bd.best_ask,
                "sum_asks": round((bu.best_ask or 0) + (bd.best_ask or 0), 4),
                "end_date": m.end_date.isoformat() if m.end_date else None,
            })

        return {
            "portfolio": self.kernel.snapshot(),
            "performance": {
                "hoy": await self.repo.daily_summary(today),
                "ayer": await self.repo.daily_summary(today - timedelta(days=1)),
            },
            "journal": await self.repo.recent_journal(limit=25),
            "markets": markets_view,
        }

    # ── el ejecutor (sin IA) ─────────────────────────────────────────────

    async def execute_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._refresh_books()
                if not self.kernel.killed:
                    await self._execute_policy()
            except Exception as exc:  # noqa: BLE001
                log.exception("el ejecutor falló")
                self.kernel.trip(f"excepción en el ejecutor: {exc!r}")
                await self._on_kill()
            await asyncio.sleep(EXECUTE_INTERVAL_S)

    async def _refresh_books(self) -> None:
        tokens = [t for m in self.markets.values() for t in (m.token_up, m.token_down)]
        self.books = await self.market_data.get_books(tokens)
        self._last_data = datetime.now(UTC)

    async def _execute_policy(self) -> None:
        """
        Traduce la política a intenciones. Deliberadamente tonto: comprar el
        lado que la política pide, hasta el tamaño que la política permite.
        Toda la inteligencia está arriba; todo el control, abajo.
        """
        if self.policy.is_flat:
            with contextlib.suppress(Exception):
                await self.exec_client.cancel_all()
            return

        want_up = self.policy.bias is Bias.LONG
        for slug in self.policy.target_markets[: self.policy.max_concurrent_markets]:
            market = self.markets.get(slug)
            if market is None:
                continue
            token = market.token_up if want_up else market.token_down
            book = self.books.get(token)
            if book is None or book.best_ask is None:
                continue

            current_usd = abs(self.kernel.position(token)) * book.best_ask
            room = self.policy.max_position_usd - current_usd
            if room <= 1.0:      # no perseguimos sobras de un dólar
                continue

            size = round(room / book.best_ask, 2)
            intent = OrderIntent(
                pilot=PILOT,
                token_id=token,
                side=Side.BUY,
                price=book.best_ask,
                size=size,
                market_slug=slug,
                reason=self.policy.thesis[:200],
            )
            await self._submit(intent, mid=book.mid)
            if self.kernel.killed:
                return

    async def _submit(self, intent: OrderIntent, *, mid: float | None) -> None:
        verdict = self.kernel.check(intent, mid=mid)
        intent_id = await self.repo.record_intent(
            intent, verdict, mid=mid, strategy="llm_policy", policy_id=self.policy_id
        )
        if not verdict.approved:
            if self.kernel.killed:
                await self._on_kill()
            return

        book = self.books[intent.token_id]
        placed = await self.exec_client.place(intent, book=book)
        self.kernel.on_order_sent(intent)
        await self.repo.record_order(
            placed.order_id, intent_id, intent,
            status=placed.status, is_paper=placed.is_paper,
            latency_ms=placed.latency_ms, raw=placed.raw,
        )

        if placed.status == "filled":
            self.kernel.on_order_closed()
            realized = self.kernel.on_fill(
                intent.token_id, intent.side, placed.price, placed.size
            )
            await self.repo.record_fill(
                placed.order_id, intent.token_id, str(intent.side),
                placed.price, placed.size, realized_pnl=realized,
                slippage_bps=(placed.price - mid) * 10_000 if mid else None,
                is_paper=placed.is_paper,
            )
            if self.kernel.killed:
                await self._on_kill()

    # ── guardarraíles (idénticos al piloto B, a propósito) ───────────────

    async def reconcile_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(RECONCILE_INTERVAL_S)
            if self.kernel.killed:
                continue
            try:
                verdict = self.kernel.reconcile(await self.exec_client.positions())
                if not verdict.approved:
                    await self.repo.record_risk_event(
                        "reconcile_drift", verdict.detail, self.kernel.snapshot()
                    )
                    await self._on_kill()
            except Exception as exc:  # noqa: BLE001
                log.warning("reconciliación falló: %s", exc)

    async def watchdog_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            if self.kernel.killed or self._last_data is None:
                continue
            stale = (datetime.now(UTC) - self._last_data).total_seconds()
            if stale > max(self.settings.stale_data_seconds, EXECUTE_INTERVAL_S * 2):
                self.kernel.trip(f"datos congelados hace {stale:.0f}s")
                with contextlib.suppress(Exception):
                    await self.exec_client.cancel_all()
                await self._on_kill()

    async def report_loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(UTC)
            nxt = (now + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=(nxt - now).total_seconds())
            if self._stop.is_set():
                return
            day = (datetime.now(UTC) - timedelta(days=1)).date()
            stats = await self.repo.daily_summary(day)
            stats["llm_cost_usd"] = await db.pool().fetchval(
                "SELECT COALESCE(sum(cost_usd), 0) FROM pilot_a.policies WHERE ts::date = $1",
                day,
            )
            await self.alerts.daily_report(
                PILOT, stats, await self.repo.rejection_breakdown(day)
            )

    async def _on_kill(self) -> None:
        snap = self.kernel.snapshot()
        await self.repo.record_risk_event("trip", self.kernel.kill_reason, snap)
        await self.alerts.killed(PILOT, self.kernel.kill_reason, snap)
        with contextlib.suppress(Exception):
            await self.exec_client.cancel_all()
        self._stop.set()

    # ── ciclo de vida ────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.start()
        await self._refresh_books()   # el estratega necesita books desde el primer ciclo
        tasks = [
            asyncio.create_task(self.strategist_loop(), name="strategist"),
            asyncio.create_task(self.execute_loop(), name="execute"),
            asyncio.create_task(self.reconcile_loop(), name="reconcile"),
            asyncio.create_task(self.watchdog_loop(), name="watchdog"),
            asyncio.create_task(self.report_loop(), name="report"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.exec_client.cancel_all()
            await self.market_data.aclose()
            await db.close_pool()
            log.info("piloto A abajo")

    def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    pilot = PilotA()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, pilot.stop)
    await pilot.run()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
