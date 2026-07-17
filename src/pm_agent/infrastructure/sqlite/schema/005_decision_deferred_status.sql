-- Allow the 'deferred' decision status so skipped decisions persist a stable
-- state across resume boundaries instead of being re-prompted every loop.
PRAGMA foreign_keys = OFF;

ALTER TABLE decisions_v2 RENAME TO decisions_v2_old;

CREATE TABLE decisions_v2 (
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
    updated_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT ''
);

INSERT INTO decisions_v2 (
    id, project_id, session_id, topic, title, decision, reason, status,
    supersedes_id, created_at, updated_at, fingerprint
)
SELECT
    id, project_id, session_id, topic, title, decision, reason, status,
    supersedes_id, created_at, updated_at, fingerprint
FROM decisions_v2_old;

DROP TABLE decisions_v2_old;

CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions_v2(project_id, status, updated_at DESC);

-- Rebuild decision FTS rows to match the recreated table (delete-then-insert
-- avoids duplicates).
DELETE FROM memory_fts WHERE kind = 'decision';
INSERT INTO memory_fts (project_id, kind, source_id, title, content, created_at, status)
SELECT project_id, 'decision', id, title, decision, created_at, status
FROM decisions_v2;

PRAGMA foreign_keys = ON;
