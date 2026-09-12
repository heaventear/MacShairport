"""Portfolio Manager (spec 模块 #7 + 十一、数据记录要求).

Tracks cash, positions, average cost, realized / unrealized PnL, and settlement.
Positions are keyed by (market_id, outcome). Shares are the number of outcome
tokens held; each token settles at 1.0 if its outcome wins and 0.0 otherwise.

Provides the :class:`PortfolioSnapshot` the Risk Manager consumes, and the
reconciliation hook comparing the local ledger against an external view.
"""

from __future__ import annotations

from .models import Outcome, Position
from .risk import PortfolioSnapshot


class PortfolioManager:
    def __init__(self, starting_cash: float):
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.positions: dict[tuple[str, Outcome], Position] = {}
        self.market_event: dict[str, str] = {}
        self.realized_pnl = 0.0
        self.total_fees = 0.0

    # -- fills -------------------------------------------------------------- #
    def apply_buy(
        self,
        market_id: str,
        event_id: str,
        outcome: Outcome,
        token_id: str,
        fill_price: float,
        notional: float,
        fee: float,
    ) -> None:
        """Record a BUY fill. ``notional`` is USD-equivalent cost (excl. fee)."""
        if fill_price <= 0:
            return
        self.market_event[market_id] = event_id
        key = (market_id, outcome)
        pos = self.positions.get(key) or Position(
            market_id=market_id, outcome=outcome, token_id=token_id
        )
        shares_added = notional / fill_price
        new_shares = pos.shares + shares_added
        new_cost = pos.size + notional
        pos.avg_cost = new_cost / new_shares if new_shares > 0 else 0.0
        pos.shares = new_shares
        pos.size = new_cost
        pos.fees_paid += fee
        self.positions[key] = pos

        self.cash -= notional + fee
        self.total_fees += fee

    def apply_sell_to_close(
        self,
        market_id: str,
        outcome: Outcome,
        fill_price: float,
        shares: float,
        fee: float,
    ) -> float:
        """Sell (part of) a position before resolution. Returns realized PnL."""
        key = (market_id, outcome)
        pos = self.positions.get(key)
        if pos is None or pos.shares <= 0:
            return 0.0
        shares = min(shares, pos.shares)
        proceeds = shares * fill_price - fee
        cost = shares * pos.avg_cost
        pnl = proceeds - cost
        pos.shares -= shares
        pos.size = pos.shares * pos.avg_cost
        pos.realized_pnl += pnl
        pos.fees_paid += fee
        self.realized_pnl += pnl
        self.total_fees += fee
        self.cash += proceeds
        if pos.shares <= 1e-9:
            pos.shares = 0.0
            pos.size = 0.0
        return pnl

    def settle(self, market_id: str, outcome: Outcome, won: bool) -> float:
        """Resolve a position at settlement. Returns realized PnL."""
        key = (market_id, outcome)
        pos = self.positions.get(key)
        if pos is None or pos.resolved or pos.shares <= 0:
            return 0.0
        value = pos.shares * (1.0 if won else 0.0)
        cost = pos.size
        pnl = value - cost
        pos.realized_pnl += pnl
        pos.resolved = True
        pos.resolution_value = 1.0 if won else 0.0
        self.realized_pnl += pnl
        self.cash += value
        pos.shares = 0.0
        pos.size = 0.0
        return pnl

    # -- valuation ---------------------------------------------------------- #
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if not p.resolved and p.shares > 0]

    def total_open_notional(self) -> float:
        return sum(p.size for p in self.open_positions())

    def unrealized_pnl(self, marks: dict[tuple[str, Outcome], float]) -> float:
        total = 0.0
        for key, pos in self.positions.items():
            if pos.resolved or pos.shares <= 0:
                continue
            mark = marks.get(key, pos.avg_cost)
            total += pos.unrealized_pnl(mark)
        return total

    def equity(self, marks: dict[tuple[str, Outcome], float]) -> float:
        """Cash + mark-to-market value of open positions."""
        pos_value = 0.0
        for key, pos in self.positions.items():
            if pos.resolved or pos.shares <= 0:
                continue
            mark = marks.get(key, pos.avg_cost)
            pos_value += pos.shares * mark
        return self.cash + pos_value

    def snapshot(self, marks: dict[tuple[str, Outcome], float]) -> PortfolioSnapshot:
        per_market: dict[str, float] = {}
        per_event: dict[str, float] = {}
        for pos in self.open_positions():
            per_market[pos.market_id] = per_market.get(pos.market_id, 0.0) + pos.size
            evt = self.market_event.get(pos.market_id, pos.market_id)
            per_event[evt] = per_event.get(evt, 0.0) + pos.size
        return PortfolioSnapshot(
            equity=self.equity(marks),
            cash=self.cash,
            total_open_notional=self.total_open_notional(),
            per_market_notional=per_market,
            per_event_notional=per_event,
        )

    # -- reconciliation ----------------------------------------------------- #
    def reconcile_cash(self, external_cash: float) -> tuple[float, float]:
        """Return (local, external) cash for the daily 资金对账."""
        return self.cash, external_cash
