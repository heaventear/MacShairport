# Architecture

This document maps the project spec (十、系统模块设计) onto the code and explains
the control flow and the safety boundaries.

## Control flow (one trading decision)

```
                      ┌──────────────────────────┐
                      │   Market Data Service     │  market list, resolution
                      │  (offline / live, RO)     │  rules, order books
                      └────────────┬──────────────┘
                                   │ markets
                                   ▼
                      ┌──────────────────────────┐
                      │      Market Selector      │  whitelist: liquidity,
                      │                           │  spread, clear rules,
                      └────────────┬──────────────┘  resolution horizon
                                   │ candidates
                                   ▼
                      ┌──────────────────────────┐
                      │   AI Probability Engine   │  structured Prediction
                      │   (proposes only)         │  (prob, conf, evidence…)
                      └────────────┬──────────────┘
                                   │ prediction
                                   ▼
                      ┌──────────────────────────┐
                      │      Edge Calculator      │  net_edge = p − price
                      │                           │  − fee − slip − exit
                      └────────────┬──────────────┘
                          net_edge ≥ min_edge?
                                   ▼
                      ┌──────────────────────────┐
                      │       Risk Manager        │  size caps, cash buffer,
                      │       (VETO power)         │  circuit breakers
                      └────────────┬──────────────┘
                          approved size
                                   ▼
                      ┌──────────────────────────┐
                      │       Order Manager       │  split into child limit
                      │  state machine + idempot. │  orders, submit, track
                      └────────────┬──────────────┘
                                   │ fills
                                   ▼
                      ┌──────────────────────────┐
                      │     Portfolio Manager     │  positions, avg cost,
                      │                           │  realized/unrealized PnL
                      └────────────┬──────────────┘
                                   │ state
                                   ▼
                      ┌──────────────────────────┐
                      │  Monitoring & Reporting   │  daily + 7-day reports,
                      │                           │  Brier, calibration
                      └──────────────────────────┘
```

Every gate must pass (spec 七、交易触发条件). A failure at any stage is recorded
in the SQLite `events` table with a reason, so each decision is auditable.

## Safety boundaries

| Principle (设计原则)                         | How it is enforced in code |
|---------------------------------------------|----------------------------|
| AI only proposes (#1)                       | `ai/engine.py` returns a `Prediction`; it never calls the Order Manager or Portfolio. |
| Risk Manager can veto (#2)                  | `engine_loop` calls `risk.approve_order` before any order is built. |
| AI cannot touch keys (#4)                   | No key material anywhere in this phase; `LiveExecutor` is inert. |
| AI cannot change risk params (#5)           | `Config` is a frozen dataclass; the engine is never handed a reference. |
| No leverage (#6)                            | Sizing is bounded by cash minus buffer; no borrow path exists. |
| Simulate first (#8)                         | `execution.mode` must be `simulation`; the runner refuses otherwise. |
| Every trade traceable (#9)                  | `storage.py` persists predictions, orders, fills, events, reconciliations. |

## Order state machine

`orders/state_machine.py` encodes the legal transitions (spec 八、订单状态).
Key rules:

* `UNKNOWN` is reachable from any non-terminal state (e.g. a timeout during a
  status query). Once `UNKNOWN`, the market is blocked for new orders until a
  reconciliation resolves it (`RiskManager.on_unknown_order`).
* A submission timeout is **never** retried blindly — it becomes `UNKNOWN`, not
  a second order (spec: 不能因为网络超时而重复下单).
* Idempotency: the `orders.client_key` unique index makes a duplicate
  submission impossible at the storage layer.

## Money & PnL model

* A binary outcome token trades in `[0, 1]`; buying `N` USD of an outcome at
  price `p` yields `N/p` shares. At resolution each share pays `1.0` (win) or
  `0.0` (lose).
* `avg_cost` is the share-weighted entry price. Realized PnL on settlement is
  `shares * resolution_value - cost`; on an early exit it is
  `shares * exit_price - shares * avg_cost - fee`.
* Equity is `cash + Σ shares * mark_price` over open positions.

## Circuit breakers (`risk.py`)

Escalation is monotonic: `RUNNING → NO_NEW_POSITIONS → READ_ONLY → HARD_HALT`.
Recovery is manual only (`manual_resume`), never automatic and never AI-driven.

| Trigger                                   | Level |
|-------------------------------------------|-------|
| Daily loss ≥ 2%                           | NO_NEW_POSITIONS |
| Drawdown ≥ 5%                             | size halved |
| Drawdown ≥ 8%                             | NO_NEW_POSITIONS |
| API error streak ≥ N                      | READ_ONLY |
| Reconciliation mismatch                   | HARD_HALT |
| Order status UNKNOWN                      | READ_ONLY (until reconciled) |
| Security anomaly (key/signature)          | HARD_HALT |

## Extending the system

* **Real LLM forecaster**: implement `ai.ProbabilityEngine.predict` backed by an
  LLM + news/data retrieval, returning the same `Prediction`. Nothing else
  changes.
* **Live execution**: implement `orders.LiveExecutor` against the Polymarket
  CLOB client in a later phase — see `docs/ROADMAP.md`. Keys must be injected
  out-of-band and never logged.
