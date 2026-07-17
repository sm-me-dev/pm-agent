from __future__ import annotations

import json
from pathlib import Path
import pytest

from pm_agent.config import AgentConfig
from pm_agent.presentation.cli import parse_args, build_repl
from pm_agent.domain.approval_rules import make_approval_rule, proposal_matches_rule
from pm_agent.domain.models import ActionProposal, DecisionCandidate, DecisionStatus, ActionCandidate
from pm_agent.domain.enums import ActionType
from pm_agent.domain.policies import ActionPolicy, PolicyViolation
from pm_agent.infrastructure.repository import LocalRepositoryAnalyzer
from pm_agent.ports.repository_context import SnapshotRequest
from pm_agent.application.context_service import ContextService
from pm_agent.application.action_service import ActionService
from pm_agent.infrastructure.hosts import StandaloneHostBridge, IntegrationHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.presentation.repl import PMAgentREPL, REPLServices

def test_always_approve_env_variable_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("PM_AGENT_ACCEPT_ALL", "true")
    config = AgentConfig(repo_path=str(tmp_path))
    assert config.always_approve is True

    # Check CLI helper overrides base
    args = parse_args([])
    resolved = args.always_approve or config.always_approve
    assert resolved is True

def test_make_approval_rule_no_payload_pattern():
    proposal = ActionProposal(
        id="p1", project_id="proj1", session_id="s1",
        action_type=ActionType.GITHUB, tool_category="github", operation="create_issue",
        reason="test", impact="test", payload={"repository": "a/b", "title": "test"},
        payload_sha256="hash", risk_level="medium", status="proposed", created_at="now"
    )
    rule = make_approval_rule("proj1", proposal)
    assert rule.payload_pattern == '{"repository":"a/b"}'
    
    # Now check if it matches a proposal with a DIFFERENT payload
    diff_proposal = ActionProposal(
        id="p2", project_id="proj1", session_id="s1",
        action_type=ActionType.GITHUB, tool_category="github", operation="create_issue",
        reason="test", impact="test", payload={"repository": "a/b", "title": "different"},
        payload_sha256="hash2", risk_level="medium", status="proposed", created_at="now"
    )
    assert proposal_matches_rule(diff_proposal, rule) is True

def test_invalid_decision_status_validation():
    from pm_agent.prompts.parser import ResponseParser, ResponseValidationError
    parser = ResponseParser()
    with pytest.raises(ResponseValidationError, match="decision status must be one of"):
        parser._decision({
            "topic": "topic",
            "title": "title",
            "decision": "decision",
            "reason": "reason",
            "status": "completed"
        })

def test_composer_json_parsed(tmp_path):
    (tmp_path / "composer.json").write_text(json.dumps({
        "name": "laravel/laravel",
        "description": "The Laravel Framework.",
        "require": {"php": "^8.2", "laravel/framework": "^11.0"},
        "require-dev": {"phpunit/phpunit": "^10.0"}
    }))
    snapshot = LocalRepositoryAnalyzer().build_snapshot(
        SnapshotRequest("p1", str(tmp_path), "main")
    )
    assert "composer.json" in snapshot.summary["manifests"]
    assert snapshot.summary["manifests"]["composer.json"]["name"] == "laravel/laravel"

def test_context_loading_deduplication(tmp_path):
    context_dir = tmp_path / "custom_context"
    context_dir.mkdir()
    (context_dir / "notes.txt").write_text("Hello planning context")

    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    # First load
    loaded1 = context.load_context_files(project.id, session.id, base_path=str(context_dir))
    assert len(loaded1) == 1
    assert "notes.txt" in loaded1

    # Second load - should deduplicate
    loaded2 = context.load_context_files(project.id, session.id, base_path=str(context_dir))
    assert len(loaded2) == 1
    
    with store.factory.connect() as conn:
        notes_count = conn.execute("SELECT COUNT(*) FROM repository_notes").fetchone()[0]
        assert notes_count == 1

