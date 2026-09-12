"""Reconciliation framework (spec 三/九: 每日资金对账、每日持仓对账、三方对账).

Compares the local ledger against one or more external views (the Polymarket
API, and on-chain balances) and reports any discrepancy beyond tolerance. A
mismatch is a hard-halt trigger (原则: 账实不一致立即暂停).

In this phase the only concrete external source is
:class:`SimulatedExchangeSource`, which mirrors the local portfolio (optionally
with an injected drift for testing the mismatch path). Real sources — a
Polymarket API reader and an on-chain reader — implement the same
:class:`LedgerSource` interface and drop in without touching the reconciler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .models import Outcome
from .portfolio import PortfolioManager


@dataclass
class SourceView:
    """A normalized snapshot of cash + positions from one source."""

    name: str
    cash: float
    # (market_id, outcome) -> shares held
    positions: dict[tuple[str, str], float] = field(default_factory=dict)


class LedgerSource(Protocol):
    def view(self) -> SourceView: ...


class LocalPortfolioSource:
    """The local ledger's own view (the source of truth we reconcile against)."""

    def __init__(self, portfolio: PortfolioManager, name: str = "local"):
        self.portfolio = portfolio
        self.name = name

    def view(self) -> SourceView:
        positions = {
            (mid, outcome.value): pos.shares
            for (mid, outcome), pos in self.portfolio.positions.items()
            if pos.shares > 0 and not pos.resolved
        }
        return SourceView(self.name, round(self.portfolio.cash, 6), positions)


class SimulatedExchangeSource:
    """Stand-in external source that mirrors the local portfolio.

    ``cash_drift`` / ``share_drift`` inject a discrepancy to exercise the
    mismatch path in tests; left at zero it always reconciles, as a correctly
    functioning simulation should.
    """

    def __init__(self, portfolio: PortfolioManager, name: str = "exchange",
                 cash_drift: float = 0.0, share_drift: float = 0.0):
        self.portfolio = portfolio
        self.name = name
        self.cash_drift = cash_drift
        self.share_drift = share_drift

    def view(self) -> SourceView:
        base = LocalPortfolioSource(self.portfolio).view()
        positions = {k: v + self.share_drift for k, v in base.positions.items()}
        return SourceView(self.name, base.cash + self.cash_drift, positions)


@dataclass
class ReconciliationEntry:
    source: str
    field: str          # "cash" or "shares:<market>:<outcome>"
    local: float
    external: float
    diff: float
    consistent: bool


@dataclass
class ReconciliationReport:
    entries: list[ReconciliationEntry] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return all(e.consistent for e in self.entries)

    def mismatches(self) -> list[ReconciliationEntry]:
        return [e for e in self.entries if not e.consistent]


class Reconciler:
    def __init__(self, cash_tol: float = 0.50, share_tol: float = 1e-6):
        self.cash_tol = cash_tol
        self.share_tol = share_tol

    def reconcile(
        self, local: SourceView, externals: list[SourceView]
    ) -> ReconciliationReport:
        report = ReconciliationReport()
        for ext in externals:
            # Cash.
            diff = abs(local.cash - ext.cash)
            report.entries.append(ReconciliationEntry(
                ext.name, "cash", local.cash, ext.cash, diff, diff <= self.cash_tol))
            # Positions: union of keys across both views.
            keys = set(local.positions) | set(ext.positions)
            for k in sorted(keys):
                lv = local.positions.get(k, 0.0)
                ev = ext.positions.get(k, 0.0)
                d = abs(lv - ev)
                report.entries.append(ReconciliationEntry(
                    ext.name, f"shares:{k[0]}:{k[1]}", lv, ev, d, d <= self.share_tol))
        return report
