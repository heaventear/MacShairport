import unittest

from polytrader.models import Outcome
from polytrader.portfolio import PortfolioManager
from polytrader.reconcile import (
    LocalPortfolioSource, Reconciler, SimulatedExchangeSource,
)


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.pm = PortfolioManager(starting_cash=1000.0)
        self.pm.apply_buy("m1", "e1", Outcome.YES, "t", 0.5, 20.0, 0.0)

    def test_matching_sources_reconcile(self):
        local = LocalPortfolioSource(self.pm).view()
        ext = SimulatedExchangeSource(self.pm).view()
        report = Reconciler().reconcile(local, [ext])
        self.assertTrue(report.consistent)
        self.assertEqual(report.mismatches(), [])

    def test_cash_drift_flags_mismatch(self):
        local = LocalPortfolioSource(self.pm).view()
        ext = SimulatedExchangeSource(self.pm, cash_drift=5.0).view()
        report = Reconciler(cash_tol=0.5).reconcile(local, [ext])
        self.assertFalse(report.consistent)
        cash_entry = next(e for e in report.entries if e.field == "cash")
        self.assertFalse(cash_entry.consistent)
        self.assertAlmostEqual(cash_entry.diff, 5.0)

    def test_share_drift_flags_mismatch(self):
        local = LocalPortfolioSource(self.pm).view()
        ext = SimulatedExchangeSource(self.pm, share_drift=2.0).view()
        report = Reconciler().reconcile(local, [ext])
        self.assertFalse(report.consistent)
        self.assertTrue(any(e.field.startswith("shares:") for e in report.mismatches()))

    def test_multiple_sources(self):
        local = LocalPortfolioSource(self.pm).view()
        exchange = SimulatedExchangeSource(self.pm, name="exchange").view()
        onchain = SimulatedExchangeSource(self.pm, name="onchain").view()
        report = Reconciler().reconcile(local, [exchange, onchain])
        names = {e.source for e in report.entries}
        self.assertEqual(names, {"exchange", "onchain"})
        self.assertTrue(report.consistent)


if __name__ == "__main__":
    unittest.main()
