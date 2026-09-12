import unittest

from polytrader.config import load_config
from polytrader.edge import compute_edge, estimate_slippage
from polytrader.models import BookLevel, OrderBook, Side


def make_book(best_ask=0.59, best_bid=0.57, depth=500.0):
    return OrderBook(
        market_id="m",
        token_id="t",
        bids=[BookLevel(best_bid, depth), BookLevel(best_bid - 0.01, depth)],
        asks=[BookLevel(best_ask, depth), BookLevel(best_ask + 0.01, depth)],
    )


class TestEdge(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_positive_edge_is_tradeable(self):
        book = make_book(best_ask=0.59)
        res = compute_edge(self.cfg, ai_probability=0.68, book=book, side=Side.BUY,
                           size=20, is_maker=True, high_liquidity=True)
        # gross edge 0.09; minus slippage/exit still above 6% high-liq threshold
        self.assertGreater(res.gross_edge, 0.08)
        self.assertTrue(res.tradeable)

    def test_thin_edge_rejected(self):
        book = make_book(best_ask=0.65)
        res = compute_edge(self.cfg, ai_probability=0.68, book=book, side=Side.BUY,
                           size=20, is_maker=True, high_liquidity=True)
        self.assertFalse(res.tradeable)

    def test_no_liquidity_not_tradeable(self):
        book = OrderBook("m", "t", bids=[], asks=[])
        res = compute_edge(self.cfg, ai_probability=0.9, book=book, side=Side.BUY,
                           size=20, is_maker=True, high_liquidity=True)
        self.assertFalse(res.tradeable)

    def test_slippage_scales_with_size(self):
        book = make_book(depth=100.0)
        small = estimate_slippage(self.cfg, book, Side.BUY, 10)
        large = estimate_slippage(self.cfg, book, Side.BUY, 200)
        self.assertGreater(large, small)

    def test_normal_market_needs_more_edge(self):
        # gross edge 0.09; net after slippage(~0.005)+exit(0.01) ~= 0.075.
        book = make_book(best_ask=0.59)
        hi = compute_edge(self.cfg, 0.68, book, Side.BUY, 20, True, high_liquidity=True)
        lo = compute_edge(self.cfg, 0.68, book, Side.BUY, 20, True, high_liquidity=False)
        # ~7.5% net clears the 6% high-liq bar but not the 9% normal bar.
        self.assertTrue(hi.tradeable)
        self.assertFalse(lo.tradeable)


if __name__ == "__main__":
    unittest.main()
