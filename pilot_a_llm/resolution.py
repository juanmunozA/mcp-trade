"""
Cierre del bucle: qué pasó de verdad.

Este módulo es lo único que convierte al piloto A en algo que aprende.
Sin él hay un generador de opiniones caro; con él hay un sistema que se
corrige.

LA REGLA QUE EVITA EL AUTOENVENENAMIENTO
────────────────────────────────────────
Al `knowledge` solo entra lo que salió de un pronóstico **ya resuelto**,
junto con su resultado real. Nunca uno abierto, nunca razonamiento sin
validar.

La razón: si el agente recupera su propio razonamiento no verificado,
refuerza sus errores en lugar de corregirlos. Pronostica mal → guarda ese
razonamiento → lo recupera → se equivoca con más convicción. Es el exceso
de confianza que documentó PolyBench, pero con un mecanismo que lo amplifica
cada semana.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from core.storage import db

log = logging.getLogger("resolution")

GAMMA = "https://gamma-api.polymarket.com"


@dataclass(frozen=True, slots=True)
class Resolved:
    forecast_id: int
    condition_id: str
    outcome: bool
    p_forecast: float
    p_market: float
    category: str
    action: str
    size_usd: float
    shares: float

    @property
    def brier(self) -> float:
        return (self.p_forecast - float(self.outcome)) ** 2

    @property
    def brier_market(self) -> float:
        return (self.p_market - float(self.outcome)) ** 2

    @property
    def beat_market(self) -> bool:
        """Lo único que importa: ¿pronosticaste mejor que el precio?"""
        return self.brier < self.brier_market

    @property
    def pnl_usd(self) -> float:
        """
        PnL en paper. Cada share paga $1 si tu lado ganó, $0 si no.
        Ya pagaste `size_usd` al entrar.
        """
        if self.action == "PASS" or self.shares <= 0:
            return 0.0
        won = (self.action == "YES" and self.outcome) or (
            self.action == "NO" and not self.outcome
        )
        return round((self.shares if won else 0.0) - self.size_usd, 6)


class ResolutionTracker:
    """Corre a diario. Busca pronósticos cuyo mercado ya cerró."""

    def __init__(self, repo_schema: str = "pilot_a") -> None:
        self.schema = repo_schema

    async def pending(self) -> list[dict]:
        """Pronósticos sin resolución registrada y con fecha ya pasada."""
        rows = await db.pool().fetch(
            f"""
            SELECT f.id, f.condition_id, f.slug, f.question, f.category,
                   f.p_calibrated, f.p_market, f.action, f.size_usd, f.shares,
                   f.end_date, f.means_yes, f.key_evidence,
                   f.what_would_change_my_mind
            FROM {self.schema}.forecasts f
            LEFT JOIN {self.schema}.resolutions r ON r.forecast_id = f.id
            WHERE r.id IS NULL
              AND f.end_date IS NOT NULL
              AND f.end_date < now()
            ORDER BY f.end_date
            LIMIT 100
            """
        )
        return [dict(r) for r in rows]

    async def check_outcome(self, condition_id: str) -> bool | None:
        """
        Consulta a Gamma si el mercado cerró y con qué resultado.
        None = todavía no resolvió (o no se pudo determinar).

        `closed=true` NO es opcional. Gamma filtra por defecto a mercados
        abiertos, asi que pedir uno cerrado sin ese parametro devuelve una
        lista vacia -- y este metodo lo leia como "todavia no resolvio".

        Buscar mercados cerrados con una consulta que excluye los cerrados
        significaba que NADA podia resolverse nunca. Medido: dos mercados
        resueltos hace dos dias, `resolutions` en cero, y el resolver
        corriendo cada 6 horas sin encontrar nada.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(
                    f"{GAMMA}/markets",
                    params={"condition_ids": condition_id, "closed": "true"},
                )
                if r.status_code != 200:
                    return None
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo consultar %s: %s", condition_id, exc)
            return None

        market = data[0] if isinstance(data, list) and data else data
        if not isinstance(market, dict) or not market.get("closed"):
            return None

        # Polymarket expone el resultado de varias formas según el mercado.
        prices = market.get("outcomePrices")
        if isinstance(prices, str):
            import json
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, TypeError):
                prices = None
        if prices and len(prices) == 2:
            try:
                # Al resolver, el ganador queda en 1 y el perdedor en 0.
                p_yes = float(prices[0])
                if p_yes > 0.99:
                    return True
                if p_yes < 0.01:
                    return False
            except (TypeError, ValueError):
                pass
        return None

    async def sweep(self) -> list[Resolved]:
        """Registra todo lo que ya resolvió. Idempotente."""
        out: list[Resolved] = []
        for row in await self.pending():
            outcome = await self.check_outcome(row["condition_id"])
            if outcome is None:
                continue

            r = Resolved(
                forecast_id=row["id"],
                condition_id=row["condition_id"],
                outcome=outcome,
                p_forecast=float(row["p_calibrated"]),
                p_market=float(row["p_market"]),
                category=row["category"] or "",
                action=row["action"],
                size_usd=float(row["size_usd"] or 0),
                shares=float(row["shares"] or 0),
            )
            await self._record(r, row)
            out.append(r)

            verdict = "GANÓ al mercado" if r.beat_market else "perdió contra el mercado"
            log.info(
                "%s resolvió %s | dijimos %.3f, mercado %.3f | brier %.4f vs %.4f — %s",
                row["slug"][:40], "SÍ" if outcome else "NO",
                r.p_forecast, r.p_market, r.brier, r.brier_market, verdict,
            )
        return out

    async def _record(self, r: Resolved, row: dict) -> None:
        post_mortem = self._post_mortem(r, row)
        resolution_id = await db.pool().fetchval(
            f"""
            INSERT INTO {self.schema}.resolutions
                (forecast_id, condition_id, outcome, p_forecast, p_market,
                 brier, brier_market, category, pnl_usd, post_mortem)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (forecast_id) DO NOTHING
            RETURNING id
            """,
            r.forecast_id, r.condition_id, r.outcome, r.p_forecast, r.p_market,
            r.brier, r.brier_market, r.category, r.pnl_usd, post_mortem,
        )

        # Solo los errores grandes generan lección. Guardar los aciertos
        # llenaría el knowledge de ruido autocomplaciente.
        if resolution_id and r.brier > 0.25:
            await db.pool().execute(
                f"""
                INSERT INTO {self.schema}.knowledge (kind, content, category, learned_from)
                VALUES ('post_mortem', $1, $2, $3)
                """,
                post_mortem, r.category, resolution_id,
            )

    @staticmethod
    def _post_mortem(r: Resolved, row: dict) -> str:
        real = "SÍ" if r.outcome else "NO"
        lines = [
            f"Mercado: {row['question'][:200]}",
            f"Resolvió: {real}. Dijimos {r.p_forecast:.3f}; el mercado, {r.p_market:.3f}.",
            f"Brier {r.brier:.4f} vs mercado {r.brier_market:.4f} → "
            f"{'le ganamos' if r.beat_market else 'nos ganó'}.",
        ]
        if row.get("means_yes"):
            lines.append(f"Entendimos SÍ como: {row['means_yes'][:200]}")
        if r.brier > 0.25:
            lines.append(
                "ERROR GRANDE. Revisar: ¿leímos mal la pregunta, la tasa base, "
                "o faltó evidencia?"
            )
            if row.get("what_would_change_my_mind"):
                lines.append(
                    f"Dijimos que nos refutaría: {row['what_would_change_my_mind'][:200]}"
                )
        return "\n".join(lines)


