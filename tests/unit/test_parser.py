from __future__ import annotations

import json

import pytest

from pm_agent.domain.enums import ActionType, DecisionStatus
from pm_agent.prompts import ResponseParser, ResponseValidationError


def valid_response() -> dict:
    return {
        "summary": "Summary",
        "analysis": "Analysis",
        "risks": ["Risk"],
        "recommendations": ["Next"],
        "decisions": [
            {
                "topic": "storage",
                "title": "Use SQLite",
                "decision": "Use SQLite.",
                "reason": "Local persistence.",
                "status": "proposed",
            }
        ],
        "actions_requiring_approval": [
            {
                "action_type": "git",
                "tool_category": "git",
                "operation": "status",
                "reason": "Confirm branch state.",
                "impact": "Read-only output.",
                "payload": {"command": "git status"},
            }
        ],
    }


def test_parses_typed_response():
    response = ResponseParser().parse(json.dumps(valid_response()))
    assert response.decisions[0].status is DecisionStatus.PROPOSED
    assert response.actions_requiring_approval[0].action_type is ActionType.GIT


def test_rejects_markdown_and_missing_fields():
    with pytest.raises(ResponseValidationError):
        ResponseParser().parse("```json\n{}\n```")
    with pytest.raises(ResponseValidationError):
        ResponseParser().parse('{"summary": "x"}')


def test_model_cannot_accept_decision():
    data = valid_response()
    data["decisions"][0]["status"] = "accepted"
    with pytest.raises(ResponseValidationError):
        ResponseParser().parse(json.dumps(data))
