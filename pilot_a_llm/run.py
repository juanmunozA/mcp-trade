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
from core.events import BUS, emit
from core.polymarket.client import ReadOnlyClient, build_execution_client
from core.risk import RiskKernel, RiskLimits
from core.storage import db
from pilot_a_llm.bankroll import BankrollReader
from pilot_a_llm.budget import Budget
from pilot_a_llm.calibration import (
    MIN_SAMPLES_TO_CALIBRATE,
    CalibrationMap,
    Resolution,
    ensemble_weights,
)
from pilot_a_llm.forecast import Forecaster, VetoConfig
from pilot_a_llm.markets import CATEGORIAS, MarketFilter, select, summarize
from pilot_a_llm.pipeline import (
    ANALISIS,
    OPERACION,
    MarketRun,
    correr,
    resumen,
)
from pilot_a_llm.providers import (
    CloudflareProvider,
    GeminiProvider,
    GroqProvider,
    MistralProvider,
    OllamaProvider,
)
from pilot_a_llm.research import Researcher
from pilot_a_llm.store import save_run
from pilot_a_llm.vault import Vault

log = logging.getLogger("pilot_a")

PILOT = "a"
RESOLVER_INTERVAL_S = 6 * 3600
ANALISIS_TTL_S = int(os.getenv("ANALISIS_TTL_S", "3600"))
"""
Cuánto vale un pronóstico antes de operarlo. El precio se recotiza siempre,
así que lo que caduca es la evidencia, no el book.
"""
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
        self.max_order_usd: float = 0.0
        self.budget = Budget()
        self.vault = Vault(os.getenv("VAULT_DIR", "vault"))
        # Serializa el tramo check→orden del kernel cuando hay concurrencia.
        self._order_lock = asyncio.Lock()
        self._stop = asyncio.Event()

        # Lo que el explorador eligió, por slug. `/market` solo acepta estos:
        # es el filtro y la whitelist del kernel actuando como puerta.
        self._picks: dict = {}
        # Lo que /forecast dejó listo, y lo que /trade ya consumió: sin esto
        # el orquestador podría operar sin analizar, o dos veces el mismo.
        self._analizados: dict = {}
        self._operados: set = set()
        self._bankroll_usd: float = 0.0
        self._scout_note: str = ""
        # Mismo techo de concurrencia que el ciclo interno. Sin esto, n8n en
        # fan-out podría lanzar 15 pronósticos a la vez contra un Ollama que
        # atiende de a uno, y todos se quedan esperando hasta el timeout.
        self._market_sem = asyncio.Semaphore(CONCURRENCY)

    # ── arranque ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.s.mode is Mode.COLLECT:
            raise SystemExit("MODE=collect: usa paper o live para operar")

        if rescued := await net.ensure_resolvable(*net.hosts_from_settings(self.s)):
            log.warning("DNS rescatado por DoH: %s", rescued)

        await db.init_pool(self.s.database_url)

        providers, fallback = (
            await self._proveedores_locales() if self.s.local_mode
            else self._proveedores_nube()
        )

        calib, weights = await self._load_learning()
        self.forecaster = Forecaster(
            providers, calibration=calib, weights=weights,
            vetoes=VetoConfig(), vault=self.vault, fallback=fallback,
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
        self.max_order_usd = limits.max_order_usd
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
            candidates_fn=self._candidates_now,
            market_fn=self._market_now,
            forecast_fn=self._forecast_now,
            trade_fn=self._trade_now,
            activity_fn=self._activity_now,
            diag_fn=self._diagnostico,
        )
        await self._api.start()

        await self.alerts.send(
            Level.INFO,
            f"Piloto A arriba ({self.s.mode})",
            f"ensemble: {', '.join(providers)}\ninvestigación: {self.s.strategist_model}",
        )
        log.info("piloto A arriba | modo=%s | ensemble=%s", self.s.mode, list(providers))

    # ── proveedores: local o nube, mismo pipeline ────────────────────────

    async def _proveedores_locales(self) -> tuple[dict, dict]:
        """
        Todo en la máquina: Ollama para los modelos, SearXNG para buscar.
        Sin cuotas, sin credenciales, sin nadie de quien depender.

        El ensemble usa UN modelo en vez de dos. En la nube tener dos
        familias distintas daba errores descorrelacionados y con eso el veto
        por desacuerdo; en local, dos modelos serían el doble de tiempo de
        CPU para un desacuerdo mucho menos informativo — vienen del mismo
        linaje. Se prefiere un modelo más grande corriendo una vez.
        """
        from pilot_a_llm.research import LocalResearcher
        from pilot_a_llm.search import LocalSearch

        modelo = OllamaProvider(self.s.ollama_host, self.s.ollama_model)
        if not await modelo.available():
            raise SystemExit(
                f"Ollama no responde en {self.s.ollama_host} o le falta "
                f"'{self.s.ollama_model}'.\n"
                f"  docker compose up -d ollama\n"
                f"  docker compose exec ollama ollama pull {self.s.ollama_model}"
            )

        buscador = LocalSearch(self.s.searxng_host)
        # Destila con un modelo chico si está configurado: resumir páginas no
        # necesita razonamiento y en CPU la diferencia de tiempo es grande.
        destilador = (
            OllamaProvider(self.s.ollama_host, self.s.ollama_model_triage)
            if self.s.ollama_model_triage else modelo
        )
        self.researcher = LocalResearcher(buscador, destilador)

        providers = {self.s.ollama_model: modelo}

        # Groq como SEGUNDA opinión: recupera el veto por desacuerdo que
        # se pierde al usar un solo modelo. Es otra familia (Llama contra
        # DeepSeek), así que sus errores están descorrelacionados — que es
        # lo que hace informativo el desacuerdo.
        #
        # Va en el segundo escalón porque su cuota es chica: lo que ata son
        # ~100K tokens/día, unos 18 pronósticos. Suficiente para los ~4 por
        # ciclo que el modelo local marca como interesantes; no para los 15.
        if self.s.groq_api_key:
            providers[self.s.groq_model] = GroqProvider(
                self.s.groq_api_key.get_secret_value(), self.s.groq_model
            )
            log.info("Groq activo como segunda opinión: %s", self.s.groq_model)

        log.info(
            "modo LOCAL | modelo=%s | búsqueda=%s | ensemble=%s",
            self.s.ollama_model, self.s.searxng_host, list(providers),
        )
        # Sin respaldo: en local no hay cuota que agotar. Si Ollama se cae,
        # el ciclo falla y se reintenta.
        return providers, {}

    def _proveedores_nube(self) -> tuple[dict, dict]:
        """
        Sin máquinas propias: los modelos corren en servidores ajenos.

        ORDEN = CASCADA. El primero se consulta SIEMPRE, así que tiene que
        ser el de mayor cupo; el caro solo ve lo que el barato marca.
        Medido, por pronóstico y por día:

            ministral-14b (Mistral)      ~256/día    ← primero
            deepseek-r1-32b (Cloudflare)  ~50/día    ← solo escalados
            gemini                        250/día    ← respaldo

        Con DeepSeek de primero, un ciclo de 40 mercados agotaba el cupo
        diario entero. Pasó, y por eso el orden importa.

        Mistral entra de primero porque su cuota es diez veces la de
        Cloudflare. No es mejor juez —ante la misma pregunta, ministral-8b
        dijo 0.30 y el 14b 0.70— pero de filtro sirve, y deja el cupo caro
        para los mercados que valen el gasto.
        """
        if self.s.gemini_api_key is None:
            raise SystemExit(
                "falta GEMINI_API_KEY (la búsqueda web no es opcional).\n"
                "¿Querías modo local? Pon LOCAL_MODE=true en el .env."
            )

        providers: dict = {}

        if self.s.mistral_api_key:
            providers[self.s.mistral_model] = MistralProvider(
                self.s.mistral_api_key.get_secret_value(), self.s.mistral_model
            )

        acc, tok = self.s.cloudflare_account_id, self.s.cloudflare_api_token
        if acc and tok:
            secret = tok.get_secret_value()
            if not providers:
                # Sin Mistral, el escalón barato lo hace llama.
                providers["llama-3.1-8b"] = CloudflareProvider(
                    acc, secret, "@cf/meta/llama-3.1-8b-instruct"
                )
            providers["deepseek-r1-32b"] = CloudflareProvider(
                acc, secret, "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"
            )

        if not providers:
            raise SystemExit(
                "el ensemble necesita al menos MISTRAL_API_KEY o "
                "CLOUDFLARE_ACCOUNT_ID/API_TOKEN"
            )

        gemini = GeminiProvider(
            self.s.gemini_api_key.get_secret_value(), self.s.strategist_model
        )
        self.researcher = Researcher(gemini)

        # Groq suma una cuarta familia si está. Cuota chica (~18/día), así
        # que va al final: solo lo alcanzan los que sobreviven la cascada.
        if self.s.groq_api_key:
            providers[self.s.groq_model] = GroqProvider(
                self.s.groq_api_key.get_secret_value(), self.s.groq_model
            )

        log.info("modo NUBE | cascada=%s", list(providers))
        # Respaldo en otra nube: cuando el resto agota su cuota, Gemini
        # sigue pronosticando con la suya.
        return providers, {"gemini-respaldo": gemini}

    async def _load_learning(self) -> tuple[CalibrationMap | None, dict]:
        """
        Reconstruye lo aprendido desde la base. Con pocas resoluciones esto
        devuelve (None, {}) y el agente opera sin corrección — que es lo
        correcto: calibrar con 8 muestras es superstición.
        """
        rows = await db.pool().fetch(
            "SELECT p_forecast, outcome, p_market, category, condition_id "
            "FROM pilot_a.resolutions"
        )
        if not rows:
            return None, {}
        # `condition_id` identifica el mercado: sin él, los 30 pronósticos
        # del mismo mercado contarían como 30 muestras independientes.
        records = [
            Resolution(
                p_forecast=float(r["p_forecast"]), outcome=r["outcome"],
                p_market=float(r["p_market"]), category=r["category"] or "",
                market_slug=r["condition_id"],
            )
            for r in rows
        ]
        calib = CalibrationMap(records)
        log.info("calibración: %s (%d resoluciones)", calib, len(records))

        per_model = await db.pool().fetch(
            """
            SELECT key AS model, (value)::text::numeric AS p, r.outcome,
                   f.p_market, r.condition_id
            FROM pilot_a.forecasts f
            JOIN pilot_a.resolutions r ON r.forecast_id = f.id
            CROSS JOIN LATERAL jsonb_each(f.per_model)
            """
        )
        history: dict[str, list[Resolution]] = {}
        for r in per_model:
            history.setdefault(r["model"], []).append(
                Resolution(float(r["p"]), r["outcome"], p_market=float(r["p_market"]),
                           market_slug=r["condition_id"])
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

    def _persistir_kill(self, motivo: str) -> None:
        """
        Gancho del kernel. Es sincrono porque el kernel lo es, asi que
        agenda la escritura; el freno local ya quedo puesto pase lo que pase
        con la base.
        """
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._guardar_kill(motivo))

    async def _guardar_kill(self, motivo: str) -> None:
        try:
            await db.guardar_kill(PILOT, motivo)
            log.warning("kill switch persistido en la base: %s", motivo)
        except Exception as exc:  # noqa: BLE001
            log.error("NO se pudo persistir el kill switch: %r", exc)

    async def _slots_rapidos(self, tope: int) -> int:
        """
        Cuantos lugares del ciclo reservar para mercados que cierran ya.

        Depende de cuantas resoluciones haya: con cero, la mitad del ciclo
        va a conseguir datos porque sin ellos no hay nada que aprender.
        Al acercarse a las 30 la reserva baja sola, y al llegar desaparece
        -- el ciclo vuelve a perseguir edge sin que nadie toque nada.
        """
        meta = MIN_SAMPLES_TO_CALIBRATE
        try:
            n = await db.pool().fetchval(
                "SELECT count(DISTINCT condition_id) FROM pilot_a.resolutions"
            ) or 0
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo contar resoluciones: %s", exc)
            return 0
        if n >= meta:
            return 0
        falta = 1.0 - (n / meta)
        return max(1, round(tope * 0.5 * falta))

    async def _vistos(self) -> frozenset[str]:
        """
        Lo pronosticado hace poco, para que el explorador prefiera lo nuevo.

        La ventana es corta a proposito. Con un filtro estrecho puede que
        solo 26 mercados pasen, y excluir todo lo visto dejaria al agente
        sin nada que hacer. Esto no excluye: reordena.
        """
        try:
            horas = int(os.getenv("REVISIT_HOURS", "48"))
        except ValueError:
            horas = 48
        if horas <= 0:
            return frozenset()
        try:
            rows = await db.pool().fetch(
                "SELECT DISTINCT slug FROM pilot_a.forecasts "
                "WHERE ts > now() - make_interval(hours => $1)", horas
            )
        except Exception as exc:  # noqa: BLE001
            # Sin base, el explorador sigue: peor es no explorar.
            log.warning("no se pudo leer lo ya pronosticado: %s", exc)
            return frozenset()
        return frozenset(r["slug"] for r in rows if r["slug"])

    async def explorar(self, limit: int = 0) -> list:
        """
        El explorador, solo. Filtra, puntúa, abre la whitelist del kernel y
        deja los elegidos listos para pronosticar.

        Es la mitad barata del ciclo —sin IA, sin costo— y por eso es un
        paso propio: n8n puede pedirla, ver qué salió y repartir el trabajo
        mercado por mercado en vez de disparar un ciclo opaco de 10 minutos.

        Devuelve [] si no hay capital, no hay presupuesto o no hay nada que
        valga la pena. El motivo queda en `self._scout_note`.
        """
        self._scout_note = ""
        bank = await self.bankroll.read()
        log.info("capital: %s", bank)
        if bank.deployable_usd <= 1.0:
            self._scout_note = "sin capital desplegable"
            log.warning("sin capital desplegable, saltando ciclo")
            return []
        self._bankroll_usd = bank.deployable_usd

        # Presupuesto ANTES de investigar. Quedarse sin Neurons a mitad del
        # ciclo tira a la basura llamadas a Gemini ya pagadas, y deja el
        # resto del día muerto sin que nadie se entere.
        models = [p.model for p in self.forecaster.providers.values()]
        affordable = self.budget.markets_affordable(models)

        if affordable <= 0:
            # Cloudflare agotado. Con respaldo el ciclo sigue —Gemini tiene
            # su propia cuota—; sin respaldo no hay nada que hacer.
            if self.forecaster.fallback and self.budget.gemini_left > 2:
                # Gemini investiga Y pronostica, así que gasta 2 llamadas
                # por mercado en vez de 1.
                affordable = self.budget.gemini_left // 2
                log.warning(
                    "Cloudflare agotado — respaldo con Gemini, %d mercados posibles",
                    affordable,
                )
            else:
                self._scout_note = "presupuesto agotado hasta las 00:00 UTC"
                log.warning("%s — %s", self._scout_note, self.budget.snapshot())
                return []

        pedido = limit if limit > 0 else MARKETS_PER_CYCLE
        tope = min(pedido, affordable)
        if tope < pedido:
            log.info(
                "presupuesto recorta el ciclo: %d → %d mercados (%.0f neurons libres)",
                pedido, tope, self.budget.usable_left,
            )

        raw = await self._fetch_markets()
        vistos = await self._vistos()
        rapidos = await self._slots_rapidos(tope)
        picks = select(raw, self._filtro(rapidos), limit=tope, vistos=vistos)
        emit("explorar", f"{len(raw)} mercados → {len(picks)} candidatos",
             crudos=len(raw), elegidos=len(picks), ya_vistos=len(vistos),
             categorias=sorted({c.category for c in picks}),
             nuevos=sum(1 for c in picks if c.slug not in vistos))
        log.info("%d mercados → %d candidatos\n%s", len(raw), len(picks), summarize(picks))
        if not picks:
            self._scout_note = "ningún mercado pasó el filtro"
            return []

        # La whitelist del kernel se cierra sobre los mercados de ESTE ciclo.
        tokens = frozenset(t for c in picks for t in (c.token_yes, c.token_no))
        self.kernel = RiskKernel(
            PILOT, RiskLimits.from_env(PILOT, allowed_token_ids=tokens),
            on_trip=self._persistir_kill,
        )
        # Un kill guardado en la base gana sobre cualquier arranque limpio:
        # en disco efimero el archivo local no sobrevive al reinicio.
        if (motivo := await db.leer_kill(PILOT)) and not self.kernel.killed:
            self.kernel.restaurar_kill(motivo)
        if self.kernel.killed:
            await self.alerts.killed(PILOT, self.kernel.kill_reason, self.kernel.snapshot())
            self._stop.set()
            self._scout_note = f"kill switch activo: {self.kernel.kill_reason}"
            return []

        # Lo que `/market` puede aceptar. Un slug que no esté aquí no pasó
        # por el filtro ni está en la whitelist del kernel, así que no entra
        # a la ruta de ejecución por mucho que lo pidan.
        self._picks = {c.slug: c for c in picks}
        self._analizados.clear()
        self._operados.clear()
        return picks

    def _filtro(self, slots_rapidos: int = 0) -> MarketFilter:
        """
        El filtro, con el horizonte configurable.

        Por diseño son 2-150 dias: menos de 2 no alcanzas a investigar ni a
        que se mueva el precio. Pero durante el arranque eso choca con algo
        mas urgente -- la calibracion no se activa hasta tener 30
        resoluciones, y con mercados a 15-45 dias eso son seis semanas
        operando a ciegas.

        Apuntando a mercados de 1-3 dias las resoluciones llegan en una
        semana. Se paga un precio: calibrar sobre "pasa manana" no traslada
        perfecto a "pasa en tres meses", son tareas distintas. Pero
        `resolutions` guarda la categoria y el horizonte, asi que despues se
        puede segmentar. Calibrar con datos imperfectos le gana a no
        calibrar.

            MARKET_MIN_DAYS=1  MARKET_MAX_DAYS=3   -> arranque rapido
            (sin poner nada)                       -> 2-150, el diseno
        """
        cats = os.getenv("MARKET_CATEGORIES", "")
        extra = {}
        if cats:
            # Abrir cripto y deportes tiene sentido SOLO porque la
            # calibracion ahora segmenta por categoria: su sesgo se corrige
            # aparte y no contamina al de politica. Sin eso, mezclarlas
            # empeora las dos.
            permitidas = frozenset(c.strip() for c in cats.split(",") if c.strip())
            extra = {"allowed_categories": permitidas,
                     "exclude_categories": frozenset(),
                     "sports_narrative_only": False}
        return MarketFilter(
            min_price=float(os.getenv("MARKET_MIN_PRICE", "0.12")),
            max_price=float(os.getenv("MARKET_MAX_PRICE", "0.88")),
            max_per_category=int(os.getenv("MARKET_MAX_PER_CATEGORY", "2")),
            min_days=int(os.getenv("MARKET_MIN_DAYS", "2")),
            max_days=int(os.getenv("MARKET_MAX_DAYS", "150")),
            min_liquidity_usd=float(os.getenv("MARKET_MIN_LIQUIDITY", "15000")),
            slots_rapidos=slots_rapidos,
            dias_rapido=int(os.getenv("MARKET_DIAS_RAPIDO", "3")),
            **extra,
        )

    async def _cycle(self) -> None:
        started = datetime.now(UTC)
        log.info("─── ciclo iniciado ───")

        picks = await self.explorar()
        if not picks:
            return

        # Investigación y pronóstico van en paralelo (son esperas de red).
        # Los pasos que tocan el kernel van bajo lock — ver pipeline.BAJO_LOCK.
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(c) -> MarketRun | None:
            # La cuota se comprueba DENTRO del bucle, no solo al empezar:
            # el presupuesto vive en memoria y se reinicia con el proceso,
            # así que un 429 puede llegar cuando creíamos tener cupo.
            if self.kernel.killed or self.forecaster.quota_exhausted:
                return None
            async with sem:
                if self.forecaster.quota_exhausted:
                    return None
                return await self._run_market(c, self._bankroll_usd)

        runs = [r for r in await asyncio.gather(*(one(c) for c in picks)) if r]

        self._last_cycle = datetime.now(UTC)
        self._cycle_stats = resumen(runs) | {
            "duracion_s": round((self._last_cycle - started).total_seconds(), 1),
            "presupuesto": self.budget.snapshot()["pct_consumido"],
        }
        log.info("─── ciclo terminado: %s ───", self._cycle_stats)

    # ── el pipeline de un mercado vive en pipeline.py ────────────────────
    # Aquí solo queda la orquestación: cuántos mercados, con qué capital y
    # cuándo. El "qué le pasa a cada mercado" es una lista de pasos leíble.

    async def _run_market(self, c, bankroll_usd: float) -> MarketRun:
        r = MarketRun(candidate=c, bankroll_usd=bankroll_usd)
        await correr(r, self, lock=self._order_lock)
        await save_run(r, bankroll_usd=bankroll_usd)
        self._to_vault(r)
        return r

    def _to_vault(self, r: MarketRun) -> None:
        """Escribir el vault nunca puede tumbar un ciclo."""
        if r.forecast is None:
            return
        try:
            size = r.decision.size_usd if (r.decision and r.decision) else None
            self.vault.write_forecast(r.forecast, r.accion, size)
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo escribir la nota del vault: %s", exc)

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
        return self._cycle_stats or {"vistos": 0, "nota": self._scout_note or "sin candidatos"}

    async def _candidates_now(self, n: int = 0) -> dict:
        """
        GET /candidates — el explorador, sin pronosticar.

        Lo que n8n hace con esto: un item por mercado, y de ahí un fan-out
        donde cada mercado es una ejecución que se ve, se reintenta y se
        ramifica sola.
        """
        picks = await self.explorar(n)
        return {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "nota": self._scout_note,
            "bankroll_usd": round(self._bankroll_usd, 2),
            # Las categorías que el agente sabe emitir. El orquestador filtra
            # por nombre, y un nombre que no exista no da error: simplemente
            # no filtra nada. Publicarlas deja que el nodo lo detecte.
            "categorias": CATEGORIAS,
            "n": len(picks),
            "candidatos": [
                {
                    "slug": c.slug,
                    "pregunta": c.question,
                    "precio": round(c.price, 4),
                    "liquidez": round(c.liquidity, 2),
                    "puntaje": round(c.score, 4),
                    "categoria": c.category,
                    "cierra": c.end_date.isoformat() if c.end_date else None,
                    "dias": c.days_to_resolution,
                }
                for c in picks
            ],
        }

    async def _forecast_now(self, slug: str) -> dict:
        """
        POST /forecast — investiga y pronostica. NO toca el mercado.

        Deja el resultado guardado para que `/trade` lo use. El orquestador
        ve la probabilidad, los vetos y la divergencia, y decide si vale la
        pena operarlo.
        """
        c = self._picks.get(slug)
        if c is None:
            return {"slug": slug, "ok": False,
                    "motivo": "no es candidato — corre GET /candidates primero"}
        if self.forecaster.quota_exhausted:
            return {"slug": slug, "ok": False, "motivo": "cuota de inferencia agotada"}

        r = MarketRun(candidate=c, bankroll_usd=self._bankroll_usd)
        async with self._market_sem:
            await correr(r, self, lock=self._order_lock, pasos=ANALISIS)
        self._to_vault(r)
        self._analizados[slug] = (r, datetime.now(UTC))

        f = r.forecast
        return {
            "slug": slug,
            "ok": f is not None and not r.parado_en,
            "pregunta": c.question,
            "categoria": c.category,
            "parado_en": r.parado_en,
            "motivo": r.motivo,
            "vetado": bool(f and f.vetoed),
            "p": round(f.p_calibrated, 4) if f else None,
            "precio_mercado": round(c.price, 4),
            "divergencia": round(abs(f.p_calibrated - c.price), 4) if f else None,
            "escalado": r.escalado,
            "dias": c.days_to_resolution,
            "fuentes": len(r.evidence.items) if r.evidence else 0,
            "costo_usd": round(r.cost_usd, 6),
        }

    async def _trade_now(self, slug: str) -> dict:
        """
        POST /trade — cotiza, dimensiona, valida riesgo y ordena.

        Exige un pronóstico previo de `/forecast`. Tres guardas, y las tres
        fallan cerrado: sin análisis no se opera, un análisis viejo no se
        opera, y un mercado ya operado no se repite. Un orquestador mal
        editado no puede saltarse el análisis ni duplicar una posición.
        """
        previo = self._analizados.get(slug)
        if previo is None:
            return {"slug": slug, "opero": False,
                    "motivo": "sin pronóstico — corre POST /forecast primero"}

        r, cuando = previo
        edad = (datetime.now(UTC) - cuando).total_seconds()
        if edad > ANALISIS_TTL_S:
            return {"slug": slug, "opero": False,
                    "motivo": f"pronóstico de hace {edad / 60:.0f} min "
                              f"(máximo {ANALISIS_TTL_S // 60})"}
        if slug in self._operados:
            return {"slug": slug, "opero": False, "motivo": "ya se operó en este ciclo"}
        if r.forecast is None or r.forecast.vetoed:
            return {"slug": slug, "opero": False,
                    "motivo": r.motivo or "vetado en el análisis"}
        if self.kernel is None or self.kernel.killed:
            motivo = self.kernel.kill_reason if self.kernel else "kernel sin inicializar"
            return {"slug": slug, "opero": False, "motivo": f"kill switch: {motivo}"}

        bank = await self.bankroll.read()
        if bank.deployable_usd <= 1.0:
            return {"slug": slug, "opero": False, "motivo": "sin capital desplegable"}
        r.bankroll_usd = bank.deployable_usd

        self._operados.add(slug)
        await correr(r, self, lock=self._order_lock, pasos=OPERACION)
        await save_run(r, bankroll_usd=bank.deployable_usd)
        self._last_cycle = datetime.now(UTC)

        d = r.decision
        return {
            "slug": slug,
            "opero": r.opero,
            "parado_en": r.parado_en or "completado",
            "motivo": r.motivo,
            "accion": r.accion,
            "edge": round(d.edge, 4) if (d is not None and d) else None,
            "size_usd": round(d.size_usd, 2) if (d is not None and d) else None,
            "orden_id": r.order.order_id if r.order else None,
        }

    async def _market_now(self, slug: str) -> dict:
        """
        POST /market {"slug": ...} — UN mercado por el pipeline completo.

        Indivisible a propósito: `validar` y `ejecutar` comparten el estado
        en memoria del kernel bajo un lock. Partirlos en dos llamadas HTTP
        devolvería la carrera que ese lock existe para evitar.
        """
        c = self._picks.get(slug)
        if c is None:
            return {
                "slug": slug, "corrido": False,
                "motivo": "no está entre los candidatos — corre GET /candidates primero",
            }
        if self.kernel is None or self.kernel.killed:
            motivo = self.kernel.kill_reason if self.kernel else "kernel sin inicializar"
            return {"slug": slug, "corrido": False, "motivo": f"kill switch: {motivo}"}
        if self.forecaster.quota_exhausted:
            return {"slug": slug, "corrido": False, "motivo": "cuota de inferencia agotada"}

        # El capital se relee por mercado: cada fill cambia lo desplegable,
        # y en fan-out pueden pasar minutos entre un mercado y el siguiente.
        bank = await self.bankroll.read()
        if bank.deployable_usd <= 1.0:
            return {"slug": slug, "corrido": False, "motivo": "sin capital desplegable"}

        async with self._market_sem:
            r = await self._run_market(c, bank.deployable_usd)

        self._last_cycle = datetime.now(UTC)
        d = r.decision
        return {
            "slug": slug,
            "corrido": True,
            "pregunta": c.question,
            "parado_en": r.parado_en or "completado",
            "motivo": r.motivo,
            "accion": r.accion,
            "opero": r.opero,
            "escalado": r.escalado,
            "precio_mercado": round(c.price, 4),
            "p_pronostico": round(r.forecast.p_calibrated, 4) if r.forecast else None,
            "edge": round(d.edge, 4) if (d is not None and d) else None,
            "size_usd": round(d.size_usd, 2) if (d is not None and d) else None,
            "orden_id": r.order.order_id if r.order else None,
            "neurons": round(r.neurons, 1),
            "costo_usd": round(r.cost_usd, 6),
        }

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

    async def _diagnostico(self) -> dict:
        """
        La vista que faltaba: en que se va el trabajo y que se rompe.

        Existe porque el agente parecia prudente -- 178 PASS-- y estaba
        roto: 105 de esos PASS eran excepciones. Un contador de PASS no
        distingue "no encontre nada" de "me cai", y esa diferencia es la
        unica que importaba.
        """
        q = db.pool()
        motivos = await q.fetch(
            """
            SELECT CASE
                     WHEN pass_reason LIKE '%%excepci%%' THEN 'se rompió'
                     WHEN pass_reason LIKE '%%ningún modelo%%' THEN 'sin modelo'
                     WHEN pass_reason LIKE '%%ambigua%%'  THEN 'pregunta ambigua'
                     WHEN pass_reason LIKE '%%divergencia%%' THEN 'veto por divergencia'
                     WHEN pass_reason = '' OR pass_reason IS NULL THEN 'decisión limpia'
                     ELSE 'otro'
                   END AS motivo,
                   count(*) AS n, max(ts) AS ultimo
            FROM pilot_a.forecasts WHERE action = 'PASS'
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        ensemble = await q.fetch(
            "SELECT n_models, count(*) n FROM pilot_a.forecasts GROUP BY 1 ORDER BY 1"
        )
        modelos = await q.fetch(
            "SELECT key AS modelo, count(*) n FROM pilot_a.forecasts "
            "CROSS JOIN LATERAL jsonb_each(per_model) GROUP BY 1 ORDER BY 2 DESC"
        )
        sesgo = await q.fetchrow(
            """
            SELECT count(*) n, count(DISTINCT slug) mercados,
                   avg(p_calibrated - p_market) sesgo,
                   count(*) FILTER (WHERE p_calibrated < p_market) debajo,
                   count(*) FILTER (WHERE abs(p_calibrated - p_market) > 0.30) lejos
            FROM pilot_a.forecasts
            """
        )
        cats = await q.fetch(
            "SELECT category, count(*) n, count(DISTINCT slug) mercados "
            "FROM pilot_a.forecasts GROUP BY 1 ORDER BY 2 DESC"
        )
        d = dict(sesgo)
        d["sesgo"] = float(d["sesgo"]) if d["sesgo"] is not None else None
        d["por_mercado"] = round(d["n"] / d["mercados"], 2) if d["mercados"] else 0
        return {
            "pass": [dict(r) for r in motivos],
            "ensemble": [dict(r) for r in ensemble],
            "modelos": [dict(r) for r in modelos],
            "resumen": d,
            "categorias": [dict(r) for r in cats],
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def _activity_now(self, n: int = 25) -> dict:
        """
        Lo ultimo que pronostico, mercado por mercado.

        Lleva `token_yes` porque el tablero grafica el precio con el, y
        `outcome` para poder marcar los que ya resolvieron.
        """
        n = max(1, min(n, 100))
        rows = await db.pool().fetch(
            """
            SELECT f.ts, f.slug, f.question, f.category, f.action,
                   f.p_calibrated, f.p_market, f.edge, f.size_usd,
                   f.end_date, f.days_to_resolution, f.token_yes,
                   f.condition_id, f.confidence, f.pass_reason,
                   r.outcome, r.brier
            FROM pilot_a.forecasts f
            LEFT JOIN pilot_a.resolutions r ON r.forecast_id = f.id
            ORDER BY f.ts DESC LIMIT $1
            """,
            n,
        )
        items = []
        for r in rows:
            d = dict(r)
            for k in ("p_calibrated", "p_market", "edge", "size_usd", "brier"):
                if d.get(k) is not None:
                    d[k] = float(d[k])
            items.append(d)
        return {"n": len(items), "items": items, "at": datetime.now(UTC).isoformat(timespec="seconds")}

    @staticmethod
    async def _report_now() -> dict:
        from pilot_a_llm.resolution import learning_report

        return await learning_report()

    def _kill_now(self, reason: str) -> None:
        """POST /kill — botón de pánico desde n8n o Telegram."""
        if self.kernel is not None:
            self.kernel.trip(reason)
        else:
            # Sin kernel todavia (antes del primer ciclo) igual hay que
            # dejarlo grabado, o el proximo arranque no se entera.
            self._persistir_kill(f"[manual] {reason}")
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