def test_category_agreement_validation():
    policy = ActionPolicy()
    
    # 1. GITHUB with invalid category is blocked
    dec1 = policy.evaluate(ActionCandidate(
        action_type=ActionType.GITHUB, tool_category="filesystem", operation="list_issues",
        reason="reason", impact="impact", payload={"repository": "a/b"}
    ))
    assert not dec1.allowed
    assert "Action type GITHUB requires valid GitHub tool_category" in dec1.reason

    # 2. GIT with invalid category is blocked
    dec2 = policy.evaluate(ActionCandidate(
        action_type=ActionType.GIT, tool_category="github", operation="status",
        reason="reason", impact="impact", payload={"command": "git status"}
    ))
    assert not dec2.allowed
    assert "Action type GIT requires tool_category in" in dec2.reason

    # 3. BASH with invalid category is blocked
    dec3 = policy.evaluate(ActionCandidate(
        action_type=ActionType.BASH, tool_category="github", operation="cat",
        reason="reason", impact="impact", payload={"command": "cat file.txt"}
    ))
    assert not dec3.allowed
    assert "Action type BASH requires tool_category in" in dec3.reason

def test_safety_rule_matching():
    # 1. Bash command executable check
    prop_bash1 = ActionProposal(
        id="p1", project_id="proj1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem", operation="cat",
        reason="r", impact="i", payload={"command": "cat file1.txt"},
        payload_sha256="hash", risk_level="medium", status="proposed", created_at="now"
    )
    rule_bash = make_approval_rule("proj1", prop_bash1)
    
    prop_bash2 = ActionProposal(
        id="p2", project_id="proj1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem", operation="cat",
        reason="r", impact="i", payload={"command": "cat file2.txt"},
        payload_sha256="hash2", risk_level="medium", status="proposed", created_at="now"
    )
    prop_bash_diff = ActionProposal(
        id="p3", project_id="proj1", session_id="s1",
        action_type=ActionType.BASH, tool_category="filesystem", operation="cat",
        reason="r", impact="i", payload={"command": "rg file2.txt"},
        payload_sha256="hash3", risk_level="medium", status="proposed", created_at="now"
    )
    assert proposal_matches_rule(prop_bash2, rule_bash) is True
    assert proposal_matches_rule(prop_bash_diff, rule_bash) is False

    # 2. Git subcommand check
    prop_git1 = ActionProposal(
        id="g1", project_id="proj1", session_id="s1",
        action_type=ActionType.GIT, tool_category="git", operation="log",
        reason="r", impact="i", payload={"command": "git log"},
        payload_sha256="hash", risk_level="medium", status="proposed", created_at="now"
    )
    rule_git = make_approval_rule("proj1", prop_git1)
    
    prop_git2 = ActionProposal(
        id="g2", project_id="proj1", session_id="s1",
        action_type=ActionType.GIT, tool_category="git", operation="log",
        reason="r", impact="i", payload={"command": "git log -n 10"},
        payload_sha256="hash2", risk_level="medium", status="proposed", created_at="now"
    )
    prop_git_diff = ActionProposal(
        id="g3", project_id="proj1", session_id="s1",
        action_type=ActionType.GIT, tool_category="git", operation="log",
        reason="r", impact="i", payload={"command": "git status"},
        payload_sha256="hash3", risk_level="medium", status="proposed", created_at="now"
    )
    assert proposal_matches_rule(prop_git2, rule_git) is True
    assert proposal_matches_rule(prop_git_diff, rule_git) is False

