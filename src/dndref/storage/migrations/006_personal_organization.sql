-- User-owned records keep stable logical entry IDs and deliberately do not
-- reference imported rows with foreign keys: dataset replacement may remove them.
CREATE TABLE user_favorites (
    entry_identity TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    edition TEXT,
    entry_name TEXT NOT NULL,
    source_identity TEXT,
    source_label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_collections (
    collection_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_collection_entries (
    collection_id INTEGER NOT NULL REFERENCES user_collections(collection_id) ON DELETE CASCADE,
    entry_identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    edition TEXT,
    entry_name TEXT NOT NULL,
    source_identity TEXT,
    source_label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id, entry_identity)
);
CREATE INDEX user_collection_entries_identity_idx
    ON user_collection_entries(entry_identity);

CREATE TABLE user_tags (
    tag_id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0)
);

CREATE TABLE user_entry_tags (
    entry_identity TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES user_tags(tag_id) ON DELETE CASCADE,
    PRIMARY KEY (entry_identity, tag_id)
);
CREATE INDEX user_entry_tags_tag_idx ON user_entry_tags(tag_id, entry_identity);

CREATE TABLE user_notes (
    entry_identity TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    edition TEXT,
    entry_name TEXT NOT NULL,
    source_identity TEXT,
    source_label TEXT,
    note_text TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
