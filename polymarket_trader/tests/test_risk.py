import unittest

from polytrader.config import load_config
from polytrader.risk import HaltLevel, PortfolioSnapshot, RiskManager


def snap(cash=1000.0, total_open=0.0, per_market=None, per_event=None, equity=1000.0):
    return PortfolioSnapshot(
        equity=equity, cash=cash, total_open_notional=total_open,
        per_market_notional=per_market or {}, per_event_notional=per_event or {},
    )


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.rm = RiskManager(self.cfg)

    def _approve(self, **kw):
        base = dict(
            market_id="m", event_id="e", requested_size=20, confidence=0.8,
            net_edge=0.10, high_liquidity=True, book_depth_usd=1000.0,
            snapshot=snap(),
        )
        base.update(kw)
        return self.rm.approve_order(**base)

    def test_approves_valid_order(self):
        d = self._approve()
        self.assertTrue(d.approved)
        self.assertLessEqual(d.approved_size, self.cfg.sizing.max_per_order)

    def test_low_confidence_rejected(self):
        self.assertFalse(self._approve(confidence=0.5).approved)

    def test_low_edge_rejected(self):
        self.assertFalse(self._approve(net_edge=0.01).approved)

    def test_per_market_cap(self):
        s = snap(per_market={"m": self.cfg.sizing.max_per_market})
        self.assertFalse(self._approve(snapshot=s).approved)

    def test_per_event_cap(self):
        s = snap(per_event={"e": self.cfg.sizing.max_per_event})
        self.assertFalse(self._approve(snapshot=s).approved)

    def test_total_open_cap(self):
        s = snap(total_open=self.cfg.sizing.max_total_open)
        self.assertFalse(self._approve(snapshot=s).approved)

    def test_cash_buffer_protected(self):
        s = snap(cash=self.cfg.account.cash_buffer_usd)  # nothing investable
        self.assertFalse(self._approve(snapshot=s).approved)

    def test_daily_loss_halts_new_positions(self):
        self.rm.start_new_day(1, 1000.0)
        self.rm.observe_equity(975.0)  # -2.5% > 2% daily halt
        self.assertFalse(self.rm.can_open_new_positions)
        self.assertFalse(self._approve().approved)

    def test_drawdown_halve_then_halt(self):
        self.rm.observe_equity(1000.0)
        self.rm.observe_equity(945.0)  # -5.5% drawdown -> halve
        self.assertEqual(self.rm.size_multiplier, 0.5)
        self.rm.observe_equity(910.0)  # -9% drawdown -> halt new positions
        self.assertEqual(self.rm.halt_level, HaltLevel.NO_NEW_POSITIONS)

    def test_api_errors_read_only(self):
        self.rm.observe_api_errors(self.cfg.circuit_breakers.api_error_streak_halt)
        self.assertEqual(self.rm.halt_level, HaltLevel.READ_ONLY)

    def test_reconciliation_mismatch_hard_halt(self):
        self.rm.on_reconciliation(consistent=False, kind="cash")
        self.assertEqual(self.rm.halt_level, HaltLevel.HARD_HALT)

    def test_manual_resume(self):
        self.rm.manual_pause()
        self.assertFalse(self.rm.can_open_new_positions)
        self.rm.manual_resume()
        self.assertTrue(self.rm.can_open_new_positions)

    def test_halve_reduces_size(self):
        self.rm.observe_equity(1000.0)
        self.rm.observe_equity(945.0)
        d = self._approve(requested_size=20)
        self.assertLessEqual(d.approved_size, 10.0 + 1e-9)


if __name__ == "__main__":
    unittest.main()
