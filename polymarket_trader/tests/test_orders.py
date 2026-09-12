import unittest

from polytrader.config import load_config
from polytrader.models import (
    BookLevel, Order, OrderBook, OrderStatus, OrderType, Outcome, Side,
)
from polytrader.orders import OrderManager, SimulationExecutor, LiveExecutor
from polytrader.orders.state_machine import can_transition
from polytrader.storage import Storage


def book(ask=0.5, depth=100.0):
    return OrderBook("m", "t",
                     bids=[BookLevel(ask - 0.01, depth)],
                     asks=[BookLevel(ask, depth), BookLevel(ask + 0.01, depth)])


def an_order(size=20.0, limit=0.55, key="k1", now=0.0):
    return Order(
        order_id="ord1", market_id="m", token_id="t", outcome=Outcome.YES,
        side=Side.BUY, order_type=OrderType.LIMIT, limit_price=limit, size=size,
        client_key=key, created_ts=now, expires_ts=now + 120,
    )


class TestStateMachine(unittest.TestCase):
    def test_legal_and_illegal(self):
        self.assertTrue(can_transition(OrderStatus.CREATED, OrderStatus.SUBMITTED))
        self.assertTrue(can_transition(OrderStatus.LIVE, OrderStatus.CANCEL_REQUESTED))
        self.assertFalse(can_transition(OrderStatus.FILLED, OrderStatus.LIVE))
        self.assertFalse(can_transition(OrderStatus.CANCELLED, OrderStatus.FILLED))


class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.storage = Storage(":memory:")
        self.om = OrderManager(self.cfg, SimulationExecutor(self.cfg), self.storage)

    def test_full_fill(self):
        o = an_order(size=20, limit=0.55)
        ev = self.om.submit(o, book(ask=0.5, depth=100))
        self.assertEqual(o.status, OrderStatus.FILLED)
        self.assertAlmostEqual(ev.filled_size, 20.0)
        self.assertAlmostEqual(o.avg_fill_price, 0.5)

    def test_partial_fill_when_thin(self):
        # limit 0.50 only crosses the touch (0.50); the 0.51 level is out of
        # reach, so only its 30 units fill of the 50 requested.
        o = an_order(size=50, limit=0.50)
        ev = self.om.submit(o, book(ask=0.5, depth=30))
        self.assertEqual(o.status, OrderStatus.PARTIALLY_FILLED)
        self.assertAlmostEqual(ev.filled_size, 30.0)

    def test_no_cross_rests_live(self):
        o = an_order(size=20, limit=0.45)  # below ask 0.5 -> no cross
        self.om.submit(o, book(ask=0.5, depth=100))
        self.assertEqual(o.status, OrderStatus.LIVE)

    def test_idempotency_blocks_duplicate(self):
        o1 = an_order(size=20, key="same")
        self.om.submit(o1, book())
        o2 = an_order(size=20, key="same")
        o2.order_id = "ord2"
        ev = self.om.submit(o2, book())
        self.assertEqual(ev.status, OrderStatus.FAILED)
        self.assertTrue(o2.anomalous)

    def test_ttl_expiry_cancels(self):
        o = an_order(size=20, limit=0.45, now=0.0)  # rests LIVE
        self.om.submit(o, book(ask=0.5))
        expired = self.om.expire_stale_orders(now=200.0)
        self.assertEqual(len(expired), 1)
        self.assertEqual(o.status, OrderStatus.CANCELLED)

    def test_cancel_all(self):
        o = an_order(size=20, limit=0.45)
        self.om.submit(o, book(ask=0.5))
        cancelled = self.om.cancel_all()
        self.assertEqual(len(cancelled), 1)

    def test_child_order_split(self):
        children = self.om.build_child_orders(
            "m", "e", Outcome.YES, "t", total_size=45.0, limit_price=0.5,
            reason="r", prediction_ts=0.0, now=0.0,
        )
        self.assertEqual(len(children), self.cfg.strategy.child_order_count)
        self.assertLessEqual(sum(c.size for c in children), 45.0 + 1e-9)


class TestLiveExecutorInert(unittest.TestCase):
    def test_live_executor_refuses(self):
        cfg = load_config()
        live = LiveExecutor(cfg)
        with self.assertRaises(RuntimeError):
            live.submit(an_order(), book())


if __name__ == "__main__":
    unittest.main()
