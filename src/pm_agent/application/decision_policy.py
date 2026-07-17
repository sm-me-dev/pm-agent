from __future__ import annotations

import re

from pm_agent.domain.enums import TaskClass
from pm_agent.domain.models import ExecutionNeeds, PMResponse

# Phrases that indicate the model is pushing executable work back to the user
# instead of doing it. These are heuristics used only to surface a warning, not
# to block the response.
_OFFLOAD_PATTERNS = [
    re.compile(
        r"you should (inspect|review|read|check|look at|examine) (the|your) "
        r"(repo|repository|codebase|project)",
        re.I,
    ),
    re.compile(
        r"(please |kindly |manually )?(inspect|review|write|create|document|"
        r"break down|analyze) (the|your) (repo|repository|docs?|documentation|"
        r"issues?|breakdown|mvp)",
        re.I,
    ),
    re.compile(r"(you need to|you must) (write|create|inspect|review|document|break down)", re.I),
]


class DecisionPolicy:
    """Lightweight guardrail around the model's ask-vs-act decision.

    The authoritative behaviour lives in ``prompts/system.md`` (autonomy and
    clarification policy). This class provides a code-side safety net so the
    agent's stance is explicit, testable, and can be flagged when it regresses.
    """

    @staticmethod
    def classify(response: PMResponse) -> ExecutionNeeds:
        """Return the explicit execution needs, deriving a default if absent."""
        if response.execution_needs is not None:
            return response.execution_needs
        if response.actions_requiring_approval:
            classification = TaskClass.AGENT_EXECUTABLE
        elif response.decisions:
            classification = TaskClass.USER_DECISION_REQUIRED
        else:
            classification = TaskClass.AGENT_EXECUTABLE
        return ExecutionNeeds(classification=classification)

    @staticmethod
    def detect_offloading(response: PMResponse) -> list[str]:
        """Warn when the response delegates agent-executable work to the user."""
        haystacks = [response.summary, response.analysis, *response.recommendations]
        for decision in response.decisions:
            haystacks.append(f"{decision.title} {decision.decision} {decision.reason}")
        text = "\n".join(haystacks)
        for pattern in _OFFLOAD_PATTERNS:
            if pattern.search(text):
                return [
                    "Response delegates work to the user that the agent can perform "
                    "autonomously from accessible artifacts (repo, issues, memory). "
                    "Prefer emitting actions/analysis over asking the user to do it."
                ]
        return []

    @staticmethod
    def validate(response: PMResponse) -> list[str]:
        """Return human-readable warnings about the ask-vs-act stance."""
        warnings: list[str] = []
        needs = DecisionPolicy.classify(response)
        warnings.extend(DecisionPolicy.detect_offloading(response))
        if needs.classification is TaskClass.USER_DECISION_REQUIRED and not needs.open_questions:
            warnings.append(
                "Classified as user_decision_required but no open_questions were "
                "supplied. Narrow the missing decision to a single explicit question."
            )
        if needs.classification is TaskClass.EXTERNAL_ACCESS_REQUIRED and not needs.missing_access:
            warnings.append(
                "Classified as external_access_required but missing_access is empty. "
                "Name exactly what permission or integration access is missing."
            )
        return warnings

    @staticmethod
    def categorize_block(operation: str) -> str:
        """Categorize a blocked action so refusal messaging is precise."""
        if operation.startswith("github") or operation in {
            "create_issue",
            "create_issues",
            "create_issue_comment",
            "create_sub_issue",
            "create_milestone",
            "update_milestone",
            "setup_sprint",
            "add_issue_to_project",
            "create_project",
            "update_issue",
        }:
            return "external_access"
        return "approval"
