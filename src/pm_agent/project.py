from __future__ import annotations

import os
import sqlite3
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pm_agent.domain.models import new_id, project_fingerprint, utc_now


@dataclass
class ProjectLocal:
    root: Path
    pm_agent_dir: Path | None = None
    project_config: dict[str, Any] = field(default_factory=dict)
    global_config: dict[str, Any] = field(default_factory=dict)
    memory: str = ""

    @property
    def is_initialized(self) -> bool:
        return self.pm_agent_dir is not None

    @property
    def default_db_path(self) -> Path:
        if self.pm_agent_dir:
            return self.pm_agent_dir / "state.db"
        return legacy_global_db_path()

    @property
    def log_dir(self) -> Path:
        base = self.pm_agent_dir or self.root
        return base / "logs"

    @property
    def history_path(self) -> Path:
        base = self.pm_agent_dir or self.root
        return base / "prompt-history"

    @property
    def context_dir(self) -> Path:
        if self.pm_agent_dir:
            pm_ctx = self.pm_agent_dir / "context"
            if pm_ctx.is_dir():
                return pm_ctx
        return self.root / "context"

    @property
    def default_error_log_path(self) -> Path:
        return self.log_dir / "pm-agent-errors.log"


def global_config_dir() -> Path:
    plat = sys.platform
    if plat == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif plat == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pm-agent"


def global_config_path() -> Path:
    return global_config_dir() / "config.toml"


def legacy_global_db_path() -> Path:
    plat = sys.platform
    if plat == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif plat == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    primary = base / "pm-agent" / "state.db"
    if primary.exists():
        return primary
    fallback = Path.home() / ".local" / "share" / "stateful-pm-agent" / "state.db"
    return fallback


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    return tomllib.loads(raw)


def load_project_config(pm_agent_dir: Path) -> dict[str, Any]:
    return _load_toml(pm_agent_dir / "project.toml")


def load_global_config() -> dict[str, Any]:
    path = global_config_path()
    if not path.is_file():
        return {}
    try:
        return _load_toml(path)
    except tomllib.TOMLDecodeError as exc:
        import warnings as _w
        _w.warn(f"Invalid global config at {path}: {exc}", stacklevel=2)
        return {}


def load_memory(pm_agent_dir: Path) -> str:
    path = pm_agent_dir / "memory.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def resolve_config_path(value: str, base: Path) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p.resolve()
    return (base / p).resolve()


def resolve_db_path(
    proj: ProjectLocal | None,
    cli_db: str | None,
    env_db: str | None,
) -> Path:
    if cli_db:
        return Path(cli_db).expanduser().resolve()
    if env_db:
        return Path(env_db).expanduser().resolve()
    if proj and proj.project_config:
        cfg_path = _nested_get(proj.project_config, ("paths", "db_path"))
        if cfg_path:
            return resolve_config_path(cfg_path, proj.root)
    if proj and proj.global_config:
        cfg_path = _nested_get(proj.global_config, ("paths", "db_path"))
        if cfg_path:
            return resolve_config_path(cfg_path, global_config_dir())
    return proj.default_db_path if proj else legacy_global_db_path()


def _nested_get(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k, {})
    return d if d != {} else None


def discover(start: Path) -> ProjectLocal | None:
    current = start.resolve()
    while True:
        pm_dir = current / ".pm-agent"
        if pm_dir.is_dir():
            cfg = load_project_config(pm_dir)
            gcfg = load_global_config()
            mem = load_memory(pm_dir)
            return ProjectLocal(
                root=current,
                pm_agent_dir=pm_dir,
                project_config=cfg,
                global_config=gcfg,
                memory=mem,
            )
        if _is_git_root(current):
            gcfg = load_global_config()
            return ProjectLocal(root=current, pm_agent_dir=None, global_config=gcfg)
        parent = current.parent
        if parent == current:
            return None
        current = parent


