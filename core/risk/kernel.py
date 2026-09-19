"""
⛔ RISK KERNEL — la única capa que puede aprobar una orden.

Reglas de diseño, en orden de importancia:

 1. **Sin dependencias externas.** Solo stdlib. Un risk kernel que no se puede
    testear en 10ms no se testea, y uno que no se testea no sirve.
 2. **Los límites son inmutables en runtime.** `RiskLimits` es un frozen
    dataclass que se construye UNA vez desde variables de entorno, al arrancar.
    Ni el LLM, ni el estratega, ni el ejecutor pueden mutarlos.
 3. **El kill switch sobrevive a un reinicio.** Se persiste a disco. Si el bot
    se mata y lo relanzas, sigue muerto hasta que TÚ lo desarmes a mano. Esto
    es deliberado: el modo de falla clásico es "se cayó, lo reinicié, siguió
    perdiendo".
 4. **Fail closed.** Whitelist vacía = nada pasa. Precio desconocido = rechazo.
    Excepción inesperada = rechazo.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path

# ─────────────────────────────── dominio ────────────────────────────────


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Reject(StrEnum):
    """Códigos de rechazo. Se loguean y se agregan en métricas."""

    KILLED = "killed"
    NOT_WHITELISTED = "not_whitelisted"
    BAD_PRICE = "bad_price"
    PRICE_OUT_OF_BAND = "price_out_of_band"
    BAD_SIZE = "bad_size"
    ORDER_TOO_BIG = "order_too_big"
    PRICE_DEVIATION = "price_deviation"
    NO_REFERENCE_PRICE = "no_reference_price"
    RATE_LIMIT = "rate_limit"
    TOO_MANY_OPEN = "too_many_open"
    POSITION_CAP = "position_cap"
    GROSS_EXPOSURE_CAP = "gross_exposure_cap"
    DAILY_LOSS = "daily_loss"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Lo que el ejecutor QUIERE hacer. No es una orden hasta que el kernel la aprueba."""

    pilot: str
    token_id: str
    side: Side
    price: float          # 0..1 (Polymarket cotiza probabilidad)
    size: float           # shares
    market_slug: str = ""
    reason: str = ""      # queda en el journal; obligatorio en la práctica

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(frozen=True, slots=True)
class Verdict:
    approved: bool
    code: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.approved


OK = Verdict(True)


def _deny(code: Reject, detail: str) -> Verdict:
    return Verdict(False, str(code), detail)


# ─────────────────────────────── límites ────────────────────────────────


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_usd: float
    max_position_usd: float
    max_gross_exposure_usd: float
    max_daily_loss_usd: float
    max_orders_per_min: int
    max_open_orders: int
    max_price_deviation: float   # fracción, vs el mid del book
    min_price: float
    max_price: float
    allowed_token_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 0 < self.min_price < self.max_price < 1:
            raise ValueError(f"banda de precio inválida: {self.min_price}..{self.max_price}")
        for name in (
            "max_order_usd",
            "max_position_usd",
            "max_gross_exposure_usd",
            "max_daily_loss_usd",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} debe ser > 0")
        if self.max_order_usd > self.max_position_usd:
            raise ValueError("max_order_usd > max_position_usd: el límite de orden es inútil")
        if self.max_position_usd > self.max_gross_exposure_usd:
            raise ValueError("max_position_usd > max_gross_exposure_usd")

    @classmethod
    def from_env(cls, pilot: str, *, allowed_token_ids: frozenset[str] = frozenset()) -> RiskLimits:
        """Lee RISK_<PILOT>_*. Falla ruidosamente si falta algo: no hay defaults permisivos."""
        p = f"RISK_{pilot.upper()}_"

        def req(key: str, cast):
            raw = os.getenv(p + key)
            if raw is None or raw == "":
                raise RuntimeError(f"falta la variable de entorno {p + key}")
            return cast(raw)

        return cls(
            max_order_usd=req("MAX_ORDER_USD", float),
            max_position_usd=req("MAX_POSITION_USD", float),
            max_gross_exposure_usd=req("MAX_GROSS_EXPOSURE_USD", float),
            max_daily_loss_usd=req("MAX_DAILY_LOSS_USD", float),
            max_orders_per_min=req("MAX_ORDERS_PER_MIN", int),
            max_open_orders=req("MAX_OPEN_ORDERS", int),
            max_price_deviation=req("MAX_PRICE_DEVIATION", float),
            min_price=req("MIN_PRICE", float),
            max_price=req("MAX_PRICE", float),
            allowed_token_ids=allowed_token_ids,
        )


