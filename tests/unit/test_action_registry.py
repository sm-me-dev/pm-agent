from __future__ import annotations

import pytest

from pm_agent.domain.actions import (
    ALL_GITHUB_OPERATIONS,
    Capability,
    GITHUB_ACTIONS,
    GITHUB_AUTH_OPERATIONS,
    GITHUB_READ_OPERATIONS,
    GITHUB_WRITE_OPERATIONS,
    capabilities_from_scopes,
    parse_token_scopes,
    required_capabilities,
    scopes_for_capabilities,
)


def test_registry_covers_read_write_and_auth():
    assert GITHUB_READ_OPERATIONS
    assert GITHUB_WRITE_OPERATIONS
    assert GITHUB_AUTH_OPERATIONS
    assert GITHUB_READ_OPERATIONS | GITHUB_WRITE_OPERATIONS | GITHUB_AUTH_OPERATIONS == ALL_GITHUB_OPERATIONS


def test_every_registered_action_declares_capabilities():
    for name, action in GITHUB_ACTIONS.items():
        assert action.kind in {"read", "write", "auth"}
        if action.kind != "auth":
            assert action.capabilities, f"{name} must declare capabilities"


def test_update_milestone_requires_write_milestones_capability():
    assert required_capabilities("update_milestone") == frozenset({Capability.WRITE_MILESTONES})


def test_repo_scope_grants_issue_and_milestone_but_not_project():
    granted = capabilities_from_scopes(["repo"])
    assert Capability.WRITE_ISSUES in granted
    assert Capability.WRITE_MILESTONES in granted
    assert Capability.READ_ISSUES in granted
    assert Capability.READ_MILESTONES in granted
    assert Capability.WRITE_PROJECTS not in granted
    assert Capability.READ_PROJECTS not in granted


def test_project_scopes_grant_project_capabilities():
    granted = capabilities_from_scopes(["read:project", "project"])
    assert Capability.READ_PROJECTS in granted
    assert Capability.WRITE_PROJECTS in granted


def test_capabilities_from_scopes_strips_quotes():
    granted = capabilities_from_scopes(["'repo'", '"read:project"'])
    assert Capability.WRITE_ISSUES in granted
    assert Capability.READ_PROJECTS in granted


def test_scopes_for_capabilities_reverses_mapping():
    assert "project" in scopes_for_capabilities({Capability.WRITE_PROJECTS})
    assert "read:project" in scopes_for_capabilities({Capability.READ_PROJECTS})
    assert "repo" in scopes_for_capabilities({Capability.WRITE_MILESTONES})


def test_parse_token_scopes_extracts_list():
    output = (
        "github.com\n"
        "  ✓ Logged in to github.com account u (keyring)\n"
        "  - Token scopes: 'admin:public_key', 'gist', 'read:org', 'read:project', 'repo'\n"
    )
    scopes = parse_token_scopes(output)
    assert scopes is not None
    assert "repo" in scopes
    assert "read:project" in scopes


def test_parse_token_scopes_returns_none_when_unauthenticated():
    assert parse_token_scopes("gh: To use GitHub CLI, please authenticate.\n") is None


@pytest.mark.parametrize(
    "operation,capability",
    [
        ("list_issues", Capability.READ_ISSUES),
        ("create_issue", Capability.WRITE_ISSUES),
        ("list_milestones", Capability.READ_MILESTONES),
        ("update_milestone", Capability.WRITE_MILESTONES),
        ("list_projects", Capability.READ_PROJECTS),
        ("create_project", Capability.WRITE_PROJECTS),
        ("add_issue_to_project", Capability.WRITE_PROJECTS),
    ],
)
def test_required_capabilities_match_expectations(operation, capability):
    assert capability in required_capabilities(operation)
