"""
Vuelca al vault los pronósticos que ya están en la base.

    python -m scripts.backfill_vault

Sirve para dos cosas: poblar el vault la primera vez, y reconstruirlo
cuando se pierde — por ejemplo si el agente corría en Render, cuyo disco
es efímero y borra las notas en cada reinicio.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from core.config import get_settings
from core.storage import db
from pilot_a_llm.forecast import Forecast
from pilot_a_llm.markets import Candidate
from pilot_a_llm.vault import Vault


def _to_forecast(r: dict) -> Forecast:
    c = Candidate(
        condition_id=r["condition_id"] or "",
        slug=r["slug"] or "sin-slug",
        question=r["question"] or "",
        rules="",
        token_yes=r["token_yes"] or "",
        token_no=r["token_no"] or "",
        price=float(r["p_market"]),
        liquidity=float(r["liquidity"] or 0),
        volume=0.0,
        days_to_resolution=int(r["days_to_resolution"] or 0),
        category=r["category"] or "otros",
        end_date=r["end_date"],
    )
    c.score = float(r["opportunity_score"] or 0)

    f = Forecast(
        candidate=c,
        p_raw=float(r["p_raw"]),
        p_calibrated=float(r["p_calibrated"]),
        per_model={k: float(v) for k, v in (r["per_model"] or {}).items()},
        disagreement=float(r["disagreement"] or 0),
        means_yes=r["means_yes"] or "",
        is_ambiguous=bool(r["is_ambiguous"]),
        ambiguity_note=r["ambiguity_note"] or "",
        reference_class=r["reference_class"] or "",
        base_rate=float(r["base_rate"] or 0.5),
        key_evidence=list(r["key_evidence"] or []),
        steps_required=r["steps_required"] or "",
        confidence=r["confidence"] or "low",
        what_would_change_my_mind=r["what_would_change_my_mind"] or "",
        resolution_risk=r["resolution_risk"] or "",
        cost_usd=float(r["cost_usd"] or 0),
    )
    # El motivo del PASS es lo que hace útil leer estas notas después.
    if r["action"] == "PASS" and r["pass_reason"]:
        f.vetoed = True
        f.veto_reason = r["pass_reason"]
    return f


async def main() -> int:
    s = get_settings()
    await db.init_pool(s.database_url)
    vault = Vault("vault")
    try:
        rows = await db.pool().fetch(
            """
            SELECT condition_id, slug, question, token_yes, token_no, category,
                   end_date, days_to_resolution, p_market, liquidity,
                   opportunity_score, p_raw, p_calibrated, disagreement,
                   per_model, means_yes, is_ambiguous, ambiguity_note,
                   reference_class, base_rate, key_evidence, steps_required,
                   confidence, what_would_change_my_mind, resolution_risk,
                   action, pass_reason, size_usd, cost_usd, ts
            FROM pilot_a.forecasts
            ORDER BY ts
            """
        )
    finally:
        await db.close_pool()

    if not rows:
        print("no hay pronósticos en la base todavía")
        return 0

    escritos = 0
    por_accion: dict[str, int] = {}
    for r in rows:
        d = dict(r)
        try:
            f = _to_forecast(d)
            size = float(d["size_usd"]) if d["size_usd"] is not None else None
            path = vault.write_forecast(f, d["action"], size)
            # Fecha real del pronóstico, no la de hoy: así Obsidian ordena bien.
            if (ts := d["ts"]) is not None:
                nuevo = path.with_name(f"{ts:%Y-%m-%d}-{path.name.split('-', 3)[-1]}")
                if nuevo != path:
                    path.replace(nuevo)
            escritos += 1
            por_accion[d["action"]] = por_accion.get(d["action"], 0) + 1
        except Exception as exc:  # noqa: BLE001
            print(f"  saltado {d['slug'][:40]}: {exc}")

    _escribir_indice(vault, rows, por_accion)
    vault.write_graph_config()

    print(f"{escritos} notas escritas en vault/pronosticos/\n")
    for accion, n in sorted(por_accion.items(), key=lambda kv: -kv[1]):
        print(f"  {accion:<6} {n}")
    print(f"\ncontexto que el agente lee: "
          f"{len(vault.read_context())} caracteres")
    print(f"generado {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return 0


def _escribir_indice(vault: Vault, rows: list, por_accion: dict[str, int]) -> None:
    """
    Nota de entrada del vault. Empieza con "_" para que el agente no la lea
    como lección: es un tablero para ti, no contexto para él.
    """
    total = len(rows)
    ordenes = por_accion.get("YES", 0) + por_accion.get("NO", 0)

    vetos: dict[str, int] = {}
    for r in rows:
        motivo = (r["pass_reason"] or "")
        if r["action"] != "PASS" or not motivo:
            continue
        clave = (
            "ambigüedad declarada" if "ambigua" in motivo
            else "desacuerdo del ensemble" if "desacuerdo" in motivo
            else "divergencia extrema" if "divergencia" in motivo
            else "edge insuficiente" if "edge" in motivo
            else "otro"
        )
        vetos[clave] = vetos.get(clave, 0) + 1

    pass_n = por_accion.get("PASS", 0)
    lineas = [
        "---", "tags: [indice]", "---", "",
        "# Agente de pronóstico", "",
        "> Este archivo empieza con `_`: el agente **no** lo lee. Es tu tablero.",
        "",
        f"**{total}** pronósticos · **{ordenes}** órdenes · "
        f"**{pass_n}** descartados · tasa de descarte "
        f"**{pass_n / max(total, 1) * 100:.0f}%**", "",
        "## El flujo", "",
    ]
    # La espina del grafo, en orden, como enlaces navegables.
    from pilot_a_llm.vault import FLUJO

    for i, (slug, titulo, _) in enumerate(FLUJO, 1):
        lineas.append(f"{i}. [[{slug}|{titulo}]]")

    lineas += ["", "## Por qué no operó", ""]
    for motivo, n in sorted(vetos.items(), key=lambda kv: -kv[1]):
        lineas.append(f"- **{n}** — {motivo}")

    lineas += [
        "", "## Órdenes abiertas", "",
    ]
    for r in rows:
        if r["action"] in ("YES", "NO"):
            lineas.append(
                f"- `{r['action']}` [[{r['ts']:%Y-%m-%d}-{r['slug'][:55]}]] — "
                f"agente {float(r['p_calibrated']):.3f} vs mercado "
                f"{float(r['p_market']):.3f}"
            )

    lineas += [
        "", "## Cómo leer el grafo", "", "```",
        "naranja   1-flujo/       la espina: los 6 pasos en orden",
        "verde     órdenes        lo que sí operó",
        "rojo      vetos          lo descartado, agrupado por motivo",
        "morado    4-temas/       por categoría",
        "oro       5-modelos/     quién opinó",
        "gris      3-mercados/    cada pronóstico, en la periferia",
        "turquesa  contexto/      TUS lecciones — lo único que llega al agente",
        "```", "",
        "Cuando un pronóstico salga mal, busca **en qué paso** se rompió. "
        "Si ves un patrón que se repite, escríbelo en `contexto/`.", "",
        "---", f"*generado {datetime.now(UTC):%Y-%m-%d %H:%M} UTC*", "",
    ]
    (vault.root / "_INICIO.md").write_text("\n".join(lineas), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
