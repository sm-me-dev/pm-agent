CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canonical_path TEXT NOT NULL UNIQUE,
    repo_fingerprint TEXT NOT NULL,
    default_branch TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    branch TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','closed','abandoned')),
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS messages_v2 (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions_v2(id),
    sequence_no INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS decisions_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT REFERENCES sessions_v2(id),
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('proposed','accepted','rejected','deferred','superseded')),
    supersedes_id TEXT REFERENCES decisions_v2(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions_v2(id),
    key_topics_json TEXT NOT NULL,
    decisions_json TEXT NOT NULL,
    planned_actions_json TEXT NOT NULL,
    open_questions_json TEXT NOT NULL,
    narrative TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repository_notes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT REFERENCES sessions_v2(id),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('filesystem','git','graphify','mcp','human')),
    source_ref TEXT,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    stale_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    branch TEXT NOT NULL,
    head_ref TEXT,
    tree_digest TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_by_action_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL REFERENCES sessions_v2(id),
    action_type TEXT NOT NULL CHECK(action_type IN ('bash','git','github','mcp')),
    tool_category TEXT NOT NULL,
    operation TEXT NOT NULL,
    reason TEXT NOT NULL,
    impact TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','blocked')),
    status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected','dispatched','succeeded','failed','expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS action_events (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES action_proposals(id),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_outcomes (
    action_id TEXT PRIMARY KEY REFERENCES action_proposals(id),
    host_correlation_id TEXT,
    exit_code INTEGER,
    stdout_redacted TEXT,
    stderr_redacted TEXT,
    result_json TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_import_map (
    entity_type TEXT NOT NULL,
    legacy_id TEXT NOT NULL,
    new_id TEXT NOT NULL,
    PRIMARY KEY(entity_type, legacy_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions_v2(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages_v2(session_id, sequence_no DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions_v2(project_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_project ON repository_notes(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON repo_snapshots(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_project ON action_proposals(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_events_action ON action_events(action_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    project_id UNINDEXED,
    kind UNINDEXED,
    source_id UNINDEXED,
    title,
    content,
    created_at UNINDEXED,
    status UNINDEXED,
    tokenize='unicode61'
);
