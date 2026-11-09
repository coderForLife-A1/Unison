-- Unison SQLite schema, migration 001.
-- Requires SQLite 3.35+ and foreign_keys = ON.
PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE sources (
    source_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(source_id),
    sender          TEXT NOT NULL,
    title           TEXT,
    content         TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    content_hash    BLOB NOT NULL,
    received_at     TEXT NOT NULL, -- UTC ISO-8601, e.g. 2026-11-09T17:46:00.000Z
    ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    state           TEXT NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'triaged', 'suppressed', 'failed')),
    priority        TEXT CHECK (priority IN ('High', 'Low', 'Noise')),
    summary         TEXT,
    category_id     INTEGER REFERENCES categories(category_id),
    model_version   TEXT,
    ruleset_version TEXT,
    error_code      TEXT,
    UNIQUE(source_id, content_hash, received_at)
) STRICT;

CREATE TABLE notification_embeddings (
    notification_id TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    model_id        TEXT NOT NULL,
    dimensions      INTEGER NOT NULL CHECK (dimensions > 0),
    vector_f32      BLOB NOT NULL, -- packed little-endian float32 values
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (notification_id, model_id)
) WITHOUT ROWID, STRICT;

CREATE TABLE notification_categories (
    notification_id TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    category_id     INTEGER NOT NULL REFERENCES categories(category_id),
    confidence      REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    is_primary      INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    assigned_by     TEXT NOT NULL CHECK (assigned_by IN ('rule', 'semantic', 'user')),
    assigned_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (notification_id, category_id)
) WITHOUT ROWID, STRICT;

CREATE UNIQUE INDEX one_primary_category
    ON notification_categories(notification_id)
    WHERE is_primary = 1;

CREATE TABLE notification_actions (
    action_id       INTEGER PRIMARY KEY,
    notification_id TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    action_type     TEXT NOT NULL CHECK (action_type IN
                    ('reply', 'open', 'verify_otp', 'review', 'custom')),
    action_required INTEGER NOT NULL DEFAULT 0 CHECK (action_required IN (0, 1)),
    status          TEXT NOT NULL DEFAULT 'open' CHECK
                    (status IN ('open', 'completed', 'dismissed', 'snoozed')),
    suggested_reply TEXT,
    deep_link       TEXT,
    due_at          TEXT,
    completed_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
                    CHECK (json_valid(metadata_json)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (completed_at IS NULL OR status = 'completed')
) STRICT;

-- A semantic worker writes one canonical undirected edge for each near-duplicate pair.
-- source_notification_id must sort before target_notification_id.
CREATE TABLE duplicate_edges (
    source_notification_id TEXT NOT NULL
        REFERENCES notifications(notification_id) ON DELETE CASCADE,
    target_notification_id TEXT NOT NULL
        REFERENCES notifications(notification_id) ON DELETE CASCADE,
    similarity             REAL NOT NULL CHECK (similarity BETWEEN 0.0 AND 1.0),
    model_id               TEXT NOT NULL,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (source_notification_id, target_notification_id),
    CHECK (source_notification_id < target_notification_id)
) WITHOUT ROWID, STRICT;

CREATE TABLE notification_threads (
    thread_id        TEXT PRIMARY KEY,
    representative_id TEXT NOT NULL REFERENCES notifications(notification_id),
    first_received_at TEXT NOT NULL,
    last_received_at  TEXT NOT NULL,
    member_count      INTEGER NOT NULL DEFAULT 0 CHECK (member_count >= 0),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE thread_members (
    thread_id        TEXT NOT NULL REFERENCES notification_threads(thread_id) ON DELETE CASCADE,
    notification_id  TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    similarity       REAL CHECK (similarity IS NULL OR similarity BETWEEN 0.0 AND 1.0),
    PRIMARY KEY (thread_id, notification_id),
    UNIQUE (notification_id)
) WITHOUT ROWID, STRICT;

CREATE INDEX notifications_received_at
    ON notifications(received_at DESC);
CREATE INDEX notifications_source_received
    ON notifications(source_id, received_at DESC);
CREATE INDEX notifications_state_received
    ON notifications(state, received_at ASC);
CREATE INDEX notifications_content_hash_received
    ON notifications(content_hash, received_at);
CREATE INDEX notification_actions_open_due
    ON notification_actions(status, due_at)
    WHERE status IN ('open', 'snoozed');
CREATE INDEX embeddings_model_created
    ON notification_embeddings(model_id, created_at DESC);
CREATE INDEX duplicate_edges_target
    ON duplicate_edges(target_notification_id, similarity DESC);
CREATE INDEX duplicate_edges_similarity
    ON duplicate_edges(similarity DESC);
CREATE INDEX thread_members_notification
    ON thread_members(notification_id);

INSERT INTO schema_migrations(version) VALUES (1);
COMMIT;
