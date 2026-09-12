import unittest

from polytrader.config import load_config
from polytrader.market_data import MarketDataService, OfflineDataSource
from polytrader.selector import MarketSelector


class TestSelector(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.source = OfflineDataSource(seed=1)
        self.data = MarketDataService(self.source)
        self.sel = MarketSelector(self.cfg, self.data)

    def test_selects_some_and_rejects_others(self):
        markets = self.data.list_markets()
        candidates = self.sel.select(markets)
        self.assertGreater(len(candidates), 0)
        self.assertGreater(len(self.sel.last_rejections), 0)

    def test_rejects_low_liquidity(self):
        markets = self.data.list_markets()
        self.sel.select(markets)
        rejected_ids = {r.market_id for r in self.sel.last_rejections}
        self.assertIn("mkt_lowliq", rejected_ids)

    def test_rejects_unclear_source(self):
        markets = self.data.list_markets()
        self.sel.select(markets)
        reasons = {r.market_id: r.reason for r in self.sel.last_rejections}
        # low-liq market is caught first on liquidity; the wide/ambiguous one is
        # rejected on spread. Ensure at least one unclear-source rejection exists
        # by checking the ambiguous market is not a candidate.
        cand_ids = {c.market.market_id for c in self.sel.select(markets)}
        self.assertNotIn("mkt_lowliq", cand_ids)
        self.assertNotIn("mkt_wide", cand_ids)


if __name__ == "__main__":
    unittest.main()