async def learning_report(schema: str = "pilot_a") -> dict:
    """
    El tablero que decide si el piloto vive. `skill_vs_market > 0` significa
    que pronosticas mejor que el precio; si no, no hay negocio.
    """
    row = await db.pool().fetchrow(
        f"""
        SELECT count(*) n,
               avg(brier) brier,
               avg(brier_market) brier_market,
               count(*) FILTER (WHERE brier < brier_market) wins,
               COALESCE(sum(pnl_usd), 0) pnl
        FROM {schema}.resolutions
        """
    )
    n = row["n"] or 0
    brier = float(row["brier"] or 0)
    brier_mkt = float(row["brier_market"] or 0)
    by_cat = await db.pool().fetch(
        f"""
        SELECT category, count(*) n, avg(brier) brier, avg(brier_market) brier_market
        FROM {schema}.resolutions
        GROUP BY 1 HAVING count(*) >= 5 ORDER BY 2 DESC
        """
    )
    return {
        "n_resolved": n,
        "brier": round(brier, 4),
        "brier_market": round(brier_mkt, 4),
        "skill_vs_market": round(1 - brier / brier_mkt, 4) if brier_mkt > 0 else None,
        "beat_market_rate": round((row["wins"] or 0) / n, 3) if n else None,
        "pnl_usd": round(float(row["pnl"] or 0), 2),
        "ready_to_judge": n >= 30,
        "by_category": [dict(r) for r in by_cat],
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
