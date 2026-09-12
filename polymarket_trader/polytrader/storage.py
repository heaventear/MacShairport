"""SQLite ledger — the local record of truth for auditing and reconciliation.

Everything the spec requires to be trackable/replayable (十一、数据记录要求) is
persisted here: predictions, orders, fills (trades), and the running cash /
position ledger. The DB is the local side of the three-way reconciliation
(local vs Polymarket vs on-chain) described in the Day-7 wind-down.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import Order, Prediction, Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, market_id TEXT, question TEXT, selected_outcome TEXT,
    estimated_probability REAL, confidence REAL, market_price REAL,
    estimated_edge REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    client_key TEXT, market_id TEXT, outcome TEXT, side TEXT,
    order_type TEXT, limit_price REAL, size REAL, status TEXT,
    filled_size REAL, avg_fill_price REAL, fee_paid REAL, slippage REAL,
    is_maker INTEGER, reason TEXT, created_ts REAL, filled_ts REAL,
    expires_ts REAL, anomalous INTEGER, payload TEXT
);
-- Idempotency guard: one client_key may only ever map to one order.
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_key
    ON orders(client_key) WHERE client_key <> '';
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT, market_id TEXT, question TEXT, outcome TEXT, side TEXT,
    ai_probability REAL, market_price_at_decision REAL, fill_price REAL,
    size REAL, filled_size REAL, fee REAL, slippage REAL,
    created_ts REAL, filled_ts REAL, reason TEXT, risk_check TEXT,
    outcome_result TEXT, pnl REAL, anomalous INTEGER, payload TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, kind TEXT, market_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, kind TEXT, local_value REAL,
    external_value REAL, diff REAL, consistent INTEGER
);
CREATE TABLE IF NOT EXISTS daily_snapshots (
    day INTEGER PRIMARY KEY,
    ts REAL, equity REAL, cash REAL, realized_pnl REAL, unrealized_pnl REAL,
    open_positions INTEGER, drawdown_pct REAL, halt_level TEXT
);
"""


