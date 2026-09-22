CREATE TABLE datasets (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    ruleset TEXT NOT NULL,
    language TEXT NOT NULL,
    license_identifier TEXT NOT NULL,
    attribution TEXT NOT NULL,
    origin_url TEXT,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dataset_dependencies (
    dataset_id TEXT NOT NULL,
    dependency_dataset_id TEXT NOT NULL,
    version TEXT,
    PRIMARY KEY (dataset_id, dependency_dataset_id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    title TEXT NOT NULL,
    edition TEXT,
    citation TEXT,
    UNIQUE (dataset_id, source_key),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    UNIQUE (dataset_id, relative_path),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE entries (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    local_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('item', 'spell', 'feat', 'class')),
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

CREATE INDEX entries_dataset_name_idx ON entries(dataset_id, normalized_name);
CREATE INDEX entries_source_idx ON entries(source_id);

CREATE TABLE entry_sections (
    entry_id INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    heading TEXT NOT NULL,
    body TEXT NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    PRIMARY KEY (entry_id, section_key),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE spells (
    entry_id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 9),
    school TEXT NOT NULL,
    casting_time TEXT NOT NULL,
    range TEXT NOT NULL,
    verbal INTEGER NOT NULL CHECK (verbal IN (0, 1)),
    somatic INTEGER NOT NULL CHECK (somatic IN (0, 1)),
    material INTEGER NOT NULL CHECK (material IN (0, 1)),
    material_description TEXT,
    duration TEXT NOT NULL,
    concentration INTEGER NOT NULL CHECK (concentration IN (0, 1)),
    ritual INTEGER NOT NULL CHECK (ritual IN (0, 1)),
    higher_level_effects TEXT,
    UNIQUE (dataset_id, entry_id),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE spell_classes (
    dataset_id TEXT NOT NULL,
    spell_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    PRIMARY KEY (spell_id, class_id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (spell_id) REFERENCES spells(entry_id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(entry_id) ON DELETE CASCADE
);

CREATE TABLE items (
    entry_id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_type TEXT NOT NULL,
    subtype TEXT,
    rarity TEXT,
    requires_attunement INTEGER NOT NULL CHECK (requires_attunement IN (0, 1)),
    attunement_prerequisite TEXT,
    weight_display TEXT,
    weight TEXT,
    cost_display TEXT,
    cost_amount TEXT,
    cost_currency TEXT,
    UNIQUE (dataset_id, entry_id),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE item_properties (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    property_key TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    UNIQUE (dataset_id, property_key),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE item_property_links (
    dataset_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    value TEXT,
    PRIMARY KEY (item_id, property_id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES items(entry_id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES item_properties(id) ON DELETE CASCADE
);

CREATE TABLE weapon_details (
    entry_id INTEGER PRIMARY KEY,
    damage_expression TEXT NOT NULL,
    damage_type TEXT NOT NULL,
    range TEXT NOT NULL,
    versatile_damage TEXT,
    mastery TEXT,
    FOREIGN KEY (entry_id) REFERENCES items(entry_id) ON DELETE CASCADE
);

CREATE TABLE armor_details (
    entry_id INTEGER PRIMARY KEY,
    armor_category TEXT NOT NULL,
    ac_expression TEXT NOT NULL,
    strength_requirement INTEGER,
    stealth_disadvantage INTEGER NOT NULL CHECK (stealth_disadvantage IN (0, 1)),
    FOREIGN KEY (entry_id) REFERENCES items(entry_id) ON DELETE CASCADE
);

CREATE TABLE feats (
    entry_id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    category TEXT NOT NULL,
    prerequisite TEXT,
    minimum_level INTEGER CHECK (minimum_level IS NULL OR minimum_level BETWEEN 1 AND 20),
    repeatable INTEGER NOT NULL CHECK (repeatable IN (0, 1)),
    ability_increase TEXT,
    UNIQUE (dataset_id, entry_id),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE classes (
    entry_id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    hit_die INTEGER NOT NULL CHECK (hit_die IN (6, 8, 10, 12)),
    primary_ability TEXT NOT NULL,
    saving_throw_proficiencies TEXT NOT NULL,
    skill_choices TEXT NOT NULL,
    weapon_proficiencies TEXT NOT NULL,
    armor_proficiencies TEXT NOT NULL,
    tool_proficiencies TEXT NOT NULL,
    starting_equipment TEXT NOT NULL,
    multiclassing TEXT NOT NULL,
    spellcasting_ability TEXT,
    UNIQUE (dataset_id, entry_id),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);

CREATE TABLE class_features (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 20),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    source_id INTEGER,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    UNIQUE (dataset_id, class_id, feature_key),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(entry_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE subclasses (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    subclass_key TEXT NOT NULL,
    name TEXT NOT NULL,
    introduction TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    UNIQUE (dataset_id, class_id, subclass_key),
    UNIQUE (dataset_id, id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(entry_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE subclass_features (
    id INTEGER PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    subclass_id INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 20),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    source_id INTEGER,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    UNIQUE (dataset_id, subclass_id, feature_key),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (subclass_id) REFERENCES subclasses(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE class_levels (
    class_id INTEGER NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 20),
    proficiency_bonus TEXT,
    PRIMARY KEY (class_id, level),
    FOREIGN KEY (class_id) REFERENCES classes(entry_id) ON DELETE CASCADE
);

CREATE TABLE class_progression_columns (
    class_id INTEGER NOT NULL,
    column_key TEXT NOT NULL,
    label TEXT NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order >= 0),
    PRIMARY KEY (class_id, column_key),
    FOREIGN KEY (class_id) REFERENCES classes(entry_id) ON DELETE CASCADE
);

CREATE TABLE class_progression_values (
    class_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    column_key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (class_id, level, column_key),
    FOREIGN KEY (class_id, level) REFERENCES class_levels(class_id, level) ON DELETE CASCADE,
    FOREIGN KEY (class_id, column_key)
        REFERENCES class_progression_columns(class_id, column_key) ON DELETE CASCADE
);

CREATE INDEX spell_classes_class_idx ON spell_classes(class_id);
CREATE INDEX item_property_links_property_idx ON item_property_links(property_id);
CREATE INDEX class_features_class_idx ON class_features(class_id, level, display_order);
CREATE INDEX subclass_features_subclass_idx ON subclass_features(subclass_id, level, display_order);
CREATE INDEX class_levels_class_idx ON class_levels(class_id, level);
