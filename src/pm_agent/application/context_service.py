from __future__ import annotations

from pathlib import Path

from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ActionCandidate, RepoSnapshot
from pm_agent.ports.repository_context import SnapshotRequest


class ContextService:
    def __init__(self, store, analyzer, action_service) -> None:
        self.store = store
        self.analyzer = analyzer
        self.action_service = action_service

    def propose_refresh(self, project_id: str, session_id: str, repo_path: str):
        return self.action_service.propose(
            project_id,
            session_id,
            ActionCandidate(
                action_type=ActionType.MCP,
                tool_category="filesystem",
                operation="inspect_repository",
                reason="Repository context is missing or stale.",
                impact="Build a bounded read-only repository snapshot for future PM analysis.",
                payload={"path": repo_path, "mode": "read_only", "mutating": False},
            ),
        )

    def record_host_snapshot(self, snapshot: RepoSnapshot, action_id: str) -> RepoSnapshot:
        action = self.store.get_action(action_id)
        if action is None or action.status.value not in {"approved", "dispatched"}:
            raise ValueError("Repository inspection requires an approved action.")
        if snapshot.project_id != action.project_id:
            raise ValueError("Snapshot project does not match the approved action.")
        if snapshot.created_by_action_id != action_id:
            raise ValueError("Snapshot must reference the approved action.")
        self.store.save_snapshot(snapshot)
        self.action_service.record_outcome(
            action_id,
            exit_code=0,
            result={"snapshot_id": snapshot.id, "tree_digest": snapshot.tree_digest},
        )
        return snapshot

    def complete_local_refresh(
        self,
        action_id: str,
        branch: str,
        expected_repo_path: str,
    ) -> RepoSnapshot:
        action = self.store.get_action(action_id)
        if action is None:
            raise KeyError(f"Unknown action: {action_id}")
        if action.status.value != "approved":
            raise ValueError("Local repository refresh requires an approved action.")
        if action.action_type is not ActionType.MCP or action.tool_category != "filesystem":
            raise ValueError("Action is not a filesystem repository refresh.")
        if action.operation not in {"inspect_repository", "read_repository"}:
            raise ValueError("Action is not a supported repository refresh.")
        approved_path = Path(str(action.payload.get("path", ""))).resolve()
        expected_path = Path(expected_repo_path).resolve()
        if approved_path != expected_path:
            raise ValueError("Repository refresh path must match the active project root.")
        snapshot = self.analyzer.build_snapshot(
            SnapshotRequest(
                project_id=action.project_id,
                repo_path=str(approved_path),
                branch=branch,
                action_id=action.id,
            )
        )
        return self.record_host_snapshot(snapshot, action.id)

    def load_context_files(
        self,
        project_id: str,
        session_id: str,
        base_path: str | None = None,
    ) -> list[str]:
        base = Path(base_path).expanduser().resolve() if base_path else Path.cwd()
        if (base / "context").is_dir():
            context_dir = base / "context"
        else:
            context_dir = base
        if not context_dir.is_dir():
            return []
        supported: frozenset[str] = frozenset({
            ".md", ".txt", ".json", ".yaml", ".yml", ".csv",
        })
        max_bytes = 100_000
        loaded: list[str] = []
        for path in sorted(context_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in supported:
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            if not content.strip():
                continue

            from pm_agent.domain.enums import MemoryKind
            from pm_agent.infrastructure.security.redaction import redact_text

            title = f"Context: {path.name}"
            new_note_content = f"Filename: {path.name}\n\n{content}"
            redacted_new_content = redact_text(new_note_content)

            with self.store.factory.connect() as connection:
                row = connection.execute(
                    "SELECT id, content FROM repository_notes WHERE project_id = ? AND category = ? AND title = ?",
                    (project_id, "planning_context", title),
                ).fetchone()
                if row:
                    if row["content"] == redacted_new_content:
                        loaded.append(path.name)
                        continue
                    # Content changed, delete old note and its FTS index
                    connection.execute("DELETE FROM repository_notes WHERE id = ?", (row["id"],))
                    connection.execute(
                        "DELETE FROM memory_fts WHERE kind = ? AND source_id = ?",
                        (MemoryKind.REPO_NOTE.value, row["id"]),
                    )

            self.store.add_repository_note(
                project_id,
                session_id,
                category="planning_context",
                title=title,
                content=new_note_content,
                source_type="filesystem",
                confidence=1.0,
            )
            loaded.append(path.name)
        return loaded
