from __future__ import annotations

import sqlite3

from pm_agent.infrastructure.sqlite import SQLiteStore

LEGACY_SCHEMA = """
CREATE TABLE sessions (
 id INTEGER PRIMARY KEY, name TEXT, repo_path TEXT, branch TEXT, started_at TEXT, ended_at TEXT
);
CREATE TABLE messages (
 id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT, created_at TEXT
);
CREATE TABLE decisions (
 id INTEGER PRIMARY KEY, session_id INTEGER, topic TEXT, title TEXT, decision TEXT,
 rationale TEXT, status TEXT, created_at TEXT
);
CREATE TABLE actions (
 id INTEGER PRIMARY KEY, session_id INTEGER, action_type TEXT, command TEXT, status TEXT,
 exit_code INTEGER, stdout TEXT, stderr TEXT, rationale TEXT, created_at TEXT, executed_at TEXT
);
"""


def test_imports_legacy_database_once(tmp_path):
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO sessions VALUES (1, 'old', ?, 'main', '2026-01-01', '2026-01-02')",
        (str(tmp_path),),
    )
    connection.execute(
        "INSERT INTO messages VALUES (1, 1, 'user', 'hello', '2026-01-01')"
    )
    connection.execute(
        """INSERT INTO decisions VALUES
           (1, 1, 'db', 'SQLite', 'Use SQLite', 'local', 'active', '2026-01-01')"""
    )
    connection.execute(
        """INSERT INTO actions VALUES
           (1, 1, 'shell', 'git status', 'executed', 0, 'clean', '', 'inspect',
            '2026-01-01', '2026-01-01')"""
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(db)
    project = store.resolve_project(tmp_path, "main")
    counts = store.memory_counts(project.id)
    assert counts.messages == 1
    assert counts.decisions == 1
    assert len(store.list_actions(project.id)) == 1

    reopened = SQLiteStore(db)
    assert reopened.memory_counts(project.id) == counts
