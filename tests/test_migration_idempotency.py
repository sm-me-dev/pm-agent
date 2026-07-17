from __future__ import annotations

import sqlite3

from pm_agent.project import migrate_project_data


def test_migration_is_idempotent(tmp_path):
    """Test that running migration twice does not fail or create duplicates."""
    # Create a source (legacy) database
    source_db = tmp_path / "legacy.db"
    source_conn = sqlite3.connect(source_db)
    source_conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            canonical_path TEXT NOT NULL UNIQUE,
            repo_fingerprint TEXT NOT NULL,
            default_branch TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE sessions_v2 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            branch TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE messages_v2 (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, sequence_no)
        );
        CREATE TABLE decisions_v2 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            supersedes_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE action_proposals (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            tool_category TEXT NOT NULL,
            operation TEXT NOT NULL,
            reason TEXT NOT NULL,
            impact TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        CREATE TABLE action_events (
            id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE action_outcomes (
            action_id TEXT PRIMARY KEY,
            host_correlation_id TEXT,
            exit_code INTEGER,
            stdout_redacted TEXT,
            stderr_redacted TEXT,
            result_json TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE approval_rules (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            action_type TEXT,
            tool_category TEXT,
            operation TEXT,
            payload_pattern TEXT,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT
        );
        CREATE TABLE repository_notes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            confidence REAL NOT NULL,
            stale_after TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE repo_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            head_ref TEXT,
            tree_digest TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_by_action_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE session_summaries (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            key_topics_json TEXT NOT NULL,
            decisions_json TEXT NOT NULL,
            planned_actions_json TEXT NOT NULL,
            open_questions_json TEXT NOT NULL,
            narrative TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    # Insert test data
    source_conn.execute(
        "INSERT INTO projects (id, name, canonical_path, repo_fingerprint, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("proj1", "Test Project", str(tmp_path), "fp123", "2026-01-01", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO sessions_v2 (id, project_id, name, model, provider, branch, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess1", "proj1", "test-session", "glm-5.2", "openai", "main", "active", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO messages_v2 (id, session_id, sequence_no, role, content, content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("msg1", "sess1", 1, "user", "Hello", "sha256", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO action_proposals (id, project_id, session_id, action_type, tool_category, operation, reason, impact, payload_json, payload_sha256, risk_level, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("act1", "proj1", "sess1", "bash", "shell", "git status", "Check status", "low", "{}", "sha256", "low", "dispatched", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO action_events (id, action_id, event_type, actor, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("evt1", "act1", "dispatched", "system", "{}", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO action_outcomes (action_id, exit_code, stdout_redacted, stderr_redacted, result_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("act1", 0, "clean", "", "{}", "2026-01-01"),
    )
    source_conn.commit()
    source_conn.close()

    # Destination database
    dest_db = tmp_path / "dest.db"

    # First migration
    migrate_project_data(source_db, dest_db, tmp_path)

    # Second migration (should not fail)
    result2 = migrate_project_data(source_db, dest_db, tmp_path)

    # Verify no duplicate outcomes or events
    dest_conn = sqlite3.connect(dest_db)
    outcomes = dest_conn.execute("SELECT COUNT(*) FROM action_outcomes").fetchone()[0]
    events = dest_conn.execute("SELECT COUNT(*) FROM action_events").fetchone()[0]
    actions = dest_conn.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0]
    dest_conn.close()

    # Should be idempotent - no duplicates
    assert outcomes == 1, f"Expected 1 outcome, got {outcomes}"
    assert events == 1, f"Expected 1 event, got {events}"
    assert actions == 1, f"Expected 1 action, got {actions}"

    # Second migration should report all as already existed
    assert result2.get("already_existed", 0) > 0 or result2.get("actions", 0) == 0


def test_first_run_succeeds(tmp_path):
    """Test that first migration succeeds correctly."""
    source_db = tmp_path / "legacy.db"
    source_conn = sqlite3.connect(source_db)
    source_conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            canonical_path TEXT NOT NULL UNIQUE,
            repo_fingerprint TEXT NOT NULL,
            default_branch TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE sessions_v2 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            branch TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE messages_v2 (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, sequence_no)
        );
        CREATE TABLE decisions_v2 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            supersedes_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE action_proposals (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            tool_category TEXT NOT NULL,
            operation TEXT NOT NULL,
            reason TEXT NOT NULL,
            impact TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        CREATE TABLE action_events (
            id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE action_outcomes (
            action_id TEXT PRIMARY KEY,
            host_correlation_id TEXT,
            exit_code INTEGER,
            stdout_redacted TEXT,
            stderr_redacted TEXT,
            result_json TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE approval_rules (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            action_type TEXT,
            tool_category TEXT,
            operation TEXT,
            payload_pattern TEXT,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT
        );
        CREATE TABLE repository_notes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            confidence REAL NOT NULL,
            stale_after TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE repo_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            head_ref TEXT,
            tree_digest TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            created_by_action_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE session_summaries (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            key_topics_json TEXT NOT NULL,
            decisions_json TEXT NOT NULL,
            planned_actions_json TEXT NOT NULL,
            open_questions_json TEXT NOT NULL,
            narrative TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    source_conn.execute(
        "INSERT INTO projects (id, name, canonical_path, repo_fingerprint, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("proj1", "Test", str(tmp_path), "fp", "2026-01-01", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO sessions_v2 (id, project_id, name, model, provider, branch, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess1", "proj1", "s", "m", "p", "main", "active", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO action_proposals (id, project_id, session_id, action_type, tool_category, operation, reason, impact, payload_json, payload_sha256, risk_level, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("act1", "proj1", "sess1", "bash", "shell", "ls", "list", "low", "{}", "sha", "low", "dispatched", "2026-01-01"),
    )
    source_conn.execute(
        "INSERT INTO action_outcomes (action_id, exit_code, result_json, recorded_at) VALUES (?, ?, ?, ?)",
        ("act1", 0, "{}", "2026-01-01"),
    )
    source_conn.commit()
    source_conn.close()

    dest_db = tmp_path / "dest.db"
    migrate_project_data(source_db, dest_db, tmp_path)

    dest_conn = sqlite3.connect(dest_db)
    assert dest_conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert dest_conn.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 1
    assert dest_conn.execute("SELECT COUNT(*) FROM action_outcomes").fetchone()[0] == 1
    dest_conn.close()
