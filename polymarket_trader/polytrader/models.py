"""Core data models shared across the system.

These are plain dataclasses / enums so they serialise cleanly to the SQLite
ledger and to JSON reports. Prices are probabilities in [0, 1] (a Polymarket
binary token trades between 0 and 1); sizes are USD-equivalent notional unless
noted.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Outcome(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderType(str, Enum):
    LIMIT = "LIMIT"  # Phase 1 only allows limit orders (下单和执行策略)


class OrderStatus(str, Enum):
    """The order state machine (spec 订单状态机)."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    LIVE = "LIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # forces reconciliation before new orders on the market


TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED}
)


def _now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class BookLevel:
    price: float
    size: float  # notional USD-equivalent available at this price


@dataclass
class OrderBook:
    """Top-of-book snapshot plus a shallow ladder."""

    market_id: str
    token_id: str
    bids: list[BookLevel] = field(default_factory=list)  # sorted desc by price
    asks: list[BookLevel] = field(default_factory=list)  # sorted asc by price
    ts: float = field(default_factory=_now)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def depth(self, side: Side) -> float:
        levels = self.asks if side is Side.BUY else self.bids
        return sum(lvl.size for lvl in levels)

    def depth_top_ok(self, min_usd: float) -> bool:
        """True iff top-of-book has at least ``min_usd`` on *both* sides."""
        if not self.bids or not self.asks:
            return False
        return self.bids[0].size >= min_usd and self.asks[0].size >= min_usd


@dataclass
class Market:
    """A binary Polymarket market plus the resolution metadata the spec
    requires us to read and store before trading (市场筛选规则)."""

    market_id: str
    question: str
    event_id: str
    yes_token_id: str
    no_token_id: str
    # Resolution rules — must be present and understood before any trade.
    resolution_source: str = ""
    resolution_rules: str = ""
    resolution_time: float | None = None  # epoch seconds
    # Liquidity / activity metadata.
    liquidity_usd: float = 0.0
    volume_usd: float = 0.0
    active: bool = True
    accepting_orders: bool = True
    tags: tuple[str, ...] = ()

    def days_to_resolution(self, now: float | None = None) -> float | None:
        if self.resolution_time is None:
            return None
        now = now if now is not None else _now()
        return (self.resolution_time - now) / 86400.0

    def token_for(self, outcome: Outcome) -> str:
        return self.yes_token_id if outcome is Outcome.YES else self.no_token_id


@dataclass
class Prediction:
    """Structured AI output (spec 六、AI 预测格式). Free-text-only opinions are
    rejected by construction: every field below is required."""

    market_id: str
    question: str
    selected_outcome: Outcome
    estimated_probability: float
    confidence: float
    market_price: float
    estimated_edge: float
    time_horizon: str
    evidence: list[str]
    invalidating_conditions: list[str]
    recommended_action: str  # e.g. "BUY_LIMIT"
    recommended_price: float
    recommended_size: float
    ts: float = field(default_factory=_now)

    def validate(self) -> None:
        if not (0.0 <= self.estimated_probability <= 1.0):
            raise ValueError("estimated_probability out of range")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence out of range")
        if not self.evidence:
            raise ValueError("prediction must cite at least one evidence source")
        if not self.invalidating_conditions:
            raise ValueError("prediction must state invalidating conditions")


@dataclass
class Order:
    order_id: str
    market_id: str
    token_id: str
    outcome: Outcome
    side: Side
    order_type: OrderType
    limit_price: float
    size: float  # notional USD-equivalent requested
    status: OrderStatus = OrderStatus.CREATED
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    fee_paid: float = 0.0
    slippage: float = 0.0
    is_maker: bool = True
    reason: str = ""              # trade rationale (交易理由)
    prediction_ts: float | None = None
    created_ts: float = field(default_factory=_now)
    filled_ts: float | None = None
    expires_ts: float | None = None
    # Idempotency key: same key must never submit twice (防止重复下单).
    client_key: str = ""
    anomalous: bool = False       # 是否属于异常交易

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled_size)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass
class Position:
    market_id: str
    outcome: Outcome
    token_id: str
    size: float = 0.0        # notional USD-equivalent at cost
    shares: float = 0.0      # number of outcome tokens held
    avg_cost: float = 0.0    # average entry price (probability)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    resolved: bool = False
    resolution_value: float | None = None  # 1.0 if outcome won, 0.0 if lost

    def unrealized_pnl(self, mark_price: float) -> float:
        if self.resolved or self.shares <= 0:
            return 0.0
        return self.shares * (mark_price - self.avg_cost)


@dataclass
class Trade:
    """An audit record for a fill (spec 十一、数据记录要求). One row per fill."""

    trade_id: str
    market_id: str
    question: str
    outcome: Outcome
    side: Side
    ai_probability: float
    market_price_at_decision: float
    fill_price: float
    size: float
    order_type: OrderType
    order_id: str
    filled_size: float
    fee: float
    slippage: float
    created_ts: float
    filled_ts: float
    reason: str
    evidence: list[str]
    risk_check: str          # summary of the risk decision
    outcome_result: str = "" # final settlement result if known
    pnl: float = 0.0
    anomalous: bool = False
