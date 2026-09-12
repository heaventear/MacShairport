"""Live-trading preflight — the single choke point that must pass before any
real order. Fails closed: every gate must be explicitly satisfied.

This is the code embodiment of the spec's live-trading prerequisites (十五、
第四阶段) and safety principles (原则 #4/#5/#7). It runs no I/O and moves no
funds; it only decides whether live execution is permitted to proceed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..config import Config
from ..security import GeoblockResult, NoSigner, Signer

AUTH_ENV_VAR = "POLYTRADER_LIVE_AUTHORIZED"


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str


@dataclass
class PreflightReport:
    gates: list[Gate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    def blockers(self) -> list[Gate]:
        return [g for g in self.gates if not g.passed]

    def render(self) -> str:
        lines = ["Live-trading preflight:"]
        for g in self.gates:
            lines.append(f"  [{'PASS' if g.passed else 'BLOCK'}] {g.name}: {g.detail}")
        lines.append(f"  => {'READY' if self.ok else 'BLOCKED'}")
        return "\n".join(lines)


class LiveExecutionBlocked(RuntimeError):
    """Raised when live execution is attempted while preflight is not satisfied."""

    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__("live execution blocked:\n" + report.render())


def authorization_from_env(env: dict | None = None) -> bool:
    """True only if a human has set the explicit authorization flag.

    Deliberately a separate signal from ``execution.mode`` so that flipping the
    config alone is not enough to trade — a second, out-of-band human action is
    required.
    """
    env = env if env is not None else os.environ
    return env.get(AUTH_ENV_VAR) == "1"


def live_preflight(
    cfg: Config,
    *,
    signer: Signer | None,
    geoblock: GeoblockResult | None,
    authorized: bool,
    reconciliation_ok: bool,
    halt_level: str,
) -> PreflightReport:
    gates = [
        Gate("execution_mode_live", cfg.execution.mode == "live",
             f"execution.mode={cfg.execution.mode!r} (must be 'live', human-set)"),
        Gate("human_authorization", authorized,
             f"env {AUTH_ENV_VAR} must equal '1' (a second, human action)"),
        Gate("isolated_signer",
             signer is not None and not isinstance(signer, NoSigner),
             "an out-of-process Signer must be configured; keys never enter this process"),
        Gate("geoblock_passed", bool(geoblock and geoblock.allowed),
             geoblock.reason if geoblock else "no geoblock result (fail closed)"),
        Gate("ledger_reconciled", reconciliation_ok,
             "local ledger must reconcile with exchange/on-chain before trading"),
        Gate("not_halted", halt_level == "RUNNING",
             f"risk halt level is {halt_level!r} (must be RUNNING)"),
    ]
    return PreflightReport(gates)
