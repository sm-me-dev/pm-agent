from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ActionCandidate, IntegrationInfo


class _GitHubAuth(NamedTuple):
    status: str
    authentication: str
    username: str | None


def _check_gh_auth() -> _GitHubAuth:
    gh = shutil.which("gh")
    if gh is None:
        return _GitHubAuth("unavailable", "GitHub CLI not installed.", None)
    try:
        result = subprocess.run(
            [gh, "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Logged in to github.com" in line:
                    username = line.split()[-1].strip()
                    return _GitHubAuth("connected", f"Authenticated as {username}", username)
            return _GitHubAuth("connected", "Authenticated (GitHub CLI)", None)
        stderr = (result.stderr or "").strip()
        if "not logged in" in stderr.lower():
            return _GitHubAuth("available", "GitHub CLI installed; not logged in.", None)
        if "auth token" in stderr.lower() or "oauth" in stderr.lower():
            return _GitHubAuth("available", "GitHub CLI installed; token expired or invalid.", None)
        return _GitHubAuth("available", f"GitHub CLI installed; auth check failed: {stderr[:80]}", None)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _GitHubAuth("available", f"GitHub CLI auth check error: {exc}", None)


class IntegrationService:
    def __init__(self, store, action_service) -> None:
        self.store = store
        self.action_service = action_service

    def list(self, project_id: str, repo_path: str) -> list[IntegrationInfo]:
        gh_auth = _check_gh_auth()

        git_available = (Path(repo_path) / ".git").exists() and shutil.which("git") is not None
        graphify_available = shutil.which("graphify") is not None
        return [
            IntegrationInfo(
                key="filesystem",
                name="Filesystem",
                status="available",
                authentication="not required",
                capabilities=[
                    "Read-only repository discovery",
                    "Bounded project snapshots",
                ],
            ),
            IntegrationInfo(
                key="git",
                name="Git",
                status="available" if git_available else "unavailable",
                authentication="not required for local inspection",
                capabilities=[
                    "Status and branch awareness",
                    "History, diff, show, and blame",
                ],
                setup_hint=None if git_available else "Initialize/open a Git repository.",
            ),
            IntegrationInfo(
                key="github",
                name="GitHub",
                status=gh_auth.status,
                authentication=gh_auth.authentication,
                capabilities=[
                    "Repository and issue inspection",
                    "Pull-request and release planning",
                    "Approval-gated issues, milestones, and sprint metadata",
                ],
                setup_hint=None if gh_auth.status != "unavailable" else "Install GitHub CLI (`gh`) first.",
            ),
            IntegrationInfo(
                key="graphify",
                name="Graphify",
                status="available" if graphify_available else "unavailable",
                authentication="host-managed",
                capabilities=[
                    "Architecture mapping",
                    "Dependency and impact analysis",
                ],
                setup_hint=None if graphify_available else "Install or enable the Graphify MCP server.",
            ),
            IntegrationInfo(
                key="sequential-thinking",
                name="Sequential Thinking",
                status="host-managed",
                authentication="not required",
                capabilities=[
                    "Roadmap decomposition",
                    "Sprint and dependency planning",
                ],
                setup_hint="Enable the MCP server in the OpenCode host.",
            ),
        ]

    def get(self, project_id: str, repo_path: str, key: str) -> IntegrationInfo | None:
        normalized = key.strip().lower()
        return next(
            (
                integration
                for integration in self.list(project_id, repo_path)
                if integration.key == normalized
            ),
            None,
        )

    def propose_connect_github(self, project_id: str, session_id: str):
        if shutil.which("gh") is None:
            raise ValueError("GitHub CLI (`gh`) is not installed or not available on PATH.")
        auth = _check_gh_auth()
        if auth.status == "connected":
            raise ValueError(f"GitHub is already connected: {auth.authentication}")
        return self.action_service.propose(
            project_id,
            session_id,
            ActionCandidate(
                action_type=ActionType.GITHUB,
                tool_category="github",
                operation="authenticate_browser",
                reason="Connect GitHub without placing credentials in agent memory or SQLite.",
                impact=(
                    "Opens GitHub's browser authentication flow and lets GitHub CLI store "
                    "credentials in its configured secure credential store."
                ),
                payload={
                    "hostname": "github.com",
                    "git_protocol": "https",
                    "browser": True,
                    "store_credentials": "github_cli",
                },
            ),
        )
