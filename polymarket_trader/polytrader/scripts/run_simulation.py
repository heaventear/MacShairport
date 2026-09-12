"""CLI entry point for the paper-trading simulation.

Examples
--------
    python3 -m polytrader.scripts.run_simulation --days 7 --offline
    python3 -m polytrader.scripts.run_simulation --days 2 --live-data

The live-data mode reads Polymarket's public APIs (read-only) and paper-trades
against them; it never places orders and never settles positions.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_config
from ..engine_loop import build_live_data_engine, build_offline_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket paper-trading simulation")
    parser.add_argument("--days", type=int, default=7, help="number of days to simulate")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (offline)")
    parser.add_argument("--db", default="polytrader.db", help="SQLite ledger path")
    parser.add_argument("--config", default=None, help="path to settings.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", default=True,
                      help="use the bundled offline fixture (default)")
    mode.add_argument("--live-data", action="store_true",
                      help="read live Polymarket public data (read-only)")
    parser.add_argument("--quiet", action="store_true", help="suppress daily reports")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if cfg.execution.mode != "simulation":
        print("REFUSING TO RUN: execution.mode must be 'simulation' in this phase.",
              file=sys.stderr)
        return 2

    if args.live_data:
        print("Running against LIVE read-only Polymarket data (no orders placed).")
        engine = build_live_data_engine(cfg, storage_path=args.db,
                                        verbose=not args.quiet)
    else:
        engine = build_offline_engine(cfg, storage_path=args.db, seed=args.seed,
                                      verbose=not args.quiet)

    report = engine.run(days=args.days)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
