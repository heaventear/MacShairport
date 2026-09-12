import json
import os
import tempfile
import unittest

from polytrader.alerts import (
    Alert, AlertManager, JsonlAlertSink, Severity,
)


class _RecordingSink:
    def __init__(self):
        self.got = []

    def emit(self, alert):
        self.got.append(alert)


class _BrokenSink:
    def emit(self, alert):
        raise RuntimeError("sink is down")


class TestAlerts(unittest.TestCase):
    def test_dispatch_to_sinks(self):
        rec = _RecordingSink()
        am = AlertManager(sinks=[rec])
        am.alert("WARNING", "circuit_breaker", "drawdown high", drawdown=0.06)
        self.assertEqual(len(rec.got), 1)
        self.assertEqual(rec.got[0].kind, "circuit_breaker")
        self.assertEqual(rec.got[0].context["drawdown"], 0.06)

    def test_broken_sink_does_not_propagate(self):
        rec = _RecordingSink()
        am = AlertManager(sinks=[_BrokenSink(), rec])
        # Must not raise, and the healthy sink still receives the alert.
        am.alert(Severity.CRITICAL, "security", "key anomaly")
        self.assertEqual(len(rec.got), 1)

    def test_jsonl_sink_writes(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()
        try:
            am = AlertManager(sinks=[JsonlAlertSink(tmp.name)])
            am.alert("INFO", "test", "hello", n=1)
            am.alert("INFO", "test", "world", n=2)
            with open(tmp.name) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["message"], "hello")
            self.assertEqual(lines[1]["context"]["n"], 2)
        finally:
            os.unlink(tmp.name)

    def test_counts_by_severity(self):
        am = AlertManager()
        am.alert("WARNING", "a", "x")
        am.alert("WARNING", "b", "y")
        am.alert("CRITICAL", "c", "z")
        self.assertEqual(am.counts_by_severity(), {"WARNING": 2, "CRITICAL": 1})

    def test_ring_buffer_caps_recent(self):
        am = AlertManager(keep=5)
        for i in range(20):
            am.alert("INFO", "k", str(i))
        self.assertEqual(len(am.recent), 5)
        self.assertEqual(am.recent[-1].message, "19")


if __name__ == "__main__":
    unittest.main()
