"""CLI entry point for the read-only monitoring dashboard.

Examples
--------
    # 1) produce a ledger by running a simulation to a file DB
    python3 -m polytrader.scripts.run_simulation --days 7 --offline --db polytrader.db

    # 2) view it in the browser
    python3 -m polytrader.scripts.serve_dashboard --db polytrader.db --port 8000

The dashboard is read-only: it only reads the SQLite ledger, never places
orders and never mutates state.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..web import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket Trader dashboard (read-only)")
    parser.add_argument("--db", default="polytrader.db", help="SQLite ledger path")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8000, help="bind port")
    parser.add_argument("--config", default=None, help="path to settings.yaml")
    args = parser.parse_args(argv)

    if not Path(args.db).exists():
        print(f"NOTE: ledger '{args.db}' does not exist yet. Run a simulation first:\n"
              f"  python3 -m polytrader.scripts.run_simulation --days 7 --offline --db {args.db}")
    serve(db_path=args.db, host=args.host, port=args.port, config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
