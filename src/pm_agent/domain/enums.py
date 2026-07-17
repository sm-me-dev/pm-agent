from __future__ import annotations

from enum import StrEnum


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"


class ActionType(StrEnum):
    BASH = "bash"
    GIT = "git"
    GITHUB = "github"
    MCP = "mcp"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class MemoryKind(StrEnum):
    DECISION = "decision"
    SUMMARY = "summary"
    REPO_NOTE = "repo_note"
    ACTION_OUTCOME = "action_outcome"
    MESSAGE = "message"
