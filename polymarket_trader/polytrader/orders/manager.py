"""Order Manager (spec 模块 #6 + 八、下单和执行策略).

Responsibilities:

* Create limit orders (Phase 1 allows *only* limit orders).
* Enforce idempotency so a network timeout never double-submits (防止重复下单).
* Drive the order state machine and record every transition.
* Split a target into 2–3 child orders and re-check edge after each fill.
* Cancel expired / unfilled orders (no追价, no infinite resting orders).

Execution is pluggable:

* :class:`SimulationExecutor` fills against the order-book snapshot with a
  modelled fee + slippage. No network, no keys.
* :class:`LiveExecutor` is intentionally inert: it raises unless an explicit
  human-set flag *and* out-of-band credentials are present. It never handles
  keys in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..config import Config
from ..models import (
    Order,
    OrderBook,
    OrderStatus,
    OrderType,
    Outcome,
    Side,
    new_id,
)
from ..storage import Storage
from .state_machine import can_transition


@dataclass
class FillEvent:
    order_id: str
    filled_size: float
    fill_price: float
    fee: float
    slippage: float
    status: OrderStatus


class Executor(Protocol):
    def submit(self, order: Order, book: OrderBook) -> FillEvent: ...
    def cancel(self, order: Order) -> bool: ...


# --------------------------------------------------------------------------- #
# Simulation executor.
# --------------------------------------------------------------------------- #
class SimulationExecutor:
    """Fills a BUY limit order by walking the ask ladder up to the limit price.

    Models slippage as the difference between the volume-weighted fill price and
    the touch, and a fee as a fraction of filled notional. Any unfilled remainder
    is left ``LIVE`` (the manager cancels it at TTL).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def submit(self, order: Order, book: OrderBook) -> FillEvent:
        levels = book.asks if order.side is Side.BUY else book.bids
        if not levels:
            return FillEvent(order.order_id, 0.0, 0.0, 0.0, 0.0, OrderStatus.LIVE)

        touch = levels[0].price
        remaining = order.remaining
        filled_notional = 0.0
        cost = 0.0
        for lvl in levels:
            crosses = (
                lvl.price <= order.limit_price
                if order.side is Side.BUY
                else lvl.price >= order.limit_price
            )
            if not crosses:
                break
            take = min(remaining, lvl.size)
            if take <= 0:
                continue
            filled_notional += take
            cost += take * lvl.price
            remaining -= take
            if remaining <= 0:
                break

        if filled_notional <= 0:
            # Resting maker order that didn't cross yet.
            return FillEvent(order.order_id, 0.0, 0.0, 0.0, 0.0, OrderStatus.LIVE)

        avg_price = cost / filled_notional
        slippage = abs(avg_price - touch)
        fee_rate = self.cfg.costs.maker_fee_rate if order.is_maker else self.cfg.costs.taker_fee_rate
        fee = fee_rate * filled_notional

        total = order.filled_size + filled_notional
        status = OrderStatus.FILLED if total >= order.size - 1e-9 else OrderStatus.PARTIALLY_FILLED
        return FillEvent(order.order_id, filled_notional, avg_price, fee, slippage, status)

    def cancel(self, order: Order) -> bool:
        return True


class LiveExecutor:
    """Placeholder for real CLOB execution — inert by design in this phase."""

    def __init__(self, cfg: Config):
        if cfg.execution.mode != "live":
            self._reason = "execution.mode is not 'live'"
        else:
            self._reason = "live execution not implemented in this phase"

    def submit(self, order: Order, book: OrderBook) -> FillEvent:
        raise RuntimeError(
            "LiveExecutor is disabled: "
            f"{self._reason}. Real-money execution requires a future phase, "
            "explicit human authorisation, and out-of-band key management."
        )

    def cancel(self, order: Order) -> bool:
        raise RuntimeError("LiveExecutor is disabled.")


