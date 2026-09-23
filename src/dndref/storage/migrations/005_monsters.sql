-- requires foreign_keys=off: SQLite cannot alter the entries.kind CHECK in place.
CREATE TABLE entries_new (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    local_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('item', 'spell', 'feat', 'class', 'monster')),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    source_locator TEXT,
    image_id INTEGER,
    content_hash TEXT NOT NULL,
    UNIQUE (dataset_id, local_key),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE SET NULL
);
INSERT INTO entries_new SELECT * FROM entries;
DROP TABLE entries;
ALTER TABLE entries_new RENAME TO entries;
CREATE INDEX entries_dataset_name_idx ON entries(dataset_id, normalized_name);
CREATE INDEX entries_source_idx ON entries(source_id);
CREATE INDEX entries_kind_name_idx ON entries(kind, normalized_name, dataset_id, local_key);

CREATE TABLE monsters (
    entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    page INTEGER,
    group_name TEXT,
    variant TEXT,
    size TEXT NOT NULL,
    creature_type TEXT NOT NULL,
    subtype TEXT,
    alignment TEXT,
    armor_class TEXT NOT NULL,
    hit_points INTEGER CHECK (hit_points IS NULL OR hit_points >= 0),
    hit_points_text TEXT NOT NULL,
    hit_dice TEXT,
    speed_json TEXT NOT NULL,
    speed_text TEXT,
    abilities_json TEXT NOT NULL,
    saving_throws_json TEXT NOT NULL,
    skills_json TEXT NOT NULL,
    proficiency_bonus TEXT,
    damage_vulnerabilities TEXT,
    damage_resistances TEXT,
    damage_immunities TEXT,
    condition_immunities TEXT,
    senses TEXT,
    passive_perception INTEGER,
    languages TEXT,
    telepathy TEXT,
    challenge_rating TEXT NOT NULL,
    cr_eighths INTEGER NOT NULL CHECK (cr_eighths >= 0),
    xp INTEGER,
    legendary_intro TEXT
);
CREATE INDEX monsters_filters_idx ON monsters(cr_eighths, creature_type, size);

CREATE TABLE monster_abilities (
    id INTEGER PRIMARY KEY,
    monster_id INTEGER NOT NULL REFERENCES monsters(entry_id) ON DELETE CASCADE,
    section TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    cost INTEGER CHECK (cost IS NULL OR cost >= 1),
    UNIQUE (monster_id, section, display_order)
);
CREATE INDEX monster_abilities_order_idx ON monster_abilities(monster_id, section, display_order);
