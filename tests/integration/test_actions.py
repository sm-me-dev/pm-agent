from __future__ import annotations

import pytest

from pm_agent.application.action_service import ActionService
from pm_agent.domain.enums import ActionStatus, ActionType
from pm_agent.domain.models import ActionCandidate
from pm_agent.infrastructure.hosts import StandaloneHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore


def setup(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    return store, project, session, ActionService(store, StandaloneHostBridge())


def test_standalone_approval_never_executes(tmp_path):
    store, project, session, service = setup(tmp_path)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GIT,
            "git",
            "status",
            "Confirm branch.",
            "Read only.",
            {"command": "git status"},
        ),
    )
    receipt = service.approve(proposal.id)
    stored = store.get_action(proposal.id)
    assert not receipt.dispatched
    assert stored.status is ActionStatus.APPROVED


def test_blocked_proposal_is_audited_and_rejected(tmp_path):
    store, project, session, service = setup(tmp_path)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GIT,
            "git",
            "push",
            "Push work.",
            "Mutates remote.",
            {"command": "git push"},
        ),
    )
    assert proposal.status is ActionStatus.REJECTED
    assert len(store.list_actions(project.id)) == 1


def test_rejected_action_cannot_be_approved_and_can_be_retried(tmp_path):
    store, project, session, service = setup(tmp_path)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "read_repository",
            "Inspect repository.",
            "Read metadata.",
            {},
        ),
    )
    assert proposal.status is ActionStatus.REJECTED

    with pytest.raises(ValueError, match="cannot be approved"):
        service.approve(proposal.id)

    retried = service.retry(proposal.id, session.id)
    assert retried.id != proposal.id
    assert retried.status is ActionStatus.REJECTED
    assert len(store.list_actions(project.id)) == 2


def test_retry_re_evaluates_old_rejected_action_under_current_policy(tmp_path):
    store, project, session, service = setup(tmp_path)
    old = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "read_repository",
            "Inspect repository.",
            "Read metadata.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )
    store.transition_action(old.id, ActionStatus.REJECTED, "test")

    retried = service.retry(old.id, session.id)

    assert retried.id != old.id
    assert retried.status is ActionStatus.PROPOSED


def test_approved_action_cannot_be_approved_twice(tmp_path):
    store, project, session, service = setup(tmp_path)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GIT,
            "git",
            "status",
            "Confirm branch.",
            "Read only.",
            {"command": "git status"},
        ),
    )
    service.approve(proposal.id)

    with pytest.raises(ValueError, match="cannot be approved"):
        service.approve(proposal.id)


def test_external_outcome_is_idempotent(tmp_path):
    store, project, session, service = setup(tmp_path)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GIT,
            "git",
            "status",
            "Confirm branch.",
            "Read only.",
            {"command": "git status"},
        ),
    )
    service.approve(proposal.id)
    first = service.record_outcome(proposal.id, 0, stdout="clean")
    second = store.record_outcome(
        __import__("pm_agent.domain.models", fromlist=["ActionOutcome"]).ActionOutcome(
            action_id=proposal.id,
            host_correlation_id=None,
            exit_code=0,
            stdout="duplicate",
            stderr="",
            result={},
            started_at=None,
            completed_at=None,
            recorded_at="2026-01-01",
        )
    )
    assert first.status is ActionStatus.SUCCEEDED
    assert second.status is ActionStatus.SUCCEEDED
