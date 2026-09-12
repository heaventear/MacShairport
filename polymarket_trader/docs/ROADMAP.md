# Roadmap

Maps the spec's suggested development order (十五、建议开发顺序) to what exists
today and what remains. The guiding rule (原则 #8): **simulate first, then small
live trading.**

## ✅ Phase 1 — Read-only data (done)

- [x] Connect to Polymarket market data (`market_data/LiveDataSource`, read-only).
- [x] Order-book access (CLOB `/book`).
- [x] Persist markets & resolution rules (stored on each `Market`; ledger in SQLite).
- [x] Local database (`storage.py`).
- [x] Market selection module (`selector.py`).

## ✅ Phase 2 — Simulated trading (done)

- [x] AI probability analysis interface + baseline (`ai/engine.py`).
- [x] Simulated order generation (limit orders, child-order splitting).
- [x] Simulated fills against the order book (`orders/SimulationExecutor`).
- [x] Modelled fees & slippage (`edge.py`, executor).
- [x] Simulated positions & PnL (`portfolio.py`).
- [x] Risk-limit enforcement + circuit breakers (`risk.py`).
- [x] Daily + 7-day reports incl. Brier / calibration (`reporting.py`).
- [x] Read-only web monitoring dashboard over the ledger (`web/`): trades, AI
      predictions, orders, event log, risk state, strategy params, equity curve.

## ⏳ Phase 3 — Execution & risk hardening (framework complete)

- [x] Order state machine (`orders/state_machine.py`).
- [x] Cancellation + TTL expiry + one-click cancel-all.
- [x] Balance / cash-buffer checks.
- [x] Reconciliation hook + global pause / read-only modes.
- [x] **Three-way reconciliation framework** (`reconcile.py`): a `Reconciler`
      comparing the local ledger against N external `LedgerSource` views
      (exchange + on-chain), with a `SimulatedExchangeSource` for now. Real
      Polymarket-API and on-chain readers implement the same interface and drop
      in — they need authenticated reads (a human/Phase-4 step).
- [x] Real-time alerting sink (`alerts.py`): pluggable console / JSONL /
      webhook sinks; the Risk Manager raises alerts on every circuit-breaker
      escalation, mirrored into the event log.
- [x] Key/private-key **isolation interface** (`security.py`): a `Signer`
      abstraction whose default (`NoSigner`) fails closed, plus an
      `ExternalSigner` boundary to an out-of-process KMS/hardware signer.
      **Keys never enter this process.** Connecting a real signer is a
      Phase-4 step.

## ⛔ Phase 4 — Small live trading (framework built & tested; gated on humans)

The live-execution *framework* now exists and is unit-tested (`polytrader/live/`):
a `GatedClobExecutor` that runs a fail-closed `live_preflight` at construction
and delegates signing to an isolated `Signer` and placement to a `ClobClient`
(verified against a `FakeClobClient`). It **cannot trade**: the real adapter is a
stub and every gate below must be satisfied by a human. See
[`GO_LIVE.md`](GO_LIVE.md) for the runbook.

Prerequisites before a single real order:

1. `execution.mode` flipped to `live` **by a human**, plus a separate explicit
   authorization flag. `LiveExecutor` already enforces this — it enumerates the
   unmet prerequisites and refuses.
2. Execution against `py-clob-client` — **implemented** (`live/executor.py`
   `PyClobClientAdapter`, built from `LiveCredentials.from_env`). Orders are
   signed by the wallet key held in a `LocalKeySigner` (loaded from env, masked
   in logs; the AI never sees it). Operate a funded account with
   `scripts/run_live.py` (`--check` read-only, `--place-test-order` for a single
   tiny fill). Not yet done: real exchange/on-chain reconciliation, precise fill
   accounting, and an autonomous scan→trade loop on a real clock. For larger
   capital, move signing to an out-of-process `ExternalSigner`.
3. **pUSD / collateral handling**: USDT is only an on-ramp. A funding step must
   convert deposits to Polymarket's collateral asset and the Portfolio Manager
   must track collateral, not USDT. Do not treat USDT as the settlement asset.
4. Geoblock compliance check that **fails closed** — `check_geoblock`
   (`security.py`) already does this (原则 #7 — never bypass). It must be fed a
   real jurisdiction determination and a human compliance acknowledgement.
5. Start at ~300 USDT, 10–20 USDT per trade; scale only after stability criteria
   (spec 十三) hold across the reconciliation and anomaly metrics.

**Why this phase is not automated here:** every item above requires a human
decision, real credentials/keys, or a legal/compliance judgement that an AI must
not make on its own (原则 #4/#5/#7). The code deliberately stops at this line.

## Phase 5 — 7-day review

- [x] Report scaffolding for profit / drawdown / AI accuracy / execution quality
      / fees & slippage / best & worst market types.
- [x] Per-topic, per-confidence, and evidence-vs-none breakdowns
      (`reporting.performance_breakdowns`), shown in the terminal report and on
      the dashboard.
- [ ] Decision gate for whether to build v2 (a human judgement call informed by
      the metrics — intentionally not automated).

## Explicit non-goals for v1 (十四)

No leverage, no borrowing, no HFT, no unbounded scanning, no unclear-rules
markets, no AI-modified risk params, no AI key access, no geoblock bypass, no
automatic capital scaling, and no treating a 7-day result as proof of long-term
edge.
