from __future__ import annotations

import json

import pytest

from pm_agent.application.action_service import ActionService
from pm_agent.application.conversation_service import ConversationService
from pm_agent.infrastructure.hosts import StandaloneHostBridge
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.ports.model import ModelResult
from pm_agent.prompts import PromptBuilder, ResponseParser, ResponseValidationError


class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelResult(next(self.responses), used_native_schema=True)


def payload(actions=None, decisions=None):
    return json.dumps(
        {
            "summary": "Answer",
            "analysis": "Reasoning",
            "risks": [],
            "recommendations": ["Next step"],
            "decisions": decisions or [],
            "actions_requiring_approval": actions or [],
        }
    )


def setup(tmp_path, provider):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "glm-5.2", "test", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    service = ConversationService(
        store, provider, PromptBuilder(), ResponseParser(), actions
    )
    return store, project, session, service


def test_turn_persists_one_user_and_one_canonical_assistant_message(tmp_path):
    provider = FakeProvider([payload()])
    store, project, session, service = setup(tmp_path, provider)
    service.handle(project, session, "Plan storage")
    messages = store.get_recent_messages(session.id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert json.loads(messages[1].content)["summary"] == "Answer"
    request_user_messages = [
        message for message in provider.requests[0].messages if message["role"] == "user"
    ]
    assert len(request_user_messages) == 1


def test_invalid_response_repairs_once(tmp_path):
    provider = FakeProvider(["not-json", payload()])
    store, project, session, service = setup(tmp_path, provider)
    response, _ = service.handle(project, session, "Plan")
    assert response.summary == "Answer"
    assert len(provider.requests) == 2


def test_second_invalid_response_creates_no_assistant_artifacts(tmp_path):
    provider = FakeProvider(["bad", "still bad"])
    store, project, session, service = setup(tmp_path, provider)
    with pytest.raises(ResponseValidationError):
        service.handle(project, session, "Plan")
    assert [message.role for message in store.get_recent_messages(session.id)] == ["user"]
    assert store.list_decisions(project.id) == []
    assert store.list_actions(project.id) == []


def test_blocked_model_action_does_not_appear_as_approvable(tmp_path):
    provider = FakeProvider(
        [
            payload(
                [
                    {
                        "action_type": "git",
                        "tool_category": "git",
                        "operation": "push",
                        "reason": "Publish.",
                        "impact": "Mutates remote.",
                        "payload": {"command": "git push"},
                    }
                ]
            )
        ]
    )
    store, project, session, service = setup(tmp_path, provider)
    response, actions = service.handle(project, session, "Push")
    assert actions == []
    assert "Safety policy rejected" in response.risks[-1]
    assert store.list_actions(project.id)[0].status.value == "rejected"


def test_repository_inspection_and_sprint_tasks_are_approvable(tmp_path):
    provider = FakeProvider(
        [
            payload(
                [
                    {
                        "action_type": "github",
                        "tool_category": "github",
                        "operation": "read_repository",
                        "reason": "Confirm current project state.",
                        "impact": "Read repository metadata.",
                        "payload": {
                            "repository": "sm-me-dev/unified-workspace-engine"
                        },
                    },
                    {
                        "action_type": "github",
                        "tool_category": "github",
                        "operation": "create_issues",
                        "reason": "Create the reviewed sprint backlog.",
                        "impact": "Creates two GitHub issues after approval.",
                        "payload": {
                            "repository": "sm-me-dev/unified-workspace-engine",
                            "issues": [
                                {
                                    "title": "Normalize Git operation detection",
                                    "body": "Support safe read-only Git aliases.",
                                    "labels": ["bug", "sprint-1"],
                                },
                                {
                                    "title": "Add repository planning snapshots",
                                    "body": "Store approved repository context.",
                                    "labels": ["enhancement", "sprint-1"],
                                },
                            ],
                        },
                    },
                ]
            )
        ]
    )
    store, project, session, service = setup(tmp_path, provider)
    response, actions = service.handle(
        project,
        session,
        "Assess @sm-me-dev/unified-workspace-engine and create sprint tasks.",
    )
    assert len(actions) == 2
    assert len(response.actions_requiring_approval) == 2
    assert all(action.status.value == "proposed" for action in actions)


DECISION = {
    "topic": "Storage",
    "title": "Use SQLite",
    "decision": "We will use SQLite for local state.",
    "reason": "Simple and portable.",
    "status": "proposed",
}

READ_REPO_ACTION = {
    "action_type": "github",
    "tool_category": "github",
    "operation": "read_repository",
    "reason": "Confirm state.",
    "impact": "Read metadata.",
    "payload": {"repository": "sm-me-dev/unified-workspace-engine"},
}

PUSH_ACTION = {
    "action_type": "git",
    "tool_category": "git",
    "operation": "push",
    "reason": "Publish.",
    "impact": "Mutates remote.",
    "payload": {"command": "git push"},
}


def test_repeated_identical_decision_is_not_duplicated(tmp_path):
    provider = FakeProvider([payload(decisions=[DECISION]), payload(decisions=[DECISION])])
    store, project, session, service = setup(tmp_path, provider)
    service.handle(project, session, "Plan storage")
    service.handle(project, session, "Plan storage again")
    decisions = store.list_decisions(project.id)
    assert len(decisions) == 1
    assert decisions[0].status.value == "proposed"


def test_resolved_decision_is_not_reprompted(tmp_path):
    from pm_agent.application.decision_service import DecisionService

    provider = FakeProvider([payload(decisions=[DECISION]), payload(decisions=[DECISION])])
    store, project, session, service = setup(tmp_path, provider)
    service.handle(project, session, "Plan storage")
    pending = store.list_decisions(project.id)
    DecisionService(store).accept(project.id, pending[0].id)
    service.handle(project, session, "Plan storage again")
    assert len(store.list_decisions(project.id)) == 1


def test_repeated_identical_action_is_not_reprompted(tmp_path):
    provider = FakeProvider(
        [payload(actions=[READ_REPO_ACTION]), payload(actions=[READ_REPO_ACTION])]
    )
    store, project, session, service = setup(tmp_path, provider)
    _, first = service.handle(project, session, "Assess repo")
    _, second = service.handle(project, session, "Assess repo again")
    assert len(first) == 1
    assert len(second) == 0
    assert len(store.list_actions(project.id)) == 1


def test_rejected_action_is_not_reprompted(tmp_path):
    provider = FakeProvider([payload(actions=[PUSH_ACTION]), payload(actions=[PUSH_ACTION])])
    store, project, session, service = setup(tmp_path, provider)
    _, first = service.handle(project, session, "Push")
    _, second = service.handle(project, session, "Push again")
    assert len(first) == 0
    assert len(second) == 0
    actions = store.list_actions(project.id)
    assert len(actions) == 1
    assert actions[0].status.value == "rejected"


def test_materially_changed_action_creates_new_proposal(tmp_path):
    changed = {**READ_REPO_ACTION, "payload": {"repository": "octocat/Hello-World"}}
    provider = FakeProvider(
        [payload(actions=[READ_REPO_ACTION]), payload(actions=[changed])]
    )
    store, project, session, service = setup(tmp_path, provider)
    service.handle(project, session, "Assess repo")
    service.handle(project, session, "Assess other repo")
    assert len(store.list_actions(project.id)) == 2