# --------------------------------------------------------------------------- #
# Order Manager.
# --------------------------------------------------------------------------- #
class OrderManager:
    def __init__(self, cfg: Config, executor: Executor, storage: Storage):
        self.cfg = cfg
        self.executor = executor
        self.storage = storage
        self.live_orders: dict[str, Order] = {}

    def _transition(self, order: Order, dst: OrderStatus) -> None:
        if order.status == dst:
            return
        if not can_transition(order.status, dst):
            raise ValueError(f"illegal transition {order.status} -> {dst}")
        order.status = dst

    def build_child_orders(
        self,
        market_id: str,
        event_id: str,
        outcome: Outcome,
        token_id: str,
        total_size: float,
        limit_price: float,
        reason: str,
        prediction_ts: float,
        now: float,
    ) -> list[Order]:
        """Split a target into N child orders (拆成 2–3 个小订单)."""
        n = self.cfg.strategy.child_order_count
        per_order_cap = self.cfg.sizing.max_per_order
        chunk = min(per_order_cap, total_size / n)
        orders: list[Order] = []
        allocated = 0.0
        for i in range(n):
            size = min(chunk, total_size - allocated)
            if size < 1.0:
                break
            allocated += size
            # Idempotency key is unique per (market, outcome, decision-time, child).
            # Uses the caller's clock (``now``) so distinct daily decisions never
            # collide, while a genuine re-submission of the same batch is blocked.
            client_key = f"{market_id}:{outcome.value}:{int(now)}:{i}"
            orders.append(
                Order(
                    order_id=new_id("ord"),
                    market_id=market_id,
                    token_id=token_id,
                    outcome=outcome,
                    side=Side.BUY,
                    order_type=OrderType.LIMIT,
                    limit_price=limit_price,
                    size=round(size, 2),
                    reason=reason,
                    prediction_ts=prediction_ts,
                    created_ts=now,
                    expires_ts=now + self.cfg.strategy.order_ttl_seconds,
                    client_key=client_key,
                    is_maker=True,
                )
            )
        return orders

    def submit(self, order: Order, book: OrderBook) -> FillEvent:
        # Idempotency: never submit the same client_key twice (防止重复下单).
        if self.storage.client_key_exists(order.client_key):
            self.storage.log_event(
                "duplicate_suppressed", f"client_key {order.client_key} already exists",
                order.market_id,
            )
            order.status = OrderStatus.FAILED
            order.anomalous = True
            return FillEvent(order.order_id, 0.0, 0.0, 0.0, 0.0, OrderStatus.FAILED)

        self.storage.upsert_order(order)  # persist CREATED with client_key first
        self._transition(order, OrderStatus.SUBMITTED)

        try:
            event = self.executor.submit(order, book)
        except Exception as exc:  # noqa: BLE001 - timeouts become UNKNOWN, not retried
            self._transition(order, OrderStatus.UNKNOWN)
            order.anomalous = True
            self.storage.upsert_order(order)
            self.storage.log_event("order_unknown", str(exc), order.market_id)
            return FillEvent(order.order_id, 0.0, 0.0, 0.0, 0.0, OrderStatus.UNKNOWN)

        self._apply_fill(order, event)
        return event

    def _apply_fill(self, order: Order, event: FillEvent) -> None:
        if event.filled_size > 0:
            prev_notional = order.avg_fill_price * order.filled_size
            order.filled_size += event.filled_size
            order.avg_fill_price = (
                (prev_notional + event.fill_price * event.filled_size) / order.filled_size
            )
            order.fee_paid += event.fee
            order.slippage = max(order.slippage, event.slippage)

        if event.status is OrderStatus.FILLED:
            self._transition(order, OrderStatus.FILLED)
            order.filled_ts = order.created_ts
        elif event.status is OrderStatus.PARTIALLY_FILLED:
            self._transition(order, OrderStatus.PARTIALLY_FILLED)
            self.live_orders[order.order_id] = order
        elif event.status is OrderStatus.LIVE:
            self._transition(order, OrderStatus.LIVE)
            self.live_orders[order.order_id] = order

        self.storage.upsert_order(order)

    def cancel(self, order: Order) -> None:
        if order.is_terminal:
            return
        self._transition(order, OrderStatus.CANCEL_REQUESTED)
        ok = self.executor.cancel(order)
        self._transition(order, OrderStatus.CANCELLED if ok else OrderStatus.FAILED)
        self.live_orders.pop(order.order_id, None)
        self.storage.upsert_order(order)

    def expire_stale_orders(self, now: float) -> list[Order]:
        """Cancel unfilled/partially-filled orders past their TTL (未成交自动撤销)."""
        expired = []
        for oid, order in list(self.live_orders.items()):
            if order.expires_ts is not None and now >= order.expires_ts:
                self.cancel(order)
                expired.append(order)
        return expired

    def cancel_all(self) -> list[Order]:
        """One-click cancel of every live order (一键取消全部订单)."""
        cancelled = []
        for order in list(self.live_orders.values()):
            self.cancel(order)
            cancelled.append(order)
        return cancelled
