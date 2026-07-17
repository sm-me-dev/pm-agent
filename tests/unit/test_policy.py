from __future__ import annotations

import pytest

from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ActionCandidate
from pm_agent.domain.policies import ActionPolicy, PolicyViolation


def candidate(action_type: ActionType, operation: str, payload: dict, category: str = "bash"):
    return ActionCandidate(
        action_type=action_type,
        tool_category=category,
        operation=operation,
        reason="Need confirmed context.",
        impact="Read project metadata.",
        payload=payload,
    )


def test_allows_read_only_git_status():
    result = ActionPolicy().evaluate(
        candidate(ActionType.GIT, "status", {"command": "git status"}, "git")
    )
    assert result.allowed
    assert result.risk_level == "low"


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/project status",
        "git --no-pager log --oneline -10",
        "git -c color.ui=false diff --stat",
    ],
)
def test_allows_common_read_only_git_global_options(command):
    result = ActionPolicy().evaluate(
        candidate(ActionType.GIT, "inspect_repository", {"command": command}, "git")
    )
    assert result.allowed


@pytest.mark.parametrize(
    "command",
    ["git commit -m x", "git reset --hard", "git checkout main", "git push origin main"],
)
def test_blocks_mutating_git(command):
    result = ActionPolicy().evaluate(
        candidate(ActionType.GIT, "inspect", {"command": command}, "git")
    )
    assert not result.allowed
    assert result.risk_level == "blocked"


def test_blocks_file_write_mcp():
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.MCP,
            "write_file",
            {"path": "src/app.py", "write": True},
            "filesystem",
        )
    )
    assert not result.allowed


def test_blocks_shell_pipeline_and_destructive_command():
    policy = ActionPolicy()
    assert not policy.evaluate(
        candidate(ActionType.BASH, "inspect", {"command": "cat file | head"})
    ).allowed
    with pytest.raises(PolicyViolation):
        policy.enforce(candidate(ActionType.BASH, "delete", {"command": "rm -rf ."}))


def test_allows_read_repository_github_alias_with_explicit_repository():
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.GITHUB,
            "read_repository",
            {"repository": "sm-me-dev/unified-workspace-engine"},
            "github",
        )
    )
    assert result.allowed
    assert result.risk_level == "medium"


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        (
            "create_milestone",
            {"milestone": {"title": "Sprint 1", "description": "Stabilize Git"}},
        ),
        (
            "create_issues",
            {"issues": [{"title": "Fix detection", "body": "Normalize operations."}]},
        ),
        (
            "setup_sprint",
            {
                "sprint": {
                    "title": "Sprint 1",
                    "goal": "Stabilize planning",
                    "start_date": "2026-07-13",
                    "end_date": "2026-07-27",
                }
            },
        ),
        (
            "add_issue_to_project",
            {"project_number": 1, "issue_numbers": [10, 11]},
        ),
    ],
)
def test_allows_approved_github_planning_operations(operation, payload):
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.GITHUB,
            operation,
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                **payload,
            },
            "github",
        )
    )
    assert result.allowed
    assert result.risk_level == "high"


def test_blocks_vague_github_sprint_proposal():
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.GITHUB,
            "setup_sprint",
            {
                "repository": "sm-me-dev/unified-workspace-engine",
                "sprint": {"title": "Sprint 1"},
            },
            "github",
        )
    )
    assert not result.allowed


def test_github_requires_explicit_repository_slug():
    result = ActionPolicy().evaluate(
        candidate(ActionType.GITHUB, "list_issues", {}, "github")
    )
    assert not result.allowed


def test_allows_browser_authentication_without_credentials_in_payload():
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.GITHUB,
            "authenticate_browser",
            {
                "hostname": "github.com",
                "git_protocol": "https",
                "browser": True,
            },
            "github",
        )
    )
    assert result.allowed
    assert result.risk_level == "high"


def test_blocks_credentials_inside_authentication_payload():
    result = ActionPolicy().evaluate(
        candidate(
            ActionType.GITHUB,
            "authenticate_browser",
            {
                "hostname": "github.com",
                "git_protocol": "https",
                "token": "never-store-this",
            },
            "github",
        )
    )
    assert not result.allowed
