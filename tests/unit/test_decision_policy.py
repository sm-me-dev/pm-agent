from __future__ import annotations

from pm_agent.application.decision_policy import DecisionPolicy
from pm_agent.domain.enums import TaskClass
from pm_agent.domain.models import (
    DecisionCandidate,
    ExecutionNeeds,
    PMResponse,
)


def _response(actions=None, decisions=None, needs=None, text=""):
    return PMResponse(
        summary=text,
        analysis=text,
        decisions=decisions or [],
        actions_requiring_approval=actions or [],
        execution_needs=needs,
    )


def test_classify_uses_explicit_needs():
    needs = ExecutionNeeds(classification=TaskClass.USER_DECISION_REQUIRED,
                           open_questions=["Pick a market?"])
    response = _response(needs=needs)
    assert DecisionPolicy.classify(response).classification is TaskClass.USER_DECISION_REQUIRED


def test_classify_derives_agent_executable_from_actions():
    action = _action("write_document")
    response = _response(actions=[action])
    assert DecisionPolicy.classify(response).classification is TaskClass.AGENT_EXECUTABLE


def test_classify_derives_user_decision_from_decisions():
    decision = DecisionCandidate(
        topic="product", title="scope", decision="x", reason="y",
        status=__import__("pm_agent.domain.enums", fromlist=["DecisionStatus"]).DecisionStatus.PROPOSED,
    )
    response = _response(decisions=[decision])
    assert DecisionPolicy.classify(response).classification is TaskClass.USER_DECISION_REQUIRED


def test_detect_offloading_flags_delegation():
    response = _response(text="You should inspect the repository and write the docs yourself.")
    warnings = DecisionPolicy.detect_offloading(response)
    assert warnings
    assert "delegate" in warnings[0].lower()


def test_detect_offloading_clean_when_autonomous():
    response = _response(actions=[_action("write_document")],
                         text="I inspected the repo and generated the architecture doc.")
    assert DecisionPolicy.detect_offloading(response) == []


def test_validate_warns_when_user_decision_missing_question():
    needs = ExecutionNeeds(classification=TaskClass.USER_DECISION_REQUIRED)
    response = _response(needs=needs)
    warnings = DecisionPolicy.validate(response)
    assert any("open_questions" in w for w in warnings)


def test_validate_warns_when_external_missing_access_empty():
    needs = ExecutionNeeds(classification=TaskClass.EXTERNAL_ACCESS_REQUIRED)
    response = _response(needs=needs)
    warnings = DecisionPolicy.validate(response)
    assert any("missing_access" in w for w in warnings)


def test_categorize_block_distinguishes_access_from_approval():
    assert DecisionPolicy.categorize_block("create_issue_comment") == "external_access"
    assert DecisionPolicy.categorize_block("write_document") == "approval"


def _action(operation: str):
    from pm_agent.domain.enums import ActionType
    from pm_agent.domain.models import ActionCandidate

    return ActionCandidate(
        action_type=ActionType.MCP,
        tool_category="filesystem",
        operation=operation,
        reason="r",
        impact="i",
        payload={},
    )
