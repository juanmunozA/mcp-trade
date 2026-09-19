"""
El orquestador, como script. Reemplaza a n8n donde no hay dónde alojarlo.

    python -m scripts.orquestar ciclo        # el ciclo de pronóstico
    python -m scripts.orquestar salud        # ¿vive el agente?
    python -m scripts.orquestar aprender     # resoluciones + veredicto

POR QUÉ EXISTE
──────────────
n8n necesita ~1 GB y ningún free tier lo da: Render son 512 MB (murió por
heap dos veces) y Hugging Face cobra PRO para correr Docker. GitHub Actions
da un runner de 2 núcleos y 7 GB, gratis, 2.000 minutos al mes en repos
privados — y estos ciclos gastan ~30.

Hace lo mismo que el workflow `1-ciclo` de n8n, con la misma política. Lo
que se pierde es la interfaz visual; los umbrales siguen siendo constantes
editables, aquí arriba.

El agente sigue siendo el que decide QUÉ pasa. Esto solo decide cuándo y
por cuál camino.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = os.environ.get("AGENT_URL", "http://localhost:10000").rstrip("/")
TOKEN = os.environ.get("AGENT_API_TOKEN", "")
AUTH = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


# ── POLÍTICA · editable sin tocar el agente ──────────────────────────────

SIN_ALPHA = ["cripto"]
"""Categorías donde el modelo está mal calibrado. PolyBench lo mide en cripto."""

DIAS_MIN = 2
"""Resolver mañana no da tiempo a que el mercado reaccione."""

TOPE = 3
"""Cuántos mercados analizar por ciclo."""

DIVERGENCIA_MIN = 0.06
"""Por debajo de esto no compensa pagar el spread."""

SOLO_ESCALADOS = False
"""True exige que el ensemble haya pedido segunda opinión."""


def que_mirar(cuerpo: dict) -> list[dict]:
    """Primera decisión: de todo lo que hay, qué vale la pena analizar."""
    validas = cuerpo.get("categorias") or []
    fantasmas = [c for c in SIN_ALPHA if validas and c not in validas]
    if fantasmas:
        # Un nombre mal escrito no filtra nada y te deja creyendo que sí.
        raise SystemExit(
            f"SIN_ALPHA tiene categorías que no existen: {fantasmas}. "
            f"Válidas: {validas}"
        )
    cs = cuerpo.get("candidatos") or []
    pasan = [
        c for c in cs
        if c.get("categoria") not in SIN_ALPHA and (c.get("dias") or 999) >= DIAS_MIN
    ]
    pasan.sort(key=lambda c: c.get("puntaje") or 0, reverse=True)
    return pasan[:TOPE]


def vale_operarlo(f: dict) -> tuple[bool, str]:
    """
    Segunda decisión: con el pronóstico en la mano, ¿se opera?

    Los tres vetos y el risk kernel NO están aquí: los aplica el agente
    siempre. Son seguridad, no política.
    """
    if not f.get("ok"):
        return False, f.get("motivo") or f.get("parado_en") or "análisis falló"
    if f.get("vetado"):
        return False, "vetado por el agente"
    div = f.get("divergencia")
    if div is None:
        return False, "sin divergencia"
    if div < DIVERGENCIA_MIN:
        return False, f"divergencia {div} < {DIVERGENCIA_MIN}"
    if SOLO_ESCALADOS and not f.get("escalado"):
        return False, "sin segunda opinión"
    return True, "pasa"


# ── llamadas al agente ───────────────────────────────────────────────────


def pedir(metodo: str, ruta: str, cuerpo: dict | None = None, timeout: int = 900) -> dict:
    r = httpx.request(metodo, f"{URL}{ruta}", headers=AUTH, json=cuerpo, timeout=timeout)
    if r.status_code == 409:
        raise SystemExit("el agente ya tiene un ciclo corriendo; no se solapa")
    r.raise_for_status()
    return r.json()


# ── los tres modos ───────────────────────────────────────────────────────


def ciclo() -> int:
    cand = pedir("GET", f"/candidates?n={TOPE * 3}", timeout=180)
    elegidos = que_mirar(cand)
    print(f"{cand.get('n', 0)} candidatos → {len(elegidos)} elegidos"
          f"{'  · ' + cand['nota'] if cand.get('nota') else ''}")
    if not elegidos:
        return 0

    freno = {"agente": 0, "politica": 0, "kernel": 0}
    ordenes = []
    for c in elegidos:
        slug = c["slug"]
        f = pedir("POST", "/forecast", {"slug": slug})
        operar, razon = vale_operarlo(f)

        etiqueta = f"{f.get('p')} vs {f.get('precio_mercado')}"
        if not operar:
            freno["agente" if (f.get("vetado") or not f.get("ok")) else "politica"] += 1
            print(f"  ·  {slug[:44]:44} {etiqueta:18} {razon[:46]}")
            continue

        t = pedir("POST", "/trade", {"slug": slug}, timeout=180)
        if t.get("opero"):
            ordenes.append(t)
            print(f"  🟢 {slug[:44]:44} {etiqueta:18} "
                  f"{t.get('accion')} ${t.get('size_usd')} edge {t.get('edge')}")
        else:
            freno["kernel"] += 1
            print(f"  ✗  {slug[:44]:44} {etiqueta:18} {str(t.get('motivo'))[:46]}")

    print(f"\n{len(ordenes)} órdenes · frenó: agente {freno['agente']} · "
          f"política {freno['politica']} · kernel {freno['kernel']}")
    return 0


def salud() -> int:
    r = httpx.get(f"{URL}/health", timeout=120)
    d = r.json()
    print(json.dumps(d, indent=1, ensure_ascii=False)[:1200])
    if d.get("killed"):
        print(f"\nKILL SWITCH ACTIVO: {d.get('kill_reason')}")
        return 1
    return 0 if r.status_code == 200 else 1


def aprender() -> int:
    d = pedir("POST", "/resolve", timeout=600)
    n = d.get("n_resolved", 0)
    bss = d.get("skill_vs_market")
    print(f"resoluciones: {n}  ·  skill_vs_market: {bss}  ·  PnL ${d.get('pnl_usd')}")
    if not d.get("ready_to_judge"):
        print("todavía no se puede juzgar: hacen falta 30 resoluciones")
    elif bss is not None and bss <= 0:
        print("APAGAR: el precio de mercado pronostica igual o mejor que el agente")
        return 1
    else:
        print("hay ventaja sobre el mercado")
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "ciclo"
    sys.exit({"ciclo": ciclo, "salud": salud, "aprender": aprender}[modo]())
