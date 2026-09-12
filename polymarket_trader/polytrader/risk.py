"""Risk Manager (spec 模块 #5 + 九、资金和风险控制).

The Risk Manager has veto power over every order (设计原则 #2). It enforces:

* Per-order / per-market / per-event / total-open position limits.
* The cash buffer.
* Minimum confidence and minimum net edge.
* Global circuit breakers: daily loss halt, drawdown halve, drawdown halt,
  reconciliation mismatch pause, API-error read-only mode.

It reads the immutable :class:`Config` and holds *mutable runtime state* (halt
flags, equity high-water mark). The AI engine never gets a reference to this
object, so it can neither widen limits nor size beyond them (原则 #5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import Config


class HaltLevel(str, Enum):
    RUNNING = "RUNNING"                 # normal operation
    NO_NEW_POSITIONS = "NO_NEW_POSITIONS"  # can manage/exit, cannot open
    READ_ONLY = "READ_ONLY"            # cancel-only / no submissions
    HARD_HALT = "HARD_HALT"            # full stop; manual recovery required


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    approved_size: float = 0.0


@dataclass
class PortfolioSnapshot:
    """Everything the Risk Manager needs to know about current exposure."""

    equity: float
    cash: float
    total_open_notional: float
    per_market_notional: dict[str, float] = field(default_factory=dict)
    per_event_notional: dict[str, float] = field(default_factory=dict)


class RiskManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.halt_level = HaltLevel.RUNNING
        self.size_multiplier = 1.0
        self.halt_reasons: list[str] = []
        start = cfg.account.total_capital_usd
        self.start_equity = start
        self.peak_equity = start
        self.day_start_equity = start
        self.current_day = 1

    # -- lifecycle / breakers ---------------------------------------------- #
    def start_new_day(self, day: int, equity: float) -> None:
        self.current_day = day
        self.day_start_equity = equity

    def observe_equity(self, equity: float) -> None:
        """Update the high-water mark and evaluate drawdown breakers."""
        self.peak_equity = max(self.peak_equity, equity)
        cb = self.cfg.circuit_breakers

        drawdown = (self.peak_equity - equity) / self.peak_equity if self.peak_equity else 0.0
        if drawdown >= cb.drawdown_halt_pct:
            self._escalate(HaltLevel.NO_NEW_POSITIONS,
                           f"drawdown {drawdown:.2%} >= halt {cb.drawdown_halt_pct:.2%}")
        elif drawdown >= cb.drawdown_halve_pct:
            self.size_multiplier = 0.5
            self._log(f"drawdown {drawdown:.2%} >= halve threshold; sizing halved")

        day_loss = (self.day_start_equity - equity) / self.day_start_equity \
            if self.day_start_equity else 0.0
        if day_loss >= cb.daily_loss_halt_pct:
            self._escalate(HaltLevel.NO_NEW_POSITIONS,
                           f"daily loss {day_loss:.2%} >= {cb.daily_loss_halt_pct:.2%}")

    def observe_api_errors(self, consecutive_errors: int) -> None:
        if consecutive_errors >= self.cfg.circuit_breakers.api_error_streak_halt:
            self._escalate(HaltLevel.READ_ONLY,
                           f"{consecutive_errors} consecutive API errors -> read-only")

    def on_reconciliation(self, consistent: bool, kind: str) -> None:
        if not consistent:
            self._escalate(HaltLevel.HARD_HALT,
                           f"{kind} reconciliation mismatch -> hard halt")

    def on_unknown_order(self, market_id: str) -> None:
        # An UNKNOWN order status must block new orders on that market until
        # reconciled. We conservatively escalate to read-only globally.
        self._escalate(HaltLevel.READ_ONLY,
                       f"order status UNKNOWN on {market_id}; blocking until reconciled")

    def on_security_anomaly(self, detail: str) -> None:
        self._escalate(HaltLevel.HARD_HALT, f"security anomaly: {detail}")

    def manual_pause(self) -> None:
        self._escalate(HaltLevel.READ_ONLY, "manual pause")

    def manual_resume(self) -> None:
        """Human-only recovery. Never called by the AI engine."""
        self.halt_level = HaltLevel.RUNNING
        self.size_multiplier = 1.0
        self._log("manual resume: trading re-enabled")

    @property
    def can_open_new_positions(self) -> bool:
        return self.halt_level == HaltLevel.RUNNING

    @property
    def can_submit_orders(self) -> bool:
        return self.halt_level in (HaltLevel.RUNNING, HaltLevel.NO_NEW_POSITIONS)

    # -- per-order approval ------------------------------------------------- #
    def approve_order(
        self,
        market_id: str,
        event_id: str,
        requested_size: float,
        confidence: float,
        net_edge: float,
        high_liquidity: bool,
        book_depth_usd: float,
        snapshot: PortfolioSnapshot,
    ) -> RiskDecision:
        cfg = self.cfg
        if not self.can_open_new_positions:
            return RiskDecision(False, f"halted: {self.halt_level.value}")

        if confidence < cfg.strategy.min_confidence:
            return RiskDecision(
                False, f"confidence {confidence:.2f} < min {cfg.strategy.min_confidence:.2f}")

        min_edge = cfg.min_edge_for(high_liquidity)
        if net_edge < min_edge:
            return RiskDecision(False, f"net edge {net_edge:.3f} < min {min_edge:.3f}")

        # Start from the requested size, clamp by the sizing ladder.
        size = min(requested_size, cfg.sizing.max_per_order) * self.size_multiplier

        market_room = cfg.sizing.max_per_market - snapshot.per_market_notional.get(market_id, 0.0)
        if market_room <= 0:
            return RiskDecision(False, "per-market cap reached")
        size = min(size, market_room)

        event_room = cfg.sizing.max_per_event - snapshot.per_event_notional.get(event_id, 0.0)
        if event_room <= 0:
            return RiskDecision(False, "per-event cap reached")
        size = min(size, event_room)

        total_room = cfg.sizing.max_total_open - snapshot.total_open_notional
        if total_room <= 0:
            return RiskDecision(False, "total open exposure cap reached")
        size = min(size, total_room)

        # Cash buffer must always survive the order.
        investable_cash = snapshot.cash - cfg.account.cash_buffer_usd
        if investable_cash <= 0:
            return RiskDecision(False, "cash buffer would be breached")
        size = min(size, investable_cash)

        # Don't take more than a fraction of visible book depth.
        size = min(size, book_depth_usd)

        if size < 1.0:  # dust threshold
            return RiskDecision(False, f"approved size {size:.2f} below minimum")

        return RiskDecision(True, "approved", approved_size=round(size, 2))

    # -- internals ---------------------------------------------------------- #
    def _escalate(self, level: HaltLevel, reason: str) -> None:
        order = [HaltLevel.RUNNING, HaltLevel.NO_NEW_POSITIONS,
                 HaltLevel.READ_ONLY, HaltLevel.HARD_HALT]
        if order.index(level) > order.index(self.halt_level):
            self.halt_level = level
        self._log(reason)

    def _log(self, reason: str) -> None:
        self.halt_reasons.append(reason)
