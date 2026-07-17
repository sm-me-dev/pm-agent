from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pm_agent.domain.approval_rules import ApprovalRule, proposal_matches_rule
from pm_agent.domain.enums import ActionStatus, ActionType, DecisionStatus, MemoryKind
from pm_agent.domain.models import (
    ActionOutcome,
    ActionProposal,
    ContextPacket,
    Decision,
    MemoryCounts,
    MemoryItem,
    Message,
    Project,
    RepositoryNote,
    RepoSnapshot,
    Session,
    SessionSummary,
    canonical_json,
    new_id,
    payload_hash,
    project_fingerprint,
    utc_now,
)
from pm_agent.domain.transitions import ensure_action_transition
from pm_agent.infrastructure.security.redaction import redact_text, redact_value
from pm_agent.ports.memory import RetrievalQuery

from .connection import SQLiteConnectionFactory
from .migrations import apply_migrations

_FTS_TOKEN = re.compile(r"[\w-]+", re.UNICODE)


class SQLiteStore:
    def __init__(self, db_path: str | Path) -> None:
        self.factory = SQLiteConnectionFactory(db_path)
        self.db_path = self.factory.db_path
        with self.factory.connect() as connection:
            apply_migrations(connection)
            self._import_legacy(connection)

    def resolve_project(self, repo_path: str | Path, branch: str = "unknown") -> Project:
        canonical = str(Path(repo_path).resolve())
        now = utc_now()
        with self.factory.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE canonical_path = ?", (canonical,)
            ).fetchone()
            if row is None:
                project_id = new_id()
                connection.execute(
                    """INSERT INTO projects
                       (id, name, canonical_path, repo_fingerprint, default_branch, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        Path(canonical).name,
                        canonical,
                        project_fingerprint(canonical),
                        branch,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
            else:
                connection.execute(
                    "UPDATE projects SET default_branch = ?, updated_at = ? WHERE id = ?",
                    (branch, now, row["id"]),
                )
        return self._project(row)

    def start_session(
        self,
        project_id: str,
        name: str,
        model: str,
        provider: str,
        branch: str,
    ) -> Session:
        session = Session(
            id=new_id(),
            project_id=project_id,
            name=name,
            model=model,
            provider=provider,
            branch=branch,
            status="active",
            started_at=utc_now(),
        )
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO sessions_v2
                   (id, project_id, name, model, provider, branch, status, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.project_id,
                    session.name,
                    session.model,
                    session.provider,
                    session.branch,
                    session.status,
                    session.started_at,
                ),
            )
        return session

    def close_session(self, session_id: str, status: str = "closed") -> None:
        if status not in {"closed", "abandoned"}:
            raise ValueError("Session can only close as closed or abandoned.")
        with self.factory.connect() as connection:
            connection.execute(
                "UPDATE sessions_v2 SET status = ?, ended_at = ? WHERE id = ?",
                (status, utc_now(), session_id),
            )

    def add_message(self, session_id: str, role: str, content: str) -> Message:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("Invalid message role.")
        redacted = redact_text(content)
        with self.factory.connect() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM messages_v2 WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            message = Message(
                id=new_id(),
                session_id=session_id,
                sequence_no=sequence,
                role=role,
                content=redacted,
                created_at=utc_now(),
            )
            connection.execute(
                """INSERT INTO messages_v2
                   (id, session_id, sequence_no, role, content, content_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.id,
                    message.session_id,
                    message.sequence_no,
                    message.role,
                    message.content,
                    hashlib.sha256(message.content.encode()).hexdigest(),
                    message.created_at,
                ),
            )
            project_id = connection.execute(
                "SELECT project_id FROM sessions_v2 WHERE id = ?", (session_id,)
            ).fetchone()[0]
            self._index_memory(
                connection,
                project_id,
                MemoryKind.MESSAGE,
                message.id,
                role,
                message.content,
                message.created_at,
                "active",
            )
        return message

    def get_recent_messages(self, session_id: str, limit: int = 75) -> list[Message]:
        bounded = max(1, min(limit, 100))
        with self.factory.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM messages_v2 WHERE session_id = ?
                   ORDER BY sequence_no DESC LIMIT ?""",
                (session_id, bounded),
            ).fetchall()
        return [self._message(row) for row in reversed(rows)]

    def add_decision(
        self,
        project_id: str,
        session_id: str | None,
        topic: str,
        title: str,
        decision: str,
        reason: str,
        status: DecisionStatus = DecisionStatus.PROPOSED,
        supersedes_id: str | None = None,
        fingerprint: str = "",
    ) -> Decision:
        now = utc_now()
        item = Decision(
            id=new_id(),
            project_id=project_id,
            session_id=session_id,
            topic=topic,
            title=title,
            decision=redact_text(decision),
            reason=redact_text(reason),
            status=status,
            supersedes_id=supersedes_id,
            created_at=now,
            updated_at=now,
            fingerprint=fingerprint,
        )
        with self.factory.connect() as connection:
            if supersedes_id:
                connection.execute(
                    """UPDATE decisions_v2 SET status = 'superseded', updated_at = ?
                       WHERE id = ? AND project_id = ?""",
                    (now, supersedes_id, project_id),
                )
            connection.execute(
                """INSERT INTO decisions_v2
                    (id, project_id, session_id, topic, title, decision, reason, status,
                     supersedes_id, created_at, updated_at, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id,
                    item.project_id,
                    item.session_id,
                    item.topic,
                    item.title,
                    item.decision,
                    item.reason,
                    item.status.value,
                    item.supersedes_id,
                    item.created_at,
                    item.updated_at,
                    item.fingerprint,
                ),
            )
            self._index_memory(
                connection,
                project_id,
                MemoryKind.DECISION,
                item.id,
                f"{topic} | {title}",
                f"{item.decision}\nReason: {item.reason}",
                item.created_at,
                item.status.value,
            )
        return item

    def find_decision_in_session(
        self, project_id: str, session_id: str, fingerprint: str
    ) -> Decision | None:
        """Return the most recent decision with the same fingerprint in this session.

        Used to suppress re-prompting for a decision the model re-proposes
        on a later REPL loop iteration after it was already resolved.
        """
        if not fingerprint:
            return None
        with self.factory.connect() as connection:
            row = connection.execute(
                """SELECT * FROM decisions_v2
                   WHERE project_id = ? AND session_id = ? AND fingerprint = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (project_id, session_id, fingerprint),
            ).fetchone()
        return self._decision(row) if row else None

    def set_decision_status(
        self, decision_id: str, project_id: str, status: DecisionStatus
    ) -> None:
        now = utc_now()
        with self.factory.connect() as connection:
            connection.execute(
                "UPDATE decisions_v2 SET status = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (status.value, now, decision_id, project_id),
            )
            connection.execute(
                "UPDATE memory_fts SET status = ? WHERE kind = ? AND source_id = ?",
                (status.value, MemoryKind.DECISION.value, decision_id),
            )

    def list_decisions(
        self, project_id: str, statuses: tuple[DecisionStatus, ...] | None = None, limit: int = 50
    ) -> list[Decision]:
        params: list[Any] = [project_id]
        sql = "SELECT * FROM decisions_v2 WHERE project_id = ?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.factory.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._decision(row) for row in rows]

    def add_repository_note(
        self,
        project_id: str,
        session_id: str | None,
        category: str,
        title: str,
        content: str,
        source_type: str,
        source_ref: str | None = None,
        confidence: float = 1.0,
        stale_after: str | None = None,
    ) -> RepositoryNote:
        if source_type not in {"filesystem", "git", "graphify", "mcp", "human"}:
            raise ValueError("Invalid repository note source.")
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between zero and one.")
        now = utc_now()
        item = RepositoryNote(
            id=new_id(),
            project_id=project_id,
            session_id=session_id,
            category=category,
            title=title,
            content=redact_text(content),
            source_type=source_type,
            source_ref=source_ref,
            confidence=confidence,
            stale_after=stale_after,
            created_at=now,
            updated_at=now,
        )
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO repository_notes
                   (id, project_id, session_id, category, title, content, source_type,
                    source_ref, confidence, stale_after, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id,
                    item.project_id,
                    item.session_id,
                    item.category,
                    item.title,
                    item.content,
                    item.source_type,
                    item.source_ref,
                    item.confidence,
                    item.stale_after,
                    item.created_at,
                    item.updated_at,
                ),
            )
            self._index_memory(
                connection,
                project_id,
                MemoryKind.REPO_NOTE,
                item.id,
                item.title,
                item.content,
                item.created_at,
                "active",
            )
        return item

    def save_snapshot(self, snapshot: RepoSnapshot) -> None:
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO repo_snapshots
                   (id, project_id, branch, head_ref, tree_digest, summary_json,
                    created_by_action_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.id,
                    snapshot.project_id,
                    snapshot.branch,
                    snapshot.head_ref,
                    snapshot.tree_digest,
                    canonical_json(redact_value(snapshot.summary)),
                    snapshot.created_by_action_id,
                    snapshot.created_at,
                ),
            )

    def latest_snapshot(self, project_id: str) -> RepoSnapshot | None:
        with self.factory.connect() as connection:
            row = connection.execute(
                """SELECT * FROM repo_snapshots WHERE project_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        return self._snapshot(row) if row else None

    def save_summary(self, summary: SessionSummary) -> None:
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO session_summaries
                   (id, project_id, session_id, key_topics_json, decisions_json,
                    planned_actions_json, open_questions_json, narrative, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                    key_topics_json = excluded.key_topics_json,
                    decisions_json = excluded.decisions_json,
                    planned_actions_json = excluded.planned_actions_json,
                    open_questions_json = excluded.open_questions_json,
                    narrative = excluded.narrative,
                    created_at = excluded.created_at""",
                (
                    summary.id,
                    summary.project_id,
                    summary.session_id,
                    canonical_json(summary.key_topics),
                    canonical_json(summary.decisions),
                    canonical_json(summary.planned_actions),
                    canonical_json(summary.open_questions),
                    redact_text(summary.narrative),
                    summary.created_at,
                ),
            )
            self._index_memory(
                connection,
                summary.project_id,
                MemoryKind.SUMMARY,
                summary.id,
                "Session summary",
                redact_text(summary.narrative),
                summary.created_at,
                "active",
            )

    def create_action(self, proposal: ActionProposal) -> None:
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO action_proposals
                    (id, project_id, session_id, action_type, tool_category, operation,
                     reason, impact, payload_json, payload_sha256, risk_level, status,
                     created_at, expires_at, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.id,
                    proposal.project_id,
                    proposal.session_id,
                    proposal.action_type.value,
                    proposal.tool_category,
                    proposal.operation,
                    redact_text(proposal.reason),
                    redact_text(proposal.impact),
                    canonical_json(redact_value(proposal.payload)),
                    proposal.payload_sha256,
                    proposal.risk_level,
                    proposal.status.value,
                    proposal.created_at,
                    proposal.expires_at,
                    proposal.fingerprint,
                ),
            )
            self._append_action_event(
                connection, proposal.id, "proposed", "agent", {"risk": proposal.risk_level}
            )

    def find_action_in_session(
        self, project_id: str, session_id: str, fingerprint: str
    ) -> ActionProposal | None:
        """Return the most recent action proposal with the same fingerprint in this session.

        Used to suppress re-prompting for an action the model re-proposes
        on a later REPL loop iteration after it was already resolved.
        """
        if not fingerprint:
            return None
        with self.factory.connect() as connection:
            row = connection.execute(
                """SELECT * FROM action_proposals
                   WHERE project_id = ? AND session_id = ? AND fingerprint = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, session_id, fingerprint),
            ).fetchone()
        return self._action(row) if row else None

    def get_action(self, action_id: str) -> ActionProposal | None:
        with self.factory.connect() as connection:
            row = connection.execute(
                "SELECT * FROM action_proposals WHERE id = ?", (action_id,)
            ).fetchone()
        return self._action(row) if row else None

    def list_actions(self, project_id: str, limit: int = 50) -> list[ActionProposal]:
        with self.factory.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM action_proposals WHERE project_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        return [self._action(row) for row in rows]

    def transition_action(
        self,
        action_id: str,
        target: ActionStatus,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> ActionProposal:
        with self.factory.connect() as connection:
            row = connection.execute(
                "SELECT * FROM action_proposals WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown action: {action_id}")
            current = ActionStatus(row["status"])
            ensure_action_transition(current, target)
            connection.execute(
                "UPDATE action_proposals SET status = ? WHERE id = ?",
                (target.value, action_id),
            )
            self._append_action_event(connection, action_id, target.value, actor, details or {})
            updated = connection.execute(
                "SELECT * FROM action_proposals WHERE id = ?", (action_id,)
            ).fetchone()
        return self._action(updated)

    def action_has_approval(self, action_id: str, payload_sha256: str) -> bool:
        with self.factory.connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM action_proposals p
                   WHERE p.id = ? AND p.status = 'approved' AND p.payload_sha256 = ?
                   AND EXISTS (
                     SELECT 1 FROM action_events e
                     WHERE e.action_id = p.id AND e.event_type = 'approved'
                   )""",
                (action_id, payload_sha256),
            ).fetchone()
        return row is not None

    def record_outcome(self, outcome: ActionOutcome) -> ActionProposal:
        target = ActionStatus.SUCCEEDED if outcome.exit_code in {0, None} else ActionStatus.FAILED
        with self.factory.connect() as connection:
            row = connection.execute(
                "SELECT * FROM action_proposals WHERE id = ?", (outcome.action_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown action: {outcome.action_id}")
            current = ActionStatus(row["status"])
            if current in {ActionStatus.SUCCEEDED, ActionStatus.FAILED}:
                return self._action(row)
            ensure_action_transition(current, target)
            connection.execute(
                """INSERT INTO action_outcomes
                   (action_id, host_correlation_id, exit_code, stdout_redacted,
                    stderr_redacted, result_json, started_at, completed_at, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(action_id) DO NOTHING""",
                (
                    outcome.action_id,
                    outcome.host_correlation_id,
                    outcome.exit_code,
                    redact_text(outcome.stdout),
                    redact_text(outcome.stderr),
                    canonical_json(redact_value(outcome.result)),
                    outcome.started_at,
                    outcome.completed_at,
                    outcome.recorded_at,
                ),
            )
            connection.execute(
                "UPDATE action_proposals SET status = ? WHERE id = ?",
                (target.value, outcome.action_id),
            )
            self._append_action_event(
                connection,
                outcome.action_id,
                target.value,
                "host",
                {"exit_code": outcome.exit_code},
            )
            updated = connection.execute(
                "SELECT * FROM action_proposals WHERE id = ?", (outcome.action_id,)
            ).fetchone()
            self._index_memory(
                connection,
                updated["project_id"],
                MemoryKind.ACTION_OUTCOME,
                outcome.action_id,
                f"Action {target.value}: {updated['operation']}",
                redact_text(outcome.stdout or outcome.stderr or canonical_json(outcome.result)),
                outcome.recorded_at,
                target.value,
            )
        return self._action(updated)

    def memory_counts(self, project_id: str) -> MemoryCounts:
        with self.factory.connect() as connection:
            messages = connection.execute(
                """SELECT COUNT(*) FROM messages_v2 m
                   JOIN sessions_v2 s ON s.id = m.session_id WHERE s.project_id = ?""",
                (project_id,),
            ).fetchone()[0]
            decisions = connection.execute(
                "SELECT COUNT(*) FROM decisions_v2 WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            notes = connection.execute(
                "SELECT COUNT(*) FROM repository_notes WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        return MemoryCounts(messages=messages, decisions=decisions, repo_notes=notes)

    def retrieve(self, query: RetrievalQuery) -> ContextPacket:
        recent = self.get_recent_messages(query.session_id, query.history_limit)
        snapshot = self.latest_snapshot(query.project_id)
        terms = _FTS_TOKEN.findall(query.text.lower())
        items: list[MemoryItem] = []
        if terms:
            fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])
            with self.factory.connect() as connection:
                rows = connection.execute(
                    """SELECT kind, source_id, title, content, created_at, status,
                              bm25(memory_fts) AS rank
                       FROM memory_fts
                       WHERE memory_fts MATCH ? AND project_id = ?
                       ORDER BY rank LIMIT ?""",
                    (fts_query, query.project_id, query.item_limit * 3),
                ).fetchall()
            weights = {
                MemoryKind.DECISION.value: 5.0,
                MemoryKind.REPO_NOTE.value: 4.0,
                MemoryKind.SUMMARY.value: 3.0,
                MemoryKind.ACTION_OUTCOME.value: 2.0,
                MemoryKind.MESSAGE.value: 1.0,
            }
            for row in rows:
                if row["kind"] == MemoryKind.DECISION.value and row["status"] != "accepted":
                    continue
                try:
                    age_days = max(
                        0.0,
                        (
                            datetime.now(UTC)
                            - datetime.fromisoformat(row["created_at"]).astimezone(UTC)
                        ).total_seconds()
                        / 86_400,
                    )
                except (TypeError, ValueError):
                    age_days = 365.0
                recency = 1.0 / (1.0 + age_days / 30.0)
                score = (
                    weights.get(row["kind"], 0.0)
                    + max(0.0, -float(row["rank"]))
                    + recency
                )
                items.append(
                    MemoryItem(
                        kind=MemoryKind(row["kind"]),
                        source_id=row["source_id"],
                        title=row["title"],
                        content=row["content"],
                        created_at=row["created_at"],
                        score=score,
                    )
                )
        items.sort(key=lambda item: (item.score, item.created_at), reverse=True)
        bounded: list[MemoryItem] = []
        used = 0
        for item in items:
            size = len(item.title) + len(item.content)
            if used + size > query.character_budget:
                continue
            bounded.append(item)
            used += size
            if len(bounded) >= query.item_limit:
                break
        return ContextPacket(items=bounded, recent_messages=recent, repository_snapshot=snapshot)

    def add_approval_rule(self, rule: ApprovalRule) -> None:
        with self.factory.connect() as connection:
            connection.execute(
                """INSERT INTO approval_rules
                   (id, project_id, action_type, tool_category, operation,
                    payload_pattern, reason, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.id,
                    rule.project_id,
                    rule.action_type,
                    rule.tool_category,
                    rule.operation,
                    rule.payload_pattern,
                    rule.reason,
                    rule.created_at,
                    rule.created_by,
                ),
            )

    def find_approval_rule(self, proposal: ActionProposal) -> ApprovalRule | None:
        with self.factory.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM approval_rules WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (proposal.project_id,),
            ).fetchall()
        for row in rows:
            rule = self._approval_rule(row)
            if proposal_matches_rule(proposal, rule):
                return rule
        return None

    def list_approval_rules(self, project_id: str) -> list[ApprovalRule]:
        with self.factory.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM approval_rules WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        return [self._approval_rule(row) for row in rows]

    def revoke_approval_rule(self, rule_id: str, project_id: str) -> None:
        with self.factory.connect() as connection:
            connection.execute(
                "DELETE FROM approval_rules WHERE id = ? AND project_id = ?",
                (rule_id, project_id),
            )

    def checkpoint(self) -> None:
        with self.factory.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def _import_legacy(self, connection: Any) -> None:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {"sessions", "messages", "decisions", "actions"} <= tables:
            return
        imported = connection.execute(
            "SELECT 1 FROM legacy_import_map WHERE entity_type = 'database' AND legacy_id = 'v1'"
        ).fetchone()
        if imported:
            return

        project_ids: dict[str, str] = {}
        session_ids: dict[int, str] = {}
        for row in connection.execute("SELECT * FROM sessions ORDER BY id").fetchall():
            repo_path = str(Path(row["repo_path"]).expanduser().resolve())
            project_id = project_ids.get(repo_path)
            if not project_id:
                existing = connection.execute(
                    "SELECT id FROM projects WHERE canonical_path = ?", (repo_path,)
                ).fetchone()
                project_id = existing["id"] if existing else new_id()
                if not existing:
                    now = row["started_at"] or utc_now()
                    connection.execute(
                        """INSERT INTO projects
                           (id, name, canonical_path, repo_fingerprint, default_branch,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            project_id,
                            Path(repo_path).name,
                            repo_path,
                            project_fingerprint(repo_path),
                            row["branch"],
                            now,
                            now,
                        ),
                    )
                project_ids[repo_path] = project_id
            session_id = new_id()
            session_ids[row["id"]] = session_id
            connection.execute(
                """INSERT INTO sessions_v2
                   (id, project_id, name, model, provider, branch, status, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    project_id,
                    row["name"],
                    "unknown",
                    "legacy",
                    row["branch"],
                    "closed" if row["ended_at"] else "abandoned",
                    row["started_at"],
                    row["ended_at"],
                ),
            )
            connection.execute(
                "INSERT INTO legacy_import_map VALUES ('session', ?, ?)",
                (str(row["id"]), session_id),
            )

        sequences: dict[int, int] = {}
        for row in connection.execute("SELECT * FROM messages ORDER BY id").fetchall():
            session_id = session_ids.get(row["session_id"])
            if not session_id:
                continue
            sequences[row["session_id"]] = sequences.get(row["session_id"], 0) + 1
            message_id = new_id()
            content = redact_text(row["content"])
            connection.execute(
                """INSERT INTO messages_v2
                   (id, session_id, sequence_no, role, content, content_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    session_id,
                    sequences[row["session_id"]],
                    row["role"],
                    content,
                    hashlib.sha256(content.encode()).hexdigest(),
                    row["created_at"],
                ),
            )
            project_id = connection.execute(
                "SELECT project_id FROM sessions_v2 WHERE id = ?", (session_id,)
            ).fetchone()[0]
            self._index_memory(
                connection,
                project_id,
                MemoryKind.MESSAGE,
                message_id,
                row["role"],
                content,
                row["created_at"],
                "active",
            )

        status_map = {"active": "accepted", "superseded": "superseded", "rejected": "rejected"}
        for row in connection.execute("SELECT * FROM decisions ORDER BY id").fetchall():
            session_id = session_ids.get(row["session_id"])
            if not session_id:
                continue
            project_id = connection.execute(
                "SELECT project_id FROM sessions_v2 WHERE id = ?", (session_id,)
            ).fetchone()[0]
            decision_id = new_id()
            status = status_map.get(row["status"], "proposed")
            connection.execute(
                """INSERT INTO decisions_v2
                   (id, project_id, session_id, topic, title, decision, reason, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    project_id,
                    session_id,
                    row["topic"],
                    row["title"],
                    redact_text(row["decision"]),
                    redact_text(row["rationale"]),
                    status,
                    row["created_at"],
                    row["created_at"],
                ),
            )
            self._index_memory(
                connection,
                project_id,
                MemoryKind.DECISION,
                decision_id,
                f"{row['topic']} | {row['title']}",
                f"{row['decision']}\nReason: {row['rationale']}",
                row["created_at"],
                status,
            )

        action_status = {
            "planned": "proposed",
            "executed": "succeeded",
            "rejected": "rejected",
            "failed": "failed",
        }
        for row in connection.execute("SELECT * FROM actions ORDER BY id").fetchall():
            session_id = session_ids.get(row["session_id"])
            if not session_id:
                continue
            project_id = connection.execute(
                "SELECT project_id FROM sessions_v2 WHERE id = ?", (session_id,)
            ).fetchone()[0]
            action_id = new_id()
            payload = {"command": row["command"]}
            status = action_status.get(row["status"], "proposed")
            action_type = "git" if str(row["command"]).lstrip().startswith("git ") else "bash"
            connection.execute(
                """INSERT INTO action_proposals
                   (id, project_id, session_id, action_type, tool_category, operation,
                    reason, impact, payload_json, payload_sha256, risk_level, status,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    action_id,
                    project_id,
                    session_id,
                    action_type,
                    action_type,
                    "legacy_command",
                    redact_text(row["rationale"] or "Imported legacy action."),
                    "Imported from the v0.1 audit trail.",
                    canonical_json(payload),
                    payload_hash(payload),
                    "medium",
                    status,
                    row["created_at"],
                ),
            )
            self._append_action_event(
                connection, action_id, "legacy_imported", "migration", {"legacy_status": row["status"]}
            )
            if status in {"succeeded", "failed"}:
                connection.execute(
                    """INSERT INTO action_outcomes
                       (action_id, exit_code, stdout_redacted, stderr_redacted, result_json,
                        completed_at, recorded_at)
                       VALUES (?, ?, ?, ?, '{}', ?, ?)""",
                    (
                        action_id,
                        row["exit_code"],
                        redact_text(row["stdout"] or ""),
                        redact_text(row["stderr"] or ""),
                        row["executed_at"],
                        row["executed_at"] or row["created_at"],
                    ),
                )
        connection.execute(
            "INSERT INTO legacy_import_map VALUES ('database', 'v1', ?)", (new_id(),)
        )

    @staticmethod
    def _index_memory(
        connection: Any,
        project_id: str,
        kind: MemoryKind,
        source_id: str,
        title: str,
        content: str,
        created_at: str,
        status: str,
    ) -> None:
        connection.execute(
            "DELETE FROM memory_fts WHERE kind = ? AND source_id = ?",
            (kind.value, source_id),
        )
        connection.execute(
            """INSERT INTO memory_fts
               (project_id, kind, source_id, title, content, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, kind.value, source_id, title, content, created_at, status),
        )

    @staticmethod
    def _append_action_event(
        connection: Any,
        action_id: str,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO action_events
               (id, action_id, event_type, actor, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), action_id, event_type, actor, canonical_json(details), utc_now()),
        )

    def get_action_rejection_reason(self, action_id: str) -> str | None:
        with self.factory.connect() as connection:
            row = connection.execute(
                "SELECT details_json FROM action_events WHERE action_id = ? AND event_type = 'rejected' ORDER BY created_at DESC LIMIT 1",
                (action_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            import json
            details = json.loads(row["details_json"])
            return details.get("reason")
        except Exception:
            return None

    @staticmethod
    def _project(row: Any) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            canonical_path=row["canonical_path"],
            repo_fingerprint=row["repo_fingerprint"],
            default_branch=row["default_branch"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message(row: Any) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            sequence_no=row["sequence_no"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _decision(row: Any) -> Decision:
        return Decision(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            topic=row["topic"],
            title=row["title"],
            decision=row["decision"],
            reason=row["reason"],
            status=DecisionStatus(row["status"]),
            supersedes_id=row["supersedes_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            fingerprint=row["fingerprint"] if "fingerprint" in row else "",
        )

    @staticmethod
    def _snapshot(row: Any) -> RepoSnapshot:
        return RepoSnapshot(
            id=row["id"],
            project_id=row["project_id"],
            branch=row["branch"],
            head_ref=row["head_ref"],
            tree_digest=row["tree_digest"],
            summary=json.loads(row["summary_json"]),
            created_by_action_id=row["created_by_action_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _action(row: Any) -> ActionProposal:
        return ActionProposal(
            id=row["id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            action_type=ActionType(row["action_type"]),
            tool_category=row["tool_category"],
            operation=row["operation"],
            reason=row["reason"],
            impact=row["impact"],
            payload=json.loads(row["payload_json"]),
            payload_sha256=row["payload_sha256"],
            risk_level=row["risk_level"],
            status=ActionStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            fingerprint=row["fingerprint"] if "fingerprint" in row else "",
        )

    @staticmethod
    def _approval_rule(row: Any) -> ApprovalRule:
        return ApprovalRule(
            id=row["id"],
            project_id=row["project_id"],
            action_type=row["action_type"],
            tool_category=row["tool_category"],
            operation=row["operation"],
            payload_pattern=row["payload_pattern"],
            reason=row["reason"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )
