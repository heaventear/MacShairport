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

## ⏳ Phase 3 — Execution & risk hardening (partially scaffolded)

- [x] Order state machine (`orders/state_machine.py`).
- [x] Cancellation + TTL expiry + one-click cancel-all.
- [x] Balance / cash-buffer checks.
- [x] Reconciliation hook + global pause / read-only modes.
- [ ] **Three-way reconciliation** against Polymarket API *and* on-chain state
      (currently local-vs-local in simulation). Requires authenticated reads.
- [ ] Real-time monitoring/alerting sink (webhook/email) — hook exists via
      `storage.log_event`; wire to an alerting channel.
- [ ] API-key / private-key isolation service (KMS or hardware signer). **Keys
      must never enter this process's memory unencrypted.**

## ⛔ Phase 4 — Small live trading (NOT started; gated)

Prerequisites before a single real order:

1. `execution.mode` flipped to `live` **by a human**, plus a separate explicit
   authorization flag.
2. `LiveExecutor` implemented against `py-clob-client` with orders **signed by an
   isolated signer**, not by this process.
3. **pUSD / collateral handling**: USDT is only an on-ramp. A funding step must
   convert deposits to Polymarket's collateral asset and the Portfolio Manager
   must track collateral, not USDT. Do not treat USDT as the settlement asset.
4. Geoblock compliance check that **fails closed** (原则 #7 — never bypass).
5. Start at ~300 USDT, 10–20 USDT per trade; scale only after stability criteria
   (spec 十三) hold across the reconciliation and anomaly metrics.

## Phase 5 — 7-day review

- [x] Report scaffolding for profit / drawdown / AI accuracy / execution quality
      / fees & slippage / best & worst market types.
- [ ] Per-topic and per-confidence breakdowns (data is captured in predictions/
      trades; add the aggregation queries).
- [ ] Decision gate for whether to build v2.

## Explicit non-goals for v1 (十四)

No leverage, no borrowing, no HFT, no unbounded scanning, no unclear-rules
markets, no AI-modified risk params, no AI key access, no geoblock bypass, no
automatic capital scaling, and no treating a 7-day result as proof of long-term
edge.
