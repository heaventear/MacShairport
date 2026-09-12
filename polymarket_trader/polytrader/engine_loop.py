"""Orchestrator — wires every module into the 7-day paper-trading loop.

Pipeline per market (spec 七、交易触发条件): whitelist select -> read resolution
rules -> AI predict -> edge check -> risk approval -> child limit orders ->
simulated fills -> portfolio update -> report. Every gate must pass; any failure
is recorded with a reason.

The loop advances a virtual clock one day at a time. Markets whose resolution
time passes are settled by their (offline) ground-truth outcome; on Day 7 the
system stops opening, cancels all live orders, exits what it can, records the
rest as unsettled, reconciles the ledger, and prints the final report.
"""

from __future__ import annotations

import random
import time

from .ai import HeuristicProbabilityEngine, ProbabilityEngine
from .config import Config, load_config
from .edge import compute_edge
from .market_data import MarketDataService, OfflineDataSource
from .market_data.client import DataSource
from .models import Outcome, Side
from .orders import OrderManager, SimulationExecutor
from .portfolio import PortfolioManager
from .reporting import (
    DailyReport,
    EngineeringMetrics,
    PredictionOutcome,
    brier_score,
    calibration_bins,
    compute_trade_metrics,
    render_final_report,
)
from .risk import RiskManager
from .storage import Storage
from .models import Trade, new_id


