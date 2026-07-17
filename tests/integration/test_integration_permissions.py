from __future__ import annotations

from types import SimpleNamespace

from pm_agent.application.action_service import ActionService
from pm_agent.domain.enums import ActionType
from pm_agent.domain.errors import ErrorCategory, classify_action_error
from pm_agent.domain.models import ActionCandidate, DispatchReceipt
from pm_agent.infrastructure.hosts import IntegrationHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore


def _scopes_output(*scopes: str) -> str:
    listed = ", ".join(f"'{s}'" for s in scopes)
    return (
        "github.com\n"
        f"  ✓ Logged in to github.com account u (keyring)\n"
        f"  - Token scopes: {listed}\n"
    )


def _run_with_scopes(monkeypatch, scopes, action_run=None):
    auth_output = _scopes_output(*scopes)

    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=auth_output, stderr="")
        return action_run(command, **kwargs) if action_run else SimpleNamespace(
            returncode=0, stdout="[]", stderr=""
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )


def _propose_and_approve(tmp_path, monkeypatch, scopes, operation, payload, action_run=None):
    _run_with_scopes(monkeypatch, scopes, action_run)
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id, session.id,
        ActionCandidate(ActionType.GITHUB, "github", operation, "r", "i", payload),
    )
    return service.approve(proposal.id)


def test_write_action_with_sufficient_scopes_proceeds(tmp_path, monkeypatch):
    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo",),
        "create_milestone",
        {"repository": "o/r", "milestone": {"title": "v1.0"}},
    )
    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert receipt.error_category is None


def test_read_action_with_sufficient_scopes_proceeds(tmp_path, monkeypatch):
    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo",),
        "list_issues",
        {"repository": "o/r"},
    )
    assert receipt.dispatched
    assert receipt.exit_code == 0


def test_missing_project_scope_halts_with_clear_message(tmp_path, monkeypatch):
    # list_projects needs read:project; token only has repo.
    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo",),
        "list_projects",
        {"repository": "o/r"},
    )
    assert receipt.dispatched
    assert receipt.exit_code == 1
    assert receipt.error_category == "missing_scope"
    assert "read:project" in receipt.message
    assert "gh auth refresh -s read:project" in receipt.message


def test_missing_project_write_scope_halts_for_create_project(tmp_path, monkeypatch):
    # create_project needs the `project` scope (write_projects), not just read:project.
    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo", "read:project"),
        "create_project",
        {"repository": "o/r", "name": "Board"},
    )
    assert receipt.dispatched
    assert receipt.exit_code == 1
    assert receipt.error_category == "missing_scope"
    assert "project" in receipt.message


def test_runtime_403_is_classified_as_missing_scope(tmp_path, monkeypatch):
    def action_run(command, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout="",
            stderr="HTTP 403 Forbidden: resource is not accessible by the token",
        )

    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo",),
        "list_issues",
        {"repository": "o/r"},
        action_run=action_run,
    )
    assert receipt.exit_code == 1
    assert receipt.error_category == "missing_scope"
    error = classify_action_error(receipt)
    assert error.category is ErrorCategory.USER_ACTION_REQUIRED
    assert error.agent_fixable is False
    assert error.retryable is False


def test_runtime_missing_scope_classified_user_action_required(tmp_path, monkeypatch):
    def action_run(command, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout="",
            stderr="error: your authentication token is missing required scopes [read:project]",
        )

    receipt = _propose_and_approve(
        tmp_path, monkeypatch, ("repo",),
        "list_projects",
        {"repository": "o/r"},
        action_run=action_run,
    )
    error = classify_action_error(receipt)
    assert error.category is ErrorCategory.USER_ACTION_REQUIRED
    assert error.retryable is False


def test_missing_scope_receipt_halts_repl_loop(tmp_path, monkeypatch):
    # A missing_scope failure must halt the agent loop, never trigger recovery.
    from pm_agent.presentation.repl import PMAgentREPL
    from pm_agent.ports.host import HostCapabilities
    from rich.console import Console
    from io import StringIO

    store = SQLiteStore(tmp_path / "s.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")

    class FakeHost:
        def capabilities(self):
            return HostCapabilities(can_dispatch=True, supported_categories=frozenset({"github"}))

        def dispatch(self, action):
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                error_category="missing_scope",
                message="GitHub action 'list_projects' requires capabilities [read_projects].",
                stderr="missing required scopes [read:project]",
            )

    service = ActionService(store, FakeHost())
    proposal = service.propose(
        project.id, session.id,
        ActionCandidate(ActionType.GITHUB, "github", "list_projects", "r", "i",
                        {"repository": "o/r"}),
    )
    repl = PMAgentREPL.__new__(PMAgentREPL)
    repl.console = Console(file=StringIO(), force_terminal=False, width=100)
    repl._error_logger = None
    repl._recovery_attempts = 0
    repl._halt_requested = False
    action = store.get_action(proposal.id)
    receipt = FakeHost().dispatch(None)
    event = repl._handle_failed_dispatch(action, receipt)
    assert repl._halt_requested is True
    assert event == ""
