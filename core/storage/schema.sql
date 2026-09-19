-- ════════════════════════════════════════════════════════════════════
--  Esquema de MCP Trade.
--
--  Esto es simultáneamente:
--    · tu contabilidad (la DIAN va a querer ver esto algún día)
--    · tu dataset de entrenamiento
--    · tu post-mortem cuando algo salga mal
--
--  Por eso se graba TODO, incluidas las órdenes rechazadas y las
--  decisiones del LLM que nunca se ejecutaron. Los rechazos son la
--  señal más útil que vas a tener sobre si tus límites están bien
--  calibrados.
-- ════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS pilot_a;
CREATE SCHEMA IF NOT EXISTS pilot_b;


-- ─────────────────────── datos de mercado (compartidos) ──────────────

CREATE TABLE IF NOT EXISTS market.markets (
    condition_id    TEXT PRIMARY KEY,
    slug            TEXT NOT NULL,
    question        TEXT,
    token_up        TEXT NOT NULL,
    token_down      TEXT NOT NULL,
    end_date        TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    raw             JSONB,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_markets_slug ON market.markets (slug);
CREATE INDEX IF NOT EXISTS idx_markets_active ON market.markets (active, end_date);

-- Snapshots del top del book. Alta frecuencia: particionar por día.
CREATE TABLE IF NOT EXISTS market.book_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    token_id        TEXT NOT NULL,
    best_bid        NUMERIC(10, 6),
    best_ask        NUMERIC(10, 6),
    bid_size        NUMERIC(18, 6),
    ask_size        NUMERIC(18, 6),
    mid             NUMERIC(10, 6),
    spread          NUMERIC(10, 6),
    -- profundidad agregada dentro de 5c del mid, para medir liquidez real
    depth_bid_5c    NUMERIC(18, 6),
    depth_ask_5c    NUMERIC(18, 6)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS idx_book_token_ts ON market.book_snapshots (token_id, ts DESC);

-- Trades públicos (para medir flujo y quién más está operando)
CREATE TABLE IF NOT EXISTS market.public_trades (
    ts              TIMESTAMPTZ NOT NULL,
    token_id        TEXT NOT NULL,
    price           NUMERIC(10, 6) NOT NULL,
    size            NUMERIC(18, 6) NOT NULL,
    side            TEXT
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS idx_ptrades_token_ts ON market.public_trades (token_id, ts DESC);

-- Particiones DEFAULT para que los INSERT funcionen desde el minuto cero.
-- `scripts/init_db.py --partitions` crea las mensuales; la DEFAULT es la red
-- de seguridad para que nunca se pierda un tick por falta de partición.
CREATE TABLE IF NOT EXISTS market.book_snapshots_default
    PARTITION OF market.book_snapshots DEFAULT;
CREATE TABLE IF NOT EXISTS market.public_trades_default
    PARTITION OF market.public_trades DEFAULT;

-- Salud del collector. Sin esto no puedes probar el gate de fase 0.
CREATE TABLE IF NOT EXISTS market.collector_health (
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    event           TEXT NOT NULL,      -- connected | disconnected | gap | error
    detail          TEXT,
    gap_seconds     NUMERIC(10, 2)
);
CREATE INDEX IF NOT EXISTS idx_health_ts ON market.collector_health (ts DESC);


-- ─────────────────── tablas por piloto (mismo DDL, 2 schemas) ────────

DO $$
DECLARE s TEXT;
BEGIN
FOREACH s IN ARRAY ARRAY['pilot_a', 'pilot_b'] LOOP

  -- Toda intención, aprobada o no. Los rechazos son el dato valioso.
  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS %I.order_intents (
        id           BIGSERIAL PRIMARY KEY,
        ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
        token_id     TEXT NOT NULL,
        market_slug  TEXT,
        side         TEXT NOT NULL,
        price        NUMERIC(10, 6) NOT NULL,
        size         NUMERIC(18, 6) NOT NULL,
        notional     NUMERIC(18, 6) NOT NULL,
        mid_at_check NUMERIC(10, 6),
        approved     BOOLEAN NOT NULL,
        reject_code  TEXT,
        reject_detail TEXT,
        reason       TEXT,
        strategy     TEXT,
        policy_id    BIGINT
    )$f$, s);
  EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_intents_ts ON %I.order_intents (ts DESC)', s, s);
  EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_intents_reject ON %I.order_intents (reject_code) WHERE NOT approved', s, s);

  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS %I.orders (
        order_id     TEXT PRIMARY KEY,
        intent_id    BIGINT REFERENCES %I.order_intents(id),
        ts_sent      TIMESTAMPTZ NOT NULL DEFAULT now(),
        ts_final     TIMESTAMPTZ,
        token_id     TEXT NOT NULL,
        side         TEXT NOT NULL,
        price        NUMERIC(10, 6) NOT NULL,
        size         NUMERIC(18, 6) NOT NULL,
        filled_size  NUMERIC(18, 6) NOT NULL DEFAULT 0,
        status       TEXT NOT NULL,  -- sent|open|filled|partial|cancelled|rejected
        is_paper     BOOLEAN NOT NULL,
        latency_ms   INTEGER,
        raw          JSONB
    )$f$, s, s);
  EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_orders_status ON %I.orders (status, ts_sent DESC)', s, s);

  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS %I.fills (
        id           BIGSERIAL PRIMARY KEY,
        ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
        order_id     TEXT,
        token_id     TEXT NOT NULL,
        side         TEXT NOT NULL,
        price        NUMERIC(10, 6) NOT NULL,
        size         NUMERIC(18, 6) NOT NULL,
        fee          NUMERIC(18, 6) NOT NULL DEFAULT 0,
        realized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
        -- slippage vs el mid al momento de la intención: mide tu edge real
        slippage_bps NUMERIC(10, 2),
        is_paper     BOOLEAN NOT NULL
    )$f$, s);
  EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_fills_ts ON %I.fills (ts DESC)', s, s);

  -- Foto diaria: alimenta el reporte de Telegram y los gates de fase.
  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS %I.daily_stats (
        day             DATE PRIMARY KEY,
        realized_pnl    NUMERIC(18, 6) NOT NULL DEFAULT 0,
        unrealized_pnl  NUMERIC(18, 6) NOT NULL DEFAULT 0,
        equity          NUMERIC(18, 6),
        n_intents       INTEGER NOT NULL DEFAULT 0,
        n_rejected      INTEGER NOT NULL DEFAULT 0,
        n_orders        INTEGER NOT NULL DEFAULT 0,
        n_fills         INTEGER NOT NULL DEFAULT 0,
        fill_rate       NUMERIC(6, 4),
        max_drawdown    NUMERIC(18, 6),
        killed          BOOLEAN NOT NULL DEFAULT FALSE,
        kill_reason     TEXT
    )$f$, s);

  -- Eventos del risk kernel: cada kill switch queda registrado para siempre.
  EXECUTE format($f$
    CREATE TABLE IF NOT EXISTS %I.risk_events (
        id           BIGSERIAL PRIMARY KEY,
        ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
        event        TEXT NOT NULL,   -- trip | reset | reconcile_drift
        reason       TEXT NOT NULL,
        snapshot     JSONB
    )$f$, s);

