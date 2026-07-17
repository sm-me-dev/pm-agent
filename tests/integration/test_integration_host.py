from __future__ import annotations

import json
from types import SimpleNamespace

from pm_agent.application.action_service import ActionService
from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ActionCandidate
from pm_agent.infrastructure.hosts import IntegrationHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore

_SCOPES_OUTPUT = (
    "github.com\n"
    "  ✓ Logged in to github.com account test-user (keyring)\n"
    "  - Token scopes: 'admin:public_key', 'gist', 'read:org', 'read:project', 'repo'\n"
)


def _make_fake_run(default_rc=0, default_stdout="", default_stderr=""):
    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=_SCOPES_OUTPUT, stderr="")
        return SimpleNamespace(returncode=default_rc, stdout=default_stdout, stderr=default_stderr)
    return fake_run


def test_approved_github_browser_auth_is_executed_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "api" in command:
            return SimpleNamespace(returncode=0, stdout="sm-me-dev\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "authenticate_browser",
            "Connect GitHub.",
            "Opens browser authentication.",
            {
                "hostname": "github.com",
                "git_protocol": "https",
                "browser": True,
            },
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.completed
    assert receipt.result["account"] == "sm-me-dev"
    assert store.get_action(proposal.id).status.value == "succeeded"
    assert calls[0][1:4] == ["auth", "login", "--hostname"]


def test_cancelled_browser_auth_is_recorded_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        cancel,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "authenticate_browser",
            "Connect GitHub.",
            "Opens browser authentication.",
            {"hostname": "github.com", "git_protocol": "https"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.exit_code == 130
    assert store.get_action(proposal.id).status.value == "failed"


def test_github_list_issues_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='[{"number":1,"title":"Fix bug","state":"open"}]',
            stderr="",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_issues",
            "See open issues.",
            "Read-only issue list.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert len(receipt.result) > 0
    assert receipt.result[0]["number"] == 1
    assert store.get_action(proposal.id).status.value == "succeeded"
    assert any("issue" in c and "list" in c for c in calls)


def test_github_inspect_repository_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"name":"repo","defaultBranch":"main"}',
            stderr="",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "inspect_repository",
            "Inspect the repo.",
            "Read-only repo inspection.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert receipt.result["name"] == "repo"
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_read_fails_when_gh_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: None,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_milestones",
            "See milestones.",
            "Read-only milestone list.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 1
    assert store.get_action(proposal.id).status.value == "failed"


def test_github_read_command_failure_surfaces_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="gh: Not logged in (run gh auth login)",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_issues",
            "See open issues.",
            "Read-only issue list.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 1
    assert "failed" in receipt.message
    assert "Not logged in" in receipt.stderr
    assert store.get_action(proposal.id).status.value == "failed"


def test_multiple_github_reads_are_all_dispatched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())

    operations = ["list_issues", "list_milestones", "list_projects", "list_releases"]
    for op in operations:
        proposal = service.propose(
            project.id,
            session.id,
            ActionCandidate(
                ActionType.GITHUB,
                "github",
                op,
                f"See {op}.",
                f"Read-only {op}.",
                {"repository": "sm-me-dev/unified-workspace-engine"},
            ),
        )
        receipt = service.approve(proposal.id)

        assert receipt.dispatched, f"{op} was not dispatched"
        assert receipt.completed, f"{op} was not completed"
        assert store.get_action(proposal.id).status.value in {"succeeded", "failed"}, (
            f"{op} ended in unexpected status"
        )


def test_github_read_dispatched_regardless_of_tool_category(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())

    for tc in ("github", "github_read", "issues", "repository", ""):
        proposal = service.propose(
            project.id,
            session.id,
            ActionCandidate(
                ActionType.GITHUB,
                tc,
                "list_issues",
                f"See issues.",
                f"Read-only.",
                {"repository": "sm-me-dev/unified-workspace-engine"},
            ),
        )
        receipt = service.approve(proposal.id)
        assert receipt.dispatched, (
            f"list_issues with tool_category={tc!r} was not dispatched"
        )
        assert receipt.completed, (
            f"list_issues with tool_category={tc!r} was not completed"
        )
        assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_list_projects_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        _make_fake_run(
            default_stdout='[{"number":1,"title":"Sprint Board","state":"open"}]',
        ),
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_projects",
            "See projects.",
            "Read-only project list.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert receipt.result[0]["title"] == "Sprint Board"
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_read_missing_scopes_shows_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "error: your authentication token is missing required scopes [read:project]\n"
                "To request it, run:  gh auth refresh -s read:project"
            ),
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_projects",
            "See projects.",
            "Read-only project list.",
            {"repository": "sm-me-dev/unified-workspace-engine"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.exit_code == 1
    assert "gh auth refresh -s read:project" in receipt.message


def test_github_create_milestone_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=_SCOPES_OUTPUT, stderr="")
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='{"number":1,"title":"v1.0","html_url":"https://github.com/..."}',
            stderr="",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_milestone",
            "Create a milestone.",
            "Creates a milestone on GitHub.",
            {"repository": "sm-me-dev/unified-workspace-engine", "milestone": {"title": "v1.0"}},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert receipt.result.get("number") == 1
    assert store.get_action(proposal.id).status.value == "succeeded"
    assert "api" in calls[0]
    assert any("milestones" in arg for arg in calls[0])


