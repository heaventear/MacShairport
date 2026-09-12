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

## Operating a funded account (this build)

You fund a Polymarket wallet manually; the system operates it with credentials
you provide **only through the environment** (never committed). Install the live
deps first: `pip install py-clob-client`.

Environment variables (`credentials.py`):

```bash
export POLYMARKET_PRIVATE_KEY=0x...        # funded wallet key (required)
# optional:
export POLYMARKET_FUNDER=0x...             # proxy/funder address, if using a
export POLYMARKET_SIGNATURE_TYPE=1         #   Polymarket email/Magic proxy wallet
export POLYMARKET_HOST=https://clob.polymarket.com
export POLYMARKET_CHAIN_ID=137
# API creds are derived from the key if you don't set these:
export POLYMARKET_API_KEY=... POLYMARKET_API_SECRET=... POLYMARKET_API_PASSPHRASE=...
# the two human gates:
export POLYTRADER_LIVE_AUTHORIZED=1
# and set execution.mode: live in config/settings.yaml
```

Then use the gated operator tool — it runs the preflight every time:

```bash
# 1) read-only: preflight + resolve address + read USDC balance. Places NO orders.
python3 -m polytrader.scripts.run_live --check \
    --jurisdiction GB --i-acknowledge-compliance

# 2) validate execution with ONE tiny order (auto-cancels unless --keep):
python3 -m polytrader.scripts.run_live --place-test-order \
    --token <YES_TOKEN_ID> --price 0.40 --size 1 \
    --jurisdiction GB --i-acknowledge-compliance
```

The private key is loaded into a `LocalKeySigner` in-process (masked in all
logs). This is the pragmatic "operate my wallet" mode; for larger capital move
to an out-of-process `ExternalSigner`. The AI/analysis layer never sees the key.

**Not yet wired (do before unattended trading):** live reconciliation against
the real exchange/on-chain balances (the daily reconcile still compares
local-vs-local), precise fill/price accounting via `order_status`/trades, and an
autonomous scan→trade loop on a real clock. Validate `--check` and a couple of
`--place-test-order` fills first.

## Kill switches

- `OrderManager.cancel_all()` — one-click cancel of all live orders.
- Set `execution.mode` back to `simulation`, or unset
  `POLYTRADER_LIVE_AUTHORIZED`, to block all new live orders immediately.
- Any reconciliation mismatch or security anomaly hard-halts the system;
  recovery is manual only (`RiskManager.manual_resume`), never automatic.
