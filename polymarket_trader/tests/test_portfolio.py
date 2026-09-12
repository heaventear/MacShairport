import unittest

from polytrader.models import Outcome
from polytrader.portfolio import PortfolioManager


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        self.pm = PortfolioManager(starting_cash=1000.0)

    def test_buy_updates_cash_and_shares(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", fill_price=0.5,
                          notional=20.0, fee=0.1)
        self.assertAlmostEqual(self.pm.cash, 1000.0 - 20.0 - 0.1)
        pos = self.pm.positions[("m", Outcome.YES)]
        self.assertAlmostEqual(pos.shares, 40.0)  # 20 / 0.5
        self.assertAlmostEqual(pos.avg_cost, 0.5)

    def test_average_cost_across_two_buys(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.5, 20.0, 0.0)
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.6, 30.0, 0.0)
        pos = self.pm.positions[("m", Outcome.YES)]
        # shares: 40 + 50 = 90; cost 50; avg = 50/90
        self.assertAlmostEqual(pos.shares, 90.0)
        self.assertAlmostEqual(pos.avg_cost, 50.0 / 90.0)

    def test_settle_win(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.5, 20.0, 0.0)
        pnl = self.pm.settle("m", Outcome.YES, won=True)
        # 40 shares * 1.0 - 20 cost = +20
        self.assertAlmostEqual(pnl, 20.0)
        self.assertAlmostEqual(self.pm.realized_pnl, 20.0)
        self.assertTrue(self.pm.positions[("m", Outcome.YES)].resolved)

    def test_settle_loss(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.5, 20.0, 0.0)
        pnl = self.pm.settle("m", Outcome.YES, won=False)
        self.assertAlmostEqual(pnl, -20.0)

    def test_sell_to_close(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.5, 20.0, 0.0)
        pnl = self.pm.apply_sell_to_close("m", Outcome.YES, fill_price=0.6,
                                          shares=40.0, fee=0.0)
        # sell 40 @ 0.6 = 24 proceeds - 20 cost = +4
        self.assertAlmostEqual(pnl, 4.0)
        self.assertAlmostEqual(self.pm.positions[("m", Outcome.YES)].shares, 0.0)

    def test_snapshot_caps(self):
        self.pm.apply_buy("m1", "e1", Outcome.YES, "t", 0.5, 20.0, 0.0)
        self.pm.apply_buy("m2", "e1", Outcome.NO, "t", 0.4, 16.0, 0.0)
        snap = self.pm.snapshot(marks={})
        self.assertAlmostEqual(snap.per_market_notional["m1"], 20.0)
        self.assertAlmostEqual(snap.per_event_notional["e1"], 36.0)
        self.assertAlmostEqual(snap.total_open_notional, 36.0)

    def test_equity_marks_to_market(self):
        self.pm.apply_buy("m", "e", Outcome.YES, "t", 0.5, 20.0, 0.0)
        eq = self.pm.equity(marks={("m", Outcome.YES): 0.6})
        # cash 980 + 40 shares * 0.6 = 980 + 24 = 1004
        self.assertAlmostEqual(eq, 1004.0)


if __name__ == "__main__":
    unittest.main()