END LOOP;
END $$;


-- ───────────────── específico del piloto A (el LLM) ──────────────────

-- La política que emite el estratega. Versionada: puedes reproducir
-- exactamente qué "pensaba" el modelo cuando entró cada trade.
CREATE TABLE IF NOT EXISTS pilot_a.policies (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    model           TEXT NOT NULL,
    regime          TEXT,
    bias            TEXT,
    max_position_usd NUMERIC(18, 6),
    strategies_on   TEXT[],
    notes           TEXT,
    -- reproducibilidad: sin esto no puedes depurar una decisión mala
    prompt          TEXT NOT NULL,
    raw_response    TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        NUMERIC(10, 6),
    valid           BOOLEAN NOT NULL DEFAULT TRUE,
    validation_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_policies_ts ON pilot_a.policies (ts DESC);

-- El journal: lo que el agente lee de sí mismo en el siguiente ciclo.
-- Esto es el "entrenamiento" real del piloto A.
CREATE TABLE IF NOT EXISTS pilot_a.journal (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    policy_id       BIGINT REFERENCES pilot_a.policies(id),
    thesis          TEXT NOT NULL,
    outcome_pnl     NUMERIC(18, 6),
    outcome_note    TEXT,
    closed          BOOLEAN NOT NULL DEFAULT FALSE
);


-- Un pronóstico por mercado y ciclo. Es el corazón del piloto A: sin esta
-- tabla no hay calibración, y sin calibración no hay forma de saber si el
-- agente sirve para algo.
CREATE TABLE IF NOT EXISTS pilot_a.forecasts (
    id               BIGSERIAL PRIMARY KEY,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    condition_id     TEXT NOT NULL,
    slug             TEXT NOT NULL,
    question         TEXT NOT NULL,
    token_yes        TEXT NOT NULL,
    token_no         TEXT NOT NULL,
    category         TEXT,
    end_date         TIMESTAMPTZ,
    days_to_resolution INTEGER,

    -- precio cuando pronosticamos: sin esto no se puede calcular el Brier
    -- skill score contra el mercado, que es EL criterio para matar el piloto
    p_market         NUMERIC(10, 6) NOT NULL,
    ask_yes          NUMERIC(10, 6),
    ask_no           NUMERIC(10, 6),
    liquidity        NUMERIC(18, 2),
    opportunity_score NUMERIC(6, 4),

    -- salida del ensemble
    p_raw            NUMERIC(10, 6) NOT NULL,   -- media ponderada, sin calibrar
    p_calibrated     NUMERIC(10, 6) NOT NULL,   -- tras CalibrationMap
    disagreement     NUMERIC(10, 6),            -- desv. est. entre modelos
    n_models         INTEGER NOT NULL DEFAULT 1,
    per_model        JSONB,                     -- {modelo: probabilidad}

    -- el trabajo mostrado: para leer un fallo y ver EN QUÉ PASO se rompió
    means_yes        TEXT,
    is_ambiguous     BOOLEAN NOT NULL DEFAULT FALSE,
    ambiguity_note   TEXT,
    reference_class  TEXT,
    base_rate        NUMERIC(10, 6),
    key_evidence     TEXT[],
    steps_required   TEXT,
    confidence       TEXT,
    what_would_change_my_mind TEXT,
    resolution_risk  TEXT,

    evidence         TEXT,
    sources          TEXT[],

    -- decisión
    action           TEXT NOT NULL,             -- YES | NO | PASS
    pass_reason      TEXT,
    edge             NUMERIC(10, 6),
    size_usd         NUMERIC(18, 6),
    shares           NUMERIC(18, 6),
    bankroll_usd     NUMERIC(18, 6),

    cost_usd         NUMERIC(10, 6) DEFAULT 0,
    neurons          NUMERIC(12, 2) DEFAULT 0,
    latency_ms       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_forecasts_ts ON pilot_a.forecasts (ts DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_condition ON pilot_a.forecasts (condition_id);
CREATE INDEX IF NOT EXISTS idx_forecasts_open ON pilot_a.forecasts (end_date)
    WHERE action <> 'PASS';

-- Cuando el mercado resuelve. Cada fila alimenta los bucles de aprendizaje:
-- calibración, pesos del ensemble, conocimiento y tu revisión manual.
CREATE TABLE IF NOT EXISTS pilot_a.resolutions (
    id               BIGSERIAL PRIMARY KEY,
    forecast_id      BIGINT NOT NULL REFERENCES pilot_a.forecasts(id),
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    condition_id     TEXT NOT NULL,
    outcome          BOOLEAN NOT NULL,          -- true = resolvió SÍ
    p_forecast       NUMERIC(10, 6) NOT NULL,
    p_market         NUMERIC(10, 6) NOT NULL,
    brier            NUMERIC(10, 6) NOT NULL,
    brier_market     NUMERIC(10, 6) NOT NULL,   -- ¿le ganamos al precio?
    category         TEXT,
    pnl_usd          NUMERIC(18, 6),
    post_mortem      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_resolutions_forecast
    ON pilot_a.resolutions (forecast_id);
CREATE INDEX IF NOT EXISTS idx_resolutions_ts ON pilot_a.resolutions (ts DESC);

-- Lecciones destiladas, no archivo de noticias. REGLA CRÍTICA: aquí solo
-- entra lo que salió de un pronóstico YA RESUELTO. Si el agente recupera
-- su propio razonamiento sin validar, refuerza sus errores.
CREATE TABLE IF NOT EXISTS pilot_a.knowledge (
    id               BIGSERIAL PRIMARY KEY,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind             TEXT NOT NULL,   -- resolution_pattern | base_rate
                                      -- post_mortem | source_reliability
    content          TEXT NOT NULL,
    category         TEXT,
    learned_from     BIGINT REFERENCES pilot_a.resolutions(id),
    times_used       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON pilot_a.knowledge (kind, category);


-- ─────────────── específico del piloto B (complete sets) ─────────────

CREATE TABLE IF NOT EXISTS pilot_b.sets (
    id              BIGSERIAL PRIMARY KEY,
    ts_opened       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ts_closed       TIMESTAMPTZ,
    condition_id    TEXT NOT NULL,
    market_slug     TEXT,
    shares          NUMERIC(18, 6) NOT NULL,
    cost_up         NUMERIC(10, 6) NOT NULL,
    cost_down       NUMERIC(10, 6) NOT NULL,
    combined_cost   NUMERIC(10, 6) NOT NULL,
    -- 1.00 - combined_cost, antes de fees
    edge            NUMERIC(10, 6) NOT NULL,
    resolved_pnl    NUMERIC(18, 6)
);
CREATE INDEX IF NOT EXISTS idx_sets_condition ON pilot_b.sets (condition_id);

-- Inventario sin parear = el residual direccional del que habla el tweet.
CREATE TABLE IF NOT EXISTS pilot_b.inventory (
    condition_id    TEXT PRIMARY KEY,
    shares_up       NUMERIC(18, 6) NOT NULL DEFAULT 0,
    shares_down     NUMERIC(18, 6) NOT NULL DEFAULT 0,
    avg_cost_up     NUMERIC(10, 6) NOT NULL DEFAULT 0,
    avg_cost_down   NUMERIC(10, 6) NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- El kill switch, donde de verdad dura.
--
-- El kernel lo escribe a disco, pero en un contenedor de disco efimero eso
-- se borra en cada reinicio: el freno de emergencia se soltaria solo, justo
-- cuando el agente ya perdio la cuenta de sus posiciones. Aqui sobrevive.
CREATE TABLE IF NOT EXISTS pilot_a.kill_switch (
    pilot       TEXT PRIMARY KEY,
    reason      TEXT NOT NULL,
    tripped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at  TIMESTAMPTZ,
    cleared_by  TEXT
);
