"""Market Selector (spec 模块 #2 + 五、市场筛选规则).

Filters the market universe down to a candidate list of high-liquidity,
clear-rules binary markets. Anything ambiguous, illiquid, wide-spread, or with
unclear resolution is rejected — and every rejection is recorded with a reason
so the decision is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .models import Market, Outcome
from .market_data import MarketDataService

# Keywords that hint at subjective / dispute-prone resolution sources.
_UNCLEAR_SOURCE_HINTS = ("unclear", "disputed", "conflicting", "tbd", "subjective")


@dataclass
class Candidate:
    market: Market
    high_liquidity: bool
    spread: float
    reasons: list[str]


@dataclass
class Rejection:
    market_id: str
    question: str
    reason: str


class MarketSelector:
    def __init__(self, cfg: Config, data: MarketDataService):
        self.cfg = cfg
        self.data = data
        self.last_rejections: list[Rejection] = []

    def select(self, markets: list[Market]) -> list[Candidate]:
        sel = self.cfg.selection
        candidates: list[Candidate] = []
        self.last_rejections = []

        for m in markets:
            reason = self._reject_reason(m)
            if reason is not None:
                self.last_rejections.append(Rejection(m.market_id, m.question, reason))
                continue

            book = self.data.get_order_book(m, Outcome.YES)
            if book is None or book.best_bid is None or book.best_ask is None:
                self.last_rejections.append(
                    Rejection(m.market_id, m.question, "no order book / one-sided book")
                )
                continue

            spread = book.spread or 1.0
            if spread > sel.max_spread:
                self.last_rejections.append(
                    Rejection(m.market_id, m.question, f"spread {spread:.3f} > max")
                )
                continue

            mid = book.mid or 0.5
            if not (sel.min_price <= mid <= sel.max_price):
                self.last_rejections.append(
                    Rejection(m.market_id, m.question, f"mid {mid:.3f} out of band")
                )
                continue

            if book.depth_top_ok(sel.min_book_depth_usd) is False:
                self.last_rejections.append(
                    Rejection(m.market_id, m.question, "insufficient book depth")
                )
                continue

            high_liq = m.liquidity_usd >= sel.min_liquidity_usd * 2
            candidates.append(
                Candidate(
                    market=m,
                    high_liquidity=high_liq,
                    spread=spread,
                    reasons=["passed all selection gates"],
                )
            )
        return candidates

    def _reject_reason(self, m: Market) -> str | None:
        sel = self.cfg.selection
        if not m.active or not m.accepting_orders:
            return "market inactive / not accepting orders"
        if sel.require_order_book and not m.yes_token_id:
            return "no CLOB order book"
        if m.liquidity_usd < sel.min_liquidity_usd:
            return f"liquidity {m.liquidity_usd:.0f} < min {sel.min_liquidity_usd:.0f}"
        if not m.resolution_rules.strip():
            return "missing resolution rules"
        src = m.resolution_source.lower()
        if not src or any(h in src for h in _UNCLEAR_SOURCE_HINTS):
            return f"unclear/authoritative-less resolution source: {m.resolution_source!r}"
        dtr = m.days_to_resolution()
        if dtr is None:
            return "no resolution time"
        if dtr < 0:
            return "already past resolution time"
        if dtr > sel.max_days_to_resolution:
            return f"resolves in {dtr:.0f}d > max {sel.max_days_to_resolution}d"
        return None
