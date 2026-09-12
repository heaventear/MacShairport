import unittest

from polytrader.config import load_config, parse_simple_yaml, Config


class TestConfig(unittest.TestCase):
    def test_loads_default_config(self):
        cfg = load_config()
        self.assertIsInstance(cfg, Config)
        self.assertEqual(cfg.account.total_capital_usd, 1000.0)
        self.assertEqual(cfg.execution.mode, "simulation")

    def test_min_edge_tiers(self):
        cfg = load_config()
        self.assertLess(
            cfg.min_edge_for(high_liquidity=True),
            cfg.min_edge_for(high_liquidity=False),
        )

    def test_yaml_subset_parser(self):
        data = parse_simple_yaml(
            "a:\n"
            "  b: 1\n"
            "  c: 2.5\n"
            "  d: true\n"
            "  e: hello  # comment\n"
        )
        self.assertEqual(data["a"]["b"], 1)
        self.assertEqual(data["a"]["c"], 2.5)
        self.assertIs(data["a"]["d"], True)
        self.assertEqual(data["a"]["e"], "hello")

    def test_validation_rejects_bad_buffer(self):
        import tempfile
        import os

        # cash buffer >= total capital must be rejected by _validate.
        bad = (
            "account:\n  total_capital_usd: 100\n  cash_buffer_usd: 200\n"
            "strategy:\n  min_edge_high_liquidity: 0.06\n  min_edge_normal: 0.09\n"
            "  min_confidence: 0.6\n  child_order_count: 3\n  max_price_drift: 0.02\n"
            "  order_ttl_seconds: 120\n"
            "costs:\n  taker_fee_rate: 0\n  maker_fee_rate: 0\n  base_slippage: 0.005\n"
            "  exit_cost: 0.01\n"
            "selection:\n  min_liquidity_usd: 5000\n  min_book_depth_usd: 200\n"
            "  max_spread: 0.03\n  min_price: 0.05\n  max_price: 0.95\n"
            "  max_days_to_resolution: 30\n  require_order_book: true\n"
            "sizing:\n  max_per_market: 50\n  max_per_order: 20\n  max_per_event: 80\n"
            "  max_total_open: 300\n"
            "circuit_breakers:\n  daily_loss_halt_pct: 0.02\n  drawdown_halve_pct: 0.05\n"
            "  drawdown_halt_pct: 0.08\n  max_reconcile_diff_usd: 0.5\n"
            "  api_error_streak_halt: 3\n"
            "execution:\n  mode: simulation\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_config(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
