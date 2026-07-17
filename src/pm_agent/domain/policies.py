from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from typing import Any

from .enums import ActionType
from .models import ActionCandidate

logger = logging.getLogger(__name__)


class PolicyViolation(ValueError):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    risk_level: str
    reason: str


_MUTATING_GIT = {
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit",
    "merge", "mv", "pull", "push", "rebase", "reset", "restore", "revert", "rm",
    "stash", "switch", "tag",
}
_DESTRUCTIVE_COMMANDS = {
    "chmod", "chown", "dd", "install", "kill", "mkfs", "mount", "mv", "reboot",
    "rm", "rmdir", "shutdown", "sudo", "systemctl", "truncate", "umount",
}
_WRITE_OPERATIONS = {
    "apply_patch", "create", "delete", "edit", "move", "patch", "rename", "replace",
    "truncate", "write",
}
_SHELL_META = re.compile(r"(?:^|[^\\])(?:[>|;&]|\$\(|`)")
_READ_OPERATION_WORDS = {
    "audit",
    "describe",
    "diff",
    "discover",
    "get",
    "history",
    "inspect",
    "issues",
    "list",
    "log",
    "milestones",
    "projects",
    "pull",
    "pulls",
    "read",
    "releases",
    "repository",
    "requests",
    "search",
    "show",
    "status",
    "tasks",
    "view",
}
_GITHUB_PLANNING_OPERATIONS = {
    "add_issue_to_project",
    "create_issue",
    "create_issues",
    "create_milestone",
    "create_project",
    "create_project_item",
    "setup_sprint",
    "update_issue",
    "update_milestone",
}
_GITHUB_AUTH_OPERATIONS = {"authenticate_browser", "disconnect"}


