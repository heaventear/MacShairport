"""Live-trading framework (Phase 4) — gated and inert until a human enables it.

Nothing in this package can move funds on its own. Every path is guarded by
:func:`live_preflight`, which fails closed unless ALL of these hold:

* ``execution.mode == "live"`` (a human edited the config),
* the ``POLYTRADER_LIVE_AUTHORIZED`` env flag is set by a human,
* an isolated :class:`~polytrader.security.Signer` is present (keys stay
  out-of-process),
* the geoblock check passed,
* the ledger reconciled, and
* no circuit breaker is tripped.

The real exchange adapter (:class:`PyClobClientAdapter`) is a documented stub
that requires the optional ``py-clob-client`` dependency and real credentials;
the fully-implemented, tested execution flow runs against a ``ClobClient``
interface so it can be verified with a fake in the test-suite.
"""

from .preflight import (
    Gate,
    LiveExecutionBlocked,
    PreflightReport,
    authorization_from_env,
    live_preflight,
)
from .executor import ClobClient, FakeClobClient, GatedClobExecutor, PyClobClientAdapter
from .credentials import LiveCredentials, MissingCredentials

__all__ = [
    "Gate",
    "LiveExecutionBlocked",
    "PreflightReport",
    "authorization_from_env",
    "live_preflight",
    "ClobClient",
    "FakeClobClient",
    "GatedClobExecutor",
    "PyClobClientAdapter",
    "LiveCredentials",
    "MissingCredentials",
]
