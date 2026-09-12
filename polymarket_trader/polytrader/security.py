"""Security boundary: key isolation and geoblock (spec 原则 #4, #7; 九、熔断).

This module defines — but does not cross — the boundary to real-money trading.
Two hard rules from the spec are enforced structurally here:

1. **AI never touches private keys** (原则 #4). Signing is delegated to a
   :class:`Signer` that represents an *out-of-process* isolated signer (a KMS,
   hardware wallet, or a separate signing service). No private-key material ever
   lives in this process. The default signer is :class:`NoSigner`, which fails
   closed.

2. **Never bypass geoblock** (原则 #7). :func:`check_geoblock` *fails closed*:
   trading is blocked unless a human has explicitly configured a permitted,
   non-restricted jurisdiction and acknowledged the compliance rules. The
   function can only ever *deny* more than the default — it has no path that
   turns a block into an allow implicitly.

Nothing here signs, trades, or holds secrets in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SignerUnavailable(RuntimeError):
    """Raised when signing is attempted without a real isolated signer."""


class Signer(Protocol):
    def address(self) -> str: ...
    def sign(self, payload: bytes) -> str: ...


class NoSigner:
    """Default signer: no signing capability. Fails closed on every call.

    Its presence is what makes the system safe by default — an accidental
    attempt to go live cannot sign anything."""

    def address(self) -> str:
        raise SignerUnavailable("no signer configured (keys are isolated out-of-band)")

    def sign(self, payload: bytes) -> str:
        raise SignerUnavailable("no signer configured; refusing to sign")


class ExternalSigner:
    """Boundary to an isolated signing service (KMS / hardware / separate host).

    The private key never enters this process; ``sign`` would forward the payload
    to the external signer and return only the signature. Deliberately not
    implemented in this phase — wiring it is a Phase-4 task gated on human
    authorisation and out-of-band key provisioning.
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def address(self) -> str:
        raise NotImplementedError(
            "ExternalSigner is not implemented in this phase; connect an isolated "
            "signing service (KMS/hardware) out-of-band before enabling live mode.")

    def sign(self, payload: bytes) -> str:
        raise NotImplementedError(
            "ExternalSigner.sign is not implemented in this phase.")


# --------------------------------------------------------------------------- #
# Geoblock — fail closed.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GeoblockResult:
    allowed: bool
    reason: str


# Illustrative restricted set. The real list must be maintained against
# Polymarket's terms and applicable regulation by a human compliance owner.
RESTRICTED_JURISDICTIONS = frozenset({
    "US", "USA", "UNITED STATES",
})


def check_geoblock(
    jurisdiction: str | None,
    compliance_acknowledged: bool,
) -> GeoblockResult:
    """Return whether trading is permitted from ``jurisdiction``.

    Fails closed: unknown jurisdiction, un-acknowledged compliance, or a
    restricted jurisdiction all return ``allowed=False``. There is deliberately
    no argument or code path that can force an allow when the checks fail — the
    system can only ever restrict trading further, never circumvent a block.
    """
    if not compliance_acknowledged:
        return GeoblockResult(False, "compliance rules not acknowledged (fail closed)")
    if not jurisdiction:
        return GeoblockResult(False, "jurisdiction unknown (fail closed)")
    if jurisdiction.strip().upper() in RESTRICTED_JURISDICTIONS:
        return GeoblockResult(False, f"restricted jurisdiction: {jurisdiction}")
    return GeoblockResult(True, f"permitted jurisdiction: {jurisdiction}")
