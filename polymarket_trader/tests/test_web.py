import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from polytrader.config import load_config
from polytrader.engine_loop import build_offline_engine
from polytrader.storage import Storage
from polytrader.web.server import build_summary, make_server


def _run_sim_to_db() -> str:
    """Run a short simulation into a temp file DB and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = build_offline_engine(storage_path=tmp.name, seed=77, verbose=False)
    engine.run(days=7)
    engine.storage.close()
    return tmp.name


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.db = _run_sim_to_db()
        self.cfg = load_config()

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_summary_shape(self):
        storage = Storage(self.db)
        try:
            s = build_summary(storage, self.cfg)
        finally:
            storage.close()
        for key in ("profit", "trading", "ai", "engineering", "risk"):
            self.assertIn(key, s)
        self.assertEqual(s["profit"]["start_equity"], 1000.0)
        # Settlement results are persisted, so win/loss is derivable from the DB.
        self.assertGreaterEqual(s["trading"]["total_trades"], 1)
        self.assertGreaterEqual(s["trading"]["wins"] + s["trading"]["losses"], 1)
        self.assertIsNotNone(s["ai"]["brier_score"])

    def test_daily_snapshots_persisted(self):
        storage = Storage(self.db)
        try:
            self.assertEqual(len(storage.all_daily_snapshots()), 7)
        finally:
            storage.close()


class TestServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _run_sim_to_db()
        cls.server = make_server(db_path=cls.db, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        Path(cls.db).unlink(missing_ok=True)

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()

    def test_index_serves_html(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Polymarket Trader", body)

    def test_api_endpoints_return_json(self):
        for path in ("/api/summary", "/api/daily", "/api/trades",
                     "/api/predictions", "/api/orders", "/api/events",
                     "/api/config", "/api/reconciliations"):
            status, body = self._get(path)
            self.assertEqual(status, 200, path)
            json.loads(body)  # must be valid JSON

    def test_config_endpoint_has_no_secrets(self):
        _, body = self._get("/api/config")
        text = body.decode("utf-8").lower()
        for secret in ("private", "key", "secret", "mnemonic", "seed_phrase"):
            self.assertNotIn(secret, text)

    def test_unknown_api_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/does-not-exist")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
