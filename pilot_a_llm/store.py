"""
Persistencia del piloto A.

POR QUÉ EXISTE
──────────────
El INSERT de `forecasts` tiene 38 columnas. Escrito con parámetros
posicionales (`$1 … $38`) era imposible de mantener: añadir una columna
obligaba a contar a mano, y un desajuste entre el orden de las columnas y
el de los valores no falla —guarda el dato en la columna equivocada—.

Aquí se arma desde un diccionario, así que el nombre y el valor viajan
juntos y el SQL se genera solo.
"""

from __future__ import annotations

import logging
from typing import Any

from core.storage import db

log = logging.getLogger("store")


async def save_run(r, *, bankroll_usd: float) -> int | None:
    """Guarda un MarketRun completo, haya operado o no."""
    c = r.candidate
    f = r.forecast
    d = r.decision
    ev = r.evidence

    fila: dict[str, Any] = {
        "condition_id": c.condition_id,
        "slug": c.slug,
        "question": c.question,
        "token_yes": c.token_yes,
        "token_no": c.token_no,
        "category": c.category,
        "end_date": c.end_date,
        "days_to_resolution": c.days_to_resolution,
        "p_market": c.price,
        "liquidity": c.liquidity,
        "opportunity_score": c.score,
        "ask_yes": r.quote.ask_yes if r.quote else None,
        "ask_no": r.quote.ask_no if r.quote else None,
        "bankroll_usd": bankroll_usd,
        "cost_usd": r.cost_usd,
        "neurons": r.neurons,
        "action": r.accion,
        # `parado_en` dice en qué paso murió; el motivo, por qué.
        "pass_reason": r.motivo or None,
    }

    if ev is not None:
        fila |= {
            "evidence": (ev.text or "")[:8000],
            "sources": (ev.sources or [])[:20],
        }

    if f is not None:
        fila |= {
            "p_raw": f.p_raw,
            "p_calibrated": f.p_calibrated,
            "disagreement": f.disagreement,
            "n_models": len(f.per_model),
            "per_model": dict(f.per_model),
            "means_yes": f.means_yes,
            "is_ambiguous": f.is_ambiguous,
            "ambiguity_note": f.ambiguity_note,
            "reference_class": f.reference_class,
            "base_rate": f.base_rate,
            "key_evidence": f.key_evidence,
            "steps_required": f.steps_required,
            "confidence": f.confidence,
            "what_would_change_my_mind": f.what_would_change_my_mind,
            "resolution_risk": f.resolution_risk,
            "latency_ms": f.latency_ms,
        }
    else:
        # Sin pronóstico igual se guarda la fila: saber que un mercado se
        # descartó en `investigar` es un dato, no un hueco.
        fila |= {"p_raw": c.price, "p_calibrated": c.price}

    if d is not None:
        fila |= {
            "edge": d.edge,
            "size_usd": d.size_usd,
            "shares": d.shares,
        }

    return await _insert("pilot_a.forecasts", fila)


async def _insert(tabla: str, fila: dict[str, Any]) -> int | None:
    """
    Genera el INSERT desde el diccionario. Nombre y valor viajan juntos,
    así que no hay forma de desalinearlos.
    """
    columnas = list(fila)
    marcadores = ", ".join(f"${i}" for i in range(1, len(columnas) + 1))
    sql = (
        f"INSERT INTO {tabla} ({', '.join(columnas)}) "
        f"VALUES ({marcadores}) RETURNING id"
    )
    try:
        return await db.pool().fetchval(sql, *fila.values())
    except Exception as exc:  # noqa: BLE001 — guardar nunca tumba un ciclo
        log.error("no se pudo guardar %s: %s", fila.get("slug", "?"), exc)
        return None
