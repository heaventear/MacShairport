# AI Automated Polymarket Trading System (Phase 1–2 Foundation)

A safety-first, auditable, explainable automated trading system for the
[Polymarket](https://polymarket.com) prediction market.

> **Status: SIMULATION ONLY.** This repository currently implements Phases 1
> and 2 of the roadmap (read-only market data + full paper-trading
> simulation). It **cannot** place real orders and **never touches private
> keys**. Real-money execution is deliberately stubbed behind a hard guard so
> that the strategy, risk controls, and reporting can be validated first — as
> the project spec requires ("先模拟，后小额实盘").

## What this is

The core idea (第一版核心交易策略):

> **Probability-edge strategy + liquidity filter + limit-order execution +
> strict money management.**

The AI estimates the *true* probability of a binary outcome, compares it to the
market price, subtracts fees / slippage / exit cost, and only if a meaningful
**edge** remains does the trade proceed to risk checks. Nothing trades unless
*every* gate in the pipeline passes.

```
Market Data ─► Market Selector ─► AI Probability Engine ─► Edge Calculator
                                                                │
                                              (edge ≥ min edge?) ▼
Portfolio ◄─ Order Manager ◄─────────────── Risk Manager ◄──────┘
     │                                            (all limits ok?)
     └────────────────────► Monitoring & Reporting
```

## Design principles (总体设计原则)

1. AI **only** analyses and proposes; it never decides risk and never signs.
2. The **Risk Manager** has veto power over every trade.
3. The **Order Manager** owns order lifecycle and de-duplication.
4. AI never touches private keys (there are none in this phase).
5. AI cannot change risk parameters — they are loaded from immutable config.
6. No leverage, ever.
7. Region restrictions are respected, never bypassed.
8. Simulate first, then small live trading.
9. Every trade is trackable, explainable, and replayable.
10. A 7-day result never proves long-term profitability.

## Quick start

No third-party packages are required to run the simulation — it uses only the
Python standard library and a bundled offline market fixture, so it runs
anywhere.

```bash
cd polymarket_trader

# Run a 7-day paper-trading simulation against the bundled offline fixture
python3 -m polytrader.scripts.run_simulation --days 7 --offline

# Or run against live Polymarket public market data (read-only, still no orders)
python3 -m polytrader.scripts.run_simulation --days 2 --live-data

# Run the test suite
python3 -m pytest tests -q      # or: python3 -m unittest discover -s tests
```

The run writes a SQLite ledger (`polytrader.db` by default) and prints a daily
report plus a final 7-day experiment report (收益/交易/AI/工程 metrics).

### Monitoring dashboard (web UI)

The system ships with a **read-only web dashboard** so you can inspect trades,
AI predictions (with evidence and rationale), orders, the event log, risk
state, and the strategy parameters — not just run the engine. It reads the
SQLite ledger live and, like everything else here, needs **no third-party
packages** (it uses the stdlib `http.server`).

```bash
# 1) produce a ledger by running a simulation to a file DB
python3 -m polytrader.scripts.run_simulation --days 7 --offline --db polytrader.db

# 2) open the dashboard in your browser
python3 -m polytrader.scripts.serve_dashboard --db polytrader.db --port 8000
#    -> http://127.0.0.1:8000
```

The dashboard is strictly read-only: every endpoint is a GET that only reads
the ledger. It never places orders, never mutates state, and serves no secrets.

| Tab | Shows |
|-----|-------|
| Overview | KPI cards (equity, PnL, drawdown, win rate, Brier), equity curve, risk & engineering integrity, AI calibration |
| Trades | Every fill: market, outcome, AI probability, price, fee, slippage, result, PnL |
| AI Predictions | Structured forecasts with evidence, invalidating conditions, recommended action |
| Orders | Order lifecycle with status badges |
| Event Log | Selection/edge/risk rejections, settlements, duplicates, wind-down |
| Strategy & Limits | The immutable config (risk params, sizing caps, circuit breakers) |

JSON API (for your own tooling): `/api/summary`, `/api/daily`, `/api/trades`,
`/api/predictions`, `/api/orders`, `/api/events`, `/api/reconciliations`,
`/api/config`.

## Module map (系统模块设计)

| Spec module            | Code                                   |
|------------------------|----------------------------------------|
| Market Data Service    | `polytrader/market_data/client.py`     |
| Market Selector        | `polytrader/selector.py`               |
| AI Probability Engine  | `polytrader/ai/engine.py`              |
| Edge Calculator        | `polytrader/edge.py`                    |
| Risk Manager           | `polytrader/risk.py`                    |
| Order Manager          | `polytrader/orders/`                    |
| Portfolio Manager      | `polytrader/portfolio.py`               |
| Monitoring & Reporting | `polytrader/reporting.py`               |
| Monitoring dashboard   | `polytrader/web/` (server + SPA)        |
| Alerting               | `polytrader/alerts.py`                  |
| Reconciliation         | `polytrader/reconcile.py`               |
| Key isolation / geoblock | `polytrader/security.py`              |
| Live framework (gated) | `polytrader/live/` (inert until enabled)|
| Orchestrator           | `polytrader/engine_loop.py`             |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for details, and
[`docs/GO_LIVE.md`](docs/GO_LIVE.md) for the (human-gated) path to live trading.

## Safety notes

- `pUSD` vs `USDT`: Polymarket settles trades in its collateral asset (`pUSD`
  / USDC-class). USDT is only an on-ramp. This system tracks collateral in
  abstract "USD-equivalent" units and **does not** treat USDT as the
  settlement asset. The on-ramp/conversion step is out of scope for the
  simulation and is documented in the roadmap as a Phase-4 prerequisite.
- The live executor raises unless an explicit, human-set environment flag is
  present *and* keys are provided out-of-band — it is intentionally inert here.