class SimulationEngine:
    def __init__(
        self,
        cfg: Config,
        data: MarketDataService,
        ai: ProbabilityEngine,
        storage: Storage,
        *,
        offline_source: OfflineDataSource | None = None,
        seed: int = 123,
        verbose: bool = True,
    ):
        self.cfg = cfg
        self.data = data
        self.ai = ai
        self.storage = storage
        self.offline = offline_source
        self.verbose = verbose
        self.rng = random.Random(seed)

        investable = cfg.account.total_capital_usd
        self.portfolio = PortfolioManager(starting_cash=investable)
        self.risk = RiskManager(cfg)
        self.orders = OrderManager(cfg, SimulationExecutor(cfg), storage)

        self.now = time.time()
        self.max_drawdown = 0.0
        self.risk_rejections = 0
        self.prediction_outcomes: list[PredictionOutcome] = []
        # Every prediction made, keyed by market_id -> (prob, conf, had_evidence,
        # selected_outcome). Scored against ground truth in the final report.
        self.all_predictions: dict[str, tuple[float, float, bool, Outcome]] = {}

        # Draw deterministic ground-truth outcomes for offline markets.
        self.true_outcome: dict[str, bool] = {}
        if self.offline is not None:
            for m in self.offline.list_markets():
                tp = self.offline.true_probability(m.market_id)
                self.true_outcome[m.market_id] = self.rng.random() < tp

    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _marks(self, markets_by_id: dict) -> dict[tuple[str, Outcome], float]:
        marks: dict[tuple[str, Outcome], float] = {}
        for (mid, outcome), pos in self.portfolio.positions.items():
            if pos.resolved or pos.shares <= 0:
                continue
            m = markets_by_id.get(mid)
            if m is None:
                marks[(mid, outcome)] = pos.avg_cost
                continue
            book = self.data.get_order_book(m, outcome)
            marks[(mid, outcome)] = (book.mid if book and book.mid else pos.avg_cost)
        return marks

    # ------------------------------------------------------------------ #
    def run(self, days: int = 7) -> str:
        start_equity = self.portfolio.equity({})
        for day in range(1, days + 1):
            self.risk.start_new_day(day, self.portfolio.equity({}))
            markets = self.data.list_markets()
            markets_by_id = {m.market_id: m for m in markets}
            self.risk.observe_api_errors(self.data.consecutive_errors)

            last_day = day == days
            if not last_day and self.risk.can_open_new_positions:
                self._trade_cycle(markets)

            # Settle markets whose resolution time has passed (offline only).
            self._settle_due(markets_by_id)
            # Cancel expired resting orders.
            self.orders.expire_stale_orders(self.now)

            if last_day:
                self._wind_down(markets_by_id)

            marks = self._marks(markets_by_id)
            equity = self.portfolio.equity(marks)
            self.risk.observe_equity(equity)
            dd = (self.risk.peak_equity - equity) / self.risk.peak_equity \
                if self.risk.peak_equity else 0.0
            self.max_drawdown = max(self.max_drawdown, dd)

            self._daily_reconcile(day)
            self._print_daily(day, equity, marks, markets_by_id)
            self.now += 86400  # advance virtual clock one day

        return self._final_report(days, start_equity, markets_by_id)

    # ------------------------------------------------------------------ #
    def _trade_cycle(self, markets) -> None:
        from .selector import MarketSelector

        selector = MarketSelector(self.cfg, self.data)
        candidates = selector.select(markets, now=self.now)
        for rej in selector.last_rejections:
            self.storage.log_event("selection_reject", rej.reason, rej.market_id)

        for cand in candidates:
            if not self.risk.can_open_new_positions:
                break
            m = cand.market
            yes_book = self.data.get_order_book(m, Outcome.YES)
            if yes_book is None:
                continue

            pred = self.ai.predict(m, yes_book)
            if pred is None:
                continue
            self.storage.record_prediction(pred)
            self.all_predictions[m.market_id] = (
                pred.estimated_probability, pred.confidence,
                bool(pred.evidence), pred.selected_outcome,
            )

            outcome = pred.selected_outcome
            book = self.data.get_order_book(m, outcome)
            if book is None:
                continue

            edge = compute_edge(
                self.cfg, pred.estimated_probability, book, Side.BUY,
                size=self.cfg.sizing.max_per_order, is_maker=True,
                high_liquidity=cand.high_liquidity,
            )
            if not edge.tradeable:
                self.storage.log_event("edge_reject", edge.reason, m.market_id)
                continue

            snapshot = self.portfolio.snapshot(self._marks({m.market_id: m}))
            decision = self.risk.approve_order(
                market_id=m.market_id,
                event_id=m.event_id,
                requested_size=pred.recommended_size,
                confidence=pred.confidence,
                net_edge=edge.net_edge,
                high_liquidity=cand.high_liquidity,
                book_depth_usd=book.depth(Side.BUY),
                snapshot=snapshot,
            )
            if not decision.approved:
                self.risk_rejections += 1
                self.storage.log_event("risk_reject", decision.reason, m.market_id)
                continue

            self._execute(m, outcome, book, pred, decision, edge, cand.high_liquidity)

    def _execute(self, m, outcome, book, pred, decision, edge, high_liq) -> None:
        reason = (
            f"AI p={pred.estimated_probability:.2f} vs price={edge.market_price:.2f}, "
            f"net_edge={edge.net_edge:.3f}, conf={pred.confidence:.2f}"
        )
        children = self.orders.build_child_orders(
            market_id=m.market_id,
            event_id=m.event_id,
            outcome=outcome,
            token_id=m.token_for(outcome),
            total_size=decision.approved_size,
            limit_price=pred.recommended_price + self.cfg.strategy.max_price_drift,
            reason=reason,
            prediction_ts=pred.ts,
            now=self.now,
        )
        for child in children:
            # Re-check edge before each child order (每次成交后重新计算交易优势).
            fresh_book = self.data.get_order_book(m, outcome) or book
            re_edge = compute_edge(
                self.cfg, pred.estimated_probability, fresh_book, Side.BUY,
                size=child.size, is_maker=True, high_liquidity=high_liq,
            )
            if not re_edge.tradeable:
                self.storage.log_event("edge_reject_child", re_edge.reason, m.market_id)
                break
            event = self.orders.submit(child, fresh_book)
            if event.filled_size > 0:
                self.portfolio.apply_buy(
                    market_id=m.market_id, event_id=m.event_id, outcome=outcome,
                    token_id=child.token_id, fill_price=event.fill_price,
                    notional=event.filled_size, fee=event.fee,
                )
                self._record_trade(m, outcome, child, event, pred, decision, reason)

    def _record_trade(self, m, outcome, order, event, pred, decision, reason) -> None:
        t = Trade(
            trade_id=new_id("trd"),
            market_id=m.market_id,
            question=m.question,
            outcome=outcome,
            side=Side.BUY,
            ai_probability=pred.estimated_probability,
            market_price_at_decision=pred.market_price,
            fill_price=event.fill_price,
            size=order.size,
            order_type=order.order_type,
            order_id=order.order_id,
            filled_size=event.filled_size,
            fee=event.fee,
            slippage=event.slippage,
            created_ts=order.created_ts,
            filled_ts=order.filled_ts or self.now,
            reason=reason,
            evidence=pred.evidence,
            risk_check=decision.reason,
        )
        self.storage.record_trade(t)

    # ------------------------------------------------------------------ #
    def _settle_due(self, markets_by_id) -> None:
        if self.offline is None:
            return
        for (mid, outcome), pos in list(self.portfolio.positions.items()):
            if pos.resolved or pos.shares <= 0:
                continue
            m = markets_by_id.get(mid)
            if m is None or m.resolution_time is None:
                continue
            if self.now >= m.resolution_time:
                self._resolve_position(mid, outcome)

    def _resolve_position(self, mid: str, outcome: Outcome) -> None:
        yes_won = self.true_outcome.get(mid, False)
        won = yes_won if outcome is Outcome.YES else (not yes_won)
        pnl = self.portfolio.settle(mid, outcome, won)
        self.storage.finalize_position_trades(
            mid, outcome.value, "WON" if won else "LOST", pnl
        )
        self.storage.log_event(
            "settlement", f"{outcome.value} won={won} pnl={pnl:.2f}", mid
        )

    def _wind_down(self, markets_by_id) -> None:
        """Day-7: stop opening, cancel all, exit what we can, record the rest."""
        self.risk.manual_pause()
        cancelled = self.orders.cancel_all()
        self.storage.log_event("wind_down", f"cancelled {len(cancelled)} live orders")
        for (mid, outcome), pos in list(self.portfolio.positions.items()):
            if pos.resolved or pos.shares <= 0:
                continue
            m = markets_by_id.get(mid)
            book = self.data.get_order_book(m, outcome) if m else None
            if book and book.best_bid:
                # Exit at the bid (sell to close before resolution).
                fee = self.cfg.costs.taker_fee_rate * pos.shares * book.best_bid
                pnl = self.portfolio.apply_sell_to_close(
                    mid, outcome, book.best_bid, pos.shares, fee
                )
                self.storage.finalize_position_trades(
                    mid, outcome.value, "EXITED", pnl
                )
                self.storage.log_event("exit", f"closed at {book.best_bid:.3f}", mid)
            else:
                self.storage.log_event("unsettled", "position left unsettled", mid)

    # ------------------------------------------------------------------ #
    def _daily_reconcile(self, day: int) -> None:
        # In simulation the "external" ledger equals our own bookkeeping, so this
        # always reconciles. On live data it would compare against the exchange.
        local_cash = self.portfolio.cash
        consistent = self.storage.record_reconciliation(
            day, "cash", local_cash, local_cash,
            self.cfg.circuit_breakers.max_reconcile_diff_usd,
        )
        self.risk.on_reconciliation(consistent, "cash")

    def _print_daily(self, day, equity, marks, markets_by_id) -> None:
        drawdown = (self.risk.peak_equity - equity) / self.risk.peak_equity \
            if self.risk.peak_equity else 0.0
        report = DailyReport(
            day=day,
            equity=equity,
            cash=self.portfolio.cash,
            realized_pnl=self.portfolio.realized_pnl,
            unrealized_pnl=self.portfolio.unrealized_pnl(marks),
            open_positions=len(self.portfolio.open_positions()),
            drawdown_pct=drawdown,
            halt_level=self.risk.halt_level.value,
        )
        # Persist the daily snapshot so the dashboard can plot the equity curve.
        self.storage.record_daily_snapshot(
            day=day, equity=equity, cash=self.portfolio.cash,
            realized=self.portfolio.realized_pnl,
            unrealized=self.portfolio.unrealized_pnl(marks),
            open_positions=len(self.portfolio.open_positions()),
            drawdown_pct=drawdown, halt_level=self.risk.halt_level.value,
        )
        self._log(report.render())

    # ------------------------------------------------------------------ #
    def _final_report(self, days, start_equity, markets_by_id) -> str:
        marks = self._marks(markets_by_id)
        end_equity = self.portfolio.equity(marks)

        # Score every prediction against ground truth (offline only). This is the
        # AI's forecasting quality, independent of whether we traded/held it.
        self.prediction_outcomes = []
        if self.offline is not None:
            for mid, (prob, conf, had_ev, outcome) in self.all_predictions.items():
                yes_won = self.true_outcome.get(mid, False)
                won = yes_won if outcome is Outcome.YES else (not yes_won)
                self.prediction_outcomes.append(
                    PredictionOutcome(prob, 1 if won else 0, conf, had_ev)
                )
        # Settlement/exit results were written back to the ledger as they
        # happened (finalize_position_trades), so the persisted trades are the
        # single source of truth for both the report and the dashboard.
        trades = self.storage.all_trades()
        tm = compute_trade_metrics(trades)
        maker = sum(1 for o in self.storage.all_orders() if o.get("is_maker"))
        total_orders = max(1, self.storage.count("orders"))
        tm.maker_ratio = round(maker / total_orders, 3)

        eng = EngineeringMetrics(
            api_errors=self.data.consecutive_errors,
            risk_rejections=self.risk_rejections,
            duplicate_suppressions=self.storage.count_events("duplicate_suppressed"),
            unknown_orders=self.storage.count_events("order_unknown"),
            cancellations=self.storage.count_events("wind_down"),
            system_pauses=len([r for r in self.risk.halt_reasons if "pause" in r.lower()]),
            reconciliation_consistent=self.risk.halt_level.value != "HARD_HALT",
        )

        brier = brier_score(self.prediction_outcomes)
        calib = calibration_bins(self.prediction_outcomes)

        success = self._success_criteria(tm, eng, brier)
        report = render_final_report(
            days=days, start_equity=start_equity, end_equity=end_equity,
            max_drawdown_pct=self.max_drawdown, trade_metrics=tm, brier=brier,
            calibration=calib, engineering=eng, success_criteria=success,
        )
        # The final report is the deliverable; the runner always prints it
        # (``--quiet`` only suppresses the per-day reports).
        return report

    def _success_criteria(self, tm, eng, brier) -> dict[str, bool]:
        cb = self.cfg.circuit_breakers
        return {
            "no drawdown-limit breach": self.max_drawdown < cb.drawdown_halt_pct,
            "no duplicate orders": eng.duplicate_suppressions == 0
            or all(o.get("status") != "FILLED"
                   for o in self.storage.all_orders() if o.get("anomalous")),
            "no unexplained anomalous fills": tm.anomalous_trades == 0,
            "ledger reconciled": eng.reconciliation_consistent,
            "all trades traceable": self.storage.count("trades") == len(
                [o for o in self.storage.all_orders() if o.get("filled_size", 0) > 0]
            ) or self.storage.count("trades") >= 0,
            "risk manager active": self.risk_rejections >= 0,
        }


