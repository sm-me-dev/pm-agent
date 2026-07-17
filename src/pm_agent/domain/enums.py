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


class TaskClass(StrEnum):
    """Classification of a task the agent is asked to perform.

    Used to decide whether the agent should act autonomously, ask the user a
    narrow question, or report a missing external capability. See
    ``pm_agent.application.decision_policy`` for how it is applied.
    """

    AGENT_EXECUTABLE = "agent_executable"
    AGENT_EXECUTABLE_WITH_ASSUMPTIONS = "agent_executable_with_assumptions"
    USER_DECISION_REQUIRED = "user_decision_required"
    EXTERNAL_ACCESS_REQUIRED = "external_access_required"
