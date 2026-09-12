"""AI Probability Engine (spec 模块 #3 + 六、AI 预测格式).

The engine reads a market's resolution rules and (in a real deployment) news
and data, then outputs a *structured* :class:`Prediction`. Free-text opinions
are impossible by construction — every prediction must carry an estimated
probability, confidence, evidence, invalidating conditions, and a concrete
recommended limit price/size.

Two important boundaries from the design principles:

* The engine only *proposes*. It is never given the risk config and cannot size
  beyond a soft per-order hint; the Risk Manager has the final say (原则 #1/#2).
* The engine never sees private keys and never submits orders (原则 #4).

``HeuristicProbabilityEngine`` is a deterministic baseline used for the
simulation and tests. It models an imperfectly-calibrated forecaster: it starts
from the market mid and nudges toward a (noisy) fundamental estimate. Swap in an
LLM-backed engine by implementing :class:`ProbabilityEngine`.
"""

from __future__ import annotations

import random
from typing import Protocol

from ..models import Market, OrderBook, Outcome, Prediction


class ProbabilityEngine(Protocol):
    def predict(self, market: Market, yes_book: OrderBook) -> Prediction | None: ...


class HeuristicProbabilityEngine:
    """Baseline forecaster for simulation.

    If a ``true_probability`` callable is supplied (as the offline data source
    provides), the engine samples a noisy estimate around it — modelling a
    forecaster that is right on average but wrong on any single market. Without
    it, the engine falls back to a mild mean-reversion around the market mid so
    it degrades gracefully on live data.
    """

    def __init__(
        self,
        seed: int = 7,
        min_confidence: float = 0.60,
        true_probability=None,
        noise: float = 0.06,
    ):
        self.rng = random.Random(seed)
        self.min_confidence = min_confidence
        self.true_probability = true_probability
        self.noise = noise

    def predict(self, market: Market, yes_book: OrderBook) -> Prediction | None:
        yes_mid = yes_book.mid
        if yes_mid is None:
            return None

        if self.true_probability is not None:
            base = self.true_probability(market.market_id)
            est_yes = _clip(base + self.rng.gauss(0, self.noise))
        else:
            # No ground truth: weak reversion toward 0.5 as a placeholder signal.
            est_yes = _clip(yes_mid + 0.5 * (0.5 - yes_mid) * 0.2)

        # Choose the side the estimate favours vs the market.
        if est_yes >= yes_mid:
            outcome = Outcome.YES
            est_prob = est_yes
            book = yes_book
        else:
            outcome = Outcome.NO
            est_prob = 1.0 - est_yes
            # NO price = 1 - YES price; approximate the NO ask from the YES bid.
            book = yes_book

        market_price = _outcome_ask(yes_book, outcome)
        if market_price is None:
            return None

        # Confidence: higher for liquid markets and estimates far from 0.5,
        # capped below 1. This is the AI's self-assessed reliability.
        distance = abs(est_prob - 0.5) * 2  # 0..1
        liq_factor = min(1.0, market.liquidity_usd / 50000.0)
        confidence = _clip(0.5 + 0.3 * distance + 0.2 * liq_factor, 0.0, 0.97)

        edge = est_prob - market_price

        pred = Prediction(
            market_id=market.market_id,
            question=market.question,
            selected_outcome=outcome,
            estimated_probability=round(est_prob, 4),
            confidence=round(confidence, 4),
            market_price=round(market_price, 4),
            estimated_edge=round(edge, 4),
            time_horizon=self._horizon(market),
            evidence=self._evidence(market),
            invalidating_conditions=self._invalidating(market),
            recommended_action="BUY_LIMIT",
            # Post at the current ask (maker-friendly limit); executor may improve.
            recommended_price=round(market_price, 4),
            recommended_size=20.0,  # soft hint; Risk Manager decides final size
        )
        pred.validate()
        return pred

    @staticmethod
    def _horizon(market: Market) -> str:
        d = market.days_to_resolution()
        if d is None:
            return "unknown"
        return f"~{d:.0f} days to resolution"

    @staticmethod
    def _evidence(market: Market) -> list[str]:
        return [
            f"Resolution source: {market.resolution_source}",
            f"Market liquidity ${market.liquidity_usd:,.0f}, "
            f"volume ${market.volume_usd:,.0f}",
            "Baseline model estimate vs market-implied probability",
        ]

    @staticmethod
    def _invalidating(market: Market) -> list[str]:
        return [
            "Resolution rules change or the market is disputed/extended",
            "A sudden liquidity collapse widens the spread beyond limits",
            "New authoritative information flips the fundamental estimate",
        ]


def _outcome_ask(yes_book: OrderBook, outcome: Outcome) -> float | None:
    """Ask price to BUY the given outcome, derived from the YES book.

    For a binary market, NO price = 1 - YES price, so buying NO at its ask
    corresponds to (1 - YES bid).
    """
    if outcome is Outcome.YES:
        return yes_book.best_ask
    if yes_book.best_bid is None:
        return None
    return round(1.0 - yes_book.best_bid, 4)


def _clip(x: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, x))