def test_github_create_issue_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        _make_fake_run(
            default_stdout='{"number":42,"title":"Fix bug","html_url":"https://github.com/..."}',
        ),
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_issue",
            "Create an issue.",
            "Creates an issue on GitHub.",
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                "issue": {"title": "Fix bug", "body": "Need to fix the bug."},
            },
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert receipt.result.get("number") == 42
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_create_issues_batch_is_dispatched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    call_count = [0]

    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=_SCOPES_OUTPUT, stderr="")
        call_count[0] += 1
        idx = call_count[0]
        return SimpleNamespace(
            returncode=0,
            stdout=f'{{"number":{idx},"title":"Issue {idx}","html_url":"..."}}',
            stderr="",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_issues",
            "Create multiple issues.",
            "Creates issues on GitHub.",
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                "issues": [
                    {"title": "Issue 1", "body": "First issue."},
                    {"title": "Issue 2", "body": "Second issue."},
                ],
            },
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert len(receipt.result.get("created", [])) == 2
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_create_issues_partial_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    call_count = [0]

    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=_SCOPES_OUTPUT, stderr="")
        call_count[0] += 1
        if call_count[0] == 1:
            return SimpleNamespace(returncode=0, stdout='{"number":1,"title":"Good"}', stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="API error on second issue")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_issues",
            "Create issues.",
            "Creates issues on GitHub.",
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                "issues": [
                    {"title": "Good", "body": "Works."},
                    {"title": "Bad", "body": "Fails."},
                ],
            },
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 1
    assert len(receipt.result.get("created", [])) == 1
    assert len(receipt.result.get("errors", [])) == 1


def test_github_setup_sprint_is_dispatched_and_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        _make_fake_run(default_stdout='{"number":1,"title":"Sprint 1"}'),
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "setup_sprint",
            "Set up sprint.",
            "Creates sprint milestone.",
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                "sprint": {
                    "title": "Sprint 1",
                    "goal": "Complete feature X",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-14",
                },
            },
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_create_project_is_dispatched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    # Creating a project requires the `project` scope (write_projects capability).
    _PROJECT_SCOPES = _SCOPES_OUTPUT.replace("'repo'", "'repo', 'project'")

    def fake_run(command, **kwargs):
        if "auth" in command and "status" in command:
            return SimpleNamespace(returncode=0, stdout=_PROJECT_SCOPES, stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="Created project 'My Board'.",
            stderr="",
        )

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_project",
            "Create a project.",
            "Creates a GitHub project.",
            {"repository": "sm-me-dev/unified-workspace-engine", "name": "My Board"},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 0
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_github_write_fails_when_gh_not_available(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: None,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = ActionService(store, IntegrationHostBridge())
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_milestone",
            "Create milestone.",
            "Creates milestone.",
            {"repository": "sm-me-dev/unified-workspace-engine", "milestone": {"title": "v1.0"}},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert receipt.exit_code == 1
    assert store.get_action(proposal.id).status.value == "failed"


# --- Dispatch routing robustness tests ---

def test_known_github_op_with_wrong_type_is_not_routed_to_github_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    host = IntegrationHostBridge()
    service = ActionService(store, host)

    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.MCP,
            "filesystem",
            "inspect_repository",
            "Inspect with wrong type.",
            "Testing dispatch routing.",
            {"path": "/some/path"},
        ),
    )
    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.exit_code != 0
    assert not calls  # gh CLI was not called!
    action = store.get_action(proposal.id)
    assert action.status.value == "failed"


def test_github_type_action_is_routed_to_github_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    host = IntegrationHostBridge()
    service = ActionService(store, host)

    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "list_issues",
            "List issues.",
            "Read-only.",
            {"repository": "owner/repo"},
        ),
    )
    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.completed
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_unknown_operation_with_wrong_type_falls_to_standalone(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    host = IntegrationHostBridge()
    service = ActionService(store, host)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.MCP,
            "filesystem",
            "some_unknown_op",
            "Unknown op.",
            "Testing dispatch routing.",
            {},
        ),
    )

    receipt = service.approve(proposal.id)

    assert not receipt.dispatched, "Unknown op with wrong type should not dispatch"
    assert store.get_action(proposal.id).status.value == "approved"


def test_write_milestone_action_failure_logs_to_error_logger(tmp_path, monkeypatch):
    log_path = tmp_path / "errors.jsonl"
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="API error: milestone already exists")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )

    from pm_agent.application.error_logger import ErrorLogger

    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    error_logger = ErrorLogger(log_path)
    host = IntegrationHostBridge(error_logger=error_logger)
    service = ActionService(store, host)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_milestone",
            "Create milestone.",
            "Creates milestone.",
            {"repository": "owner/repo", "milestone": {"title": "v1.0"}},
        ),
    )

    receipt = service.approve(proposal.id)

    assert receipt.dispatched
    assert receipt.exit_code == 1
    assert store.get_action(proposal.id).status.value == "failed"

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    last = json.loads(lines[-1])
    assert last["action_id"] == proposal.id
    assert last["category"] == "action_failure"
    assert last["exit_code"] == 1


def test_error_logger_entry_redacts_tokens(tmp_path, monkeypatch):
    log_path = tmp_path / "errors.jsonl"
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="token ghp_abc123def456 expired")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )

    from pm_agent.application.error_logger import ErrorLogger

    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    error_logger = ErrorLogger(log_path)
    host = IntegrationHostBridge(error_logger=error_logger)
    service = ActionService(store, host)
    proposal = service.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.GITHUB,
            "github",
            "create_milestone",
            "Create milestone.",
            "Creates milestone.",
            {"repository": "owner/repo", "milestone": {"title": "v1.0"}},
        ),
    )

    service.approve(proposal.id)

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    last = json.loads(lines[-1])
    assert "ghp_abc123def456" not in last.get("error", "")
    assert "ghp_abc123def456" not in last.get("stack_trace", "")