def discover_or_override(
    project_root: str | None,
    repo: str | None,
    cwd: Path,
) -> ProjectLocal | None:
    if project_root is not None:
        root = Path(project_root).resolve()
        if not root.is_dir():
            return None
        pm_dir = root / ".pm-agent"
        if pm_dir.is_dir():
            cfg = load_project_config(pm_dir)
            gcfg = load_global_config()
            mem = load_memory(pm_dir)
            return ProjectLocal(
                root=root,
                pm_agent_dir=pm_dir,
                project_config=cfg,
                global_config=gcfg,
                memory=mem,
            )
        gcfg = load_global_config()
        return ProjectLocal(root=root, pm_agent_dir=None, global_config=gcfg)
    return discover(Path(repo) if repo else cwd)


def _is_git_root(path: Path) -> bool:
    git_path = path / ".git"
    return git_path.exists()


def init_project(
    target: Path,
    force: bool = False,
) -> ProjectLocal:
    target = target.resolve()
    pm_dir = target / ".pm-agent"
    if pm_dir.is_dir() and not force:
        raise FileExistsError(
            f"Already initialized at {target}. Use --force to re-create config files."
        )
    pm_dir.mkdir(parents=True, exist_ok=True)

    proj_toml = pm_dir / "project.toml"
    if not proj_toml.is_file() or force:
        proj_toml.write_text(_PROJECT_TOML_TEMPLATE, encoding="utf-8")

    mem_file = pm_dir / "memory.md"
    if not mem_file.is_file() or force:
        mem_file.write_text("# Project Memory\n\n", encoding="utf-8")

    cfg = load_project_config(pm_dir)
    gcfg = load_global_config()
    mem = load_memory(pm_dir)
    return ProjectLocal(
        root=target,
        pm_agent_dir=pm_dir,
        project_config=cfg,
        global_config=gcfg,
        memory=mem,
    )


_PROJECT_TOML_TEMPLATE = """\
[project]
name = ""
language = ""
test_command = ""
lint_command = ""
build_command = ""
repo_type = ""

[constraints]
allowed_paths = []
approval_default = "prompt"
blocked_actions = []

[paths]
# context_dir = ""
# error_log_path = ""
"""


