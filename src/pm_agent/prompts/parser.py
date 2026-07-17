from __future__ import annotations

import json
from typing import Any

from pm_agent.domain.enums import ActionType, DecisionStatus, TaskClass
from pm_agent.domain.models import (
    ActionCandidate,
    DecisionCandidate,
    ExecutionNeeds,
    PMResponse,
)


class ResponseValidationError(ValueError):
    pass


class ResponseParser:
    _REQUIRED = {
        "summary",
        "analysis",
        "risks",
        "recommendations",
        "decisions",
        "actions_requiring_approval",
    }
    _OPTIONAL = {"execution_needs"}

    def parse(self, raw_response: str) -> PMResponse:
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise ResponseValidationError(f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ResponseValidationError("Response must be a JSON object.")
        missing = self._REQUIRED - data.keys()
        extra = data.keys() - (self._REQUIRED | self._OPTIONAL)
        if missing or extra:
            raise ResponseValidationError(
                f"Missing fields: {sorted(missing)}; unexpected fields: {sorted(extra)}"
            )
        summary = self._string(data["summary"], "summary")
        analysis = self._string(data["analysis"], "analysis")
        risks = self._string_list(data["risks"], "risks")
        recommendations = self._string_list(data["recommendations"], "recommendations")
        decisions = [self._decision(item) for item in self._list(data["decisions"], "decisions")]
        actions = [
            self._action(item)
            for item in self._list(
                data["actions_requiring_approval"], "actions_requiring_approval"
            )
        ]
        execution_needs = None
        if "execution_needs" in data:
            execution_needs = self._execution_needs(data["execution_needs"])
        return PMResponse(
            summary=summary,
            analysis=analysis,
            risks=risks,
            recommendations=recommendations,
            decisions=decisions,
            actions_requiring_approval=actions,
            execution_needs=execution_needs,
        )

    def _execution_needs(self, value: Any) -> ExecutionNeeds:
        item = self._object(value, "execution_needs")
        raw_class = self._string(item.get("classification", ""), "execution_needs.classification")
        try:
            classification = TaskClass(raw_class)
        except ValueError as exc:
            valid = ", ".join(e.value for e in TaskClass)
            raise ResponseValidationError(
                f"execution_needs.classification must be one of [{valid}]; "
                f"got '{raw_class}'"
            ) from exc
        return ExecutionNeeds(
            classification=classification,
            assumptions=self._string_list(item.get("assumptions", []), "execution_needs.assumptions"),
            open_questions=self._string_list(
                item.get("open_questions", []), "execution_needs.open_questions"
            ),
            missing_access=self._string_list(
                item.get("missing_access", []), "execution_needs.missing_access"
            ),
        )

    def _decision(self, value: Any) -> DecisionCandidate:
        item = self._object(value, "decision")
        required = {"topic", "title", "decision", "reason", "status"}
        if item.keys() != required:
            raise ResponseValidationError("Decision fields are invalid.")
        try:
            status = DecisionStatus(self._string(item["status"], "decision.status"))
        except ValueError as exc:
            valid_statuses = ", ".join(e.value for e in DecisionStatus)
            raise ResponseValidationError(
                f"decision status must be one of [{valid_statuses}]; got '{item.get('status')}'"
            ) from exc
        if status is DecisionStatus.ACCEPTED:
            raise ResponseValidationError("The model cannot accept a decision.")
        return DecisionCandidate(
            topic=self._string(item["topic"], "decision.topic"),
            title=self._string(item["title"], "decision.title"),
            decision=self._string(item["decision"], "decision.decision"),
            reason=self._string(item["reason"], "decision.reason"),
            status=status,
        )

    def _action(self, value: Any) -> ActionCandidate:
        item = self._object(value, "action")
        required = {
            "action_type", "tool_category", "operation", "reason", "impact", "payload"
        }
        if item.keys() != required:
            raise ResponseValidationError("Action fields are invalid.")
        payload = self._object(item["payload"], "action.payload")
        raw_type = self._string(item["action_type"], "action.action_type")
        normalized = self._normalize_action_type(raw_type)
        try:
            action_type = ActionType(normalized)
        except ValueError:
            valid = ", ".join(e.value for e in ActionType)
            category = item.get("tool_category", "?")
            raise ResponseValidationError(
                f"action_type must be one of [{valid}]; got '{raw_type}' "
                f"(tool_category={category}). For filesystem ops use 'mcp', "
                f"for shell commands use 'bash'."
            ) from None
        return ActionCandidate(
            action_type=action_type,
            tool_category=self._string(item["tool_category"], "action.tool_category"),
            operation=self._string(item["operation"], "action.operation"),
            reason=self._string(item["reason"], "action.reason"),
            impact=self._string(item["impact"], "action.impact"),
            payload=payload,
        )

    @staticmethod
    def _normalize_action_type(raw: str) -> str:
        lower = raw.lower().strip()
        mcp_operations = {
            "read_file", "write_file", "edit_file", "search", "grep", "glob",
            "find", "rg", "ls", "list_files", "file", "files",
        }
        bash_synonyms = {"shell", "sh", "zsh", "terminal", "command", "execute"}
        git_synonyms = {"git_cli", "vcs"}
        if lower in mcp_operations or lower.endswith("_file") or lower.endswith("_files"):
            return "mcp"
        if lower in bash_synonyms:
            return "bash"
        if lower in git_synonyms:
            return "git"
        return lower.replace("_", "").replace("-", "")

    @staticmethod
    def _string(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ResponseValidationError(f"{field} must be a string.")
        return value.strip()

    @staticmethod
    def _list(value: Any, field: str) -> list[Any]:
        if not isinstance(value, list):
            raise ResponseValidationError(f"{field} must be an array.")
        return value

    def _string_list(self, value: Any, field: str) -> list[str]:
        return [self._string(item, field) for item in self._list(value, field)]

    @staticmethod
    def _object(value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ResponseValidationError(f"{field} must be an object.")
        return value
