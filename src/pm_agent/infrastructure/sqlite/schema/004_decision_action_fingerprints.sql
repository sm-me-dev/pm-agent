ALTER TABLE decisions_v2 ADD COLUMN fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE action_proposals ADD COLUMN fingerprint TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_decisions_fingerprint ON decisions_v2(project_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_actions_fingerprint ON action_proposals(project_id, fingerprint);
