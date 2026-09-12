# Go-Live Runbook (Phase 4)

> **Read this in full before enabling live trading.** The system is safe by
> default and will refuse to place real orders until every step below is done by
> a human. A 7-day simulation result is **not** proof of long-term edge (spec
> 十三/十四) — do not scale up on the strength of a good week.

The live-execution *framework* is implemented and tested
(`polytrader/live/`), but it is **gated**: `live_preflight` must pass and the
real exchange adapter must be wired. These are deliberate human steps.

## Preconditions (do not skip)

1. **Legal / compliance.** Confirm you may use Polymarket from your
   jurisdiction. `security.check_geoblock` fails closed and must be given a real
   jurisdiction plus your explicit compliance acknowledgement. **Never** edit it
   to bypass a restriction (原则 #7).
2. **Funding & collateral.** USDT is only an on-ramp. Deposit and convert to
   Polymarket's collateral asset (`pUSD`/USDC-class). The Portfolio Manager
   tracks collateral, not USDT. Start with ~300 USD-equivalent.
3. **Key custody (critical).** Provision an **isolated signer** — a KMS, a
   hardware wallet, or a separate signing service. Private keys must **never**
   enter this process or the repo. Implement `security.Signer` against that
   service (or `ExternalSigner`), returning only signatures.

## Wiring steps

4. Install the optional dependency and implement the adapter:
   ```bash
   pip install py-clob-client   # pin an exact version in requirements.txt
   ```
   Implement `place_order` / `cancel_order` / `order_status` in
   `polytrader/live/executor.py::PyClobClientAdapter` against the client, using
   your `Signer` for signatures. Keep the returned dict shape the executor
   expects (`exchange_order_id`, `status`, `filled_size`, `avg_price`).
5. In `config/settings.yaml`, set `execution.mode: live` (a human edit).
6. Export the second, out-of-band authorization flag in the shell that runs the
   engine:
   ```bash
   export POLYTRADER_LIVE_AUTHORIZED=1
   ```
7. Construct the engine's `OrderManager` with a `GatedClobExecutor`
   (signer + adapter + a passing `check_geoblock` result). It runs the full
   preflight at construction and **refuses to build** unless all gates pass.

## Preflight gates (all must pass)

`live_preflight` checks, and fails closed on any of:

- `execution.mode == "live"`,
- `POLYTRADER_LIVE_AUTHORIZED == "1"`,
- an isolated signer is present (not `NoSigner`),
- the geoblock check passed,
- the ledger reconciled against exchange/on-chain,
- no circuit breaker is tripped.

## First live session

8. Run with tiny size (10–20 USD-equivalent per trade), watching the dashboard
   and the alert stream.
9. After each session verify: daily reconciliation is consistent, zero UNKNOWN
   orders, zero duplicate submissions, no unexplained anomalous fills.
10. Scale up **only** after the stability criteria (spec 十三) hold across
    several sessions — and never because of a single profitable run.

## Kill switches

- `OrderManager.cancel_all()` — one-click cancel of all live orders.
- Set `execution.mode` back to `simulation`, or unset
  `POLYTRADER_LIVE_AUTHORIZED`, to block all new live orders immediately.
- Any reconciliation mismatch or security anomaly hard-halts the system;
  recovery is manual only (`RiskManager.manual_resume`), never automatic.
