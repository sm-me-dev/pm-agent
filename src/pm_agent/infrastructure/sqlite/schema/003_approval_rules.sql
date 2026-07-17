CREATE TABLE IF NOT EXISTS approval_rules (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    action_type TEXT,
    tool_category TEXT,
    operation TEXT,
    payload_pattern TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user'
);

CREATE INDEX IF NOT EXISTS idx_approval_rules_lookup
    ON approval_rules(project_id, action_type, tool_category, operation);
