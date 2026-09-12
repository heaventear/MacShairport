# CLAUDE.md — polymarket_trader

Guidance for working in this subproject. (The rest of the `MacShairport` repo is
an unrelated Objective-C AirPlay project; this directory is self-contained.)

## What this is
A simulation-first, auditable AI trading system for Polymarket. It is currently
**SIMULATION ONLY** — it cannot place real orders and never handles private keys.

## Hard invariants (do not break)
1. **No third-party runtime dependency.** The engine, dashboard, and tests run on
   the Python standard library alone. Optional live-trading deps are commented in
   `requirements.txt`. Do not `import` a package that isn't stdlib in the core.
2. **The AI only proposes.** `ai/engine.py` returns a `Prediction`; it must never
   call the Order Manager, Portfolio, Risk Manager, or hold config/keys.
3. **Risk config is immutable.** `Config` is a frozen dataclass loaded from
   `config/settings.yaml`. Never hand a mutable reference to the AI.
4. **Keys never in the repo; the AI never touches them.** Signing goes through
   `security.Signer` (default `NoSigner` fails closed). For live operation the
   key is loaded from the environment at runtime into `LocalKeySigner` (masked
   in logs) — used only by the execution/adapter layer, never the AI. Prefer an
   out-of-process `ExternalSigner` for larger capital. Never commit key material.
5. **`execution.mode` must be `simulation`.** The runner refuses otherwise.
   `LiveExecutor` is intentionally inert.
6. **Fail closed.** `security.check_geoblock` and the circuit breakers only ever
   restrict trading further; no code path should turn a block into an allow.
7. **The ledger is the source of truth.** Persist settlement results back onto
   fills (`storage.finalize_position_trades`) so reports and the dashboard are
   reproducible from the DB alone.

## Layout
- `polytrader/` — engine and modules (see `docs/ARCHITECTURE.md`).
- `polytrader/web/` — read-only monitoring dashboard (server + SPA).
- `config/settings.yaml` — risk/strategy parameters.
- `tests/` — `unittest` suite (no pytest required).
- `docs/` — architecture and roadmap.

## Commands
```bash
python3 -m unittest discover -s tests            # run all tests
python3 -m polytrader.scripts.run_simulation --days 7 --offline --db polytrader.db
python3 -m polytrader.scripts.serve_dashboard --db polytrader.db --port 8000
```

## Testing expectations
Every new module needs `unittest` coverage. Run the full suite before committing;
it must stay green. When changing the simulation clock, remember the engine uses
a **virtual** clock (`self.now`) — time-based logic must use it, not
`time.time()` (see the selector's `now` parameter).

## The live-trading boundary
Phase 4 (real orders) is deliberately not implemented. It requires human
authorization, out-of-band keys, a real geoblock determination, and the
`execution.mode` flip. Do not cross this line autonomously.
