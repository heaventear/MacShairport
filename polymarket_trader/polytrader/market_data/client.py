"""Market Data Service (spec 模块 #1).

Provides market lists, market detail + resolution rules, Yes/No token ids, order
books, top-of-book, volume, and market status.

Two data sources implement the same interface:

* ``OfflineDataSource`` — a deterministic, self-contained fixture used for the
  paper-trading simulation and the test-suite. No network required.
* ``LiveDataSource`` — read-only access to Polymarket's public Gamma + CLOB
  HTTP APIs via ``urllib`` (stdlib). It never authenticates and never places
  orders. Used with ``--live-data``.

The service tracks consecutive API errors so the Risk Manager can trip the
"API 连续异常 -> 只读模式" circuit breaker.
"""

from __future__ import annotations

import json
import random
import time
import urllib.request
from typing import Protocol

from ..models import BookLevel, Market, OrderBook, Outcome


class DataSource(Protocol):
    def list_markets(self) -> list[Market]: ...
    def get_order_book(self, market: Market, outcome: Outcome) -> OrderBook: ...


# --------------------------------------------------------------------------- #
# Offline fixture source.
# --------------------------------------------------------------------------- #
class OfflineDataSource:
    """Deterministic synthetic markets for simulation & tests.

    Each market carries a hidden "true probability" used only by the offline AI
    engine and the simulated resolution, so the whole pipeline can be exercised
    end-to-end without touching the network.
    """

    def __init__(self, seed: int = 42, now: float | None = None):
        self.rng = random.Random(seed)
        self.now = now if now is not None else time.time()
        self._markets = self._build_markets()
        self._true_prob: dict[str, float] = {}
        for m, tp in self._market_truths:
            self._true_prob[m.market_id] = tp

    def _build_markets(self) -> list[Market]:
        specs = [
            ("mkt_election", "Will Candidate A win the 2026 special election?",
             "evt_election", "Official state election board", 0.63, 40000, 0.015, 5),
            ("mkt_fed", "Will the Fed cut rates at the next meeting?",
             "evt_macro", "Federal Reserve official release", 0.55, 60000, 0.010, 3),
            ("mkt_sports", "Will Team X win the championship final?",
             "evt_sports", "Official league result", 0.48, 25000, 0.020, 2),
            ("mkt_crypto", "Will BTC close above $120k this month?",
             "evt_crypto", "Coinbase spot close", 0.40, 80000, 0.010, 6),
            ("mkt_weather", "Will it rain in city Y on the target date?",
             "evt_weather", "National weather service", 0.30, 45000, 0.012, 4),
            ("mkt_tech", "Will Company Q ship product before the deadline?",
             "evt_tech", "Company official announcement", 0.72, 55000, 0.015, 5),
            ("mkt_lowliq", "Will obscure event Z happen?",
             "evt_misc", "Unclear / disputed source", 0.50, 800, 0.09, 6),
            ("mkt_wide", "Will ambiguous outcome W occur?",
             "evt_misc2", "Multiple conflicting sources", 0.52, 30000, 0.06, 4),
        ]
        markets: list[Market] = []
        self._market_truths: list[tuple[Market, float]] = []
        for mid, q, evt, src, true_p, liq, spread, days in specs:
            m = Market(
                market_id=mid,
                question=q,
                event_id=evt,
                yes_token_id=f"{mid}_YES",
                no_token_id=f"{mid}_NO",
                resolution_source=src,
                resolution_rules=f"Resolves YES iff the described condition for '{q}' "
                f"is met per {src}. Resolves NO otherwise.",
                resolution_time=self.now + days * 86400,
                liquidity_usd=float(liq),
                volume_usd=float(liq) * 3,
                active=True,
                accepting_orders=True,
                tags=(evt.split("_")[1],),
            )
            # attach the modelled spread for book construction
            m.__dict__["_spread"] = spread
            markets.append(m)
            self._market_truths.append((m, true_p))
        return markets

    def true_probability(self, market_id: str) -> float:
        return self._true_prob[market_id]

    def list_markets(self) -> list[Market]:
        return list(self._markets)

    def get_order_book(self, market: Market, outcome: Outcome) -> OrderBook:
        # Build a book whose mid is offset from the true probability so the AI
        # can (sometimes) find an edge. YES/NO books are mirror images.
        true_p = self._true_prob[market.market_id]
        yes_mid = min(0.97, max(0.03, true_p + self.rng.uniform(-0.10, 0.10)))
        mid = yes_mid if outcome is Outcome.YES else (1.0 - yes_mid)
        spread = market.__dict__.get("_spread", 0.02)
        best_bid = max(0.01, mid - spread / 2)
        best_ask = min(0.99, mid + spread / 2)
        # Depth scales with liquidity.
        unit = max(20.0, market.liquidity_usd / 200.0)
        token = market.token_for(outcome)
        bids = [BookLevel(round(best_bid - i * 0.01, 4), unit * (1 - 0.2 * i))
                for i in range(3) if best_bid - i * 0.01 > 0]
        asks = [BookLevel(round(best_ask + i * 0.01, 4), unit * (1 - 0.2 * i))
                for i in range(3) if best_ask + i * 0.01 < 1]
        return OrderBook(market.market_id, token, bids=bids, asks=asks, ts=self.now)