# ─────────────────────────────── kernel ─────────────────────────────────


@dataclass
class _Position:
    shares: float = 0.0     # con signo: + long, - short
    avg_cost: float = 0.0   # costo medio del lado actual


class RiskKernel:
    """
    Instancia UNA por piloto. No es thread-safe a propósito: cada piloto corre
    en un solo loop de asyncio. Si necesitas hilos, pon un lock afuera.
    """

    def __init__(
        self,
        pilot: str,
        limits: RiskLimits,
        *,
        state_dir: Path | str = "state",
        clock=time.monotonic,
        today=lambda: datetime.now(UTC).date(),
        on_trip=None,
        on_reset=None,
    ) -> None:
        """
        `on_trip` / `on_reset` avisan a quien pueda persistir el kill switch
        donde de verdad dure.

        El archivo de `state_dir` basta cuando hay disco propio, pero en un
        contenedor de disco efimero se borra en cada reinicio — y ahi el
        freno de emergencia se sueltaria solo, justo cuando el agente ya
        perdio la cuenta de sus posiciones. El kernel no habla con la base
        (es sincrono y sin dependencias, y asi se queda): avisa, y quien
        tenga la base se encarga.
        """
        self.pilot = pilot
        self.limits = limits
        self._clock = clock
        self._today_fn = today
        self._on_trip = on_trip
        self._on_reset = on_reset

        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._kill_file = self._state_dir / f".KILLED_{pilot}"

        self._order_times: deque[float] = deque()
        self._positions: dict[str, _Position] = {}
        self._open_orders = 0
        self._realized_today = 0.0
        self._day: date = self._today_fn()

        self._killed = False
        self._kill_reason = ""
        # un kill previo persistido gana sobre cualquier arranque limpio
        if self._kill_file.exists():
            self._killed = True
            self._kill_reason = self._kill_file.read_text(encoding="utf-8").strip()

    # ── estado observable ────────────────────────────────────────────────

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def kill_reason(self) -> str:
        return self._kill_reason

    @property
    def realized_pnl_today(self) -> float:
        self._roll_day()
        return self._realized_today

    def position(self, token_id: str) -> float:
        return self._positions.get(token_id, _Position()).shares

    def gross_exposure(self, marks: dict[str, float]) -> float:
        """Exposición bruta a precios de mercado. `marks` = token_id -> precio."""
        return sum(abs(p.shares) * marks.get(t, p.avg_cost) for t, p in self._positions.items())

    def snapshot(self) -> dict:
        self._roll_day()
        return {
            "pilot": self.pilot,
            "killed": self._killed,
            "kill_reason": self._kill_reason,
            "day": self._day.isoformat(),
            "realized_pnl_today": round(self._realized_today, 4),
            "open_orders": self._open_orders,
            "orders_last_min": len(self._order_times),
            "positions": {t: round(p.shares, 4) for t, p in self._positions.items() if p.shares},
        }

    # ── kill switch ──────────────────────────────────────────────────────

    def trip(self, reason: str) -> None:
        """Mata el piloto. Idempotente. Persiste a disco."""
        if self._killed:
            return
        self._killed = True
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        self._kill_reason = f"[{stamp}] {reason}"
        self._kill_file.write_text(self._kill_reason, encoding="utf-8")
        if self._on_trip is not None:
            # Nunca puede impedir el kill: si el aviso falla, el freno ya
            # esta puesto y eso es lo que importa.
            with contextlib.suppress(Exception):
                self._on_trip(self._kill_reason)

    def restaurar_kill(self, reason: str) -> None:
        """
        Marca el piloto muerto por un kill que YA estaba guardado fuera.

        No es lo mismo que `trip()`: el gancho no se dispara, porque el
        motivo viene precisamente de ahi. Llamar a `trip()` para esto
        reescribiria el registro y anidaria el texto en cada reinicio
        ("kill persistido: kill persistido: ..."), perdiendo la causa real.
        """
        if self._killed:
            return
        self._killed = True
        self._kill_reason = reason
        self._kill_file.write_text(reason, encoding="utf-8")

    def reset_kill(self, operator_ack: str) -> None:
        """
        Desarmar el kill switch es un acto humano y explícito.
        Hay que pasar literalmente el nombre del piloto como confirmación.
        """
        if operator_ack != f"reset-{self.pilot}":
            raise PermissionError(
                f"reset_kill requiere operator_ack='reset-{self.pilot}'. "
                "Si un proceso automático llegó aquí, es un bug: arréglalo, no lo sortees."
            )
        self._killed = False
        self._kill_reason = ""
        self._kill_file.unlink(missing_ok=True)
        if self._on_reset is not None:
            with contextlib.suppress(Exception):
                self._on_reset()

    # ── el check ─────────────────────────────────────────────────────────

    def check(self, intent: OrderIntent, *, mid: float | None) -> Verdict:
        """
        Única puerta de entrada. `mid` es el punto medio del book en este
        instante; si no lo tienes, pasa None y la orden se rechaza. Operar a
        ciegas no es una opción.
        """
        try:
            return self._check(intent, mid)
        except Exception as exc:  # noqa: BLE001 — fail closed, siempre
            self.trip(f"excepción en el risk kernel: {exc!r}")
            return _deny(Reject.INTERNAL, repr(exc))

    def _check(self, intent: OrderIntent, mid: float | None) -> Verdict:
        self._roll_day()
        lim = self.limits

        if self._killed:
            return _deny(Reject.KILLED, self._kill_reason)

        # fail closed: whitelist vacía no significa "todo permitido"
        if intent.token_id not in lim.allowed_token_ids:
            return _deny(Reject.NOT_WHITELISTED, intent.token_id)

        if not (0.0 < intent.price < 1.0):
            return _deny(Reject.BAD_PRICE, f"price={intent.price}")
        if not (lim.min_price <= intent.price <= lim.max_price):
            return _deny(
                Reject.PRICE_OUT_OF_BAND,
                f"{intent.price} fuera de [{lim.min_price}, {lim.max_price}]",
            )

        if intent.size <= 0:
            return _deny(Reject.BAD_SIZE, f"size={intent.size}")

        if intent.notional > lim.max_order_usd:
            return _deny(
                Reject.ORDER_TOO_BIG,
                f"${intent.notional:.2f} > ${lim.max_order_usd:.2f}",
            )

        # sanity vs mercado: atrapa el fat finger y la alucinación del LLM
        if mid is None:
            return _deny(Reject.NO_REFERENCE_PRICE, "sin mid del book")
        if abs(intent.price - mid) > lim.max_price_deviation:
            return _deny(
                Reject.PRICE_DEVIATION,
                f"|{intent.price} - {mid}| > {lim.max_price_deviation}",
            )

        # anti-loop: el bug que te arruina en 40 segundos
        self._prune_order_times()
        if len(self._order_times) >= lim.max_orders_per_min:
            return _deny(
                Reject.RATE_LIMIT,
                f"{len(self._order_times)} órdenes en 60s (máx {lim.max_orders_per_min})",
            )

        if self._open_orders >= lim.max_open_orders:
            return _deny(Reject.TOO_MANY_OPEN, f"{self._open_orders} abiertas")

        # posición resultante si esta orden llena completa
        pos = self._positions.get(intent.token_id, _Position())
        delta = intent.size if intent.side is Side.BUY else -intent.size
        projected_usd = abs(pos.shares + delta) * intent.price
        if projected_usd > lim.max_position_usd:
            return _deny(
                Reject.POSITION_CAP,
                f"posición proyectada ${projected_usd:.2f} > ${lim.max_position_usd:.2f}",
            )

        # exposición bruta del portafolio
        marks = {intent.token_id: intent.price}
        projected_gross = self.gross_exposure(marks) - abs(pos.shares) * intent.price + projected_usd
        if projected_gross > lim.max_gross_exposure_usd:
            return _deny(
                Reject.GROSS_EXPOSURE_CAP,
                f"bruto proyectado ${projected_gross:.2f} > ${lim.max_gross_exposure_usd:.2f}",
            )

        # pérdida diaria: no rechaza, MATA. Si llegaste aquí, el día se acabó.
        if -self._realized_today >= lim.max_daily_loss_usd:
            self.trip(
                f"pérdida diaria ${-self._realized_today:.2f} "
                f"alcanzó el límite ${lim.max_daily_loss_usd:.2f}"
            )
            return _deny(Reject.DAILY_LOSS, self._kill_reason)

        return OK

    # ── callbacks del ejecutor ───────────────────────────────────────────

    def on_order_sent(self, intent: OrderIntent) -> None:
        """Llamar INMEDIATAMENTE después de aprobar y enviar. Cuenta para el rate limit."""
        self._order_times.append(self._clock())
        self._open_orders += 1

    def on_order_closed(self) -> None:
        """Orden llenada, cancelada o rechazada por el exchange."""
        self._open_orders = max(0, self._open_orders - 1)

    def on_fill(self, token_id: str, side: Side, price: float, size: float) -> float:
        """
        Registra un fill y devuelve el PnL realizado por ese fill.
        Contabilidad de costo medio: cerrar contra la posición realiza PnL,
        abrir o ampliar solo mueve el costo medio.
        """
        self._roll_day()
        pos = self._positions.setdefault(token_id, _Position())
        signed = size if side is Side.BUY else -size
        realized = 0.0

        if pos.shares == 0 or (pos.shares > 0) == (signed > 0):
            # abre o amplía
            total = abs(pos.shares) + abs(signed)
            pos.avg_cost = (pos.avg_cost * abs(pos.shares) + price * abs(signed)) / total
            pos.shares += signed
        else:
            # cierra (parcial o total), y puede voltear
            closing = min(abs(signed), abs(pos.shares))
            direction = 1 if pos.shares > 0 else -1
            realized = (price - pos.avg_cost) * closing * direction
            pos.shares += signed
            if pos.shares == 0:
                pos.avg_cost = 0.0
            elif (pos.shares > 0) != (direction > 0):
                # volteó de lado: el resto abre posición nueva al precio del fill
                pos.avg_cost = price

        self._realized_today += realized

        if -self._realized_today >= self.limits.max_daily_loss_usd:
            self.trip(
                f"pérdida diaria ${-self._realized_today:.2f} "
                f"alcanzó el límite ${self.limits.max_daily_loss_usd:.2f}"
            )
        return realized

    def reconcile(self, exchange_positions: dict[str, float], *, tolerance: float = 1e-6) -> Verdict:
        """
        La verdad es el exchange, no nuestra memoria. Llamar cada ~10s.
        Una divergencia significa que perdimos un fill: doble exposición,
        que es exactamente como se pierde plata sin darse cuenta.
        """
        drift: list[str] = []
        for token_id in set(self._positions) | set(exchange_positions):
            local = self.position(token_id)
            remote = exchange_positions.get(token_id, 0.0)
            if abs(local - remote) > tolerance:
                drift.append(f"{token_id}: local={local:.4f} exchange={remote:.4f}")

        if drift:
            self.trip("desincronización de posición | " + " ; ".join(drift))
            return _deny(Reject.INTERNAL, "; ".join(drift))
        return OK

    # ── internos ─────────────────────────────────────────────────────────

    def _prune_order_times(self) -> None:
        cutoff = self._clock() - 60.0
        while self._order_times and self._order_times[0] < cutoff:
            self._order_times.popleft()

    def _roll_day(self) -> None:
        today = self._today_fn()
        if today != self._day:
            self._day = today
            self._realized_today = 0.0
            # ojo: el kill switch NO se limpia solo al cambiar el día.
