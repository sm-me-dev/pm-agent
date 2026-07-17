from __future__ import annotations

import sqlite3

from pm_agent.domain.enums import ActionStatus, ActionType, DecisionStatus
from pm_agent.domain.models import ActionProposal, payload_hash, utc_now
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.ports.memory import RetrievalQuery


def make_store(tmp_path):
    return SQLiteStore(tmp_path / "state.db")


def test_schema_enables_expected_tables_and_fts(tmp_path):
    store = make_store(tmp_path)
    connection = sqlite3.connect(store.db_path)
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")
    }
    assert {
        "projects",
        "sessions_v2",
        "messages_v2",
        "decisions_v2",
        "session_summaries",
        "repository_notes",
        "repo_snapshots",
        "action_proposals",
        "action_events",
        "action_outcomes",
        "memory_fts",
    } <= tables


def test_project_state_survives_restart_and_is_cross_session(tmp_path):
    db = tmp_path / "state.db"
    store = SQLiteStore(db)
    project = store.resolve_project(tmp_path, "main")
    first = store.start_session(project.id, "first", "glm-5.2", "test", "main")
    store.add_decision(
        project.id,
        first.id,
        "storage",
        "Use SQLite",
        "Use a global SQLite database.",
        "State must survive restarts.",
        DecisionStatus.ACCEPTED,
    )
    store.close_session(first.id)

    reopened = SQLiteStore(db)
    same_project = reopened.resolve_project(tmp_path, "main")
    second = reopened.start_session(same_project.id, "second", "glm-5.2", "test", "main")
    packet = reopened.retrieve(
        RetrievalQuery(same_project.id, second.id, "SQLite storage architecture")
    )
    assert same_project.id == project.id
    assert any(item.kind.value == "decision" for item in packet.items)


def test_recent_message_window_is_capped_at_100(tmp_path):
    store = make_store(tmp_path)
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    for number in range(120):
        store.add_message(session.id, "user", f"message {number}")
    messages = store.get_recent_messages(session.id, 500)
    assert len(messages) == 100
    assert messages[0].content == "message 20"


def test_proposed_decision_is_retrieved_as_identity(tmp_path):
    # Proposed (not-yet-accepted) decisions are the bulk of product-manager
    # state; they must be surfaced as project-identity context so the agent
    # recovers prior intent instead of asking "what is the project?" again.
    store = make_store(tmp_path)
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    store.add_decision(
        project.id,
        session.id,
        "database",
        "Maybe Postgres",
        "Use Postgres.",
        "Potential future scale.",
    )
    packet = store.retrieve(RetrievalQuery(project.id, session.id, "unrelated input"))
    assert any(
        item.kind.value == "decision" and "Postgres" in item.content
        for item in packet.items
    )


def test_action_approval_is_payload_specific(tmp_path):
    store = make_store(tmp_path)
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    payload = {"command": "git status"}
    action = ActionProposal(
        id="action",
        project_id=project.id,
        session_id=session.id,
        action_type=ActionType.GIT,
        tool_category="git",
        operation="status",
        reason="Confirm state.",
        impact="Read only.",
        payload=payload,
        payload_sha256=payload_hash(payload),
        risk_level="low",
        status=ActionStatus.PROPOSED,
        created_at=utc_now(),
    )
    store.create_action(action)
    store.transition_action(
        action.id,
        ActionStatus.APPROVED,
        "user",
        {"payload_sha256": action.payload_sha256},
    )
    assert store.action_has_approval(action.id, action.payload_sha256)
    assert not store.action_has_approval(action.id, payload_hash({"command": "git log"}))
