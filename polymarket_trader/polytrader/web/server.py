"""Read-only monitoring dashboard server (spec 模块 #8: 监控与报告).

Serves a single-page dashboard plus a small JSON API over the SQLite ledger, so
a user can inspect the system's trades, AI predictions (with evidence and
rationale), orders, event log, risk state, reconciliations, and the strategy
parameters — not just run the engine.

Design constraints (kept consistent with the rest of the project):

* **Standard library only** — ``http.server``; no web framework.
* **Read-only** — every endpoint is a GET that only reads the ledger. The
  server never mutates state, never places orders, and never exposes secrets
  (the config it serves comes from the non-sensitive settings file; there are
  no keys in this phase to leak).
* **Thread-safe** — a fresh SQLite connection is opened per request thread.
"""

from __future__ import annotations

import dataclasses
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..config import Config, load_config
from ..reporting import (
    PredictionOutcome,
    brier_score,
    calibration_bins,
    compute_trade_metrics,
    performance_breakdowns,
)
from ..storage import Storage

_DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"


# --------------------------------------------------------------------------- #
# Summary builder — the aggregate the dashboard's overview cards consume.
# --------------------------------------------------------------------------- #
def _settled_prediction_outcomes(trades: list[dict]) -> list[PredictionOutcome]:
    """Derive Brier/calibration inputs from settled trades.

    A settled trade carries the AI probability for the outcome it bought and the
    realized result (WON/LOST), which is exactly what a forecast score needs.
    """
    items: list[PredictionOutcome] = []
    for t in trades:
        result = t.get("outcome_result")
        if result not in ("WON", "LOST"):
            continue
        items.append(
            PredictionOutcome(
                predicted_prob=t.get("ai_probability", 0.0),
                realized=1 if result == "WON" else 0,
                confidence=0.0,
                had_evidence=True,
            )
        )
    return items


def build_summary(storage: Storage, cfg: Config) -> dict:
    trades = storage.all_trades()
    orders = storage.all_orders()
    daily = storage.all_daily_snapshots()

    start_equity = cfg.account.total_capital_usd
    end_equity = daily[-1]["equity"] if daily else start_equity
    max_dd = max((d["drawdown_pct"] for d in daily), default=0.0)
    realized = daily[-1]["realized_pnl"] if daily else 0.0
    unrealized = daily[-1]["unrealized_pnl"] if daily else 0.0
    halt_level = daily[-1]["halt_level"] if daily else "RUNNING"

    tm = compute_trade_metrics(trades)
    maker = sum(1 for o in orders if o.get("is_maker"))
    tm.maker_ratio = round(maker / len(orders), 3) if orders else None

    outcomes = _settled_prediction_outcomes(trades)
    brier = brier_score(outcomes)
    calib = calibration_bins(outcomes)

    return {
        "profit": {
            "start_equity": round(start_equity, 2),
            "end_equity": round(end_equity, 2),
            "net_pnl": round(end_equity - start_equity, 2),
            "return_pct": round((end_equity - start_equity) / start_equity, 4)
            if start_equity else 0.0,
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "max_drawdown_pct": round(max_dd, 4),
            "total_fees": tm.total_fees,
            "slippage_cost": tm.total_slippage_cost,
        },
        "trading": {
            "total_trades": tm.total_trades,
            "wins": tm.wins,
            "losses": tm.losses,
            "win_rate": tm.win_rate,
            "avg_win": tm.avg_win,
            "avg_loss": tm.avg_loss,
            "maker_ratio": tm.maker_ratio,
            "anomalous_trades": tm.anomalous_trades,
            "total_orders": len(orders),
        },
        "ai": {
            "brier_score": None if brier is None else round(brier, 4),
            "scored_predictions": len(outcomes),
            "calibration": calib,
        },
        "engineering": {
            "risk_rejections": storage.count_events("risk_reject"),
            "edge_rejections": storage.count_events("edge_reject")
            + storage.count_events("edge_reject_child"),
            "selection_rejections": storage.count_events("selection_reject"),
            "duplicate_suppressed": storage.count_events("duplicate_suppressed"),
            "unknown_orders": storage.count_events("order_unknown"),
            "settlements": storage.count_events("settlement"),
        },
        "risk": {
            "halt_level": halt_level,
            "days_recorded": len(daily),
        },
        "breakdowns": performance_breakdowns(trades),
    }


# --------------------------------------------------------------------------- #
# HTTP handler.
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    # Injected by make_server.
    db_path: str = "polytrader.db"
    config_path: str | None = None

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html"):
                self._send_html(_DASHBOARD_HTML.read_text(encoding="utf-8"))
                return
            if not route.startswith("/api/"):
                self._send_json({"error": "not found"}, status=404)
                return

            cfg = load_config(self.config_path)
            storage = Storage(self.db_path)
            try:
                self._route_api(route, cfg, storage)
            finally:
                storage.close()
        except FileNotFoundError:
            self._send_json({"error": "ledger or config not found"}, status=404)
        except Exception as exc:  # noqa: BLE001 - surface as 500 JSON
            self._send_json({"error": str(exc)}, status=500)

    def _route_api(self, route: str, cfg: Config, storage: Storage) -> None:
        if route == "/api/summary":
            self._send_json(build_summary(storage, cfg))
        elif route == "/api/config":
            self._send_json(dataclasses.asdict(cfg))
        elif route == "/api/daily":
            self._send_json(storage.all_daily_snapshots())
        elif route == "/api/trades":
            self._send_json(storage.all_trades())
        elif route == "/api/orders":
            self._send_json(storage.all_orders())
        elif route == "/api/predictions":
            self._send_json(storage.all_predictions())
        elif route == "/api/events":
            self._send_json(storage.all_events())
        elif route == "/api/reconciliations":
            self._send_json(storage.all_reconciliations())
        else:
            self._send_json({"error": "unknown endpoint"}, status=404)

    def log_message(self, *args) -> None:  # quieter console
        return


# --------------------------------------------------------------------------- #
# Factory / entry point.
# --------------------------------------------------------------------------- #
def make_server(
    db_path: str = "polytrader.db",
    host: str = "127.0.0.1",
    port: int = 8000,
    config_path: str | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler", (_Handler,),
        {"db_path": db_path, "config_path": config_path},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    db_path: str = "polytrader.db",
    host: str = "127.0.0.1",
    port: int = 8000,
    config_path: str | None = None,
) -> None:
    server = make_server(db_path, host, port, config_path)
    print(f"Dashboard: http://{host}:{port}  (ledger: {db_path})")
    print("Read-only. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
