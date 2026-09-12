"""Operate a funded Polymarket account — gated, opt-in, tiny by default.

This is the entry point for the "I funded a wallet and want the system to
operate it" workflow. It is deliberately conservative:

* ``--check`` (default) runs the live preflight and a **read-only** connectivity
  test (resolve address, read USDC balance). It places NO orders.
* ``--place-test-order`` places ONE small limit order on a token you name, then
  (unless ``--keep``) cancels it — so you can validate execution end to end with
  minimal risk before any automated trading.

Every path runs :func:`live_preflight`; nothing trades unless
``execution.mode == "live"``, ``POLYMARKET_PRIVATE_KEY`` is set, the geoblock
passes, and ``POLYTRADER_LIVE_AUTHORIZED=1``. Credentials come only from the
environment (see ``docs/GO_LIVE.md``); they are never printed.

Autonomous scan→trade looping is intentionally NOT wired here yet — validate
connectivity and a single order first.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_config
from ..i18n import normalize_lang, tr
from ..models import Order, OrderType, Outcome, Side, new_id
from ..security import LocalKeySigner, check_geoblock
from ..live import (
    LiveExecutionBlocked, LiveCredentials, MissingCredentials,
    PyClobClientAdapter, authorization_from_env, live_preflight,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Operate a funded Polymarket account (gated)")
    p.add_argument("--check", action="store_true", default=True,
                   help="read-only preflight + connectivity check (default)")
    p.add_argument("--place-test-order", action="store_true",
                   help="place ONE small limit order to validate execution")
    p.add_argument("--token", help="outcome token id for the test order")
    p.add_argument("--price", type=float, help="limit price in [0,1] for the test order")
    p.add_argument("--size", type=float, default=1.0,
                   help="notional USD for the test order (keep tiny; default 1)")
    p.add_argument("--keep", action="store_true",
                   help="do not auto-cancel the test order after placing it")
    p.add_argument("--jurisdiction", help="your jurisdiction code for the geoblock check")
    p.add_argument("--i-acknowledge-compliance", action="store_true",
                   help="affirm you may use Polymarket from your jurisdiction")
    p.add_argument("--lang", default="en", choices=["en", "zh"])
    args = p.parse_args(argv)
    lang = normalize_lang(args.lang)

    cfg = load_config()

    # 1) Credentials (env only).
    try:
        creds = LiveCredentials.from_env()
    except MissingCredentials as exc:
        print(f"[credentials] {exc}", file=sys.stderr)
        return 2
    print(f"[credentials] {creds!r}")

    signer = LocalKeySigner(creds.private_key)
    geoblock = check_geoblock(args.jurisdiction, args.i_acknowledge_compliance)

    # 2) Preflight — always, before anything else.
    report = live_preflight(
        cfg, signer=signer, geoblock=geoblock,
        authorized=authorization_from_env(),
        reconciliation_ok=True, halt_level="RUNNING",
    )
    print(report.render())
    if not report.ok:
        print("\nPreflight blocked — resolve the items above (see docs/GO_LIVE.md).",
              file=sys.stderr)
        return 3

    # 3) Build the real adapter (needs py-clob-client).
    try:
        adapter = PyClobClientAdapter.from_credentials(creds)
    except RuntimeError as exc:
        print(f"[adapter] {exc}", file=sys.stderr)
        return 4

    # 4) Read-only connectivity check.
    try:
        addr = adapter.address()
        bal = adapter.usdc_balance()
        print(f"[connectivity] wallet={addr}  USDC≈${bal:,.2f}")
    except Exception as exc:  # noqa: BLE001
        print(f"[connectivity] failed: {exc}", file=sys.stderr)
        return 5

    if not args.place_test_order:
        print("[done] read-only check complete. No orders placed.")
        return 0

    # 5) Optional single guarded test order.
    if not (args.token and args.price):
        print("--place-test-order requires --token and --price", file=sys.stderr)
        return 2
    from ..live import GatedClobExecutor

    try:
        ex = GatedClobExecutor(cfg, signer, adapter, geoblock,
                               authorized=authorization_from_env())
    except LiveExecutionBlocked as exc:
        print(str(exc), file=sys.stderr)
        return 3

    order = Order(
        order_id=new_id("ord"), market_id="manual-test", token_id=args.token,
        outcome=Outcome.YES, side=Side.BUY, order_type=OrderType.LIMIT,
        limit_price=args.price, size=args.size,
        client_key=f"manual-test:{new_id('k')}",
    )
    print(f"[order] placing test order: {args.size} USD @ {args.price} on {args.token}")
    event = ex.submit(order, book=None)  # book unused by the live executor
    print(f"[order] status={event.status.value} filled=${event.filled_size:.2f} "
          f"exchange_id={ex.exchange_ids.get(order.order_id)}")

    if not args.keep and not event.status.value == "FILLED":
        ok = ex.cancel(order)
        print(f"[order] auto-cancel: {'ok' if ok else 'failed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
