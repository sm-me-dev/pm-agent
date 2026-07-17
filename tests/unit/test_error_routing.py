from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from pm_agent.application.action_service import ActionService
from pm_agent.domain.enums import ActionStatus, ActionType
from pm_agent.domain.errors import ErrorCategory, classify_action_error
from pm_agent.domain.models import (
    ActionCandidate,
    ActionProposal,
    DispatchReceipt,
    Project,
    Session,
)
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.ports.host import HostCapabilities
from pm_agent.presentation.repl import MAX_RECOVERY_ATTEMPTS, PMAgentREPL, REPLServices


# --- classify_action_error -------------------------------------------------


def _receipt(**kw) -> DispatchReceipt:
    base = dict(
        correlation_id=None,
        dispatched=True,
        message="",
        completed=True,
        exit_code=1,
        stdout="",
        stderr="",
        result={},
        deferred_external=False,
        error_category=None,
    )
    base.update(kw)
    return DispatchReceipt(**base)


def test_missing_dependency_is_user_action_required():
    err = classify_action_error(_receipt(message="gh not installed", error_category="missing_dependency"))
    assert err.category is ErrorCategory.USER_ACTION_REQUIRED
    assert err.agent_fixable is False
    assert err.retryable is False


def test_scope_error_text_is_user_action_required():
    err = classify_action_error(_receipt(stderr="missing required scopes [project]"))
    assert err.category is ErrorCategory.USER_ACTION_REQUIRED


def test_permission_denied_is_user_action_required():
    err = classify_action_error(_receipt(message="fatal: permission denied"))
    assert err.category is ErrorCategory.USER_ACTION_REQUIRED


def test_generic_nonzero_exit_is_agent_fixable():
    err = classify_action_error(_receipt(message="command failed", stderr="boom: bad arg"))
    assert err.category is ErrorCategory.AGENT_FIXABLE
    assert err.agent_fixable is True
    assert err.retryable is True


def test_event_includes_classification():
    err = classify_action_error(_receipt(message="command failed", stderr="boom"))
    event = err.to_event()
    assert "[action_error]" in event
    assert "Agent-fixable: true" in event


# --- REPL routing ----------------------------------------------------------


def _make_repl(store, error_logger=None) -> PMAgentREPL:
    project = Project(
        id="p", name="p", canonical_path=str(Path("/tmp")),
        repo_fingerprint="", default_branch=None, created_at="", updated_at="",
    )
    session = Session(
        id="s", project_id="p", name="s", model="m", provider="unknown",
        branch="main", status="active", started_at="",
    )
    services = REPLServices(
        store=store, conversation=None, actions=None, decisions=None,
        integrations=None, context=None, sessions=None, summaries=None,
    )
    console = Console(file=StringIO(), force_terminal=False, width=100)
    repl = PMAgentREPL(console, project, session, services, model="m")
    repl._error_logger = error_logger
    return repl


def _action() -> ActionProposal:
    return ActionProposal(
        id="a1", project_id="p", session_id="s",
        action_type=ActionType.GITHUB, tool_category="github",
        operation="create_issue", reason="r", impact="i",
        payload={"repository": "x/y"}, payload_sha256="z", risk_level="low",
        status=ActionStatus.PROPOSED, created_at="2026-01-01",
    )


def test_external_error_halts_and_does_not_loop(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    repl = _make_repl(store)
    action = _action()
    receipt = _receipt(
        message="gh missing scopes", stderr="missing required scopes [project]",
        error_category="missing_dependency",
    )
    event = repl._handle_failed_dispatch(action, receipt)
    assert repl._halt_requested is True
    assert event == ""


def test_agent_fixable_error_allows_recovery(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    repl = _make_repl(store)
    action = _action()
    receipt = _receipt(message="command failed", stderr="boom")
    event = repl._handle_failed_dispatch(action, receipt)
    assert repl._halt_requested is False
    assert "[action_error]" in event
    assert "Agent-fixable: true" in event


def test_repeated_failure_halts_after_cap(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    repl = _make_repl(store)
    action = _action()
    receipt = _receipt(message="command failed", stderr="boom")
    for _ in range(MAX_RECOVERY_ATTEMPTS + 1):
        repl._handle_failed_dispatch(action, receipt)
    assert repl._halt_requested is True


def test_approve_external_failure_halts_and_logs_category(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")

    class FakeHostBridge:
        def capabilities(self):
            return HostCapabilities(can_dispatch=True, supported_categories=frozenset({"github"}))

        def dispatch(self, action):
            return _receipt(
                message="missing scopes", stderr="missing required scopes [project]",
                error_category="missing_dependency",
            )

    service = ActionService(store, FakeHostBridge())
    proposal = service.propose(
        project.id, session.id,
        ActionCandidate(ActionType.GITHUB, "github", "read_repository", "r", "i",
                        {"repository": "x/y"}),
    )

    class FakeErrorLogger:
        def __init__(self):
            self.entries = []

        @property
        def path(self):
            return Path("/tmp/none")

        def log_failure(self, **kwargs):
            self.entries.append(kwargs)

    logger = FakeErrorLogger()
    repl = _make_repl(store, error_logger=logger)
    repl.services.actions = service

    event = repl._approve_and_continue(proposal)
    assert repl._halt_requested is True
    assert event == ""
    assert logger.entries[-1]["category"] == "missing_dependency"
