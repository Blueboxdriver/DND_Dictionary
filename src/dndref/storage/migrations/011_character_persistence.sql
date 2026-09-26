-- Saved characters are user-owned and keep logical published identities only.
-- No reference table foreign keys are intentional: replacing a dataset must not
-- remove character decisions, and a returning exact identity resolves again.
CREATE TABLE user_characters (
    character_id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    edition TEXT NOT NULL CHECK (edition = '2024'),
    state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'complete')),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    species_identity TEXT,
    species_name TEXT,
    species_edition TEXT CHECK (species_edition IS NULL OR species_edition = '2024'),
    background_identity TEXT,
    background_name TEXT,
    background_edition TEXT CHECK (background_edition IS NULL OR background_edition = '2024'),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((species_identity IS NULL) = (species_name IS NULL)),
    CHECK ((background_identity IS NULL) = (background_name IS NULL))
);
CREATE INDEX user_characters_updated_idx ON user_characters(updated_at DESC, character_id);

CREATE TABLE user_character_levels (
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    total_level INTEGER NOT NULL CHECK (total_level BETWEEN 1 AND 20),
    class_identity TEXT NOT NULL,
    class_name TEXT NOT NULL CHECK (length(trim(class_name)) > 0),
    resulting_class_level INTEGER NOT NULL CHECK (resulting_class_level BETWEEN 1 AND 20),
    edition TEXT NOT NULL CHECK (edition = '2024'),
    PRIMARY KEY (character_id, total_level),
    UNIQUE (character_id, class_identity, resulting_class_level)
);

CREATE TABLE user_character_subclasses (
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    class_identity TEXT NOT NULL,
    class_name TEXT NOT NULL,
    subclass_identity TEXT NOT NULL,
    subclass_name TEXT NOT NULL,
    selected_class_level INTEGER NOT NULL CHECK (selected_class_level BETWEEN 1 AND 20),
    edition TEXT NOT NULL CHECK (edition = '2024'),
    PRIMARY KEY (character_id, class_identity)
);

CREATE TABLE user_character_ability_scores (
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    ability_key TEXT NOT NULL CHECK (ability_key IN ('str', 'dex', 'con', 'int', 'wis', 'cha')),
    base_score INTEGER NOT NULL CHECK (base_score BETWEEN 1 AND 30),
    PRIMARY KEY (character_id, ability_key)
);

CREATE TABLE user_character_ability_modifications (
    modification_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    ability_key TEXT NOT NULL CHECK (ability_key IN ('str', 'dex', 'con', 'int', 'wis', 'cha')),
    amount INTEGER NOT NULL CHECK (amount BETWEEN -30 AND 30 AND amount <> 0),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('background', 'asi', 'feat', 'other')),
    source_label TEXT NOT NULL,
    source_owner_dataset_id TEXT,
    source_owner_type TEXT,
    source_owner_key TEXT,
    source_choice_key TEXT,
    source_option_key TEXT,
    character_level INTEGER CHECK (character_level IS NULL OR character_level BETWEEN 1 AND 20),
    class_identity TEXT,
    class_level INTEGER CHECK (class_level IS NULL OR class_level BETWEEN 1 AND 20),
    source_rule TEXT,
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_type IS NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_key IS NULL)),
    CHECK (source_choice_key IS NULL OR source_owner_dataset_id IS NOT NULL)
);
CREATE INDEX user_character_ability_modifications_character_idx
    ON user_character_ability_modifications(character_id, ability_key);

CREATE TABLE user_character_choices (
    resolution_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    owner_dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN
        ('class', 'subclass', 'species', 'background', 'feat')),
    owner_key TEXT NOT NULL,
    choice_key TEXT NOT NULL,
    selected_option_key TEXT,
    selected_reference_kind TEXT,
    selected_reference_identity TEXT,
    selected_reference_name TEXT,
    selected_reference_edition TEXT CHECK
        (selected_reference_edition IS NULL OR selected_reference_edition = '2024'),
    selected_value TEXT,
    resolution_state TEXT NOT NULL CHECK (resolution_state IN ('resolved', 'unresolved')),
    selection_fingerprint TEXT NOT NULL,
    context_key TEXT NOT NULL,
    character_level INTEGER CHECK (character_level IS NULL OR character_level BETWEEN 1 AND 20),
    class_identity TEXT,
    class_level INTEGER CHECK (class_level IS NULL OR class_level BETWEEN 1 AND 20),
    source_rule TEXT NOT NULL,
    CHECK (selected_option_key IS NOT NULL OR selected_reference_identity IS NOT NULL
        OR selected_value IS NOT NULL),
    CHECK ((selected_reference_identity IS NULL) = (selected_reference_kind IS NULL)),
    CHECK ((selected_reference_identity IS NULL) = (selected_reference_name IS NULL)),
    CHECK ((selected_reference_identity IS NULL) = (selected_reference_edition IS NULL)),
    UNIQUE (character_id, owner_dataset_id, owner_type, owner_key, choice_key,
        context_key, selection_fingerprint)
);
CREATE INDEX user_character_choices_owner_idx ON user_character_choices(
    character_id, owner_dataset_id, owner_type, owner_key, choice_key, context_key
);

