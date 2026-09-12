"""Monitoring & Reporting (spec 模块 #8 + 十二、七天评估指标).

Computes the profit, trading, AI-quality, and engineering metrics the spec
requires, and formats the daily and final 7-day reports. All numbers are
derived from the SQLite ledger and the in-memory portfolio, so a report is fully
reproducible from persisted state.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PredictionOutcome:
    """One resolved prediction: the probability the AI assigned to the outcome
    it selected, and whether that outcome actually happened."""

    predicted_prob: float
    realized: int  # 1 if the selected outcome won, else 0
    confidence: float
    had_evidence: bool


def brier_score(items: list[PredictionOutcome]) -> float | None:
    """Mean squared error of probabilistic forecasts. Lower is better;
    always-0.5 scores 0.25."""
    if not items:
        return None
    return sum((it.predicted_prob - it.realized) ** 2 for it in items) / len(items)


def calibration_bins(items: list[PredictionOutcome], n_bins: int = 5) -> list[dict]:
    """Group predictions into probability bins and compare predicted vs actual
    frequency (概率校准度)."""
    bins: list[dict] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        group = [it for it in items if lo <= it.predicted_prob < hi or
                 (b == n_bins - 1 and it.predicted_prob == 1.0)]
        if not group:
            bins.append({"range": f"{lo:.1f}-{hi:.1f}", "count": 0,
                         "avg_pred": None, "actual_freq": None})
            continue
        avg_pred = sum(it.predicted_prob for it in group) / len(group)
        actual = sum(it.realized for it in group) / len(group)
        bins.append({"range": f"{lo:.1f}-{hi:.1f}", "count": len(group),
                     "avg_pred": round(avg_pred, 3), "actual_freq": round(actual, 3)})
    return bins


@dataclass
class TradeMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_fees: float = 0.0
    total_slippage_cost: float = 0.0
    maker_ratio: float | None = None
    anomalous_trades: int = 0


def compute_trade_metrics(trades: list[dict]) -> TradeMetrics:
    m = TradeMetrics()
    m.total_trades = len(trades)
    settled = [t for t in trades if t.get("outcome_result")]
    wins = [t for t in settled if t.get("pnl", 0) > 0]
    losses = [t for t in settled if t.get("pnl", 0) < 0]
    m.wins, m.losses = len(wins), len(losses)
    if settled:
        m.win_rate = round(len(wins) / len(settled), 3)
    if wins:
        m.avg_win = round(sum(t["pnl"] for t in wins) / len(wins), 3)
    if losses:
        m.avg_loss = round(sum(t["pnl"] for t in losses) / len(losses), 3)
    m.total_fees = round(sum(t.get("fee", 0) for t in trades), 4)
    m.total_slippage_cost = round(
        sum(t.get("slippage", 0) * t.get("filled_size", 0) for t in trades), 4
    )
    m.anomalous_trades = sum(1 for t in trades if t.get("anomalous"))
    return m


@dataclass
class EngineeringMetrics:
    api_errors: int = 0
    risk_rejections: int = 0
    duplicate_suppressions: int = 0
    unknown_orders: int = 0
    cancellations: int = 0
    system_pauses: int = 0
    reconciliation_consistent: bool = True


@dataclass
class DailyReport:
    day: int
    equity: float
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    drawdown_pct: float
    halt_level: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"=== Day {self.day} Report ===",
            f"  Equity:          ${self.equity:,.2f}",
            f"  Cash:            ${self.cash:,.2f}",
            f"  Realized PnL:    ${self.realized_pnl:,.2f}",
            f"  Unrealized PnL:  ${self.unrealized_pnl:,.2f}",
            f"  Open positions:  {self.open_positions}",
            f"  Drawdown:        {self.drawdown_pct:.2%}",
            f"  Risk state:      {self.halt_level}",
        ]
        for note in self.notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


def render_final_report(
    *,
    days: int,
    start_equity: float,
    end_equity: float,
    max_drawdown_pct: float,
    trade_metrics: TradeMetrics,
    brier: float | None,
    calibration: list[dict],
    engineering: EngineeringMetrics,
    success_criteria: dict[str, bool],
) -> str:
    net = end_equity - start_equity
    ret = net / start_equity if start_equity else 0.0
    lines = [
        "",
        "############################################################",
        f"#      {days}-Day Experiment Report (SIMULATION)",
        "############################################################",
        "",
        "-- Profit (收益指标) ------------------------------------",
        f"  Start equity:     ${start_equity:,.2f}",
        f"  End equity:       ${end_equity:,.2f}",
        f"  Net PnL:          ${net:,.2f} ({ret:+.2%})",
        f"  Max drawdown:     {max_drawdown_pct:.2%}",
        f"  Total fees:       ${trade_metrics.total_fees:,.4f}",
        f"  Slippage cost:    ${trade_metrics.total_slippage_cost:,.4f}",
        "",
        "-- Trading (交易指标) -----------------------------------",
        f"  Total trades:     {trade_metrics.total_trades}",
        f"  Win / Loss:       {trade_metrics.wins} / {trade_metrics.losses}",
        f"  Win rate:         {_pct(trade_metrics.win_rate)}",
        f"  Avg win / loss:   ${trade_metrics.avg_win:,.3f} / ${trade_metrics.avg_loss:,.3f}",
        f"  Anomalous trades: {trade_metrics.anomalous_trades}",
        "",
        "-- AI quality (AI 指标) ---------------------------------",
        f"  Brier score:      {brier if brier is None else round(brier, 4)}"
        "   (0=perfect, 0.25=always-0.5)",
        "  Calibration:",
    ]
    for b in calibration:
        if b["count"]:
            lines.append(
                f"    {b['range']}: n={b['count']:<3} "
                f"pred={b['avg_pred']} actual={b['actual_freq']}"
            )
    lines += [
        "",
        "-- Engineering (工程指标) --------------------------------",
        f"  API errors:            {engineering.api_errors}",
        f"  Risk rejections:       {engineering.risk_rejections}",
        f"  Duplicate suppressed:  {engineering.duplicate_suppressions}",
        f"  UNKNOWN orders:        {engineering.unknown_orders}",
        f"  Cancellations:         {engineering.cancellations}",
        f"  System pauses:         {engineering.system_pauses}",
        f"  Ledger reconciled:     {engineering.reconciliation_consistent}",
        "",
        "-- Success criteria (实验成功标准) -----------------------",
    ]
    for crit, ok in success_criteria.items():
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {crit}")
    overall = all(success_criteria.values())
    lines += [
        "",
        f"  OVERALL: {'PASS' if overall else 'FAIL'}",
        "",
        "  NOTE: a 7-day simulation result does not prove long-term",
        "  profitability (spec 十三/十四). Treat as a systems test only.",
        "############################################################",
    ]
    return "\n".join(lines)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"
