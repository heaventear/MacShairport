"""Gated live CLOB executor + the real Polymarket adapter.

Implements the same executor interface as ``SimulationExecutor`` (``submit`` /
``cancel`` returning a ``FillEvent``) so it drops into the existing
``OrderManager`` unchanged, with two differences:

1. it runs :func:`live_preflight` at construction and refuses to exist unless
   every safety gate passes; and
2. order placement is delegated to a :class:`ClobClient`. The real adapter
   (:class:`PyClobClientAdapter`) wraps the official ``py-clob-client``, which
   builds and EIP-712-signs each order with the wallet key held by the isolated
   signer. The AI/analysis layer never sees the key.

Unit-tested against :class:`FakeClobClient`. The real adapter's network calls
require ``py-clob-client`` + funded credentials and are exercised by the user's
own connectivity check (``scripts/run_live.py --check``).

Size convention: this system measures order size in **notional USD-equivalent**,
while the CLOB works in **shares** (outcome tokens) at a price in [0, 1]. The
adapter converts: ``shares = notional / price`` on the way out, and
``notional = shares * price`` on the way back.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..models import Order, OrderBook, OrderStatus, Side
from ..orders.manager import FillEvent
from ..security import Signer
from .preflight import LiveExecutionBlocked, authorization_from_env, live_preflight


class ClobClient(Protocol):
    """Minimal exchange interface the executor depends on. Takes our domain
    ``Order`` — the adapter is responsible for building/signing/posting it."""

    def place_order(self, order: Order) -> dict: ...
    def cancel_order(self, exchange_order_id: str) -> bool: ...
    def order_status(self, exchange_order_id: str) -> dict: ...


def _get(obj, key, default=None):
    """Read a field from a dict-or-object response defensively."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# --------------------------------------------------------------------------- #
# Real adapter.
# --------------------------------------------------------------------------- #
# Polymarket post-order statuses -> our OrderStatus.
_STATUS_MAP = {
    "matched": OrderStatus.FILLED,
    "filled": OrderStatus.FILLED,
    "live": OrderStatus.LIVE,
    "delayed": OrderStatus.LIVE,
    "unmatched": OrderStatus.LIVE,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "failed": OrderStatus.FAILED,
    # our own codes pass through
    "FILLED": OrderStatus.FILLED, "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "LIVE": OrderStatus.LIVE, "CANCELLED": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.FAILED,
}


class PyClobClientAdapter:
    """Adapter over the official ``py-clob-client``.

    Build it with :meth:`from_credentials` (which needs the optional dependency
    and funded creds), or inject a pre-built client for testing. All network
    calls are made by ``py-clob-client``; this class only translates between our
    domain model and the client's API.
    """

    def __init__(self, client, default_order_type: str = "GTC"):
        self.client = client
        self.default_order_type = default_order_type

    @classmethod
    def from_credentials(cls, creds, default_order_type: str = "GTC"):
        """Construct a real client from :class:`LiveCredentials`.

        Lazy-imports ``py-clob-client`` so the package is only needed for live
        trading. Derives L2 API creds from the key when they are not supplied.
        """
        try:
            from py_clob_client.client import ClobClient as _PyClob
        except ImportError as exc:  # pragma: no cover - requires optional dep
            raise RuntimeError(
                "py-clob-client is not installed. `pip install py-clob-client` to "
                "enable live trading (see docs/GO_LIVE.md)."
            ) from exc

        kwargs = dict(host=creds.host, key=creds.private_key, chain_id=creds.chain_id)
        if creds.funder:
            kwargs["funder"] = creds.funder
            kwargs["signature_type"] = creds.signature_type
        client = _PyClob(**kwargs)

        # L2 API creds: use supplied, else derive from the key.
        if creds.has_api_creds:
            from py_clob_client.clob_types import ApiCreds  # pragma: no cover

            client.set_api_creds(ApiCreds(
                api_key=creds.api_key, api_secret=creds.api_secret,
                api_passphrase=creds.api_passphrase))
        else:  # pragma: no cover - network
            client.set_api_creds(client.create_or_derive_api_creds())
        return cls(client, default_order_type=default_order_type)

    # -- reads (also used by the connectivity check / reconciliation) ------- #
    def address(self) -> str:  # pragma: no cover - network
        return self.client.get_address()

    def usdc_balance(self) -> float:  # pragma: no cover - network
        """Collateral (USDC) balance available to the CLOB, in USD."""
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        resp = self.client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        # Balances are returned in 6-decimal base units.
        raw = float(_get(resp, "balance", 0) or 0)
        return raw / 1_000_000.0

    @staticmethod
    def notional_to_shares(notional: float, price: float) -> float:
        """Convert a USD-notional order size to outcome-token shares."""
        if price <= 0:
            return 0.0
        return round(notional / price, 2)

    # -- execution ---------------------------------------------------------- #
    def place_order(self, order: Order) -> dict:  # pragma: no cover - network
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        price = order.limit_price
        if price <= 0:
            return {"status": "failed", "filled_size": 0.0, "avg_price": 0.0}
        shares = self.notional_to_shares(order.remaining, price)  # USD -> shares
        side = BUY if order.side is Side.BUY else SELL

        args = OrderArgs(price=price, size=shares, side=side, token_id=order.token_id)
        # neg_risk markets need the flag set when signing.
        try:
            neg_risk = bool(self.client.get_neg_risk(order.token_id))
        except Exception:  # noqa: BLE001 - default to standard market
            neg_risk = False
        if neg_risk:
            from py_clob_client.clob_types import PartialCreateOrderOptions

            signed = self.client.create_order(
                args, PartialCreateOrderOptions(neg_risk=True))
        else:
            signed = self.client.create_order(args)

        ot = getattr(OrderType, self.default_order_type, OrderType.GTC)
        resp = self.client.post_order(signed, ot)
        return self._map_place_response(resp, price)

    def _map_place_response(self, resp, price: float) -> dict:
        exch_id = _get(resp, "orderID") or _get(resp, "orderId") or _get(resp, "id") or ""
        status = str(_get(resp, "status", "") or "").lower()
        success = _get(resp, "success", True)
        if success is False and not status:
            status = "failed"
        # size_matched (shares) when the client reports it; else 0 until polled.
        matched_shares = float(_get(resp, "size_matched", 0) or _get(resp, "sizeMatched", 0) or 0)
        filled_notional = round(matched_shares * price, 6)
        return {
            "exchange_order_id": exch_id,
            "status": status or "live",
            "filled_size": filled_notional,
            "avg_price": price,
        }

    def cancel_order(self, exchange_order_id: str) -> bool:  # pragma: no cover - network
        resp = self.client.cancel(exchange_order_id)
        # cancel returns {"canceled": [...], "not_canceled": {...}} on success.
        not_canceled = _get(resp, "not_canceled") or {}
        return exchange_order_id not in not_canceled

    def order_status(self, exchange_order_id: str) -> dict:  # pragma: no cover - network
        order = self.client.get_order(exchange_order_id)
        return {
            "exchange_order_id": exchange_order_id,
            "status": str(_get(order, "status", "") or "").lower(),
            "size_matched": float(_get(order, "size_matched", 0) or 0),
        }


