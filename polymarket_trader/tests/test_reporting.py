import unittest

from polytrader.reporting import (
    PredictionOutcome, brier_score, calibration_bins, compute_trade_metrics,
)


class TestReporting(unittest.TestCase):
    def test_brier_perfect(self):
        items = [PredictionOutcome(1.0, 1, 0.9, True),
                 PredictionOutcome(0.0, 0, 0.9, True)]
        self.assertAlmostEqual(brier_score(items), 0.0)

    def test_brier_always_half(self):
        items = [PredictionOutcome(0.5, 1, 0.5, True),
                 PredictionOutcome(0.5, 0, 0.5, True)]
        self.assertAlmostEqual(brier_score(items), 0.25)

    def test_brier_none_when_empty(self):
        self.assertIsNone(brier_score([]))

    def test_calibration_bins(self):
        items = [PredictionOutcome(0.65, 1, 0.7, True),
                 PredictionOutcome(0.68, 0, 0.7, True)]
        bins = calibration_bins(items, n_bins=5)
        b = next(x for x in bins if x["range"] == "0.6-0.8")
        self.assertEqual(b["count"], 2)
        self.assertAlmostEqual(b["actual_freq"], 0.5)

    def test_trade_metrics(self):
        trades = [
            {"outcome_result": "WON", "pnl": 10.0, "fee": 0.1, "slippage": 0.0,
             "filled_size": 20, "anomalous": 0},
            {"outcome_result": "LOST", "pnl": -20.0, "fee": 0.2, "slippage": 0.01,
             "filled_size": 20, "anomalous": 0},
            {"outcome_result": "", "pnl": 0.0, "fee": 0.0, "slippage": 0.0,
             "filled_size": 10, "anomalous": 1},
        ]
        m = compute_trade_metrics(trades)
        self.assertEqual(m.total_trades, 3)
        self.assertEqual(m.wins, 1)
        self.assertEqual(m.losses, 1)
        self.assertAlmostEqual(m.win_rate, 0.5)
        self.assertEqual(m.anomalous_trades, 1)


if __name__ == "__main__":
    unittest.main()
