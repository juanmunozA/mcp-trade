"""
Adapter sobre py-clob-client.

Existe por una razón: **el SDK cambia y tú no quieres que eso te toque 20
archivos.** Todo lo que sabe de `py_clob_client` vive aquí y en ningún otro
lado. Si Polymarket renombra un método, se arregla en este archivo.

Corre `python -m scripts.verify_sdk` para comprobar que el SDK instalado
expone lo que este adapter espera, ANTES de operar.

Tres backends con la misma interfaz:
  · ReadOnlyClient  — datos de mercado, sin credenciales
  · PaperClient     — simula fills contra el book REAL, incluyendo barrido
                      de profundidad y una latencia configurable
  · LiveClient      — plata de verdad
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

import httpx

from core.polymarket.models import Book, Level, Market, PlacedOrder
from core.risk import OrderIntent, Side


class ClobError(RuntimeError):
    pass


# ───────────────────────── interfaz común ────────────────────────────────


class ExecutionClient(Protocol):
    is_paper: bool

    async def place(self, intent: OrderIntent, *, book: Book) -> PlacedOrder: ...
    async def cancel(self, order_id: str) -> bool: ...
    async def cancel_all(self) -> int: ...
    async def positions(self) -> dict[str, float]: ...


# ───────────────────────── datos de mercado ──────────────────────────────


class ReadOnlyClient:
    """Sin credenciales. Es lo único que necesitas durante toda la fase 0."""

    def __init__(self, clob_host: str, gamma_host: str, *, timeout: float = 10.0) -> None:
        self.clob_host = clob_host.rstrip("/")
        self.gamma_host = gamma_host.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_book(self, token_id: str) -> Book:
        r = await self._http.get(f"{self.clob_host}/book", params={"token_id": token_id})
        r.raise_for_status()
        return self._parse_book(token_id, r.json())

    async def get_books(self, token_ids: list[str]) -> dict[str, Book]:
        """Una sola llamada para N tokens. Úsala: /book por token te tumba el rate limit."""
        r = await self._http.post(
            f"{self.clob_host}/books",
            json=[{"token_id": t} for t in token_ids],
        )
        r.raise_for_status()
        out: dict[str, Book] = {}
        for payload in r.json():
            tid = payload.get("asset_id") or payload.get("token_id")
            if tid:
                out[tid] = self._parse_book(tid, payload)
        return out

    @staticmethod
    def _parse_book(token_id: str, payload: dict) -> Book:
        def levels(key: str, reverse: bool) -> tuple[Level, ...]:
            raw = payload.get(key) or []
            parsed = [Level(float(x["price"]), float(x["size"])) for x in raw]
            return tuple(sorted(parsed, key=lambda lv: lv.price, reverse=reverse))

        return Book(
            token_id=token_id,
            ts=datetime.now(UTC),
            bids=levels("bids", reverse=True),
            asks=levels("asks", reverse=False),
        )

    async def active_markets(self, slug_filter: list[str] | None = None) -> list[Market]:
        """
        Vía Gamma API, que es la que expone slugs y metadata legible.
        El CLOB expone token_ids; Gamma expone qué significan.
        """
        params: dict = {"active": "true", "closed": "false", "limit": 200}
        r = await self._http.get(f"{self.gamma_host}/markets", params=params)
        r.raise_for_status()

        markets: list[Market] = []
        for m in r.json():
            slug = m.get("slug", "")
            if slug_filter and not any(s in slug for s in slug_filter):
                continue
            tokens = m.get("clobTokenIds") or []
            if isinstance(tokens, str):
                import json as _json
                tokens = _json.loads(tokens)
            if len(tokens) != 2:
                continue  # solo binarios
            end = m.get("endDate")
            markets.append(
                Market(
                    condition_id=m.get("conditionId", ""),
                    slug=slug,
                    question=m.get("question", ""),
                    token_up=tokens[0],
                    token_down=tokens[1],
                    end_date=datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None,
                )
            )
        return markets


# ───────────────────────── paper ─────────────────────────────────────────


class PaperClient:
    """
    Simula contra el book real. Tres cosas que un paper trader honesto DEBE
    hacer y que casi ninguno hace:

      1. Barrer profundidad de verdad (`cost_to_fill`), no asumir el mid.
      2. Cobrar latencia: la orden llega N ms tarde, el book ya se movió.
      3. No llenar órdenes límite que no cruzan el spread.

    Si tu estrategia solo funciona sin esto, no funciona.
    """

    is_paper = True

    def __init__(self, *, latency_ms: int = 120, fee_bps: float = 0.0) -> None:
        self.latency_ms = latency_ms
        self.fee_bps = fee_bps
        self._positions: dict[str, float] = {}
        self._open: dict[str, OrderIntent] = {}

    async def place(self, intent: OrderIntent, *, book: Book) -> PlacedOrder:
        t0 = time.perf_counter()
        await asyncio.sleep(self.latency_ms / 1000)

        order_id = f"paper-{uuid.uuid4().hex[:12]}"
        avg = book.cost_to_fill(str(intent.side), intent.size)

        crosses = (
            intent.side is Side.BUY
            and book.best_ask is not None
            and intent.price >= book.best_ask
        ) or (
            intent.side is Side.SELL
            and book.best_bid is not None
            and intent.price <= book.best_bid
        )

        if crosses and avg is not None:
            status = "filled"
            delta = intent.size if intent.side is Side.BUY else -intent.size
            self._positions[intent.token_id] = self._positions.get(intent.token_id, 0.0) + delta
        else:
            status = "open"
            self._open[order_id] = intent

        return PlacedOrder(
            order_id=order_id,
            token_id=intent.token_id,
            side=str(intent.side),
            price=avg if status == "filled" and avg is not None else intent.price,
            size=intent.size,
            status=status,
            is_paper=True,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            raw={"simulated_avg_price": avg, "crossed": crosses},
        )

    async def cancel(self, order_id: str) -> bool:
        return self._open.pop(order_id, None) is not None

    async def cancel_all(self) -> int:
        n = len(self._open)
        self._open.clear()
        return n

    async def positions(self) -> dict[str, float]:
        return dict(self._positions)


# ───────────────────────── live ──────────────────────────────────────────


class LiveClient:
    """
    Plata real. Todo lo que toca el SDK de Polymarket está aquí dentro.

    **CLOB V2.** Los clientes V1 dejaron de funcionar contra producción el
    2026-04-28, y el colateral pasó de USDC.e a pUSD. El paquete es
    `py-clob-client-v2` (import `py_clob_client_v2`), NO `py-clob-client`.
    Corre `python -m scripts.verify_sdk` si actualizas.

    Solo órdenes LÍMITE. Nada de market orders: en un book delgado una market
    order barre 15 centavos y te come el edge de tres días. Si necesitas
    inmediatez, cruza el spread con un límite agresivo — así el peor precio
    posible sigue siendo el tuyo.
    """

    is_paper = False

    def __init__(
        self,
        *,
        host: str,
        private_key: str,
        funder: str,
        chain_id: int = 137,
        signature_type: int = 1,
    ) -> None:
        try:
            from py_clob_client_v2.client import ClobClient
        except ImportError as exc:  # pragma: no cover
            raise ClobError("pip install py-clob-client-v2") from exc

        # En V2 chain_id es POSICIONAL, no keyword.
        self._client = ClobClient(
            host,
            chain_id,
            key=private_key,
            signature_type=signature_type,
            funder=funder,
        )
        # L1 (firma EIP-712 con la private key) → L2 (credenciales HMAC).
        # No las emite ninguna web: se derivan de la firma.
        self._client.set_api_creds(self._client.create_or_derive_api_key())
        self._funder = funder

        signer_address = self._client.get_address()
        if signature_type == 0 and signer_address.lower() != funder.lower():
            # Con EOA, quien firma es quien tiene los fondos. Si no coinciden,
            # las órdenes se firman bien y fallan al ejecutarse.
            raise ClobError(
                f"signature_type=0 pero el firmante ({signer_address}) no es el "
                f"funder ({funder}). Para wallets proxy usa signature_type 1 o 2."
            )

    # ── comprobaciones previas ───────────────────────────────────────────

    async def preflight(self) -> dict:
        """
        Correr ANTES de la primera orden real. Detecta los tres fallos que
        hacen perder la primera hora de cada piloto en vivo.
        """
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        closed_only = await asyncio.to_thread(self._client.get_closed_only_mode)
        balance = await asyncio.to_thread(
            self._client.get_balance_allowance,
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
        )
        return {
            "signer": self._client.get_address(),
            "funder": self._funder,
            "closed_only_mode": closed_only,
            "collateral": balance,
        }

    async def is_closed_only(self) -> bool:
        """
        Polymarket puede poner una cuenta en modo *close-only* (solo cerrar
        posiciones). Si pasa, el bot debe matarse solo en vez de reintentar
        órdenes que van a ser rechazadas una y otra vez. Ver docs/RIESGOS.md.
        """
        try:
            resp = await asyncio.to_thread(self._client.get_closed_only_mode)
            if isinstance(resp, dict):
                return bool(resp.get("closed_only") or resp.get("closedOnly"))
            return bool(resp)
        except Exception:  # noqa: BLE001 — no bloquea el trading si el endpoint falla
            return False

    async def ensure_allowances(self) -> None:
        """Aprobar el colateral una vez. Las wallets de email ya vienen aprobadas."""
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        await asyncio.to_thread(
            self._client.update_balance_allowance,
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
        )

    # ── ejecución ────────────────────────────────────────────────────────

    async def place(
        self, intent: OrderIntent, *, book: Book, post_only: bool = False
    ) -> PlacedOrder:
        """
        `post_only=True` garantiza que la orden nunca cruce el spread: o entra
        como maker o se rechaza. Es lo correcto para el piloto B, cuyo negocio
        entero es capturar spread, no pagarlo.
        """
        from py_clob_client_v2.client import Side as V2Side
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType

        t0 = time.perf_counter()

        # El tick varía por mercado (0.1 … 0.0001). Redondear al tick
        # equivocado hace que el CLOB rechace la orden sin explicación útil.
        tick = await asyncio.to_thread(self._client.get_tick_size, intent.token_id)
        price = _round_to_tick(intent.price, float(tick))

        args = OrderArgsV2(
            token_id=intent.token_id,
            price=price,
            size=round(intent.size, 2),
            side=V2Side.BUY if intent.side is Side.BUY else V2Side.SELL,
        )
        resp = await asyncio.to_thread(
            self._client.create_and_post_order,
            args,
            None,                 # options: el SDK resuelve tick_size y neg_risk
            OrderType.GTC,
            post_only,
        )
        latency = int((time.perf_counter() - t0) * 1000)

        order_id = resp.get("orderID") or resp.get("orderId") or ""
        if not order_id:
            raise ClobError(f"el CLOB no devolvió orderID: {resp}")

        return PlacedOrder(
            order_id=order_id,
            token_id=intent.token_id,
            side=str(intent.side),
            price=price,
            size=intent.size,
            status=resp.get("status", "sent"),
            is_paper=False,
            latency_ms=latency,
            raw=resp,
        )

    async def cancel(self, order_id: str) -> bool:
        from py_clob_client_v2.clob_types import OrderPayload

        resp = await asyncio.to_thread(
            self._client.cancel_order, OrderPayload(orderID=order_id)
        )
        return bool(resp)

    async def cancel_all(self) -> int:
        """El botón de pánico. Lo llaman el watchdog y el apagado."""
        resp = await asyncio.to_thread(self._client.cancel_all)
        return len(resp.get("canceled", [])) if isinstance(resp, dict) else 0

    async def positions(self) -> dict[str, float]:
        """
        La verdad sobre nuestras posiciones. El kernel reconcilia contra esto.
        Si este método miente, todos los límites del kernel son decorativos.
        """
        from py_clob_client_v2.clob_types import TradeParams

        rows = await asyncio.to_thread(
            self._client.get_trades, TradeParams(maker_address=self._funder)
        )
        out: dict[str, float] = {}
        for t in rows or []:
            tid = t.get("asset_id") or t.get("token_id")
            if not tid:
                continue
            size = float(t.get("size", 0))
            out[tid] = out.get(tid, 0.0) + (size if t.get("side") == "BUY" else -size)
        return out


def _round_to_tick(price: float, tick: float) -> float:
    """Al tick más cercano, sin salirse nunca de (0, 1)."""
    if tick <= 0:
        return round(price, 3)
    snapped = round(price / tick) * tick
    decimals = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]))
    return min(max(round(snapped, decimals), tick), 1 - tick)


# ───────────────────────── factory ───────────────────────────────────────


def build_execution_client(settings, pilot: str) -> ExecutionClient:
    """Única función que decide si se juega con plata real."""
    from core.config import Mode

    if settings.mode is not Mode.LIVE:
        return PaperClient()

    key, funder = settings.credentials(pilot)
    if key is None or not funder:
        raise ClobError(f"MODE=live pero falta PILOT_{pilot.upper()}_PRIVATE_KEY/_FUNDER")

    return LiveClient(
        host=settings.clob_host,
        private_key=key.get_secret_value(),
        funder=funder,
        chain_id=settings.chain_id,
        signature_type=settings.signature_type,
    )
