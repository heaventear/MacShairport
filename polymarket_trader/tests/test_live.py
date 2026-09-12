import unittest
from dataclasses import replace

from polytrader.config import ExecutionConfig, load_config
from polytrader.models import (
    Order, OrderBook, OrderStatus, OrderType, Outcome, Side,
)
from polytrader.orders import OrderManager
from polytrader.security import NoSigner, check_geoblock
from polytrader.storage import Storage
from polytrader.live import (
    FakeClobClient, GatedClobExecutor, LiveExecutionBlocked,
    PyClobClientAdapter, live_preflight,
)


class FakeSigner:
    """Test double for an isolated signer (never a real key)."""

    def __init__(self):
        self.calls = 0

    def address(self) -> str:
        return "0xTEST"

    def sign(self, payload: bytes) -> str:
        self.calls += 1
        return "0xSIGNATURE"


def live_cfg():
    return replace(load_config(), execution=ExecutionConfig(mode="live"))


def an_order(size=20.0, limit=0.5, key="k"):
    return Order(order_id="o1", market_id="m", token_id="t", outcome=Outcome.YES,
                 side=Side.BUY, order_type=OrderType.LIMIT, limit_price=limit,
                 size=size, client_key=key)


ALLOWED = check_geoblock("GB", compliance_acknowledged=True)


class TestPreflight(unittest.TestCase):
    def _report(self, **over):
        base = dict(signer=FakeSigner(), geoblock=ALLOWED, authorized=True,
                    reconciliation_ok=True, halt_level="RUNNING")
        base.update(over)
        return live_preflight(live_cfg(), **base)

    def test_all_gates_pass(self):
        self.assertTrue(self._report().ok)

    def test_simulation_mode_blocks(self):
        r = live_preflight(load_config(), signer=FakeSigner(), geoblock=ALLOWED,
                           authorized=True, reconciliation_ok=True, halt_level="RUNNING")
        self.assertFalse(r.ok)

    def test_missing_authorization_blocks(self):
        self.assertFalse(self._report(authorized=False).ok)

    def test_no_signer_blocks(self):
        self.assertFalse(self._report(signer=NoSigner()).ok)

    def test_geoblock_denied_blocks(self):
        denied = check_geoblock("US", compliance_acknowledged=True)
        self.assertFalse(self._report(geoblock=denied).ok)

    def test_unreconciled_blocks(self):
        self.assertFalse(self._report(reconciliation_ok=False).ok)

    def test_halted_blocks(self):
        self.assertFalse(self._report(halt_level="READ_ONLY").ok)


class TestGatedExecutor(unittest.TestCase):
    def _executor(self, client=None, **over):
        kw = dict(reconciliation_ok=True, halt_level="RUNNING", authorized=True)
        kw.update(over)
        return GatedClobExecutor(live_cfg(), FakeSigner(),
                                 client or FakeClobClient(), ALLOWED, **kw)

    def test_cannot_construct_when_blocked(self):
        with self.assertRaises(LiveExecutionBlocked):
            GatedClobExecutor(load_config(), FakeSigner(), FakeClobClient(),
                              ALLOWED, authorized=True)

    def test_cannot_construct_without_authorization(self):
        with self.assertRaises(LiveExecutionBlocked):
            self._executor(authorized=False)

    def test_full_fill_signs_and_places(self):
        signer = FakeSigner()
        client = FakeClobClient(fill_ratio=1.0)
        ex = GatedClobExecutor(live_cfg(), signer, client, ALLOWED, authorized=True)
        ev = ex.submit(an_order(size=20), OrderBook("m", "t"))
        self.assertEqual(ev.status, OrderStatus.FILLED)
        self.assertAlmostEqual(ev.filled_size, 20.0)
        self.assertEqual(signer.calls, 1)          # signing was delegated
        self.assertEqual(len(client.placed), 1)

    def test_partial_fill(self):
        ex = self._executor(FakeClobClient(fill_ratio=0.5))
        ev = ex.submit(an_order(size=20), OrderBook("m", "t"))
        self.assertEqual(ev.status, OrderStatus.PARTIALLY_FILLED)
        self.assertAlmostEqual(ev.filled_size, 10.0)

    def test_timeout_becomes_unknown_via_order_manager(self):
        cfg = live_cfg()
        ex = GatedClobExecutor(cfg, FakeSigner(),
                               FakeClobClient(raise_on_place=True), ALLOWED,
                               authorized=True)
        om = OrderManager(cfg, ex, Storage(":memory:"))
        ev = om.submit(an_order(size=20), OrderBook("m", "t"))
        self.assertEqual(ev.status, OrderStatus.UNKNOWN)

    def test_cancel_delegates(self):
        client = FakeClobClient()
        ex = GatedClobExecutor(live_cfg(), FakeSigner(), client, ALLOWED,
                               authorized=True)
        order = an_order()
        ex.submit(order, OrderBook("m", "t"))
        self.assertTrue(ex.cancel(order))
        self.assertEqual(len(client.cancelled), 1)


class TestRealAdapterStub(unittest.TestCase):
    def test_pyclob_adapter_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            PyClobClientAdapter()


if __name__ == "__main__":
    unittest.main()
