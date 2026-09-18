"""
Piloto A — agente de pronóstico de eventos.   `python -m pilot_a_llm.run`

    scout      cada N horas   selecciona mercados y pronostica
    resolver   cada 6 horas   registra lo que resolvió, alimenta la calibración
    health     continuo       endpoint para Render y UptimeRobot

El ciclo completo:

    600 mercados
      → markets.py     filtro duro + puntaje         (sin IA, sin costo)
      → research.py    Gemini + búsqueda web          (hechos con fecha)
      → forecast.py    ensemble + 3 vetos             (probabilidad)
      → calibration    corrige sesgo conocido         (tras 30 resoluciones)
      → edge.py        ¿|p - precio| > umbral?        (Kelly fraccional)
      → risk kernel    límites duros                  (⛔ última palabra)
      → orden

Nada de esto decide su propio tamaño ni sus propios límites. El LLM emite
una probabilidad; todo lo demás es aritmética y reglas.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from datetime import UTC, datetime

import httpx

from core import net
from core.alerts import Level, Telegram
from core.api import AgentAPI
from core.config import Mode, get_settings
from core.polymarket.client import ReadOnlyClient, build_execution_client
from core.risk import OrderIntent, RiskKernel, RiskLimits, Side
from core.storage import db
from pilot_a_llm.bankroll import BankrollReader
from pilot_a_llm.budget import Budget
from pilot_a_llm.calibration import CalibrationMap, Resolution, ensemble_weights
from pilot_a_llm.edge import Bet, EdgeConfig, Quote, decide
from pilot_a_llm.forecast import Forecaster, VetoConfig
from pilot_a_llm.markets import MarketFilter, select, summarize
from pilot_a_llm.providers import CloudflareProvider, GeminiProvider
from pilot_a_llm.research import Researcher
from pilot_a_llm.vault import Vault

log = logging.getLogger("pilot_a")

PILOT = "a"
RESOLVER_INTERVAL_S = 6 * 3600
MARKETS_PER_CYCLE = int(os.getenv("MARKETS_PER_CYCLE", "15"))
MARKET_PAGES = int(os.getenv("MARKET_PAGES", "8"))       # 100 por página
CONCURRENCY = int(os.getenv("FORECAST_CONCURRENCY", "4"))
"""
Cuántos mercados se investigan a la vez. DeepSeek tarda ~30 s por
pronóstico, así que en serie el ciclo se hace eterno. Con 4 en paralelo
bajamos de 30 s/mercado a ~8 s efectivos.

