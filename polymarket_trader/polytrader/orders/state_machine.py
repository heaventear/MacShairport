"""Order state machine (spec 八、订单状态).

Encodes the legal transitions between order statuses so the Order Manager can
never move an order into an inconsistent state. UNKNOWN is reachable from any
non-terminal state (e.g. a timeout during status query) and, once entered,
forces reconciliation before new orders on the affected market.
"""

from __future__ import annotations

from ..models import OrderStatus as S

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.SUBMITTED, S.FAILED, S.UNKNOWN}),
    S.SUBMITTED: frozenset({S.LIVE, S.PARTIALLY_FILLED, S.FILLED, S.FAILED, S.UNKNOWN}),
    S.LIVE: frozenset(
        {S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.FAILED, S.UNKNOWN}
    ),
    S.PARTIALLY_FILLED: frozenset(
        {S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.FAILED, S.UNKNOWN}
    ),
    S.CANCEL_REQUESTED: frozenset({S.CANCELLED, S.FILLED, S.FAILED, S.UNKNOWN}),
    S.FILLED: frozenset(),
    S.CANCELLED: frozenset(),
    S.FAILED: frozenset(),
    # From UNKNOWN we may only resolve after reconciliation.
    S.UNKNOWN: frozenset({S.LIVE, S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.FAILED}),
}


def can_transition(src: S, dst: S) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, frozenset())
