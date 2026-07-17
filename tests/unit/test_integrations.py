from __future__ import annotations

from pm_agent.application.action_service import ActionService
from pm_agent.application.integration_service import IntegrationService, _GitHubAuth
from pm_agent.infrastructure.hosts import StandaloneHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore


def test_registry_lists_applicable_integrations(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.application.integration_service.shutil.which",
        lambda executable: f"/usr/bin/{executable}" if executable in {"gh", "git"} else None,
    )
    monkeypatch.setattr(
        "pm_agent.application.integration_service._check_gh_auth",
        lambda: _GitHubAuth("available", "GitHub CLI installed; not logged in.", None),
    )
    (tmp_path / ".git").mkdir()
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    service = IntegrationService(store, ActionService(store, StandaloneHostBridge()))

    integrations = service.list(project.id, str(tmp_path))

    assert [item.key for item in integrations] == [
        "filesystem",
        "git",
        "github",
        "graphify",
        "sequential-thinking",
    ]
    github = next(item for item in integrations if item.key == "github")
    assert github.status == "available"


def test_local_cat_reads_file(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello World")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {test_file}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.dispatched
    assert receipt.exit_code == 0
    assert "Hello World" in receipt.stdout


def test_local_cat_rejects_outside_path(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {outside}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 1
    assert "outside the allowed" in receipt.message


def test_local_ls_lists_directory(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "subdir").mkdir()
    (tmp_path / "file_a.txt").write_text("a")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="ls", reason="test", impact="test",
        payload={"command": f"ls {tmp_path}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "subdir/" in receipt.stdout


def test_local_cat_rejects_oversized_file(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    big = tmp_path / "big.txt"
    big.write_text("x" * 600_000)
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {big}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 1
    assert "too large" in receipt.message


def test_unsupported_bash_command_falls_to_standalone(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="rm", reason="test", impact="test",
        payload={"command": "rm -rf /"},
        payload_sha256="h", risk_level="high", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert not receipt.dispatched
    assert receipt.exit_code is None


def test_bash_action_routed_locally_not_to_github(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    test_file = tmp_path / "readme.md"
    test_file.write_text("# Project")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {test_file}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.dispatched
    assert receipt.exit_code == 0
    assert "# Project" in receipt.stdout


def test_bash_action_with_empty_tool_category_routed_locally(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    test_file = tmp_path / "notes.txt"
    test_file.write_text("content")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {test_file}"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.dispatched
    assert receipt.exit_code == 0


def test_github_action_still_routed_to_github_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "pm_agent.infrastructure.hosts.integrations.subprocess.run",
        fake_run,
    )
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.GITHUB, tool_category="github",
        operation="list_issues", reason="test", impact="test",
        payload={"repository": "owner/repo"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.dispatched
    assert receipt.exit_code == 0


def test_glob_expansion_cat_multiple_files(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "a.py").write_text("file a")
    (tmp_path / "b.py").write_text("file b")
    (tmp_path / "c.md").write_text("readme")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {tmp_path}/*.py"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "file a" in receipt.stdout
    assert "file b" in receipt.stdout
    assert "readme" not in receipt.stdout


def test_glob_expansion_no_matches_error(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="cat", reason="test", impact="test",
        payload={"command": f"cat {tmp_path}/*.nonexistent"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 1
    assert "No matches for glob" in receipt.message


def test_grep_search_basic(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def main():\n    pass")
    (src / "util.py").write_text("def helper():\n    pass")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="grep", reason="test", impact="test",
        payload={"command": f"grep -r 'def ' {tmp_path}/src"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "app.py:def main():" in receipt.stdout or "app.py:    def main():" in receipt.stdout
    assert "util.py:def helper():" in receipt.stdout or "util.py:    def helper():" in receipt.stdout


def test_grep_case_insensitive(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "readme.md").write_text("Hello World\n")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="grep", reason="test", impact="test",
        payload={"command": f"grep -i -r 'hello' {tmp_path}/readme.md"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "Hello" in receipt.stdout


def test_grep_no_match(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "readme.md").write_text("Hello World\n")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="grep", reason="test", impact="test",
        payload={"command": f"grep 'nonexistent' {tmp_path}/readme.md"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert receipt.stdout == "" or "Found 0" in receipt.message


def test_find_with_name_filter(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "main.py").write_text("x")
    (tmp_path / "util.py").write_text("x")
    (tmp_path / "readme.md").write_text("x")
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="find", reason="test", impact="test",
        payload={"command": f"find {tmp_path} -name '*.py'"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "main.py" in receipt.stdout
    assert "util.py" in receipt.stdout
    assert "readme.md" not in receipt.stdout


def test_find_with_type_file(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="find", reason="test", impact="test",
        payload={"command": f"find {tmp_path} -type f"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "a.txt" in receipt.stdout
    assert "sub" not in receipt.stdout


def test_find_with_type_directory(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="find", reason="test", impact="test",
        payload={"command": f"find {tmp_path} -type d"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    assert receipt.exit_code == 0
    assert "sub/" in receipt.stdout
    assert "a.txt" not in receipt.stdout


def test_extract_command_paths_grep():
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    assert IntegrationHostBridge._extract_command_paths("grep", ["grep", "-r", "pattern", "src/"]) == ["src/"]
    assert IntegrationHostBridge._extract_command_paths("grep", ["grep", "pattern"]) == ["."]
    assert IntegrationHostBridge._extract_command_paths("grep", ["grep", "-i", "-n", "pattern", "dir1", "dir2"]) == ["dir1", "dir2"]
    assert IntegrationHostBridge._extract_command_paths("rg", ["rg", "pattern", "."]) == ["."]
    assert IntegrationHostBridge._extract_command_paths("cat", ["cat", "file.txt"]) == ["file.txt"]


def test_glob_expansion_static():
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    # Non-glob paths pass through unchanged (relative becomes absolute via resolve)
    result = IntegrationHostBridge._expand_globs(["/tmp/exact.txt"])
    assert len(result) == 1
    assert str(result[0]) == "/tmp/exact.txt"

    # Globs that don't match raise error
    import pytest
    with pytest.raises(FileNotFoundError, match="No matches for glob"):
        IntegrationHostBridge._expand_globs(["/nonexistent/*.xyz"])


def test_git_command_falls_to_standalone_not_rejected_by_local_executor(tmp_path):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.infrastructure.hosts.integrations import IntegrationHostBridge

    proposal = ActionProposal(
        id="p1", project_id="p1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem",
        operation="git", reason="test", impact="test",
        payload={"command": "git status"},
        payload_sha256="h", risk_level="low", status="approved", created_at="now",
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="h",
                              approved_by="user", approved_at="now")
    host = IntegrationHostBridge(repo_root=str(tmp_path))
    receipt = host.dispatch(approved)
    # Should NOT be rejected by local executor (exit_code=1 with "not a supported")
    # Should fall through to standalone (dispatched=False, exit_code=None)
    assert not receipt.dispatched
    assert receipt.exit_code is None



def test_connect_github_creates_audited_authentication_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pm_agent.application.integration_service.shutil.which",
        lambda executable: "/usr/bin/gh" if executable == "gh" else None,
    )
    monkeypatch.setattr(
        "pm_agent.application.integration_service._check_gh_auth",
        lambda: _GitHubAuth("available", "GitHub CLI installed; not logged in.", None),
    )
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    service = IntegrationService(store, ActionService(store, StandaloneHostBridge()))

    proposal = service.propose_connect_github(project.id, session.id)

    assert proposal.operation == "authenticate_browser"
    assert proposal.status.value == "proposed"
    assert "token" not in proposal.payload
    assert store.list_actions(project.id)[0].id == proposal.id