CREATE TABLE user_character_feats (
    feat_selection_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    feat_identity TEXT NOT NULL,
    feat_name TEXT NOT NULL,
    edition TEXT NOT NULL CHECK (edition = '2024'),
    provenance_kind TEXT NOT NULL CHECK
        (provenance_kind IN ('background', 'class_level', 'feat', 'other')),
    provenance_label TEXT NOT NULL,
    source_owner_dataset_id TEXT,
    source_owner_type TEXT,
    source_owner_key TEXT,
    source_choice_key TEXT,
    source_option_key TEXT,
    character_level INTEGER CHECK (character_level IS NULL OR character_level BETWEEN 1 AND 20),
    class_identity TEXT,
    class_level INTEGER CHECK (class_level IS NULL OR class_level BETWEEN 1 AND 20),
    source_rule TEXT,
    resolution_state TEXT NOT NULL DEFAULT 'resolved' CHECK
        (resolution_state IN ('resolved', 'unresolved')),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_type IS NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_key IS NULL)),
    CHECK (source_choice_key IS NULL OR source_owner_dataset_id IS NOT NULL)
);
CREATE INDEX user_character_feats_character_idx ON user_character_feats(character_id);

CREATE TABLE user_character_spells (
    spell_selection_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    spell_identity TEXT NOT NULL,
    spell_name TEXT NOT NULL,
    edition TEXT NOT NULL CHECK (edition = '2024'),
    acquisition TEXT NOT NULL CHECK (acquisition IN
        ('cantrip', 'known', 'prepared', 'always_prepared', 'spellbook', 'pact_magic', 'innate')),
    source_class_identity TEXT,
    source_class_name TEXT,
    character_level INTEGER CHECK (character_level IS NULL OR character_level BETWEEN 1 AND 20),
    class_level INTEGER CHECK (class_level IS NULL OR class_level BETWEEN 1 AND 20),
    source_owner_dataset_id TEXT,
    source_owner_type TEXT,
    source_owner_key TEXT,
    source_choice_key TEXT,
    source_option_key TEXT,
    source_rule TEXT,
    resolution_state TEXT NOT NULL DEFAULT 'resolved' CHECK
        (resolution_state IN ('resolved', 'unresolved')),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_type IS NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_key IS NULL)),
    CHECK (source_choice_key IS NULL OR source_owner_dataset_id IS NOT NULL),
    CHECK ((source_class_identity IS NULL) = (source_class_name IS NULL))
);
CREATE INDEX user_character_spells_character_idx ON user_character_spells(character_id);

CREATE TABLE user_character_equipment (
    equipment_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    item_identity TEXT,
    item_name TEXT,
    unresolved_selection TEXT,
    edition TEXT NOT NULL CHECK (edition = '2024'),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    equipped INTEGER NOT NULL DEFAULT 0 CHECK (equipped IN (0, 1)),
    carried_state TEXT NOT NULL DEFAULT 'carried' CHECK
        (carried_state IN ('carried', 'stowed')),
    provenance_kind TEXT NOT NULL DEFAULT 'other',
    provenance_label TEXT NOT NULL DEFAULT 'Character equipment',
    source_owner_dataset_id TEXT,
    source_owner_type TEXT,
    source_owner_key TEXT,
    source_choice_key TEXT,
    source_option_key TEXT,
    character_level INTEGER CHECK (character_level IS NULL OR character_level BETWEEN 1 AND 20),
    class_identity TEXT,
    class_level INTEGER CHECK (class_level IS NULL OR class_level BETWEEN 1 AND 20),
    source_rule TEXT,
    resolution_state TEXT NOT NULL CHECK (resolution_state IN ('resolved', 'unresolved')),
    CHECK ((item_identity IS NOT NULL AND item_name IS NOT NULL AND unresolved_selection IS NULL)
        OR (item_identity IS NULL AND item_name IS NULL AND unresolved_selection IS NOT NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_type IS NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_key IS NULL)),
    CHECK (source_choice_key IS NULL OR source_owner_dataset_id IS NOT NULL)
);
CREATE INDEX user_character_equipment_character_idx ON user_character_equipment(character_id);

CREATE TABLE user_character_hp_choices (
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    total_level INTEGER NOT NULL CHECK (total_level BETWEEN 1 AND 20),
    class_identity TEXT NOT NULL,
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 1 AND 20),
    choice_kind TEXT NOT NULL CHECK (choice_kind IN ('fixed_average', 'rolled')),
    amount INTEGER NOT NULL CHECK (amount BETWEEN 1 AND 20),
    edition TEXT NOT NULL CHECK (edition = '2024'),
    PRIMARY KEY (character_id, total_level)
);

CREATE TABLE user_character_notes (
    character_id TEXT PRIMARY KEY REFERENCES user_characters(character_id) ON DELETE CASCADE,
    note_text TEXT NOT NULL
);
