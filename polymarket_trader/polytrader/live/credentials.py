"""Live credential loading — from environment variables only.

The user funds a Polymarket wallet manually and provides its key + (optional)
API credentials via the environment. Nothing here is ever committed, printed,
or logged; ``__repr__`` masks every secret.

Required to trade:
* ``POLYMARKET_PRIVATE_KEY`` — the wallet's private key (0x…). Loaded into the
  :class:`~polytrader.security.LocalKeySigner`; only the execution layer uses it.

Optional:
* ``POLYMARKET_HOST``        — CLOB host (default https://clob.polymarket.com)
* ``POLYMARKET_CHAIN_ID``    — chain id (default 137, Polygon)
* ``POLYMARKET_FUNDER``      — funder / proxy wallet address, if trading through
                               a Polymarket proxy wallet (email/Magic account).
* ``POLYMARKET_SIGNATURE_TYPE`` — 0 (EOA), 1 (email/Magic proxy), 2 (browser
                               proxy). Default 0.
* ``POLYMARKET_API_KEY`` / ``POLYMARKET_API_SECRET`` / ``POLYMARKET_API_PASSPHRASE``
                             — L2 API creds. If absent, they are derived from the
                               key at runtime (create_or_derive_api_creds).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_CHAIN_ID = 137


class MissingCredentials(RuntimeError):
    """Raised when a required live credential is not present in the environment."""


@dataclass
class LiveCredentials:
    private_key: str
    host: str = DEFAULT_HOST
    chain_id: int = DEFAULT_CHAIN_ID
    funder: str | None = None
    signature_type: int = 0
    api_key: str | None = None
    api_secret: str | None = None
    api_passphrase: str | None = None

    @property
    def has_api_creds(self) -> bool:
        return bool(self.api_key and self.api_secret and self.api_passphrase)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "LiveCredentials":
        env = env if env is not None else os.environ
        pk = env.get("POLYMARKET_PRIVATE_KEY", "").strip()
        if not pk:
            raise MissingCredentials(
                "POLYMARKET_PRIVATE_KEY is not set. Export your funded wallet's "
                "private key in the shell that runs the live engine (never commit it)."
            )
        sig_type_raw = env.get("POLYMARKET_SIGNATURE_TYPE", "0").strip()
        try:
            sig_type = int(sig_type_raw)
        except ValueError:
            sig_type = 0
        chain_raw = env.get("POLYMARKET_CHAIN_ID", str(DEFAULT_CHAIN_ID)).strip()
        try:
            chain_id = int(chain_raw)
        except ValueError:
            chain_id = DEFAULT_CHAIN_ID
        return cls(
            private_key=pk,
            host=env.get("POLYMARKET_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
            chain_id=chain_id,
            funder=(env.get("POLYMARKET_FUNDER") or "").strip() or None,
            signature_type=sig_type,
            api_key=(env.get("POLYMARKET_API_KEY") or "").strip() or None,
            api_secret=(env.get("POLYMARKET_API_SECRET") or "").strip() or None,
            api_passphrase=(env.get("POLYMARKET_API_PASSPHRASE") or "").strip() or None,
        )

    def __repr__(self) -> str:  # never leak secrets
        return (
            f"LiveCredentials(host={self.host!r}, chain_id={self.chain_id}, "
            f"funder={self.funder!r}, signature_type={self.signature_type}, "
            f"private_key=<hidden>, api_creds={'set' if self.has_api_creds else 'derive'})"
        )