# --------------------------------------------------------------------------- #
# Test double.
# --------------------------------------------------------------------------- #
class FakeClobClient:
    """Deterministic in-memory CLOB for unit tests. Speaks the same interface as
    the real adapter (takes our ``Order``)."""

    def __init__(self, fill_ratio: float = 1.0, raise_on_place: bool = False):
        self.fill_ratio = fill_ratio
        self.raise_on_place = raise_on_place
        self.placed: list[Order] = []
        self.cancelled: list[str] = []
        self._seq = 0

    def place_order(self, order: Order) -> dict:
        if self.raise_on_place:
            raise TimeoutError("simulated network timeout")
        self.placed.append(order)
        self._seq += 1
        filled = round(order.remaining * self.fill_ratio, 6)
        status = "FILLED" if filled >= order.remaining - 1e-9 else (
            "PARTIALLY_FILLED" if filled > 0 else "LIVE")
        return {"exchange_order_id": f"exch_{self._seq}", "status": status,
                "filled_size": filled, "avg_price": order.limit_price}

    def cancel_order(self, exchange_order_id: str) -> bool:
        self.cancelled.append(exchange_order_id)
        return True

    def order_status(self, exchange_order_id: str) -> dict:
        return {"exchange_order_id": exchange_order_id, "status": "live"}


# --------------------------------------------------------------------------- #
# Gated executor.
# --------------------------------------------------------------------------- #
class GatedClobExecutor:
    def __init__(
        self,
        cfg: Config,
        signer: Signer,
        client: ClobClient,
        geoblock,
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
        self.signer = signer          # key custody; the adapter/client signs
        self.client = client
        self.preflight = report
        self.exchange_ids: dict[str, str] = {}  # local order_id -> exchange id

    def submit(self, order: Order, book: OrderBook) -> FillEvent:
        resp = self.client.place_order(order)
        self.exchange_ids[order.order_id] = _get(resp, "exchange_order_id", "") or ""
        status = _STATUS_MAP.get(str(_get(resp, "status", "")).lower(),
                                 _STATUS_MAP.get(_get(resp, "status", ""), OrderStatus.UNKNOWN))
        filled = float(_get(resp, "filled_size", 0.0) or 0.0)
        avg = float(_get(resp, "avg_price", 0.0) or 0.0)
        # No modelled slippage on a real fill: the exchange price is authoritative.
        return FillEvent(order.order_id, filled, avg, 0.0, 0.0, status)

    def cancel(self, order: Order) -> bool:
        exch_id = self.exchange_ids.get(order.order_id, order.order_id)
        return bool(self.client.cancel_order(exch_id))