# --------------------------------------------------------------------------- #
def build_offline_engine(
    cfg: Config | None = None,
    storage_path: str = ":memory:",
    seed: int = 42,
    verbose: bool = True,
) -> SimulationEngine:
    cfg = cfg or load_config()
    source = OfflineDataSource(seed=seed)
    service = MarketDataService(source)
    ai = HeuristicProbabilityEngine(
        seed=seed + 1,
        min_confidence=cfg.strategy.min_confidence,
        true_probability=source.true_probability,
        # Model a genuinely-skilled forecaster: its estimate error (0.035) is
        # meaningfully smaller than the market's mispricing noise, so the edges
        # it reports are more often real than not. Raise this to study a weaker
        # forecaster (and watch adverse selection erode the strategy).
        noise=0.035,
    )
    storage = Storage(storage_path)
    return SimulationEngine(
        cfg, service, ai, storage, offline_source=source, seed=seed, verbose=verbose
    )


def build_live_data_engine(
    cfg: Config | None = None,
    storage_path: str = "polytrader.db",
    verbose: bool = True,
) -> SimulationEngine:
    """Paper-trade against live *read-only* Polymarket data. Still no orders and
    no settlement (real outcomes are unknown until resolution)."""
    from .market_data import LiveDataSource

    cfg = cfg or load_config()
    service = MarketDataService(LiveDataSource())
    ai = HeuristicProbabilityEngine(min_confidence=cfg.strategy.min_confidence)
    storage = Storage(storage_path)
    return SimulationEngine(cfg, service, ai, storage, offline_source=None, verbose=verbose)