No subir sin medir: cada mercado concurrente son 2 llamadas al ensemble
más 1 a Gemini, y los rate limits de los free tiers son por minuto.
"""


class PilotA:
    def __init__(self) -> None:
        self.s = get_settings()
        self.repo = db.PilotRepo(PILOT)
        self.alerts = Telegram(
            self.s.telegram_bot_token.get_secret_value()
            if self.s.telegram_bot_token else None,
            self.s.telegram_chat_id,
        )
        self.market_data = ReadOnlyClient(self.s.clob_host, self.s.gamma_host)
        self.exec_client = build_execution_client(self.s, PILOT)

        self.kernel: RiskKernel | None = None
        self.forecaster: Forecaster | None = None
        self.researcher: Researcher | None = None
        self.bankroll: BankrollReader | None = None
        self._api: AgentAPI | None = None
        self._last_cycle: datetime | None = None
        self._cycle_stats: dict = {}
        self._max_order_usd: float = 0.0
        self.budget = Budget()
        self.vault = Vault(os.getenv("VAULT_DIR", "vault"))
        # Serializa el tramo check→orden del kernel cuando hay concurrencia.
        self._order_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    # ── arranque ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.s.mode is Mode.COLLECT:
            raise SystemExit("MODE=collect: usa paper o live para operar")
        if self.s.gemini_api_key is None:
            raise SystemExit("falta GEMINI_API_KEY (la búsqueda web no es opcional)")

        if rescued := await net.ensure_resolvable(*net.hosts_from_settings(self.s)):
            log.warning("DNS rescatado por DoH: %s", rescued)

        await db.init_pool(self.s.database_url)

        # DeepSeek primero: mide mejor la comprensión de la pregunta.
        # llama-8b segundo: 39× más barato en Neurons, da la segunda opinión
        # que alimenta el detector de desacuerdo.
        acc = self.s.cloudflare_account_id
        tok = self.s.cloudflare_api_token
        providers = {}
        if acc and tok:
            secret = tok.get_secret_value()
            providers["deepseek-r1-32b"] = CloudflareProvider(
                acc, secret, "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"
            )
            providers["llama-3.1-8b"] = CloudflareProvider(
                acc, secret, "@cf/meta/llama-3.1-8b-instruct"
            )
        if not providers:
            raise SystemExit("falta CLOUDFLARE_ACCOUNT_ID/API_TOKEN para el ensemble")

        gemini = GeminiProvider(
            self.s.gemini_api_key.get_secret_value(), self.s.strategist_model
        )
        self.researcher = Researcher(gemini)

        calib, weights = await self._load_learning()
        self.forecaster = Forecaster(
            providers, calibration=calib, weights=weights,
            vetoes=VetoConfig(), vault=self.vault,
        )
        if notes := self.vault.read_context():
            log.info("vault: %d caracteres de notas del operador en contexto", len(notes))

        _, funder = self.s.credentials(PILOT)
        self.bankroll = BankrollReader(
            funder_address=funder, is_paper=not self.s.is_live
        )

        # La whitelist del kernel se abre por ciclo, con los tokens de los
        # mercados elegidos. Aquí solo se valida que los límites existan.
        limits = RiskLimits.from_env(PILOT)
        self._max_order_usd = limits.max_order_usd
        """
        El techo REAL de una orden. Sin esto, edge.py dimensiona con Kelly
        hasta su propio máximo y el kernel rechaza todo lo que lo supere —
        tirando a la basura la investigación completa del mercado.
        Medido: en una corrida de 40 mercados, 11 decisiones de operar y
        0 órdenes colocadas, todas rechazadas por $8-17 contra un tope de $5.
        """

        # La API reemplaza al health server: incluye /health y además los
        # verbos que n8n necesita para orquestar.
        self._api = AgentAPI(
            status_fn=self._status,
            cycle_fn=self._cycle_now,
            resolve_fn=self._resolve_now,
            report_fn=self._report_now,
            kill_fn=self._kill_now,
        )
        await self._api.start()

        await self.alerts.send(
            Level.INFO,
            f"Piloto A arriba ({self.s.mode})",
            f"ensemble: {', '.join(providers)}\ninvestigación: {self.s.strategist_model}",
        )
        log.info("piloto A arriba | modo=%s | ensemble=%s", self.s.mode, list(providers))

    async def _load_learning(self) -> tuple[CalibrationMap | None, dict]:
        """
        Reconstruye lo aprendido desde la base. Con pocas resoluciones esto
        devuelve (None, {}) y el agente opera sin corrección — que es lo
        correcto: calibrar con 8 muestras es superstición.
        """
        rows = await db.pool().fetch(
            "SELECT p_forecast, outcome, p_market, category FROM pilot_a.resolutions"
        )
        if not rows:
            return None, {}
        records = [
            Resolution(
                p_forecast=float(r["p_forecast"]), outcome=r["outcome"],
                p_market=float(r["p_market"]), category=r["category"] or "",
            )
            for r in rows
        ]
        calib = CalibrationMap(records)
        log.info("calibración: %s (%d resoluciones)", calib, len(records))

        per_model = await db.pool().fetch(
            """
            SELECT key AS model, (value)::text::numeric AS p, r.outcome, f.p_market
            FROM pilot_a.forecasts f
            JOIN pilot_a.resolutions r ON r.forecast_id = f.id
            CROSS JOIN LATERAL jsonb_each(f.per_model)
            """
        )
        history: dict[str, list[Resolution]] = {}
        for r in per_model:
            history.setdefault(r["model"], []).append(
                Resolution(float(r["p"]), r["outcome"], p_market=float(r["p_market"]))
            )
        weights = ensemble_weights(history) if history else {}
        if weights:
            log.info("pesos del ensemble: %s", {k: round(v, 3) for k, v in weights.items()})
        return calib, weights

    # ── ciclo principal ──────────────────────────────────────────────────

    async def scout_loop(self) -> None:
        while not self._stop.is_set():
            if self.kernel is None or not self.kernel.killed:
                try:
                    await self._cycle()
                except Exception as exc:  # noqa: BLE001
                    log.exception("ciclo falló")
                    await self.alerts.send(Level.WARN, "Ciclo falló", repr(exc)[:300])
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.s.strategist_interval_min * 60
                )

    async def _cycle(self) -> None:
        started = datetime.now(UTC)
        log.info("─── ciclo iniciado ───")

        bank = await self.bankroll.read()
        log.info("capital: %s", bank)
        if bank.deployable_usd <= 1.0:
            log.warning("sin capital desplegable, saltando ciclo")
            return

        # Presupuesto ANTES de investigar. Quedarse sin Neurons a mitad del
        # ciclo tira a la basura llamadas a Gemini ya pagadas, y deja el
        # resto del día muerto sin que nadie se entere.
        models = [p.model for p in self.forecaster.providers.values()]
        affordable = self.budget.markets_affordable(models)
        if affordable <= 0:
            log.warning(
                "presupuesto agotado hasta las 00:00 UTC — %s", self.budget.snapshot()
            )
            return

        limit = min(MARKETS_PER_CYCLE, affordable)
        if limit < MARKETS_PER_CYCLE:
            log.info(
                "presupuesto recorta el ciclo: %d → %d mercados (%.0f neurons libres)",
                MARKETS_PER_CYCLE, limit, self.budget.usable_left,
            )

        raw = await self._fetch_markets()
        picks = select(raw, MarketFilter(), limit=limit)
        log.info("%d mercados → %d candidatos\n%s", len(raw), len(picks), summarize(picks))
        if not picks:
            return

        # La whitelist del kernel se cierra sobre los mercados de ESTE ciclo.
        tokens = frozenset(t for c in picks for t in (c.token_yes, c.token_no))
        self.kernel = RiskKernel(PILOT, RiskLimits.from_env(PILOT, allowed_token_ids=tokens))
        if self.kernel.killed:
            await self.alerts.killed(PILOT, self.kernel.kill_reason, self.kernel.snapshot())
            self._stop.set()
            return

        stats = {"vistos": len(picks), "vetados": 0, "pass": 0, "ordenes": 0, "costo": 0.0}

        # Investigación y pronóstico van en paralelo (son esperas de red).
        # La parte que toca el kernel va serializada — ver _handle.
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(c):
            if self.kernel.killed:
                return
            async with sem:
                try:
                    await self._handle(c, bank.deployable_usd, stats)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s falló: %s", c.slug[:40], exc)

        await asyncio.gather(*(one(c) for c in picks))

        self._last_cycle = datetime.now(UTC)
        self._cycle_stats = stats | {
            "duracion_s": round((self._last_cycle - started).total_seconds(), 1)
        }
        log.info("─── ciclo terminado: %s ───", self._cycle_stats)

    async def _handle(self, c, bankroll_usd: float, stats: dict) -> None:
        ev = await self.researcher.investigate(c)
        self.budget.spend_gemini()
        f = await self.forecaster.forecast(c, ev)
        if f is None:
            return
        self.budget.spend_neurons(f.neurons)
        stats["costo"] += f.cost_usd + ev.cost_usd
        stats["neurons"] = round(stats.get("neurons", 0.0) + f.neurons, 1)

        # Precios reales del book: pagas el ask, no el mid.
        books = await self.market_data.get_books([c.token_yes, c.token_no])
        by, bn = books.get(c.token_yes), books.get(c.token_no)
        if by is None or bn is None or by.best_ask is None or bn.best_ask is None:
            return
        quote = Quote(by.best_ask, bn.best_ask, by.best_bid or 0, bn.best_bid or 0)

        if f.vetoed:
            stats["vetados"] += 1
            log.info("VETO %s — %s", c.slug[:40], f.veto_reason[:110])
            await self._save(f, quote, None, bankroll_usd, ev)
            self._to_vault(f, "VETO", None)
            return

        # El tope de edge.py NUNCA debe superar el del kernel: si Kelly pide
        # más de lo que el kernel acepta, la orden se rechaza y se pierde el
        # trabajo. Kelly dimensiona dentro de lo que el riesgo va a aprobar.
        d = decide(
            f.p_calibrated, quote, bankroll_usd,
            EdgeConfig(max_position_usd=min(self._max_order_usd, bankroll_usd * 0.2)),
        )
        if not d:
            stats["pass"] += 1
            await self._save(f, quote, d, bankroll_usd, ev)
            self._to_vault(f, "PASS", None)
            return

        token = c.token_yes if d.bet is Bet.YES else c.token_no
        intent = OrderIntent(
            pilot=PILOT, token_id=token, side=Side.BUY,
            price=d.price_paid, size=d.shares,
            market_slug=c.slug, reason=f.means_yes[:200],
        )
        book = by if d.bet is Bet.YES else bn

        # SECCIÓN CRÍTICA. Entre `check()` y `on_order_sent()` hay awaits, y
        # con mercados concurrentes dos órdenes podrían pasar el mismo límite
        # antes de que ninguna lo consuma — saltándose el tope de exposición
        # sin que el kernel se entere. El lock lo impide.
        async with self._order_lock:
            verdict = self.kernel.check(intent, mid=book.mid)
            intent_id = await self.repo.record_intent(
                intent, verdict, mid=book.mid, strategy="forecast"
            )
            if not verdict.approved:
                log.info("kernel rechazó %s: %s", c.slug[:40], verdict.detail[:100])
                await self._save(f, quote, d, bankroll_usd, ev)
                return

            placed = await self.exec_client.place(intent, book=book)
            self.kernel.on_order_sent(intent)

            if placed.status == "filled":
                self.kernel.on_order_closed()
                realized = self.kernel.on_fill(token, Side.BUY, placed.price, placed.size)
                if placed.is_paper:
                    self.bankroll.on_paper_fill(placed.price * placed.size)
            else:
                realized = 0.0

        await self.repo.record_order(
            placed.order_id, intent_id, intent,
            status=placed.status, is_paper=placed.is_paper,
            latency_ms=placed.latency_ms, raw=placed.raw,
        )
        if placed.status == "filled":
            await self.repo.record_fill(
                placed.order_id, token, "BUY", placed.price, placed.size,
                realized_pnl=realized, is_paper=placed.is_paper,
            )

        stats["ordenes"] += 1
        log.info(
            "ORDEN %s %s | p=%.3f vs mercado %.3f | edge %+.3f | $%.2f",
            d.bet, c.slug[:36], f.p_calibrated, c.price, d.edge, d.size_usd,
        )
        await self._save(f, quote, d, bankroll_usd, ev)
        self._to_vault(f, str(d.bet), d.size_usd)

    def _to_vault(self, f, action: str, size_usd: float | None) -> None:
        """Escribir el vault nunca puede tumbar un ciclo."""
        try:
            self.vault.write_forecast(f, action, size_usd)
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo escribir la nota del vault: %s", exc)

    async def _save(self, f, quote: Quote, d, bankroll_usd: float, ev) -> None:
        await db.pool().execute(
            """
            INSERT INTO pilot_a.forecasts
                (condition_id, slug, question, token_yes, token_no, category,
                 end_date, days_to_resolution, p_market, ask_yes, ask_no,
                 liquidity, opportunity_score, p_raw, p_calibrated, disagreement,
                 n_models, per_model, means_yes, is_ambiguous, ambiguity_note,
                 reference_class, base_rate, key_evidence, steps_required,
                 confidence, what_would_change_my_mind, resolution_risk,
                 evidence, sources, action, pass_reason, edge, size_usd, shares,
                 bankroll_usd, cost_usd, latency_ms)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                    $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,
                    $33,$34,$35,$36,$37,$38)
            """,
            f.candidate.condition_id, f.candidate.slug, f.candidate.question,
            f.candidate.token_yes, f.candidate.token_no, f.candidate.category,
            f.candidate.end_date, f.candidate.days_to_resolution,
            f.candidate.price, quote.ask_yes, quote.ask_no,
            f.candidate.liquidity, f.candidate.score,
            f.p_raw, f.p_calibrated, f.disagreement, len(f.per_model),
            dict(f.per_model), f.means_yes, f.is_ambiguous, f.ambiguity_note,
            f.reference_class, f.base_rate, f.key_evidence, f.steps_required,
            f.confidence, f.what_would_change_my_mind, f.resolution_risk,
            ev.text[:8000], ev.sources[:20],
            str(d.bet) if d else "PASS",
            # Por qué no se operó: el veto manda; si no hubo veto, el motivo
            # lo da edge.py (poco edge, fuera de banda, Kelly no positivo).
            # `d is not None and not d` = hubo decisión y fue PASS; Decision
            # define __bool__, así que `not d` solo dice "es PASS".
            f.veto_reason or (d.reason if (d is not None and not d) else None),
            d.edge if d else None, d.size_usd if d else None,
            d.shares if d else None, bankroll_usd, f.cost_usd + ev.cost_usd,
            f.latency_ms,
        )

    async def _fetch_markets(self) -> list[dict]:
        rows: list[dict] = []
        async with httpx.AsyncClient(timeout=45) as http:
            for offset in range(0, MARKET_PAGES * 100, 100):
                r = await http.get(f"{self.s.gamma_host}/markets", params={
                    "active": "true", "closed": "false", "archived": "false",
                    "limit": 100, "offset": offset,
                    "order": "volumeNum", "ascending": "false"})
                if r.status_code != 200:
                    break
                batch = r.json()
                if not batch:
                    break
                rows.extend(batch)
        return rows

    # ── resolución ───────────────────────────────────────────────────────

    async def resolver_loop(self) -> None:
        from pilot_a_llm.resolution import ResolutionTracker, learning_report

        tracker = ResolutionTracker()
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=RESOLVER_INTERVAL_S)
            if self._stop.is_set():
                return
            try:
                resolved = await tracker.sweep()
                if not resolved:
                    continue
                report = await learning_report()
                log.info("aprendizaje: %s", report)

                # Recarga calibración y pesos con los datos nuevos.
                calib, weights = await self._load_learning()
                self.forecaster.calibration = calib
                self.forecaster.weights = weights

                if report["ready_to_judge"]:
                    bss = report["skill_vs_market"]
                    verdict = "HAY EDGE" if (bss or 0) > 0 else "EL MERCADO GANA"
                    await self.alerts.send(
                        Level.INFO if (bss or 0) > 0 else Level.WARN,
                        f"{len(resolved)} mercados resueltos — {verdict}",
                        f"brier skill vs mercado: {bss}\n"
                        f"n={report['n_resolved']} · PnL ${report['pnl_usd']}",
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception("resolver falló: %s", exc)

    # ── salud y apagado ──────────────────────────────────────────────────

    # ── verbos que expone la API (los usa n8n) ───────────────────────────

    async def _cycle_now(self) -> dict:
        """POST /cycle — n8n decide cuándo; el agente decide qué."""
        await self._cycle()
        return self._cycle_stats or {"vistos": 0, "nota": "sin candidatos"}

    async def _resolve_now(self) -> dict:
        """POST /resolve — cierra el bucle de aprendizaje."""
        from pilot_a_llm.resolution import ResolutionTracker, learning_report

        resolved = await ResolutionTracker().sweep()
        if resolved:
            # Recarga calibración y pesos con lo recién aprendido.
            calib, weights = await self._load_learning()
            self.forecaster.calibration = calib
            self.forecaster.weights = weights
        report = await learning_report()
        return {"resueltos": len(resolved)} | report

    @staticmethod
    async def _report_now() -> dict:
        from pilot_a_llm.resolution import learning_report

        return await learning_report()

    def _kill_now(self, reason: str) -> None:
        """POST /kill — botón de pánico desde n8n o Telegram."""
        if self.kernel is not None:
            self.kernel.trip(reason)
        self._stop.set()

    def _status(self) -> dict:
        return {
            "service": "pilot_a",
            "mode": str(self.s.mode),
            "killed": bool(self.kernel and self.kernel.killed),
            "kill_reason": self.kernel.kill_reason if self.kernel else "",
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "last_cycle_stats": self._cycle_stats,
            "calibration_active": bool(
                self.forecaster and self.forecaster.calibration
                and self.forecaster.calibration.active
            ),
            "budget": self.budget.snapshot(),
        }

    async def run(self) -> None:
        await self.start()
        tasks = [
            asyncio.create_task(self.scout_loop(), name="scout"),
            asyncio.create_task(self.resolver_loop(), name="resolver"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.exec_client.cancel_all()
            if self._api:
                await self._api.stop()
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
