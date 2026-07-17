from __future__ import annotations

import sqlite3
from importlib.resources import files


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
           version INTEGER PRIMARY KEY,
           name TEXT NOT NULL,
           applied_at TEXT NOT NULL
        )"""
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    migrations = [
        (1, "initial", "001_initial.sql"),
        (2, "legacy_import_tracking", "002_legacy_import_tracking.sql"),
        (3, "approval_rules", "003_approval_rules.sql"),
    ]
    for version, name, filename in migrations:
        if version in applied:
            continue
        sql = files("pm_agent.infrastructure.sqlite.schema").joinpath(filename).read_text()
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, datetime('now'))",
            (version, name),
        )
