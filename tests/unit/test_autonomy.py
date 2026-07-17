from __future__ import annotations

import json

import pytest

from pm_agent.application.action_service import ActionService
from pm_agent.application.conversation_service import ConversationService
from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import (
    ActionCandidate,
    Project,
)
from pm_agent.infrastructure.hosts import IntegrationHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.prompts import PromptBuilder, ResponseParser
from pm_agent.prompts.parser import ResponseValidationError


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        from pm_agent.ports.model import ModelResult

        return ModelResult(self.responses.pop(0), used_native_schema=True)


def _project(tmp_path) -> Project:
    store = SQLiteStore(tmp_path / "state.db")
    return store.resolve_project(tmp_path)


def _setup(tmp_path, provider):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, IntegrationHostBridge(repo_root=str(tmp_path)))
    service = ConversationService(store, provider, PromptBuilder(), ResponseParser(), actions)
    return store, project, session, service, actions


def _payload(actions=None, decisions=None, needs=None):
    data = {
        "summary": "Done",
        "analysis": "Analysis",
        "risks": [],
        "recommendations": ["Next"],
        "decisions": decisions or [],
        "actions_requiring_approval": actions or [],
    }
    if needs is not None:
        data["execution_needs"] = needs
    return json.dumps(data)


def test_audit_task_proposes_analysis_and_docs_without_user_work(tmp_path):
    actions = [
        {"action_type": "mcp", "tool_category": "filesystem", "operation": "inspect_repository",
         "reason": "r", "impact": "i", "payload": {}},
        {"action_type": "mcp", "tool_category": "filesystem", "operation": "write_document",
         "reason": "r", "impact": "i", "payload": {"path": "docs/architecture.md",
         "content": "# Architecture\nLayers..."}},
        {"action_type": "mcp", "tool_category": "filesystem", "operation": "write_document",
         "reason": "r", "impact": "i", "payload": {"path": "TECH_DEBT.md",
         "content": "# Tech Debt\nCandidates..."}},
    ]
    needs = {"classification": "agent_executable_with_assumptions",
             "assumptions": ["README exists and describes setup"]}
    decisions = [{"topic": "business", "title": "MVP scope sign-off",
                  "decision": "Proceed with proposed MVP once approved.",
                  "reason": "Stakeholder approval required.", "status": "proposed"}]
    provider = FakeProvider([_payload(actions, decisions, needs)])
    store, project, session, service, _ = _setup(tmp_path, provider)
    response, stored = service.handle(project, session, "Audit this repository")

    operations = {a.operation for a in response.actions_requiring_approval}
    assert operations == {"inspect_repository", "write_document"}
    assert len(response.decisions) == 1
    # Only a narrow stakeholder question is asked, not "go inspect the repo".
    assert response.execution_needs.classification.value == "agent_executable_with_assumptions"
    assert response.execution_needs.assumptions
    assert len(stored) == 3


def test_mvp_task_drafts_scope_without_user_analysis(tmp_path):
    actions = [
        {"action_type": "github", "tool_category": "github", "operation": "create_sub_issue",
         "reason": "r", "impact": "i",
         "payload": {"repository": "a/b", "parent": 1, "title": "Auth flow",
                     "body": "As a user I can log in. AC: ..."}},
        {"action_type": "github", "tool_category": "github", "operation": "create_sub_issue",
         "reason": "r", "impact": "i",
         "payload": {"repository": "a/b", "parent": 1, "title": "Dashboard",
                     "body": "As a user I see a dashboard. AC: ..."}},
        {"action_type": "github", "tool_category": "github", "operation": "create_issue_comment",
         "reason": "r", "impact": "i",
         "payload": {"repository": "a/b", "issue_number": 1,
                     "body": "Proposed MVP (MoSCoW): Must=Auth,Dashboard; ..."}},
    ]
    needs = {"classification": "user_decision_required",
             "open_questions": ["Approve this MVP scope or reprioritize Must-haves?"]}
    decisions = [{"topic": "product", "title": "MVP scope approval",
                  "decision": "Approve the proposed MoSCoW scope.",
                  "reason": "Business sign-off required.", "status": "proposed"}]
    provider = FakeProvider([_payload(actions, decisions, needs)])
    store, project, session, service, _ = _setup(tmp_path, provider)
    response, stored = service.handle(project, session, "Draft an MVP for this repo")

    operations = {a.operation for a in response.actions_requiring_approval}
    assert operations == {"create_sub_issue", "create_issue_comment"}
    assert len(response.decisions) == 1
    assert response.execution_needs.classification.value == "user_decision_required"
    assert response.execution_needs.open_questions
    assert len(stored) == 3


def test_github_permission_failure_does_not_block_local_draft(tmp_path, monkeypatch):
    # Force the GitHub comment to fail with a precise missing-access message,
    # while local repository analysis and document writes must still succeed.
    from pm_agent.domain.models import DispatchReceipt

    def fake_scope_check(operation):
        return DispatchReceipt(
            correlation_id="cid", dispatched=True, completed=True, exit_code=1,
            message="The GitHub token is missing required scopes: [repo]",
            stderr="missing scope", error_category="missing_scope",
        )

    monkeypatch.setattr(IntegrationHostBridge, "_check_github_scopes", staticmethod(fake_scope_check))
    # Ensure the dispatch path reaches the scope check (gh present) so the
    # precise missing-access message is exercised regardless of this machine.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, IntegrationHostBridge(repo_root=str(tmp_path)))

    inspect = actions.propose(project.id, session.id, ActionCandidate(
        ActionType.MCP, "filesystem", "inspect_repository", "r", "i", {"path": str(tmp_path)}))
    doc = actions.propose(project.id, session.id, ActionCandidate(
        ActionType.MCP, "filesystem", "write_document", "r", "i",
        {"path": "docs/architecture.md", "content": "# Architecture"}))
    comment = actions.propose(project.id, session.id, ActionCandidate(
        ActionType.GITHUB, "github", "create_issue_comment", "r", "i",
        {"repository": "a/b", "issue_number": 1, "body": "Proposed MVP scope..."}))

    inspect_receipt = actions.approve(inspect.id)
    doc_receipt = actions.approve(doc.id)
    comment_receipt = actions.approve(comment.id)

    # Local drafting succeeded.
    assert inspect_receipt.exit_code == 0
    assert doc_receipt.exit_code == 0
    assert (tmp_path / "docs" / "architecture.md").is_file()

    # GitHub failure is reported precisely and does NOT block the local work.
    assert comment_receipt.exit_code != 0
    assert "missing" in comment_receipt.message.lower()


def test_parser_accepts_execution_needs():
    parser = ResponseParser()
    raw = json.dumps({
        "summary": "s", "analysis": "a", "risks": [], "recommendations": [],
        "decisions": [], "actions_requiring_approval": [],
        "execution_needs": {
            "classification": "user_decision_required",
            "open_questions": ["Pick the target market?"],
        },
    })
    response = parser.parse(raw)
    assert response.execution_needs is not None
    assert response.execution_needs.classification.value == "user_decision_required"
    assert response.execution_needs.open_questions == ["Pick the target market?"]


def test_parser_rejects_unknown_top_level_field():
    parser = ResponseParser()
    raw = json.dumps({
        "summary": "s", "analysis": "a", "risks": [], "recommendations": [],
        "decisions": [], "actions_requiring_approval": [], "bogus": 1,
    })
    with pytest.raises(ResponseValidationError):
        parser.parse(raw)
