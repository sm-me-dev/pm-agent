from __future__ import annotations

from rich.console import Console

from pm_agent.domain.enums import ActionStatus, ActionType, DecisionStatus
from pm_agent.domain.models import (
    ActionProposal,
    DecisionCandidate,
    IntegrationInfo,
    PMResponse,
    payload_hash,
    utc_now,
)
from pm_agent.presentation.renderers import (
    integration_panel,
    render_action_outcome,
    response_markdown,
    response_renderable,
)


def test_response_contains_every_required_section():
    response = PMResponse(summary="High level", analysis="Detailed")
    rendered = response_markdown(response, [])
    headings = [
        "# Summary",
        "# Analysis",
        "# Risks",
        "# Recommendations",
        "# Decisions",
        "# Actions Requiring Approval",
    ]
    assert all(heading in rendered for heading in headings)
    assert rendered.count("None.") == 4


def test_decision_and_action_templates_are_embedded():
    payload = {"command": "git status"}
    response = PMResponse(
        summary="x",
        analysis="y",
        decisions=[
            DecisionCandidate(
                topic="git",
                title="Inspect first",
                decision="Inspect status.",
                reason="Avoid invention.",
                status=DecisionStatus.PROPOSED,
            )
        ],
    )
    action = ActionProposal(
        id="a1",
        project_id="p1",
        session_id="s1",
        action_type=ActionType.GIT,
        tool_category="git",
        operation="status",
        reason="Confirm state.",
        impact="Read-only.",
        payload=payload,
        payload_sha256=payload_hash(payload),
        risk_level="low",
        status=ActionStatus.PROPOSED,
        created_at=utc_now(),
    )
    rendered = response_markdown(response, [action])
    assert "### Decision: git | Inspect first" in rendered
    assert "### Action Proposal" in rendered
    assert "Approval Required:\nYES" in rendered


def test_structured_response_renders_distinct_panels():
    console = Console(record=True, width=100)
    console.print(
        response_renderable(
            PMResponse(
                summary="High level",
                analysis="Detailed",
                risks=["Risk"],
                recommendations=["Next"],
            ),
            [],
        )
    )
    rendered = console.export_text()
    assert "Summary" in rendered
    assert "Analysis" in rendered
    assert "Risks" in rendered
    assert "Recommendations" in rendered
    assert "Actions Requiring Approval" in rendered


def test_action_outcome_separates_stdout_and_stderr():
    console = Console(record=True, width=100)
    render_action_outcome(
        console,
        exit_code=1,
        stdout="normal output",
        stderr="failure output",
        result={"status": "failed"},
    )
    rendered = console.export_text()
    assert "stdout" in rendered
    assert "normal output" in rendered
    assert "stderr" in rendered
    assert "failure output" in rendered


def test_integration_panel_lists_status_and_capabilities():
    console = Console(record=True, width=100)
    console.print(
        integration_panel(
            IntegrationInfo(
                key="github",
                name="GitHub",
                status="connected",
                authentication="GitHub CLI browser login",
                capabilities=["Issue planning", "Milestone planning"],
            )
        )
    )
    rendered = console.export_text()
    assert "GitHub [github]" in rendered
    assert "connected" in rendered
    assert "Issue planning" in rendered