# --------------------------------------------------------------------------- #
# Live (read-only) source.
# --------------------------------------------------------------------------- #
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"


class LiveDataSource:
    """Read-only Polymarket public API access. No auth, no orders."""

    def __init__(self, limit: int = 50, timeout: float = 15.0):
        self.limit = limit
        self.timeout = timeout

    def _get(self, url: str) -> object:
        req = urllib.request.Request(url, headers={"User-Agent": "polytrader-ro/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_markets(self) -> list[Market]:
        data = self._get(f"{GAMMA_URL}?closed=false&active=true&limit={self.limit}")
        rows = data if isinstance(data, list) else data.get("data", [])
        markets: list[Market] = []
        for row in rows:
            try:
                markets.append(self._parse_market(row))
            except (KeyError, ValueError, TypeError):
                continue  # skip malformed / non-binary markets
        return markets

    @staticmethod
    def _parse_market(row: dict) -> Market:
        token_ids = row.get("clobTokenIds")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        if not token_ids or len(token_ids) < 2:
            raise ValueError("not a binary CLOB market")
        end = row.get("endDate") or row.get("end_date_iso")
        res_time = _parse_iso(end) if end else None
        return Market(
            market_id=str(row.get("id") or row.get("conditionId")),
            question=row.get("question", ""),
            event_id=str(row.get("eventId") or row.get("groupItemTitle") or ""),
            yes_token_id=str(token_ids[0]),
            no_token_id=str(token_ids[1]),
            resolution_source=row.get("resolutionSource", "") or "",
            resolution_rules=row.get("description", "") or "",
            resolution_time=res_time,
            liquidity_usd=float(row.get("liquidityNum") or row.get("liquidity") or 0),
            volume_usd=float(row.get("volumeNum") or row.get("volume") or 0),
            active=bool(row.get("active", True)),
            accepting_orders=bool(row.get("acceptingOrders", True)),
            tags=tuple(),
        )

    def get_order_book(self, market: Market, outcome: Outcome) -> OrderBook:
        token = market.token_for(outcome)
        data = self._get(f"{CLOB_BOOK_URL}?token_id={token}")
        bids = [BookLevel(float(l["price"]), float(l["size"]))
                for l in data.get("bids", [])]
        asks = [BookLevel(float(l["price"]), float(l["size"]))
                for l in data.get("asks", [])]
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        return OrderBook(market.market_id, token, bids=bids, asks=asks)


def _parse_iso(s: str) -> float | None:
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except (ValueError, TypeError):
            continue
    return None


# --------------------------------------------------------------------------- #
# Service wrapper with error-streak tracking.
# --------------------------------------------------------------------------- #
class MarketDataService:
    def __init__(self, source: DataSource):
        self.source = source
        self.consecutive_errors = 0
        self.last_error: str | None = None

    def list_markets(self) -> list[Market]:
        try:
            markets = self.source.list_markets()
            self.consecutive_errors = 0
            return markets
        except Exception as exc:  # noqa: BLE001 - surface as tracked API error
            self.consecutive_errors += 1
            self.last_error = str(exc)
            return []

    def get_order_book(self, market: Market, outcome: Outcome) -> OrderBook | None:
        try:
            book = self.source.get_order_book(market, outcome)
            self.consecutive_errors = 0
            return book
        except Exception as exc:  # noqa: BLE001
            self.consecutive_errors += 1
            self.last_error = str(exc)
            return None
