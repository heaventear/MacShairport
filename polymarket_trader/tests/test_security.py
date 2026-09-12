import unittest

from polytrader.security import (
    ExternalSigner, NoSigner, SignerUnavailable, check_geoblock,
)


class TestSigner(unittest.TestCase):
    def test_no_signer_fails_closed(self):
        s = NoSigner()
        with self.assertRaises(SignerUnavailable):
            s.address()
        with self.assertRaises(SignerUnavailable):
            s.sign(b"payload")

    def test_external_signer_not_implemented(self):
        s = ExternalSigner("https://signer.internal")
        with self.assertRaises(NotImplementedError):
            s.sign(b"payload")


class TestGeoblock(unittest.TestCase):
    def test_fails_closed_without_acknowledgement(self):
        r = check_geoblock("GB", compliance_acknowledged=False)
        self.assertFalse(r.allowed)

    def test_fails_closed_on_unknown_jurisdiction(self):
        r = check_geoblock(None, compliance_acknowledged=True)
        self.assertFalse(r.allowed)

    def test_blocks_restricted_jurisdiction(self):
        r = check_geoblock("US", compliance_acknowledged=True)
        self.assertFalse(r.allowed)

    def test_allows_permitted_jurisdiction(self):
        r = check_geoblock("GB", compliance_acknowledged=True)
        self.assertTrue(r.allowed)


class TestLiveExecutorBoundary(unittest.TestCase):
    def test_live_executor_lists_unmet_prerequisites(self):
        from polytrader.config import load_config
        from polytrader.orders import LiveExecutor
        from polytrader.models import (
            Order, OrderBook, OrderType, Outcome, Side,
        )

        ex = LiveExecutor(load_config())  # simulation mode, no signer, no geoblock
        order = Order(order_id="o", market_id="m", token_id="t",
                      outcome=Outcome.YES, side=Side.BUY, order_type=OrderType.LIMIT,
                      limit_price=0.5, size=10)
        with self.assertRaises(RuntimeError) as ctx:
            ex.submit(order, OrderBook("m", "t"))
        msg = str(ctx.exception)
        self.assertIn("not 'live'", msg)
        self.assertIn("signer", msg)
        self.assertIn("geoblock", msg)


if __name__ == "__main__":
    unittest.main()
