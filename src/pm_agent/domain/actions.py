from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    """Coarse permission a logged-in integration/API key must grant for an action.

    These are provider-agnostic so the policy and executor agree on what an
    action needs without depending on raw provider scope strings.
    """

    READ_REPO = "read_repo"
    WRITE_REPO = "write_repo"
    READ_PROJECTS = "read_projects"
    WRITE_PROJECTS = "write_projects"
    READ_MILESTONES = "read_milestones"
    WRITE_MILESTONES = "write_milestones"
    READ_ISSUES = "read_issues"
    WRITE_ISSUES = "write_issues"
    READ_PULL_REQUESTS = "read_pull_requests"
    WRITE_PULL_REQUESTS = "write_pull_requests"
    READ_RELEASES = "read_releases"


@dataclass(frozen=True)
class GitHubAction:
    name: str
    kind: str  # "read" | "write" | "auth"
    capabilities: frozenset[Capability]


GITHUB_ACTIONS: dict[str, GitHubAction] = {
    # Read-only operations
    "inspect_repository": GitHubAction("inspect_repository", "read", frozenset({Capability.READ_REPO})),
    "read_repository": GitHubAction("read_repository", "read", frozenset({Capability.READ_REPO})),
    "list_issues": GitHubAction("list_issues", "read", frozenset({Capability.READ_ISSUES})),
    "list_milestones": GitHubAction("list_milestones", "read", frozenset({Capability.READ_MILESTONES})),
    "list_projects": GitHubAction("list_projects", "read", frozenset({Capability.READ_PROJECTS})),
    "list_pull_requests": GitHubAction("list_pull_requests", "read", frozenset({Capability.READ_PULL_REQUESTS})),
    "list_releases": GitHubAction("list_releases", "read", frozenset({Capability.READ_RELEASES})),
    # Write operations
    "create_milestone": GitHubAction("create_milestone", "write", frozenset({Capability.WRITE_MILESTONES})),
    "update_milestone": GitHubAction("update_milestone", "write", frozenset({Capability.WRITE_MILESTONES})),
    "create_issue": GitHubAction("create_issue", "write", frozenset({Capability.WRITE_ISSUES})),
    "update_issue": GitHubAction("update_issue", "write", frozenset({Capability.WRITE_ISSUES})),
    "create_issues": GitHubAction("create_issues", "write", frozenset({Capability.WRITE_ISSUES})),
    "create_issue_comment": GitHubAction(
        "create_issue_comment", "write", frozenset({Capability.WRITE_ISSUES})
    ),
    "create_sub_issue": GitHubAction(
        "create_sub_issue", "write", frozenset({Capability.WRITE_ISSUES})
    ),
    "setup_sprint": GitHubAction("setup_sprint", "write", frozenset({Capability.WRITE_MILESTONES})),
    "add_issue_to_project": GitHubAction("add_issue_to_project", "write", frozenset({Capability.WRITE_PROJECTS})),
    "create_project": GitHubAction("create_project", "write", frozenset({Capability.WRITE_PROJECTS})),
    "create_project_item": GitHubAction("create_project_item", "write", frozenset({Capability.WRITE_PROJECTS})),
    # Authentication operations
    "authenticate_browser": GitHubAction("authenticate_browser", "auth", frozenset()),
    "disconnect": GitHubAction("disconnect", "auth", frozenset()),
}

GITHUB_AUTH_OPERATIONS = frozenset(
    name for name, action in GITHUB_ACTIONS.items() if action.kind == "auth"
)
GITHUB_READ_OPERATIONS = frozenset(
    name for name, action in GITHUB_ACTIONS.items() if action.kind == "read"
)
GITHUB_WRITE_OPERATIONS = frozenset(
    name for name, action in GITHUB_ACTIONS.items() if action.kind == "write"
)
ALL_GITHUB_OPERATIONS = frozenset(GITHUB_ACTIONS)


# GitHub token scope -> capabilities it grants.
SCOPE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "repo": frozenset({
        Capability.READ_REPO, Capability.WRITE_REPO,
        Capability.READ_ISSUES, Capability.WRITE_ISSUES,
        Capability.READ_MILESTONES, Capability.WRITE_MILESTONES,
        Capability.READ_PULL_REQUESTS, Capability.WRITE_PULL_REQUESTS,
        Capability.READ_RELEASES,
    }),
    "public_repo": frozenset({
        Capability.READ_REPO, Capability.WRITE_REPO,
        Capability.READ_ISSUES, Capability.WRITE_ISSUES,
        Capability.READ_MILESTONES, Capability.WRITE_MILESTONES,
        Capability.READ_PULL_REQUESTS, Capability.WRITE_PULL_REQUESTS,
        Capability.READ_RELEASES,
    }),
    "read:project": frozenset({Capability.READ_PROJECTS}),
    "project": frozenset({Capability.READ_PROJECTS, Capability.WRITE_PROJECTS}),
}

# Reverse mapping: capability -> scopes that grant it (for remediation messages).
# `public_repo` is intentionally omitted from suggestions; `repo` is the
# conventional scope to recommend and covers the same capabilities.
CAPABILITY_SCOPES: dict[Capability, list[str]] = {}
for _scope, _caps in SCOPE_CAPABILITIES.items():
    if _scope == "public_repo":
        continue
    for _cap in _caps:
        CAPABILITY_SCOPES.setdefault(_cap, [])
        if _scope not in CAPABILITY_SCOPES[_cap]:
            CAPABILITY_SCOPES[_cap].append(_scope)


def capabilities_from_scopes(scopes: Iterable[str]) -> frozenset[Capability]:
    granted: set[Capability] = set()
    for scope in scopes:
        cleaned = scope.strip().strip("'").strip('"')
        granted |= set(SCOPE_CAPABILITIES.get(cleaned, set()))
    return frozenset(granted)


def required_capabilities(operation: str) -> frozenset[Capability]:
    action = GITHUB_ACTIONS.get(operation)
    return action.capabilities if action else frozenset()


def scopes_for_capabilities(caps: Iterable[Capability]) -> list[str]:
    """Suggest the minimal set of token scopes that grant the missing capabilities.

    For each capability we pick the narrowest granting scope (fewest granted
    capabilities), then drop any suggested scope whose capabilities are already
    covered by the other suggestions. This avoids over-broad advice such as
    recommending both `read:project` and `project` for a read-only need.
    """
    chosen: list[str] = []
    for cap in caps:
        candidates = CAPABILITY_SCOPES.get(cap, [])
        if not candidates:
            continue
        narrowest = min(
            candidates,
            key=lambda s: (len(SCOPE_CAPABILITIES.get(s, frozenset())), s),
        )
        if narrowest not in chosen:
            chosen.append(narrowest)
    # Drop any scope that is strictly broader than the union of the others.
    final: list[str] = []
    for scope in chosen:
        others = [s for s in chosen if s != scope]
        others_caps = set().union(*(SCOPE_CAPABILITIES.get(s, frozenset()) for s in others)) \
            if others else frozenset()
        if not SCOPE_CAPABILITIES.get(scope, frozenset()) <= others_caps:
            final.append(scope)
    return final


def parse_token_scopes(status_output: str) -> list[str] | None:
    """Extract granted token scopes from `gh auth status` output.

    Returns None when the output does not contain a parseable scope line
    (e.g. the token is not authenticated, or the CLI version differs).
    """
    for line in status_output.splitlines():
        if "Token scopes:" in line:
            inside = line.split("Token scopes:", 1)[1]
            return [
                part.strip().strip("'").strip('"')
                for part in inside.split(",")
                if part.strip()
            ]
    return None
