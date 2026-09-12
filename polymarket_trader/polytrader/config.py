"""Immutable configuration loading.

Risk and strategy parameters are loaded once from ``config/settings.yaml`` into
frozen dataclasses. Nothing in the running system — least of all the AI engine —
is handed a mutable reference to these values (spec 设计原则 #5: "AI 不能直接
修改风险参数").

A tiny indentation-based parser handles the restricted YAML subset used by the
settings file, so the project has no third-party dependency to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Minimal YAML-subset parser (nested maps, scalar values, `#` comments).
# --------------------------------------------------------------------------- #
def _coerce(token: str) -> Any:
    token = token.strip()
    if token == "" or token in ("~", "null", "None"):
        return None
    low = token.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if (token[0] == token[-1]) and token[0] in ("'", '"') and len(token) >= 2:
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _strip_comment(line: str) -> str:
    # Only strips comments that are not inside quotes. Settings file has no
    # quoted `#`, so a simple split is safe here.
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the restricted YAML subset used by settings.yaml."""
    root: dict[str, Any] = {}
    # Stack of (indent, container) pairs.
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if ":" not in line:
            raise ValueError(f"Unsupported config line (no ':'): {raw!r}")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"Bad indentation in config near: {raw!r}")
        container = stack[-1][1]
        key, _, value = line.strip().partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            child: dict[str, Any] = {}
            container[key] = child
            stack.append((indent, child))
        else:
            container[key] = _coerce(value)
    return root


# --------------------------------------------------------------------------- #
# Frozen config dataclasses.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AccountConfig:
    total_capital_usd: float
    cash_buffer_usd: float


@dataclass(frozen=True)
class StrategyConfig:
    min_edge_high_liquidity: float
    min_edge_normal: float
    min_confidence: float
    child_order_count: int
    max_price_drift: float
    order_ttl_seconds: int


@dataclass(frozen=True)
class CostConfig:
    taker_fee_rate: float
    maker_fee_rate: float
    base_slippage: float
    exit_cost: float


@dataclass(frozen=True)
class SelectionConfig:
    min_liquidity_usd: float
    min_book_depth_usd: float
    max_spread: float
    min_price: float
    max_price: float
    max_days_to_resolution: int
    require_order_book: bool


@dataclass(frozen=True)
class SizingConfig:
    max_per_market: float
    max_per_order: float
    max_per_event: float
    max_total_open: float


@dataclass(frozen=True)
class CircuitBreakerConfig:
    daily_loss_halt_pct: float
    drawdown_halve_pct: float
    drawdown_halt_pct: float
    max_reconcile_diff_usd: float
    api_error_streak_halt: int


@dataclass(frozen=True)
class ExecutionConfig:
    mode: str  # must be "simulation" in this phase


@dataclass(frozen=True)
class Config:
    account: AccountConfig
    strategy: StrategyConfig
    costs: CostConfig
    selection: SelectionConfig
    sizing: SizingConfig
    circuit_breakers: CircuitBreakerConfig
    execution: ExecutionConfig

    def min_edge_for(self, high_liquidity: bool) -> float:
        return (
            self.strategy.min_edge_high_liquidity
            if high_liquidity
            else self.strategy.min_edge_normal
        )


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    data = parse_simple_yaml(Path(path).read_text(encoding="utf-8"))
    cfg = Config(
        account=AccountConfig(**data["account"]),
        strategy=StrategyConfig(**data["strategy"]),
        costs=CostConfig(**data["costs"]),
        selection=SelectionConfig(**data["selection"]),
        sizing=SizingConfig(**data["sizing"]),
        circuit_breakers=CircuitBreakerConfig(**data["circuit_breakers"]),
        execution=ExecutionConfig(**data["execution"]),
    )
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    a = cfg.account
    s = cfg.sizing
    if a.cash_buffer_usd >= a.total_capital_usd:
        raise ValueError("cash_buffer_usd must be less than total_capital_usd")
    if s.max_per_order > s.max_per_market:
        raise ValueError("max_per_order must not exceed max_per_market")
    if s.max_per_market > s.max_per_event:
        raise ValueError("max_per_market must not exceed max_per_event")
    if s.max_total_open > (a.total_capital_usd - a.cash_buffer_usd):
        raise ValueError("max_total_open exceeds capital minus cash buffer")
    if cfg.strategy.child_order_count < 1:
        raise ValueError("child_order_count must be >= 1")
    if not (0.0 <= cfg.strategy.min_confidence <= 1.0):
        raise ValueError("min_confidence must be in [0, 1]")
