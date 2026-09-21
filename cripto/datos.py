"""
Lo que el modelo ve del mercado: velas, indicadores y libro.

QUE SE LE DA Y POR QUE
──────────────────────
Se le da MUCHO a proposito, porque la pregunta que estamos midiendo es
justamente si mas informacion ayuda. Velas de tres escalas, los
indicadores que usa todo el mundo, el desequilibrio del libro y el
contexto de 24 horas.

Si con todo eso no le gana a una moneda al aire, la respuesta no es
"faltaba data".

LOS NUMEROS VAN REDONDEADOS
Un precio con ocho decimales gasta tokens y no aporta: la decision es
"sube o baja", no cuanto exactamente.
"""

from __future__ import annotations

import asyncio

import httpx

SPOT = "https://api.binance.com/api/v3"
FUT = "https://fapi.binance.com/fapi/v1"


def _ema(vals: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(cierres: list[float], n: int = 14) -> float:
    if len(cierres) < n + 1:
        return 50.0
    subes, bajas = [], []
    for a, b in zip(cierres[-n - 1:-1], cierres[-n:], strict=False):
        d = b - a
        subes.append(max(d, 0.0))
        bajas.append(max(-d, 0.0))
    ms, mb = sum(subes) / n, sum(bajas) / n
    if mb == 0:
        return 100.0
    rs = ms / mb
    return round(100 - 100 / (1 + rs), 1)


def _pct(a: float, b: float) -> float:
    return round((b - a) / a * 100, 3) if a else 0.0


async def _velas(cli: httpx.AsyncClient, simbolo: str, intervalo: str,
                 n: int) -> list[list]:
    r = await cli.get(f"{SPOT}/klines",
                      params={"symbol": simbolo, "interval": intervalo, "limit": n})
    return r.json() if r.status_code == 200 else []


async def foto(cli: httpx.AsyncClient, simbolo: str) -> dict | None:
    """Todo lo que se sabe de un par en este instante."""
    try:
        v5, v15, v1h, libro, t24 = await asyncio.gather(
            _velas(cli, simbolo, "5m", 60),
            _velas(cli, simbolo, "15m", 32),
            _velas(cli, simbolo, "1h", 24),
            cli.get(f"{SPOT}/depth", params={"symbol": simbolo, "limit": 50}),
            cli.get(f"{SPOT}/ticker/24hr", params={"symbol": simbolo}),
        )
    except Exception:  # noqa: BLE001
        return None
    if not v5 or not v15 or libro.status_code != 200 or t24.status_code != 200:
        return None

    c5 = [float(x[4]) for x in v5]
    c15 = [float(x[4]) for x in v15]
    c1h = [float(x[4]) for x in v1h] if v1h else c15
    vol5 = [float(x[5]) for x in v5]
    precio = c5[-1]

    d = libro.json()
    compra = sum(float(p) * float(q) for p, q in d.get("bids", [])[:20])
    venta = sum(float(p) * float(q) for p, q in d.get("asks", [])[:20])
    # Desequilibrio del libro en [-1, 1]. Positivo = mas dinero queriendo
    # comprar que vender en los 20 niveles de arriba.
    desq = round((compra - venta) / (compra + venta), 3) if compra + venta else 0.0

    t = t24.json()
    rangos = [(float(x[2]) - float(x[3])) / float(x[4]) for x in v5[-20:] if float(x[4])]
    volat = round(sum(rangos) / len(rangos) * 100, 3) if rangos else 0.0

    return {
        "simbolo": simbolo,
        "precio": round(precio, 4),
        # Velas recientes como texto corto: apertura, maximo, minimo, cierre
        # en variacion porcentual contra la apertura, que es lo que importa
        # para la forma, no el nivel absoluto.
        "velas_5m": [
            [_pct(float(x[1]), float(x[2])), _pct(float(x[1]), float(x[3])),
             _pct(float(x[1]), float(x[4]))] for x in v5[-12:]
        ],
        "cambio_5m": _pct(c5[-2], c5[-1]) if len(c5) > 1 else 0.0,
        "cambio_15m": _pct(c5[-4], c5[-1]) if len(c5) > 3 else 0.0,
        "cambio_1h": _pct(c5[-13], c5[-1]) if len(c5) > 12 else 0.0,
        "cambio_4h": _pct(c1h[-5], c1h[-1]) if len(c1h) > 4 else 0.0,
        "cambio_24h": round(float(t.get("priceChangePercent", 0)), 2),
        "rsi_5m": _rsi(c5),
        "rsi_15m": _rsi(c15),
        "ema9_vs_ema21": round((_ema(c5[-30:], 9) / _ema(c5[-30:], 21) - 1) * 100, 3),
        "precio_vs_ema50": round((precio / _ema(c5, 50) - 1) * 100, 3),
        "volatilidad_pct": volat,
        "volumen_vs_medio": round(vol5[-1] / (sum(vol5[-20:]) / 20), 2)
        if sum(vol5[-20:]) else 1.0,
        "libro_desequilibrio": desq,
        "pos_en_rango_24h": round(
            (precio - float(t["lowPrice"])) /
            max(1e-9, float(t["highPrice"]) - float(t["lowPrice"])), 3),
    }


async def fotos(simbolos: list[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=25) as cli:
        res = await asyncio.gather(*[foto(cli, s) for s in simbolos])
    return [r for r in res if r]


async def precios(simbolos: list[str]) -> dict[str, float]:
    """Precio de cierre ahora. Para resolver las predicciones vencidas."""
    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.get(f"{SPOT}/ticker/price")
        if r.status_code != 200:
            return {}
        todos = {x["symbol"]: float(x["price"]) for x in r.json()}
    return {s: todos[s] for s in simbolos if s in todos}