class ActionPolicy:
    def evaluate(self, candidate: ActionCandidate) -> PolicyDecision:
        operation = candidate.operation.strip().lower().replace("-", "_")
        logger.debug(
            "policy.evaluate: type=%s tool_category=%s operation=%s payload_keys=%s",
            candidate.action_type.value,
            candidate.tool_category,
            candidate.operation,
            list(candidate.payload.keys()) if candidate.payload else [],
        )

        if candidate.action_type is ActionType.GIT:
            if candidate.tool_category not in ("git", ""):
                return PolicyDecision(False, "blocked", f"Action type GIT requires tool_category in ('git', ''), got '{candidate.tool_category}'")
            return self._evaluate_git(candidate.payload)
        if candidate.action_type is ActionType.BASH:
            if candidate.tool_category not in ("filesystem", "shell", ""):
                logger.debug(
                    "policy.evaluate: BASH tool_category=%s not in allowed set, checking if it contains filesystem/shell",
                    candidate.tool_category,
                )
                cat_lower = candidate.tool_category.lower().replace("-", "_").replace(" ", "")
                if "filesystem" in cat_lower or "shell" in cat_lower or "file" in cat_lower:
                    logger.debug("policy.evaluate: BASH tool_category=%s accepted via fuzzy match", candidate.tool_category)
                else:
                    return PolicyDecision(False, "blocked", f"Action type BASH requires tool_category in ('filesystem', 'shell', ''), got '{candidate.tool_category}'")
            return self._evaluate_bash(candidate.payload)
        if candidate.action_type is ActionType.MCP:
            if candidate.tool_category not in {"filesystem", "git", "memory", "graphify", "sequential_thinking", "github"}:
                return PolicyDecision(False, "blocked", f"Action type MCP requires valid tool_category, got '{candidate.tool_category}'")
            return self._evaluate_mcp(candidate.tool_category, operation, candidate.payload)
        if candidate.action_type is ActionType.GITHUB:
            if candidate.tool_category not in ("github", "github_read", "issues", "repository", "pull_requests", ""):
                return PolicyDecision(False, "blocked", f"Action type GITHUB requires valid GitHub tool_category, got '{candidate.tool_category}'")
            return self._evaluate_github(operation, candidate.payload)
        if any(word in operation for word in _WRITE_OPERATIONS):
            return PolicyDecision(False, "blocked", "Source or filesystem mutation is prohibited.")
        return PolicyDecision(False, "blocked", "Unsupported action type.")

    def enforce(self, candidate: ActionCandidate) -> str:
        decision = self.evaluate(candidate)
        if not decision.allowed:
            raise PolicyViolation(decision.reason)
        return decision.risk_level

    def _evaluate_git(self, payload: dict[str, Any]) -> PolicyDecision:
        command = str(payload.get("command", "")).strip()
        try:
            parts = shlex.split(command)
        except ValueError:
            return PolicyDecision(False, "blocked", "Git command cannot be parsed safely.")
        if not parts or parts[0] != "git":
            return PolicyDecision(False, "blocked", "Git actions must use an explicit git command.")
        subcommand = self._git_subcommand(parts)
        if subcommand is None:
            return PolicyDecision(False, "blocked", "Git global options cannot be parsed safely.")
        if subcommand in _MUTATING_GIT:
            return PolicyDecision(False, "blocked", f"Mutating git operation '{subcommand}' is prohibited.")
        if subcommand not in {"blame", "diff", "log", "remote", "rev-parse", "show", "status"}:
            return PolicyDecision(False, "blocked", "Only explicitly read-only git operations are allowed.")
        return PolicyDecision(True, "low", "Read-only git inspection requires approval.")

    def _evaluate_bash(self, payload: dict[str, Any]) -> PolicyDecision:
        command = str(payload.get("command", "")).strip()
        if not command:
            command = str(payload.get("cmd", "")).strip()
        if not command:
            command = str(payload.get("shell_command", "")).strip()
        if not command:
            logger.debug("policy._evaluate_bash: no command in payload keys=%s payload=%s", list(payload.keys()), payload)
            return PolicyDecision(False, "blocked", "A bash proposal requires a command.")
        if _SHELL_META.search(command):
            logger.debug("policy._evaluate_bash: shell meta detected in command=%s", command)
            return PolicyDecision(False, "blocked", "Shell redirection, pipelines, and substitutions are prohibited.")
        try:
            parts = shlex.split(command)
        except ValueError:
            return PolicyDecision(False, "blocked", "Command cannot be parsed safely.")
        executable = parts[0].rsplit("/", 1)[-1].lower()
        if executable in _DESTRUCTIVE_COMMANDS:
            return PolicyDecision(False, "blocked", f"Command '{executable}' is prohibited.")
        if executable in {"git"}:
            return self._evaluate_git(payload)
        allowed = {"cat", "find", "head", "ls", "pwd", "rg", "sed", "tail", "wc"}
        if executable not in allowed:
            logger.debug("policy._evaluate_bash: executable=%s not in allowed set", executable)
            return PolicyDecision(
                False, "blocked", "Standalone bash is limited to a small read-only inspection allowlist."
            )
        return PolicyDecision(True, "medium", "Read-only shell inspection requires approval.")

    def _evaluate_github(
        self, operation: str, payload: dict[str, Any]
    ) -> PolicyDecision:
        if operation in _GITHUB_AUTH_OPERATIONS:
            hostname = str(payload.get("hostname", "github.com")).strip().lower()
            protocol = str(payload.get("git_protocol", "https")).strip().lower()
            if hostname != "github.com" or protocol not in {"https", "ssh"}:
                return PolicyDecision(
                    False,
                    "blocked",
                    "GitHub authentication is limited to github.com and https/ssh.",
                )
            if any(key in payload for key in {"token", "password", "secret", "api_key"}):
                return PolicyDecision(
                    False,
                    "blocked",
                    "Authentication payloads must never contain credentials.",
                )
            return PolicyDecision(
                True,
                "high",
                "Browser authentication changes local GitHub CLI credentials and requires approval.",
            )
        repository = str(payload.get("repository", payload.get("repo", ""))).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]+", repository):
            return PolicyDecision(
                False,
                "blocked",
                "GitHub actions require an explicit owner/repository payload.",
            )
        if operation in _GITHUB_PLANNING_OPERATIONS:
            validation_error = self._validate_github_planning_payload(operation, payload)
            if validation_error:
                return PolicyDecision(False, "blocked", validation_error)
            return PolicyDecision(
                True,
                "high",
                "GitHub planning mutation requires explicit payload-specific approval.",
            )
        words = {part for part in operation.split("_") if part}
        if words and words <= _READ_OPERATION_WORDS and words & {
            "audit", "describe", "discover", "get", "inspect", "list", "read", "search",
            "show", "status", "view",
        }:
            return PolicyDecision(True, "medium", "Read-only GitHub access requires approval.")
        return PolicyDecision(
            False,
            "blocked",
            "Unsupported GitHub operation; only inspection and issue/milestone planning are allowed.",
        )

    @staticmethod
    def _validate_github_planning_payload(
        operation: str, payload: dict[str, Any]
    ) -> str | None:
        def populated_string(value: Any) -> bool:
            return isinstance(value, str) and bool(value.strip())

        if operation in {"create_issue", "update_issue"}:
            issue = payload.get("issue", payload)
            if not isinstance(issue, dict) or not populated_string(issue.get("title")):
                return "GitHub issue actions require an exact issue title."
            if not populated_string(issue.get("body")):
                return "GitHub issue actions require an exact issue body."
        elif operation == "create_issues":
            issues = payload.get("issues")
            if not isinstance(issues, list) or not issues:
                return "create_issues requires a non-empty issues array."
            if any(
                not isinstance(issue, dict)
                or not populated_string(issue.get("title"))
                or not populated_string(issue.get("body"))
                for issue in issues
            ):
                return "Every proposed GitHub issue requires an exact title and body."
        elif operation in {"create_milestone", "update_milestone", "setup_sprint"}:
            milestone = payload.get("milestone", payload.get("sprint", payload))
            if not isinstance(milestone, dict) or not populated_string(milestone.get("title")):
                return "Sprint and milestone actions require an exact title."
            if operation == "setup_sprint" and not all(
                populated_string(milestone.get(field))
                for field in ("goal", "start_date", "end_date")
            ):
                return "setup_sprint requires goal, start_date, and end_date."
        elif operation == "create_project":
            name = payload.get("name", payload.get("project", {}).get("name"))
            if not populated_string(name):
                return "create_project requires a project name in the payload."
        elif operation == "create_project_item":
            issue_numbers = payload.get("issue_numbers", [])
            has_project = isinstance(payload.get("project_number"), int) or populated_string(
                payload.get("project_title")
            )
            if (
                not isinstance(issue_numbers, list)
                or not issue_numbers
                or not all(isinstance(number, int) and number > 0 for number in issue_numbers)
                or not has_project
            ):
                return "create_project_item requires positive issue_numbers and a project number or title."
        elif operation == "add_issue_to_project":
            issue_numbers = payload.get("issue_numbers")
            has_project = isinstance(payload.get("project_number"), int) or populated_string(
                payload.get("project_title")
            )
            if (
                not isinstance(issue_numbers, list)
                or not issue_numbers
                or not all(isinstance(number, int) and number > 0 for number in issue_numbers)
                or not has_project
            ):
                return (
                    "add_issue_to_project requires positive issue_numbers and a project "
                    "number or title."
                )
        return None

    _GITHUB_OPS = frozenset({
        "add_issue_to_project", "create_issue", "create_issues", "create_milestone",
        "create_project", "inspect_repository", "list_issues", "list_milestones",
        "list_projects", "list_pull_requests", "list_releases", "setup_sprint",
        "update_issue", "update_milestone",
    })

    def _evaluate_mcp(
        self, category: str, operation: str, payload: dict[str, Any]
    ) -> PolicyDecision:
        normalized = category.lower()
        if normalized not in {
            "filesystem", "git", "memory", "graphify", "sequential_thinking", "github"
        }:
            return PolicyDecision(False, "blocked", "Unknown MCP category.")
        if normalized in {"filesystem", "git", "github"} and any(
            word in operation for word in _WRITE_OPERATIONS
        ):
            if operation in self._GITHUB_OPS:
                pass
            else:
                return PolicyDecision(False, "blocked", "Mutating MCP operations are prohibited.")
        if payload.get("write") is True or payload.get("mutating") is True:
            return PolicyDecision(False, "blocked", "MCP payload declares a mutating operation.")
        return PolicyDecision(True, "medium", f"{normalized} MCP access requires approval.")

    @staticmethod
    def _git_subcommand(parts: list[str]) -> str | None:
        index = 1
        options_with_values = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
        standalone_options = {"--bare", "--no-pager", "--paginate", "--version"}
        while index < len(parts):
            part = parts[index]
            if part == "--":
                index += 1
                break
            if part in options_with_values:
                index += 2
                continue
            if any(
                part.startswith(prefix)
                for prefix in ("--git-dir=", "--work-tree=", "--namespace=")
            ):
                index += 1
                continue
            if part in standalone_options:
                index += 1
                continue
            if part.startswith("-"):
                return None
            return part
        return parts[index] if index < len(parts) else ""
