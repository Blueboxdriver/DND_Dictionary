-- requires foreign_keys=off: add the two generic glossary kinds to entries.
CREATE TABLE entries_new (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    local_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('item', 'spell', 'feat', 'class', 'monster', 'condition', 'rule')),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    source_locator TEXT,
    image_id INTEGER,
    content_hash TEXT NOT NULL,
    rule_section TEXT,
    UNIQUE (dataset_id, local_key),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE SET NULL
);
INSERT INTO entries_new (
    id, dataset_id, local_key, kind, name, normalized_name, description,
    source_id, source_locator, image_id, content_hash, rule_section
)
SELECT id, dataset_id, local_key, kind, name, normalized_name, description,
       source_id, source_locator, image_id, content_hash, NULL
FROM entries;
DROP TABLE entries;
ALTER TABLE entries_new RENAME TO entries;
CREATE INDEX entries_dataset_name_idx ON entries(dataset_id, normalized_name);
CREATE INDEX entries_source_idx ON entries(source_id);
CREATE INDEX entries_kind_name_idx ON entries(kind, normalized_name, dataset_id, local_key);

CREATE TABLE entry_references (
    source_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    target_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL CHECK (content_type IN ('condition', 'rule')),
    PRIMARY KEY (source_entry_id, target_entry_id, content_type)
);
CREATE INDEX entry_references_source_idx ON entry_references(source_entry_id, content_type);