def test_deferred_external_status_lifecycle(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    
    actions = ActionService(store, StandaloneHostBridge())
    proposal = actions.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.BASH,
            "filesystem",
            "cat",
            "Read file",
            "Read-only",
            {"command": "cat test.txt"}
        )
    )
    receipt = actions.approve(proposal.id)
    assert receipt.deferred_external is True
    assert receipt.dispatched is False
    
    # Action stays in APPROVED
    stored = store.get_action(proposal.id)
    assert stored.status.value == "approved"

    # We can record the external outcome
    actions.record_outcome(proposal.id, exit_code=0, stdout="hello")
    stored = store.get_action(proposal.id)
    assert stored.status.value == "succeeded"

    # Duplicate completion raises ValueError because it is already succeeded
    with pytest.raises(ValueError, match="Only approved or dispatched actions can receive outcomes"):
        actions.record_outcome(proposal.id, exit_code=0, stdout="hello")

def test_local_mcp_inspection_execution(tmp_path, monkeypatch):
    (tmp_path / "composer.json").write_text(json.dumps({"name": "laravel/laravel"}))
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")

    monkeypatch.setenv("PM_AGENT_DB_PATH", str(tmp_path / "state.db"))

    host = IntegrationHostBridge(repo_root=str(tmp_path))
    actions = ActionService(store, host)
    
    proposal = actions.propose(
        project.id,
        session.id,
        ActionCandidate(
            ActionType.MCP,
            "filesystem",
            "inspect_repository",
            "Inspect repo",
            "Read-only",
            {"path": str(tmp_path)}
        )
    )
    receipt = actions.approve(proposal.id)
    assert receipt.dispatched is True
    assert receipt.completed is True
    assert receipt.exit_code == 0
    assert "snapshot_digest" in receipt.result

    # Confirm snapshot exists in DB
    snapshot = store.latest_snapshot(project.id)
    assert snapshot is not None
    assert snapshot.tree_digest == receipt.result["snapshot_digest"]

def test_case_insensitive_milestone_resolution(monkeypatch):
    import json
    
    # Mock _run_gh to return a specific list of milestones
    def fake_run_gh(args, **kwargs):
        milestones = [
            {"title": "Sprint 1: Core Navigation & Architecture Routing ", "number": 42},
            {"title": "Milestone B", "number": 12}
        ]
        return 0, json.dumps(milestones), ""
        
    monkeypatch.setattr(IntegrationHostBridge, "_run_gh", fake_run_gh)
    
    # Resolve exact match
    res1 = IntegrationHostBridge._resolve_milestone_titles(
        "a/b", {"Sprint 1: Core Navigation & Architecture Routing"}
    )
    assert res1.get("Sprint 1: Core Navigation & Architecture Routing") == 42
    
    # Resolve lowercase match
    res2 = IntegrationHostBridge._resolve_milestone_titles(
        "a/b", {"sprint 1: core navigation & architecture routing"}
    )
    assert res2.get("sprint 1: core navigation & architecture routing") == 42

def test_make_receipt_with_missing_scopes():
    from pm_agent.domain.models import ApprovedAction, ActionProposal
    from pm_agent.domain.enums import ActionType
    
    proposal = ActionProposal(
        id="p1", project_id="proj1", session_id="s1",
        action_type=ActionType.GITHUB, tool_category="github", operation="create_project",
        reason="reason", impact="impact", payload={"project": {"name": "Test Board"}, "repository": "a/b"},
        payload_sha256="hash", risk_level="high", status="proposed", created_at="now"
    )
    approved = ApprovedAction(proposal=proposal, approved_payload_sha256="hash", approved_by="user", approved_at="now")
    
    # Simulate missing scopes in stderr
    stderr = "error: your authentication token is missing required scopes [project read:project]\nTo request it, run:  gh auth refresh -s project,read:project"
    receipt = IntegrationHostBridge._make_receipt(
        approved, rc=1, stdout="", stderr=stderr, correlation_id="cid",
        success_msg="Success", failure_msg="Failed to create project."
    )
    
    # The message should extract the scopes and format the CLI fix suggestion
    assert "The GitHub token is missing required scopes: [project,read:project]" in receipt.message
    assert "Fix: Run:  gh auth refresh -s project,read:project" in receipt.message
