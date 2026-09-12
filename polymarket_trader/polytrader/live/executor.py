"""Gated live CLOB executor.

Implements the same executor interface as ``SimulationExecutor`` (``submit`` /
``cancel`` returning a ``FillEvent``) so it drops into the existing
``OrderManager`` unchanged. The difference is that it:

1. runs :func:`live_preflight` at construction and refuses to exist unless every
   gate passes (so an unconfigured live executor cannot be built), and
2. delegates signing to an isolated :class:`~polytrader.security.Signer` (keys
   never enter this process) and order placement to a :class:`ClobClient`.

The execution flow is fully implemented and unit-tested against
:class:`FakeClobClient`. The real adapter, :class:`PyClobClientAdapter`, is a
thin stub that requires the optional ``py-clob-client`` package and real
credentials — wiring it is the final human/ops step of Phase 4.
"""

from __future__ import annotations

import json
from typing import Protocol

from ..config import Config
from ..models import Order, OrderBook, OrderStatus, Side
from ..orders.manager import FillEvent
from ..security import GeoblockResult, Signer
from .preflight import LiveExecutionBlocked, authorization_from_env, live_preflight


class ClobClient(Protocol):
    """Minimal exchange interface the executor depends on. A real adapter wraps
    py-clob-client; the test double implements the same three methods."""

    def place_order(self, signed_order: dict) -> dict: ...
    def cancel_order(self, exchange_order_id: str) -> bool: ...
    def order_status(self, exchange_order_id: str) -> dict: ...


# --------------------------------------------------------------------------- #
# Real adapter (stub).
# --------------------------------------------------------------------------- #
class PyClobClientAdapter:
    """Adapter to Polymarket's official ``py-clob-client`` — not implemented.

    Intentionally a stub: constructing/using it raises until the optional
    dependency is installed and real credentials + an isolated signer are
    provisioned out-of-band. Kept here so the wiring point is explicit.
    """

    def __init__(self, host: str = "https://clob.polymarket.com", **kwargs):
        raise NotImplementedError(
            "PyClobClientAdapter is not implemented in this phase. To enable it: "
            "install py-clob-client, provide API credentials and an isolated "
            "signer, then implement place_order/cancel_order/order_status against "
            "the client. See docs/GO_LIVE.md.")

    def place_order(self, signed_order: dict) -> dict:  # pragma: no cover - stub
        raise NotImplementedError

    def cancel_order(self, exchange_order_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def order_status(self, exchange_order_id: str) -> dict:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Test double.
# --------------------------------------------------------------------------- #
class FakeClobClient:
    """Deterministic in-memory CLOB used to unit-test the execution flow.

    ``fill_ratio`` controls how much of an order fills (1.0 = full, 0.0 = rests
    live); ``raise_on_place`` simulates a network timeout so the OrderManager's
    UNKNOWN-state handling can be exercised.
    """

    def __init__(self, fill_ratio: float = 1.0, raise_on_place: bool = False):
        self.fill_ratio = fill_ratio
        self.raise_on_place = raise_on_place
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._seq = 0

    def place_order(self, signed_order: dict) -> dict:
        if self.raise_on_place:
            raise TimeoutError("simulated network timeout")
        self.placed.append(signed_order)
        self._seq += 1
        size = signed_order["payload"]["size"]
        price = signed_order["payload"]["price"]
        filled = round(size * self.fill_ratio, 6)
        status = "FILLED" if filled >= size - 1e-9 else (
            "PARTIALLY_FILLED" if filled > 0 else "LIVE")
        return {
            "exchange_order_id": f"exch_{self._seq}",
            "status": status,
            "filled_size": filled,
            "avg_price": price,
        }

    def cancel_order(self, exchange_order_id: str) -> bool:
        self.cancelled.append(exchange_order_id)
        return True

    def order_status(self, exchange_order_id: str) -> dict:
        return {"exchange_order_id": exchange_order_id, "status": "LIVE"}


# --------------------------------------------------------------------------- #
# Gated executor.
# --------------------------------------------------------------------------- #
_STATUS_MAP = {
    "FILLED": OrderStatus.FILLED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "LIVE": OrderStatus.LIVE,
    "CANCELLED": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.FAILED,
}


class GatedClobExecutor:
    def __init__(
        self,
        cfg: Config,
        signer: Signer,
        client: ClobClient,
        geoblock: GeoblockResult,
        *,
        reconciliation_ok: bool = True,
        halt_level: str = "RUNNING",
        authorized: bool | None = None,
    ):
        authorized = authorization_from_env() if authorized is None else authorized
        report = live_preflight(
            cfg, signer=signer, geoblock=geoblock, authorized=authorized,
            reconciliation_ok=reconciliation_ok, halt_level=halt_level,
        )
        # Fail closed: an executor that could trade cannot even be constructed
        # unless every safety gate passed.
        if not report.ok:
            raise LiveExecutionBlocked(report)
        self.cfg = cfg
        self.signer = signer
        self.client = client
        self.preflight = report
        self.exchange_ids: dict[str, str] = {}  # local order_id -> exchange id

    def _canonical_payload(self, order: Order) -> dict:
        return {
            "market_id": order.market_id,
            "token_id": order.token_id,
            "side": order.side.value,
            "price": order.limit_price,
            "size": order.remaining,
            "order_type": order.order_type.value,
            "client_key": order.client_key,
        }

    def submit(self, order: Order, book: OrderBook) -> FillEvent:
        payload = self._canonical_payload(order)
        # Keys never enter this process: the isolated signer produces the
        # signature over the canonical payload bytes.
        signature = self.signer.sign(json.dumps(payload, sort_keys=True).encode())
        resp = self.client.place_order({"payload": payload, "signature": signature})

        self.exchange_ids[order.order_id] = resp.get("exchange_order_id", "")
        status = _STATUS_MAP.get(resp.get("status", ""), OrderStatus.UNKNOWN)
        filled = float(resp.get("filled_size", 0.0))
        avg = float(resp.get("avg_price", 0.0))
        # No modelled slippage on a real fill: the exchange price is authoritative.
        return FillEvent(order.order_id, filled, avg, 0.0, 0.0, status)

    def cancel(self, order: Order) -> bool:
        exch_id = self.exchange_ids.get(order.order_id, order.order_id)
        return bool(self.client.cancel_order(exch_id))
