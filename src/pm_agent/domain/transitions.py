from __future__ import annotations

from .enums import ActionStatus

_ALLOWED: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PROPOSED: {ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.EXPIRED},
    ActionStatus.APPROVED: {ActionStatus.DISPATCHED, ActionStatus.REJECTED, ActionStatus.EXPIRED},
    ActionStatus.DISPATCHED: {ActionStatus.SUCCEEDED, ActionStatus.FAILED},
    ActionStatus.REJECTED: set(),
    ActionStatus.SUCCEEDED: set(),
    ActionStatus.FAILED: set(),
    ActionStatus.EXPIRED: set(),
}


def ensure_action_transition(current: ActionStatus, target: ActionStatus) -> None:
    if target not in _ALLOWED[current]:
        raise ValueError(f"Invalid action transition: {current.value} -> {target.value}")
