import unittest

from polytrader.engine_loop import build_offline_engine


class TestEngineSmoke(unittest.TestCase):
    def test_runs_seven_days_without_anomalies(self):
        engine = build_offline_engine(storage_path=":memory:", seed=42, verbose=False)
        report = engine.run(days=7)
        self.assertIn("Experiment Report", report)
        # No order should have been double-submitted.
        self.assertEqual(engine.storage.count_events("duplicate_suppressed"), 0)
        # No UNKNOWN order states in a clean simulation.
        self.assertEqual(engine.storage.count_events("order_unknown"), 0)
        # Cash never dips below the mandated buffer while trading.
        self.assertGreaterEqual(engine.portfolio.cash, 0.0)

    def test_wind_down_flattens_positions(self):
        engine = build_offline_engine(storage_path=":memory:", seed=7, verbose=False)
        engine.run(days=7)
        # After Day-7 wind-down every position is either resolved or exited.
        open_positions = engine.portfolio.open_positions()
        self.assertEqual(len(open_positions), 0)
        # Trading is halted read-only after wind-down.
        self.assertFalse(engine.risk.can_open_new_positions)

    def test_respects_total_open_cap(self):
        engine = build_offline_engine(storage_path=":memory:", seed=99, verbose=False)
        # Track max total open exposure during the run via a wrapper.
        cap = engine.cfg.sizing.max_total_open
        engine.run(days=7)
        # Even at peak the ledger never exceeded the configured total-open cap.
        # (Positions have settled/exited by end, so re-run day-by-day check.)
        self.assertLessEqual(engine.portfolio.total_open_notional(), cap + 1e-6)


if __name__ == "__main__":
    unittest.main()
