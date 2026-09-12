"""Alerting (spec 九: 异常告警 / 系统必须提供异常告警).

A small, dependency-free alert bus. The Risk Manager and engine raise alerts on
circuit-breaker escalations, UNKNOWN order states, reconciliation mismatches, and
security anomalies; the :class:`AlertManager` fans them out to pluggable sinks.

Sinks provided:

* ``ConsoleAlertSink`` — prints to stderr.
* ``JsonlAlertSink`` — appends one JSON object per line to a file (audit trail).
* ``WebhookAlertSink`` — POSTs JSON to a URL (stdlib urllib). Best-effort: a
  failing sink never breaks trading or the other sinks.

Nothing here places orders or touches keys; it only reports.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Alert:
    severity: Severity
    kind: str
    message: str
    context: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "severity": self.severity.value,
            "kind": self.kind,
            "message": self.message,
            "context": self.context,
        }


class AlertSink(Protocol):
    def emit(self, alert: Alert) -> None: ...


class ConsoleAlertSink:
    def __init__(self, stream=sys.stderr):
        self.stream = stream

    def emit(self, alert: Alert) -> None:
        print(f"[ALERT:{alert.severity.value}] {alert.kind}: {alert.message}",
              file=self.stream)


class JsonlAlertSink:
    def __init__(self, path: str):
        self.path = path

    def emit(self, alert: Alert) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert.to_dict()) + "\n")


class WebhookAlertSink:
    """POST alerts to a URL. Intended for Slack/Discord/PagerDuty-style hooks."""

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def emit(self, alert: Alert) -> None:
        data = json.dumps(alert.to_dict()).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=self.timeout).close()


class AlertManager:
    def __init__(self, sinks: list[AlertSink] | None = None, keep: int = 200):
        self.sinks = sinks or []
        self.recent: list[Alert] = []
        self.keep = keep

    def add_sink(self, sink: AlertSink) -> None:
        self.sinks.append(sink)

    def alert(self, severity: Severity | str, kind: str, message: str,
              **context) -> Alert:
        if isinstance(severity, str):
            severity = Severity(severity)
        a = Alert(severity=severity, kind=kind, message=message, context=context)
        self.recent.append(a)
        if len(self.recent) > self.keep:
            self.recent = self.recent[-self.keep:]
        for sink in self.sinks:
            try:
                sink.emit(a)
            except Exception:  # noqa: BLE001 - a broken sink must not break trading
                pass
        return a

    def counts_by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.recent:
            out[a.severity.value] = out.get(a.severity.value, 0) + 1
        return out
