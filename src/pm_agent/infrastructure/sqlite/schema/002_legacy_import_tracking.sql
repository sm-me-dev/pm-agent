CREATE TABLE IF NOT EXISTS legacy_import_map (
    entity_type TEXT NOT NULL,
    legacy_id TEXT NOT NULL,
    new_id TEXT NOT NULL,
    PRIMARY KEY(entity_type, legacy_id)
);
