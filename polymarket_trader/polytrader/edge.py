"""Edge Calculator (spec 模块 #4).

Turns an AI probability estimate and a live order book into a *net* expected
edge after fees, slippage, and exit cost. The core inequality (第四版核心逻辑):

    net_edge = ai_probability
             - entry_price
             - fee
             - slippage
             - exit_cost

A trade is only allowed downstream if ``net_edge >= min_edge`` for the market's
liquidity tier.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .models import OrderBook, Side


@dataclass
class EdgeResult:
    ai_probability: float
    market_price: float          # entry price we would pay (best ask for a BUY)
    implied_probability: float   # market's implied probability = price
    gross_edge: float            # ai_probability - market_price
    fee: float
    slippage: float
    exit_cost: float
    net_edge: float
    expected_value_per_dollar: float
    tradeable: bool
    reason: str


def estimate_slippage(cfg: Config, book: OrderBook, side: Side, size: float) -> float:
    """Slippage as a fraction of price, scaled by how large the order is versus
    the depth available on the relevant side of the book."""
    depth = book.depth(side)
    if depth <= 0:
        return cfg.costs.base_slippage * 5  # no depth -> punitive
    ratio = size / depth
    # base + a component that grows with the fraction of depth consumed.
    return cfg.costs.base_slippage * (1.0 + ratio)


def compute_edge(
    cfg: Config,
    ai_probability: float,
    book: OrderBook,
    side: Side,
    size: float,
    is_maker: bool,
    high_liquidity: bool,
) -> EdgeResult:
    # For a BUY we pay the ask; for a SELL we receive the bid.
    if side is Side.BUY:
        entry_price = book.best_ask
    else:
        entry_price = book.best_bid

    if entry_price is None:
        return EdgeResult(
            ai_probability=ai_probability, market_price=float("nan"),
            implied_probability=float("nan"), gross_edge=0.0, fee=0.0,
            slippage=0.0, exit_cost=cfg.costs.exit_cost, net_edge=0.0,
            expected_value_per_dollar=0.0, tradeable=False,
            reason="no order book liquidity on the required side",
        )

    fee_rate = cfg.costs.maker_fee_rate if is_maker else cfg.costs.taker_fee_rate
    fee = fee_rate  # fee expressed per unit price (fraction of notional)
    slippage = estimate_slippage(cfg, book, side, size)
    exit_cost = cfg.costs.exit_cost

    gross_edge = ai_probability - entry_price
    net_edge = gross_edge - fee - slippage - exit_cost

    min_edge = cfg.min_edge_for(high_liquidity)
    tradeable = net_edge >= min_edge
    reason = (
        f"net_edge={net_edge:.4f} >= min_edge={min_edge:.4f}"
        if tradeable
        else f"net_edge={net_edge:.4f} < min_edge={min_edge:.4f}"
    )

    return EdgeResult(
        ai_probability=ai_probability,
        market_price=entry_price,
        implied_probability=entry_price,
        gross_edge=gross_edge,
        fee=fee,
        slippage=slippage,
        exit_cost=exit_cost,
        net_edge=net_edge,
        expected_value_per_dollar=net_edge,
        tradeable=tradeable,
        reason=reason,
    )