def migrate_project_data(
    source_path: Path,
    dest_path: Path,
    project_root: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    from pm_agent.infrastructure.sqlite import SQLiteStore

    if not source_path.is_file():
        raise FileNotFoundError(f"No legacy database found at {source_path}")

    source_conn = sqlite3.connect(str(source_path))
    source_conn.row_factory = sqlite3.Row
    try:
        row = source_conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()
        if row[0] == 0:
            raise ValueError(
                "Unsupported legacy schema \u2014 requires 'projects' table. "
                "If you have a v1 database, open it with an older pm-agent version first."
            )

        canonical = str(project_root.resolve())
        proj_row = source_conn.execute(
            "SELECT * FROM projects WHERE canonical_path = ?", (canonical,)
        ).fetchone()
        if proj_row is None:
            others = [
                r["canonical_path"]
                for r in source_conn.execute(
                    "SELECT canonical_path FROM projects ORDER BY name"
                ).fetchall()
            ]
            msg = f"No legacy data for project root {canonical}."
            if others:
                msg += f" Available: {', '.join(others)}"
            raise ValueError(msg)
    finally:
        source_conn.close()

    canonical = str(project_root.resolve())

    source_conn = sqlite3.connect(str(source_path))
    source_conn.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    proj_row = source_conn.execute(
        "SELECT * FROM projects WHERE canonical_path = ?", (canonical,)
    ).fetchone()
    source_project_id = proj_row["id"]

    if dry_run:
        _count_source_entities(source_conn, source_project_id, counts, skipped)
        source_conn.close()
        return {**counts, **skipped}

    # Initialize the dest DB schema via SQLiteStore (run migrations)
    _ = SQLiteStore(dest_path)
    dest_conn = sqlite3.connect(str(dest_path))
    dest_conn.row_factory = sqlite3.Row
    try:
        _ensure_legacy_import_map(dest_conn)

        dest_proj_row = dest_conn.execute(
            "SELECT * FROM projects WHERE canonical_path = ?", (canonical,)
        ).fetchone()
        if dest_proj_row is None:
            now = utc_now()
            dest_project_id = new_id()
            dest_conn.execute(
                "INSERT INTO projects (id, name, canonical_path, repo_fingerprint, default_branch, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dest_project_id, proj_row["name"], canonical, project_fingerprint(canonical), proj_row["default_branch"], now, now),
            )
            counts["projects"] = 1
        else:
            dest_project_id = dest_proj_row["id"]
            skipped["projects"] = 1

        id_map: dict[str, str] = {}

        _migrate_sessions(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, id_map, dry_run)
        _migrate_messages(source_conn, dest_conn, id_map, counts, skipped, dry_run)
        _migrate_decisions(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, dry_run)
        _migrate_actions(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, id_map, dry_run)
        _migrate_approval_rules(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, dry_run)
        _migrate_repository_notes(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, dry_run)
        _migrate_snapshots(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, dry_run)
        _migrate_summaries(source_conn, dest_conn, source_project_id, dest_project_id, counts, skipped, dry_run)

        if not dry_run:
            dest_conn.commit()
    finally:
        source_conn.close()
        dest_conn.close()

    result = dict(counts)
    if any(skipped.values()):
        result["already_existed"] = sum(skipped.values())
    return result


def _ensure_legacy_import_map(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS legacy_import_map ("
        "  entity_type TEXT NOT NULL,"
        "  legacy_id TEXT NOT NULL,"
        "  new_id TEXT NOT NULL,"
        "  PRIMARY KEY (entity_type, legacy_id)"
        ")"
    )


def _count_source_entities(src, src_pid: str, counts: dict, skipped: dict) -> None:
    counts["projects"] = 1
    for table, entity_key, id_col in [
        ("sessions_v2", "sessions", "project_id"),
        ("decisions_v2", "decisions", "project_id"),
        ("action_proposals", "actions", "project_id"),
        ("approval_rules", "approval_rules", "project_id"),
        ("repository_notes", "repository_notes", "project_id"),
        ("repo_snapshots", "repo_snapshots", "project_id"),
        ("session_summaries", "session_summaries", "project_id"),
    ]:
        rows = src.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {id_col} = ?", (src_pid,)
        ).fetchone()
        if rows and rows[0] > 0:
            counts[entity_key] = rows[0]
    session_rows = src.execute(
        "SELECT id FROM sessions_v2 WHERE project_id = ?", (src_pid,)
    ).fetchall()
    if session_rows:
        sids = tuple(r["id"] for r in session_rows)
        if sids:
            placeholders = ",".join("?" for _ in sids)
            msg_row = src.execute(
                f"SELECT COUNT(*) FROM messages_v2 WHERE session_id IN ({placeholders})",
                sids,
            ).fetchone()
            if msg_row and msg_row[0] > 0:
                counts["messages"] = msg_row[0]


def _already_migrated(conn: sqlite3.Connection, entity_type: str, legacy_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM legacy_import_map WHERE entity_type = ? AND legacy_id = ?",
        (entity_type, legacy_id),
    ).fetchone()
    return row is not None


def _migrate_sessions(src, dest, src_pid, dst_pid, counts, skipped, id_map, dry_run):
    rows = src.execute("SELECT * FROM sessions_v2 WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "session", key):
            skipped["sessions"] = skipped.get("sessions", 0) + 1
            mapped = dest.execute(
                "SELECT new_id FROM legacy_import_map WHERE entity_type='session' AND legacy_id=?",
                (key,),
            ).fetchone()
            if mapped:
                id_map[key] = mapped["new_id"]
            continue
        new_sid = new_id()
        if not dry_run:
            dest.execute(
                "INSERT INTO sessions_v2 (id, project_id, name, model, provider, branch, status, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_sid, dst_pid, row["name"], row["model"], row["provider"], row["branch"], row["status"], row["started_at"], row["ended_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('session', ?, ?)",
                (key, new_sid),
            )
        id_map[key] = new_sid
        counts["sessions"] = counts.get("sessions", 0) + 1


def _migrate_messages(src, dest, id_map, counts, skipped, dry_run):
    for legacy_sid, new_sid in id_map.items():
        rows = src.execute(
            "SELECT * FROM messages_v2 WHERE session_id = ? ORDER BY sequence_no",
            (legacy_sid,),
        ).fetchall()
        for row in rows:
            seq = row["sequence_no"]
            existing = dest.execute(
                "SELECT 1 FROM messages_v2 WHERE session_id = ? AND sequence_no = ?",
                (new_sid, seq),
            ).fetchone()
            if existing:
                skipped["messages"] = skipped.get("messages", 0) + 1
                continue
            if not dry_run:
                dest.execute(
                    "INSERT INTO messages_v2 (id, session_id, sequence_no, role, content, content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), new_sid, seq, row["role"], row["content"], row["content_sha256"], row["created_at"]),
                )
            counts["messages"] = counts.get("messages", 0) + 1


def _migrate_decisions(src, dest, src_pid, dst_pid, counts, skipped, dry_run):
    rows = src.execute("SELECT * FROM decisions_v2 WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "decision", key):
            skipped["decisions"] = skipped.get("decisions", 0) + 1
            continue
        if not dry_run:
            new_did = new_id()
            dest.execute(
                "INSERT INTO decisions_v2 (id, project_id, session_id, topic, title, decision, reason, status, supersedes_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_did, dst_pid, row["session_id"], row["topic"], row["title"], row["decision"], row["reason"], row["status"], row["supersedes_id"], row["created_at"], row["updated_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('decision', ?, ?)",
                (key, new_did),
            )
        counts["decisions"] = counts.get("decisions", 0) + 1


def _migrate_actions(src, dest, src_pid, dst_pid, counts, skipped, id_map, dry_run):
    rows = src.execute("SELECT * FROM action_proposals WHERE project_id = ?", (src_pid,)).fetchall()
    action_id_map: dict[str, str] = {}
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "action", key):
            skipped["actions"] = skipped.get("actions", 0) + 1
            mapped = dest.execute(
                "SELECT new_id FROM legacy_import_map WHERE entity_type='action' AND legacy_id=?",
                (key,),
            ).fetchone()
            if mapped:
                action_id_map[key] = mapped["new_id"]
            continue
        new_aid = new_id()
        if not dry_run:
            session_id = id_map.get(row["session_id"], None)
            dest.execute(
                "INSERT INTO action_proposals (id, project_id, session_id, action_type, tool_category, operation, reason, impact, payload_json, payload_sha256, risk_level, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_aid, dst_pid, session_id, row["action_type"], row["tool_category"], row["operation"], row["reason"], row["impact"], row["payload_json"], row["payload_sha256"], row["risk_level"], row["status"], row["created_at"], row["expires_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('action', ?, ?)",
                (key, new_aid),
            )
        action_id_map[key] = new_aid
        counts["actions"] = counts.get("actions", 0) + 1

    if not dry_run:
        _migrate_action_events(src, dest, action_id_map)
        _migrate_action_outcomes(src, dest, action_id_map)


def _migrate_action_events(src, dest, action_id_map):
    for legacy_aid, new_aid in action_id_map.items():
        existing_count = dest.execute(
            "SELECT COUNT(*) FROM action_events WHERE action_id = ?", (new_aid,)
        ).fetchone()[0]
        if existing_count > 0:
            continue
        rows = src.execute(
            "SELECT * FROM action_events WHERE action_id = ?", (legacy_aid,)
        ).fetchall()
        for row in rows:
            dest.execute(
                "INSERT INTO action_events (id, action_id, event_type, actor, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), new_aid, row["event_type"], row["actor"], row["details_json"], row["created_at"]),
            )


def _migrate_action_outcomes(src, dest, action_id_map):
    for legacy_aid, new_aid in action_id_map.items():
        row = src.execute(
            "SELECT * FROM action_outcomes WHERE action_id = ?", (legacy_aid,)
        ).fetchone()
        if row:
            existing = dest.execute(
                "SELECT 1 FROM action_outcomes WHERE action_id = ?", (new_aid,)
            ).fetchone()
            if existing:
                continue
            dest.execute(
                "INSERT INTO action_outcomes (action_id, host_correlation_id, exit_code, stdout_redacted, stderr_redacted, result_json, started_at, completed_at, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_aid, row["host_correlation_id"], row["exit_code"], row["stdout_redacted"], row["stderr_redacted"], row["result_json"], row["started_at"], row["completed_at"], row["recorded_at"]),
            )


def _migrate_approval_rules(src, dest, src_pid, dst_pid, counts, skipped, dry_run):
    rows = src.execute("SELECT * FROM approval_rules WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "approval_rule", key):
            skipped["approval_rules"] = skipped.get("approval_rules", 0) + 1
            continue
        if not dry_run:
            dest.execute(
                "INSERT INTO approval_rules (id, project_id, action_type, tool_category, operation, payload_pattern, reason, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), dst_pid, row["action_type"], row["tool_category"], row["operation"], row["payload_pattern"], row["reason"], row["created_at"], row["created_by"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('approval_rule', ?, ?)",
                (key, new_id()),
            )
        counts["approval_rules"] = counts.get("approval_rules", 0) + 1


def _migrate_repository_notes(src, dest, src_pid, dst_pid, counts, skipped, dry_run):
    rows = src.execute("SELECT * FROM repository_notes WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "repository_note", key):
            skipped["repository_notes"] = skipped.get("repository_notes", 0) + 1
            continue
        if not dry_run:
            dest.execute(
                "INSERT INTO repository_notes (id, project_id, session_id, category, title, content, source_type, source_ref, confidence, stale_after, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), dst_pid, row["session_id"], row["category"], row["title"], row["content"], row["source_type"], row["source_ref"], row["confidence"], row["stale_after"], row["created_at"], row["updated_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('repository_note', ?, ?)",
                (key, new_id()),
            )
        counts["repository_notes"] = counts.get("repository_notes", 0) + 1


def _migrate_snapshots(src, dest, src_pid, dst_pid, counts, skipped, dry_run):
    rows = src.execute("SELECT * FROM repo_snapshots WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "repo_snapshot", key):
            skipped["repo_snapshots"] = skipped.get("repo_snapshots", 0) + 1
            continue
        if not dry_run:
            dest.execute(
                "INSERT INTO repo_snapshots (id, project_id, branch, head_ref, tree_digest, summary_json, created_by_action_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), dst_pid, row["branch"], row["head_ref"], row["tree_digest"], row["summary_json"], row["created_by_action_id"], row["created_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('repo_snapshot', ?, ?)",
                (key, new_id()),
            )
        counts["repo_snapshots"] = counts.get("repo_snapshots", 0) + 1


def _migrate_summaries(src, dest, src_pid, dst_pid, counts, skipped, dry_run):
    rows = src.execute("SELECT * FROM session_summaries WHERE project_id = ?", (src_pid,)).fetchall()
    for row in rows:
        key = str(row["id"])
        if _already_migrated(dest, "session_summary", key):
            skipped["session_summaries"] = skipped.get("session_summaries", 0) + 1
            continue
        if not dry_run:
            dest.execute(
                "INSERT INTO session_summaries (id, project_id, session_id, key_topics_json, decisions_json, planned_actions_json, open_questions_json, narrative, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), dst_pid, row["session_id"], row["key_topics_json"], row["decisions_json"], row["planned_actions_json"], row["open_questions_json"], row["narrative"], row["created_at"]),
            )
            dest.execute(
                "INSERT INTO legacy_import_map (entity_type, legacy_id, new_id) VALUES ('session_summary', ?, ?)",
                (key, new_id()),
            )
        counts["session_summaries"] = counts.get("session_summaries", 0) + 1
