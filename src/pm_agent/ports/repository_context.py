from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pm_agent.domain.models import RepoSnapshot


@dataclass(frozen=True)
class SnapshotRequest:
    project_id: str
    repo_path: str
    branch: str
    action_id: str | None = None


class RepositoryContextProvider(Protocol):
    def build_snapshot(self, request: SnapshotRequest) -> RepoSnapshot: ...