class Storage:
    def __init__(self, path: str | Path = "polytrader.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- writes ------------------------------------------------------------- #
    def record_prediction(self, p: Prediction) -> None:
        self.conn.execute(
            "INSERT INTO predictions (ts, market_id, question, selected_outcome,"
            " estimated_probability, confidence, market_price, estimated_edge,"
            " payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                p.ts, p.market_id, p.question, p.selected_outcome.value,
                p.estimated_probability, p.confidence, p.market_price,
                p.estimated_edge, json.dumps(_ser(p)),
            ),
        )
        self.conn.commit()

    def upsert_order(self, o: Order) -> None:
        self.conn.execute(
            "INSERT INTO orders (order_id, client_key, market_id, outcome, side,"
            " order_type, limit_price, size, status, filled_size, avg_fill_price,"
            " fee_paid, slippage, is_maker, reason, created_ts, filled_ts,"
            " expires_ts, anomalous, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(order_id) DO UPDATE SET status=excluded.status,"
            " filled_size=excluded.filled_size, avg_fill_price=excluded.avg_fill_price,"
            " fee_paid=excluded.fee_paid, slippage=excluded.slippage,"
            " filled_ts=excluded.filled_ts, anomalous=excluded.anomalous,"
            " payload=excluded.payload",
            (
                o.order_id, o.client_key, o.market_id, o.outcome.value, o.side.value,
                o.order_type.value, o.limit_price, o.size, o.status.value,
                o.filled_size, o.avg_fill_price, o.fee_paid, o.slippage,
                int(o.is_maker), o.reason, o.created_ts, o.filled_ts,
                o.expires_ts, int(o.anomalous), json.dumps(_ser(o)),
            ),
        )
        self.conn.commit()

    def client_key_exists(self, client_key: str) -> bool:
        if not client_key:
            return False
        cur = self.conn.execute(
            "SELECT 1 FROM orders WHERE client_key = ? LIMIT 1", (client_key,)
        )
        return cur.fetchone() is not None

    def record_trade(self, t: Trade) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO trades (trade_id, order_id, market_id, question,"
            " outcome, side, ai_probability, market_price_at_decision, fill_price,"
            " size, filled_size, fee, slippage, created_ts, filled_ts, reason,"
            " risk_check, outcome_result, pnl, anomalous, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.trade_id, t.order_id, t.market_id, t.question, t.outcome.value,
                t.side.value, t.ai_probability, t.market_price_at_decision,
                t.fill_price, t.size, t.filled_size, t.fee, t.slippage,
                t.created_ts, t.filled_ts, t.reason, t.risk_check,
                t.outcome_result, t.pnl, int(t.anomalous), json.dumps(_ser(t)),
            ),
        )
        self.conn.commit()

    def log_event(self, kind: str, detail: str, market_id: str = "") -> None:
        import time

        self.conn.execute(
            "INSERT INTO events (ts, kind, market_id, detail) VALUES (?,?,?,?)",
            (time.time(), kind, market_id, detail),
        )
        self.conn.commit()

    def record_reconciliation(
        self, day: int, kind: str, local: float, external: float, tol: float
    ) -> bool:
        import time

        diff = abs(local - external)
        consistent = diff <= tol
        self.conn.execute(
            "INSERT INTO reconciliations (ts, day, kind, local_value,"
            " external_value, diff, consistent) VALUES (?,?,?,?,?,?,?)",
            (time.time(), day, kind, local, external, diff, int(consistent)),
        )
        self.conn.commit()
        return consistent

    def finalize_position_trades(
        self, market_id: str, outcome: str, result: str, realized_pnl: float
    ) -> None:
        """Write settlement results back onto the fills of a resolved/exited
        position, pro-rating the position's realized PnL across its fills by
        filled size. Makes win/loss and forecast scoring reproducible from the
        persisted ledger alone."""
        rows = list(self.conn.execute(
            "SELECT trade_id, filled_size FROM trades WHERE market_id = ?"
            " AND outcome = ?", (market_id, outcome),
        ))
        total = sum(r["filled_size"] or 0.0 for r in rows)
        for r in rows:
            share = ((r["filled_size"] or 0.0) / total) if total > 0 else 0.0
            self.conn.execute(
                "UPDATE trades SET outcome_result = ?, pnl = ? WHERE trade_id = ?",
                (result, realized_pnl * share, r["trade_id"]),
            )
        self.conn.commit()

    def record_daily_snapshot(
        self, day: int, equity: float, cash: float, realized: float,
        unrealized: float, open_positions: int, drawdown_pct: float,
        halt_level: str,
    ) -> None:
        import time

        self.conn.execute(
            "INSERT OR REPLACE INTO daily_snapshots (day, ts, equity, cash,"
            " realized_pnl, unrealized_pnl, open_positions, drawdown_pct,"
            " halt_level) VALUES (?,?,?,?,?,?,?,?,?)",
            (day, time.time(), equity, cash, realized, unrealized,
             open_positions, drawdown_pct, halt_level),
        )
        self.conn.commit()

    # -- reads -------------------------------------------------------------- #
    def all_trades(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM trades ORDER BY created_ts")]

    def all_orders(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM orders ORDER BY created_ts")]

    def all_predictions(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM predictions ORDER BY ts")]

    def all_events(self, limit: int = 500) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def all_reconciliations(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM reconciliations ORDER BY id")]

    def all_daily_snapshots(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM daily_snapshots ORDER BY day")]

    def count(self, table: str) -> int:
        # table name is internal, never user-supplied.
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def count_events(self, kind: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ?", (kind,)
        ).fetchone()[0]

    def close(self) -> None:
        self.conn.close()


def _ser(obj) -> dict:
    """Dataclass -> JSON-safe dict (enums to their values, lists preserved)."""

    def convert(v):
        if hasattr(v, "value"):  # Enum
            return v.value
        if isinstance(v, list):
            return [convert(x) for x in v]
        return v

    return {k: convert(v) for k, v in asdict(obj).items()}
