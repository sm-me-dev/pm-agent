from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .enums import ActionStatus, ActionType, DecisionStatus, MemoryKind


def new_id() -> str:
    return uuid4().hex


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def decision_fingerprint(topic: str, title: str, decision: str) -> str:
    """Stable identity for a model-proposed decision.

    Used to detect the same decision re-proposed across REPL loop
    iterations so we do not prompt the user again.
    """
    parts = [topic.strip().lower(), title.strip().lower(), decision.strip().lower()]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def action_fingerprint(action_type: str, operation: str, payload_sha256: str) -> str:
    """Stable identity for an approval-worthy action proposal.

    Combines the action type, operation, and payload hash so repeated
    proposals of the same action in a session are recognized.
    """
    parts = [action_type.strip().lower(), operation.strip().lower(), payload_sha256]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    canonical_path: str
    repo_fingerprint: str
    default_branch: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Session:
    id: str
    project_id: str
    name: str
    model: str
    provider: str
    branch: str
    status: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class Message:
    id: str
    session_id: str
    sequence_no: int
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class Decision:
    id: str
    project_id: str
    session_id: str | None
    topic: str
    title: str
    decision: str
    reason: str
    status: DecisionStatus
    created_at: str
    updated_at: str
    supersedes_id: str | None = None
    fingerprint: str = ""


@dataclass(frozen=True)
class RepositoryNote:
    id: str
    project_id: str
    session_id: str | None
    category: str
    title: str
    content: str
    source_type: str
    source_ref: str | None
    confidence: float
    stale_after: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RepoSnapshot:
    id: str
    project_id: str
    branch: str
    head_ref: str | None
    tree_digest: str
    summary: dict[str, Any]
    created_at: str
    created_by_action_id: str | None = None


@dataclass(frozen=True)
class SessionSummary:
    id: str
    project_id: str
    session_id: str
    key_topics: list[str]
    decisions: list[str]
    planned_actions: list[str]
    open_questions: list[str]
    narrative: str
    created_at: str


@dataclass(frozen=True)
class ActionCandidate:
    action_type: ActionType
    tool_category: str
    operation: str
    reason: str
    impact: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ActionProposal:
    id: str
    project_id: str
    session_id: str
    action_type: ActionType
    tool_category: str
    operation: str
    reason: str
    impact: str
    payload: dict[str, Any]
    payload_sha256: str
    risk_level: str
    status: ActionStatus
    created_at: str
    expires_at: str | None = None
    fingerprint: str = ""


@dataclass(frozen=True)
class ApprovedAction:
    proposal: ActionProposal
    approved_payload_sha256: str
    approved_by: str
    approved_at: str


@dataclass(frozen=True)
class DispatchReceipt:
    correlation_id: str | None
    dispatched: bool
    message: str
    completed: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    deferred_external: bool = False
    error_category: str | None = None


@dataclass(frozen=True)
class IntegrationInfo:
    key: str
    name: str
    status: str
    authentication: str
    capabilities: list[str]
    setup_hint: str | None = None


@dataclass(frozen=True)
class ActionOutcome:
    action_id: str
    host_correlation_id: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    result: dict[str, Any]
    started_at: str | None
    completed_at: str | None
    recorded_at: str


@dataclass(frozen=True)
class DecisionCandidate:
    topic: str
    title: str
    decision: str
    reason: str
    status: DecisionStatus = DecisionStatus.PROPOSED


@dataclass(frozen=True)
class PMResponse:
    summary: str
    analysis: str
    risks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    decisions: list[DecisionCandidate] = field(default_factory=list)
    actions_requiring_approval: list[ActionCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryItem:
    kind: MemoryKind
    source_id: str
    title: str
    content: str
    created_at: str
    score: float


@dataclass(frozen=True)
class ContextPacket:
    items: list[MemoryItem]
    recent_messages: list[Message]
    repository_snapshot: RepoSnapshot | None


@dataclass(frozen=True)
class MemoryCounts:
    messages: int
    decisions: int
    repo_notes: int


def project_fingerprint(path: str | Path) -> str:
    canonical = str(Path(path).resolve())
    return hashlib.sha256(canonical.encode()).hexdigest()
