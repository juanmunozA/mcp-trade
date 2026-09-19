"""
Complete sets — el agente de trading.  `python -m trade.run`

Loop determinista, sin LLM. Cuatro tareas concurrentes:

  trade      evalúa mercados y propone órdenes      ~1 Hz
  reconcile  compara posiciones contra el exchange  ~0.1 Hz
  watchdog   mata todo si los datos se congelan     ~0.2 Hz
  report     resumen diario a Telegram              1/día

Cualquiera de las tres últimas puede disparar el kill switch. Ninguna de
ellas puede desarmarlo: eso lo haces tú a mano.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from core.alerts import Level, Telegram
from core.api import AgentAPI
from core.config import Mode, get_settings
from core.polymarket.client import ReadOnlyClient, build_execution_client
from core.polymarket.models import Market
from core.risk import RiskKernel, RiskLimits
from core.storage import db
from trade.engine import EngineConfig, SetEngine

log = logging.getLogger("pilot_b")

PILOT = "b"
TRADE_INTERVAL_S = 1.0
RECONCILE_INTERVAL_S = 10.0
WATCHDOG_INTERVAL_S = 5.0


class PilotB:
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

        self.engine = SetEngine(
            EngineConfig(
                max_combined_cost=self.settings.sets_max_combined_cost,
                max_imbalance_shares=self.settings.sets_max_imbalance_shares,
            )
        )
        self.markets: dict[str, Market] = {}
        self.kernel: RiskKernel | None = None   # se construye tras conocer los tokens
        self._last_data: datetime | None = None
        self._api: AgentAPI | None = None
        self._stop = asyncio.Event()

    # ── arranque ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.settings.mode is Mode.COLLECT:
            raise SystemExit("MODE=collect: este piloto no opera. Usa paper o live.")

        await db.init_pool(self.settings.database_url)

        markets = await self.market_data.active_markets(self.settings.market_slugs or None)
        if not markets:
            raise SystemExit(
                "Ningún mercado coincide con SETS_MARKET_SLUGS. "
                "Deja esa lista vacía SOLO si sabes lo que haces: sin whitelist "
                "el risk kernel rechaza todo (fail closed, por diseño)."
            )
        self.markets = {m.condition_id: m for m in markets}

        # La whitelist del kernel se cierra aquí y no cambia en toda la corrida.
        tokens = frozenset(
            t for m in markets for t in (m.token_up, m.token_down)
        )
        self.kernel = RiskKernel(
            PILOT, RiskLimits.from_env(PILOT, allowed_token_ids=tokens)
        )

        if self.kernel.killed:
            raise SystemExit(
                f"El piloto B está detenido desde antes: {self.kernel.kill_reason}\n"
                f"Revisa qué pasó, y solo entonces borra state/.KILLED_{PILOT}"
            )

        await self.alerts.send(
            Level.INFO,
            f"Piloto B arriba ({self.settings.mode})",
            f"{len(markets)} mercados · {len(tokens)} tokens",
        )
        # Render exige un puerto abierto o mata el servicio ("No open ports
        # detected"), y sin endpoint UptimeRobot no puede mantenerlo despierto.
        # De paso queda el boton de panico accesible desde fuera.
        self._api = AgentAPI(
            status_fn=self._status,
            cycle_fn=self._no_aplica,
            resolve_fn=self._no_aplica,
            report_fn=self._reporte,
            kill_fn=self._kill_ahora,
        )
        await self._api.start()

        log.info("piloto B arriba | modo=%s | %d mercados", self.settings.mode, len(markets))

    # ── superficie HTTP ──────────────────────────────────────────────────

    def _status(self) -> dict:
        """Lo que ve /health. Sin secretos: esto es publico."""
        inv = self.engine.summary() if self.engine else {}
        return {
            "service": "trade-sets",
            "mode": str(self.settings.mode),
            "killed": bool(self.kernel and self.kernel.killed),
            "kill_reason": self.kernel.kill_reason if self.kernel else "",
            "mercados": len(self.markets),
            "inventario": inv,
        }

    async def _no_aplica(self) -> dict:
        """Este agente no tiene ciclos ni resoluciones: opera en continuo."""
        return {"nota": "no aplica a complete sets; mira /health"}

    async def _reporte(self) -> dict:
        day = (datetime.now(UTC) - timedelta(days=1)).date()
        stats = await self.repo.daily_summary(day)
        return stats | self.engine.summary()

    def _kill_ahora(self, reason: str) -> None:
        if self.kernel is not None:
            self.kernel.trip(reason)
        self._stop.set()

    # ── loop de trading ──────────────────────────────────────────────────

    async def trade_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._trade_tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("trade tick falló")
                self.kernel.trip(f"excepción en el trade loop: {exc!r}")
                await self._on_kill()
            await asyncio.sleep(TRADE_INTERVAL_S)

    async def _trade_tick(self) -> None:
        if self.kernel.killed:
            return

        token_ids = [t for m in self.markets.values() for t in (m.token_up, m.token_down)]
        books = await self.market_data.get_books(token_ids)
        self._last_data = datetime.now(UTC)

        for market in self.markets.values():
            bu, bd = books.get(market.token_up), books.get(market.token_down)
            if bu is None or bd is None:
                continue

            proposal = self.engine.evaluate(market, bu, bd)
            for intent in proposal.intents:
                book = bu if intent.token_id == market.token_up else bd
                await self._submit(intent, mid=book.mid, strategy=proposal.kind)
                if self.kernel.killed:
                    return

    async def _submit(self, intent, *, mid: float | None, strategy: str) -> None:
        """Único camino hacia el exchange. Todo pasa por el kernel y queda grabado."""
        verdict = self.kernel.check(intent, mid=mid)
        intent_id = await self.repo.record_intent(
            intent, verdict, mid=mid, strategy=strategy
        )

        if not verdict.approved:
            # No es ruido: es la señal de si tus límites están bien puestos.
            log.debug("rechazada [%s] %s", verdict.code, verdict.detail)
            if self.kernel.killed:
                await self._on_kill()
            return

        book = await self.market_data.get_book(intent.token_id)
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
            slippage = (placed.price - mid) * 10_000 if mid else None
            await self.repo.record_fill(
                placed.order_id, intent.token_id, str(intent.side),
                placed.price, placed.size,
                realized_pnl=realized, slippage_bps=slippage,
                is_paper=placed.is_paper,
            )
            market = next(
                m for m in self.markets.values()
                if intent.token_id in (m.token_up, m.token_down)
            )
            self.engine.on_fill(market, intent.token_id, placed.price, placed.size)

            if self.kernel.killed:
                await self._on_kill()

    # ── reconciliación ───────────────────────────────────────────────────

    async def reconcile_loop(self) -> None:
        """La verdad es el exchange. Divergencia = perdimos un fill = kill."""
        while not self._stop.is_set():
            await asyncio.sleep(RECONCILE_INTERVAL_S)
            if self.kernel.killed:
                continue
            try:
                remote = await self.exec_client.positions()
                verdict = self.kernel.reconcile(remote)
                if not verdict.approved:
                    await self.repo.record_risk_event(
                        "reconcile_drift", verdict.detail, self.kernel.snapshot()
                    )
                    await self._on_kill()
            except Exception as exc:  # noqa: BLE001
                log.warning("reconciliación falló: %s", exc)

    # ── watchdog ─────────────────────────────────────────────────────────

    async def watchdog_loop(self) -> None:
        """
        Datos congelados con posición abierta es el escenario más peligroso:
        estás expuesto y ciego. Se cancela todo y se mata el piloto.
        """
        while not self._stop.is_set():
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            if self.kernel.killed or self._last_data is None:
                continue
            stale = (datetime.now(UTC) - self._last_data).total_seconds()
            if stale > self.settings.stale_data_seconds:
                self.kernel.trip(f"datos congelados hace {stale:.0f}s")
                with contextlib.suppress(Exception):
                    await self.exec_client.cancel_all()
                await self._on_kill()

    # ── reporte ──────────────────────────────────────────────────────────

    async def report_loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(UTC)
            nxt = (now + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=(nxt - now).total_seconds()
                )
            if self._stop.is_set():
                return
            day = (datetime.now(UTC) - timedelta(days=1)).date()
            stats = await self.repo.daily_summary(day)
            stats |= self.engine.summary()
            await self.alerts.daily_report(
                PILOT, stats, await self.repo.rejection_breakdown(day)
            )

    # ── kill ─────────────────────────────────────────────────────────────

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
        tasks = [
            asyncio.create_task(self.trade_loop(), name="trade"),
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
            # Nunca dejar órdenes vivas tras un apagado.
            with contextlib.suppress(Exception):
                n = await self.exec_client.cancel_all()
                log.info("canceladas %d órdenes al salir", n)
            if self._api is not None:
                await self._api.stop()
            await self.market_data.aclose()
            await db.close_pool()
            log.info("piloto B abajo | %s", self.engine.summary())

    def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    pilot = PilotB()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, pilot.stop)
    await pilot.run()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
