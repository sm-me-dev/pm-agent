from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Coarse classification of an action/runtime failure.

    Drives whether the agent may attempt to recover on its own or whether the
    run should halt and ask the user to intervene.
    """

    AGENT_FIXABLE = "agent_fixable"
    USER_ACTION_REQUIRED = "user_action_required"
    FATAL = "fatal"


@dataclass
class ActionError:
    """Structured, concise error context safe to feed back into the model loop."""

    category: ErrorCategory
    message: str
    agent_fixable: bool
    retryable: bool
    action_type: str = ""
    operation: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    user_guidance: str = ""

    def to_event(self) -> str:
        stderr = (self.stderr or "")[:400]
        return (
            "[action_error]\n"
            f"Action: {self.operation}\n"
            f"Type: {self.action_type}\n"
            f"Status: failed (exit {self.exit_code})\n"
            f"Category: {self.category.value}\n"
            f"Agent-fixable: {str(self.agent_fixable).lower()}\n"
            f"Retryable: {str(self.retryable).lower()}\n"
            f"Message: {self.message}\n"
            f"Stderr: {stderr}"
        )


_DEPENDENCY_CATEGORIES = frozenset(
    {"missing_dependency", "missing_scope", "external_dependency"}
)

_EXTERNAL_PATTERNS = (
    "missing required scopes",
    "permission denied",
    "unauthorized",
    "forbidden",
    "rate limit",
    "ratelimit",
    "timed out",
    "timeout",
    "econnrefused",
    "enotfound",
    "connection reset",
    "network is unreachable",
    "could not resolve",
    "no route to host",
)

_FATAL_PATTERNS = (
    "internal error",
    "unexpected error",
    "panic",
)


def classify_action_error(receipt, action=None) -> ActionError:
    """Classify a failed dispatch receipt into a structured ActionError.

    Conservative defaults: anything that looks like a missing dependency,
    permission/scope problem, network/timeout, or auth failure is treated as
    requiring user action (the agent must not loop on it). A plain non-zero
    exit is assumed to be a fixable plan/argument issue the model can revise.
    """
    category = getattr(receipt, "error_category", None)
    message = receipt.message or ""
    stderr = receipt.stderr or ""
    stdout = receipt.stdout or ""
    action_type = action.action_type.value if action else ""
    operation = action.operation if action else ""
    exit_code = receipt.exit_code
    text = f"{message}\n{stderr}".lower()

    if category in _DEPENDENCY_CATEGORIES:
        return ActionError(
            ErrorCategory.USER_ACTION_REQUIRED,
            message or f"Missing dependency for {operation}.",
            agent_fixable=False,
            retryable=False,
            action_type=action_type,
            operation=operation,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            user_guidance=(
                f"{message}\nInstall or configure the required dependency, then retry."
            ),
        )

    if any(pattern in text for pattern in _EXTERNAL_PATTERNS):
        return ActionError(
            ErrorCategory.USER_ACTION_REQUIRED,
            message or f"External/permission failure for {operation}.",
            agent_fixable=False,
            retryable=False,
            action_type=action_type,
            operation=operation,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            user_guidance=(
                f"{message}\nThis looks like an environment, permission, or network "
                f"issue the agent cannot fix. Resolve it manually, then continue."
            ),
        )

    if any(pattern in text for pattern in _FATAL_PATTERNS):
        return ActionError(
            ErrorCategory.FATAL,
            message or "Unexpected internal error.",
            agent_fixable=False,
            retryable=False,
            action_type=action_type,
            operation=operation,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            user_guidance=f"{message}\nPlease report this and retry.",
        )

    return ActionError(
        ErrorCategory.AGENT_FIXABLE,
        message or f"Action {operation} failed (exit {exit_code}).",
        agent_fixable=True,
        retryable=True,
        action_type=action_type,
        operation=operation,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )
