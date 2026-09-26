-- Reference rules only. Saved character state is intentionally out of scope.
CREATE TABLE character_builder_owners (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN
        ('class', 'subclass', 'species', 'background', 'feat', 'item', 'optional_feature')),
    owner_key TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    edition TEXT NOT NULL CHECK (edition = '2024'),
    name TEXT NOT NULL,
    parent_class_key TEXT,
    description TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (dataset_id, owner_type, owner_key),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX character_builder_owners_name_idx
    ON character_builder_owners(dataset_id, owner_type, name);

CREATE TABLE character_rule_requirements (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    requirement_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_rule TEXT NOT NULL,
    PRIMARY KEY (dataset_id, owner_type, owner_key, requirement_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE class_progression_events (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('class', 'subclass')),
    owner_key TEXT NOT NULL,
    event_key TEXT NOT NULL,
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 1 AND 20),
    title TEXT NOT NULL,
    source_rule TEXT NOT NULL,
    requirement_key TEXT,
    PRIMARY KEY (dataset_id, owner_type, owner_key, event_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, requirement_key)
        REFERENCES character_rule_requirements(dataset_id, owner_type, owner_key, requirement_key)
);

CREATE INDEX class_progression_events_level_idx
    ON class_progression_events(dataset_id, owner_type, owner_key, class_level);

CREATE TABLE character_rule_choices (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    choice_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    event_key TEXT,
    choice_type TEXT NOT NULL,
    choice_count INTEGER NOT NULL CHECK (choice_count BETWEEN 1 AND 100),
    criteria_kind TEXT,
    criteria_values_json TEXT,
    criteria_filters_json TEXT,
    requirement_key TEXT,
    depends_on_choice TEXT,
    depends_on_option TEXT,
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    source_rule TEXT NOT NULL,
    CHECK ((depends_on_choice IS NULL) = (depends_on_option IS NULL)),
    PRIMARY KEY (dataset_id, owner_type, owner_key, choice_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, event_key)
        REFERENCES class_progression_events(dataset_id, owner_type, owner_key, event_key)
        ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, requirement_key)
        REFERENCES character_rule_requirements(dataset_id, owner_type, owner_key, requirement_key),
    FOREIGN KEY (
        dataset_id, owner_type, owner_key, depends_on_choice, depends_on_option
    ) REFERENCES character_rule_choice_options(
        dataset_id, owner_type, owner_key, choice_key, option_key
    ) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX character_rule_choices_context_idx
    ON character_rule_choices(dataset_id, owner_type, owner_key, scope, event_key);

CREATE TABLE character_rule_choice_options (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    choice_key TEXT NOT NULL,
    option_key TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT,
    reference_kind TEXT,
    reference_identity TEXT,
    ability_increases_json TEXT NOT NULL DEFAULT '[]',
    group_name TEXT,
    resolved INTEGER NOT NULL CHECK (resolved IN (0, 1)),
    PRIMARY KEY (dataset_id, owner_type, owner_key, choice_key, option_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key, choice_key)
        REFERENCES character_rule_choices(dataset_id, owner_type, owner_key, choice_key)
        ON DELETE CASCADE
);

CREATE TABLE character_rule_grants (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    grant_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    event_key TEXT,
    choice_key TEXT,
    option_key TEXT,
    grant_type TEXT NOT NULL,
    value TEXT,
    reference_kind TEXT,
    reference_identity TEXT,
    proficiency_kind TEXT,
    quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
    unit TEXT,
    activation_requirement_key TEXT,
    source_rule TEXT NOT NULL,
    unresolved INTEGER NOT NULL CHECK (unresolved IN (0, 1)),
    PRIMARY KEY (dataset_id, owner_type, owner_key, grant_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, event_key)
        REFERENCES class_progression_events(dataset_id, owner_type, owner_key, event_key)
        ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, choice_key, option_key)
        REFERENCES character_rule_choice_options(
            dataset_id, owner_type, owner_key, choice_key, option_key
        ) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_type, owner_key, activation_requirement_key)
        REFERENCES character_rule_requirements(
            dataset_id, owner_type, owner_key, requirement_key
        )
);

CREATE INDEX character_rule_grants_context_idx
    ON character_rule_grants(dataset_id, owner_type, owner_key, scope, event_key);

CREATE TABLE character_builder_traits (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    trait_key TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_rule TEXT NOT NULL,
    PRIMARY KEY (dataset_id, owner_type, owner_key, trait_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE class_spellcasting (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('class', 'subclass')),
    owner_key TEXT NOT NULL,
    spellcasting_ability TEXT NOT NULL CHECK
        (spellcasting_ability IN ('str', 'dex', 'con', 'int', 'wis', 'cha')),
    spellcasting_model TEXT NOT NULL CHECK (spellcasting_model IN ('spellcasting', 'pact_magic')),
    multiclass_contribution TEXT NOT NULL CHECK (multiclass_contribution IN
        ('none', 'full', 'half_round_up', 'half_round_down', 'third_round_down',
         'pact_magic_separate')),
    acquisition TEXT NOT NULL CHECK (acquisition IN ('prepared', 'known', 'spellbook', 'special')),
    spell_list_reference_kind TEXT,
    spell_list_reference_identity TEXT,
    prepared_spells_change TEXT,
    source_rule TEXT NOT NULL,
    PRIMARY KEY (dataset_id, owner_type, owner_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE class_spellcasting_levels (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 1 AND 20),
    cantrips_known INTEGER CHECK (cantrips_known IS NULL OR cantrips_known >= 0),
    prepared_spells INTEGER CHECK (prepared_spells IS NULL OR prepared_spells >= 0),
    known_spells INTEGER CHECK (known_spells IS NULL OR known_spells >= 0),
    PRIMARY KEY (dataset_id, owner_type, owner_key, class_level),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES class_spellcasting(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE class_spell_slots (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    progression_kind TEXT NOT NULL CHECK (progression_kind IN ('standalone', 'pact')),
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 1 AND 20),
    spell_level INTEGER NOT NULL CHECK (spell_level BETWEEN 1 AND 9),
    slot_count INTEGER NOT NULL CHECK (slot_count >= 0),
    PRIMARY KEY (dataset_id, owner_type, owner_key, progression_kind, class_level, spell_level),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES class_spellcasting(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE character_builder_spell_access (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    access_key TEXT NOT NULL,
    access_type TEXT NOT NULL CHECK
        (access_type IN ('known', 'prepared', 'innate', 'expanded', 'spellbook')),
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 1 AND 20),
    level_scope TEXT NOT NULL CHECK (level_scope IN ('class', 'total')),
    source_rule TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, owner_type, owner_key, access_key),
    FOREIGN KEY (dataset_id, owner_type, owner_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE character_builder_equipment (
    dataset_id TEXT NOT NULL,
    owner_type TEXT NOT NULL DEFAULT 'item' CHECK (owner_type = 'item'),
    item_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('weapon', 'armor', 'other')),
    weapon_category TEXT CHECK (weapon_category IN ('simple', 'martial')),
    attack_type TEXT CHECK (attack_type IN ('melee', 'ranged')),
    damage TEXT,
    damage_type TEXT,
    range_json TEXT NOT NULL DEFAULT '[]',
    properties_json TEXT NOT NULL DEFAULT '[]',
    mastery_references_json TEXT NOT NULL DEFAULT '[]',
    ammunition_reference_json TEXT,
    versatile_damage TEXT,
    armor_category TEXT CHECK (armor_category IN ('light', 'medium', 'heavy', 'shield')),
    base_ac INTEGER CHECK (base_ac IS NULL OR base_ac >= 0),
    dexterity_rule TEXT CHECK (dexterity_rule IN ('full', 'cap', 'none', 'shield_bonus')),
    dexterity_cap INTEGER CHECK (dexterity_cap IS NULL OR dexterity_cap >= 0),
    strength_requirement INTEGER CHECK
        (strength_requirement IS NULL OR strength_requirement >= 0),
    stealth_disadvantage INTEGER CHECK
        (stealth_disadvantage IS NULL OR stealth_disadvantage IN (0, 1)),
    unresolved_fields_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (dataset_id, owner_type, item_key),
    FOREIGN KEY (dataset_id, owner_type, item_key)
        REFERENCES character_builder_owners(dataset_id, owner_type, owner_key) ON DELETE CASCADE
);

CREATE TABLE character_builder_skills (
    dataset_id TEXT NOT NULL,
    skill_key TEXT NOT NULL,
    name TEXT NOT NULL,
    ability_key TEXT NOT NULL CHECK
        (ability_key IN ('str', 'dex', 'con', 'int', 'wis', 'cha')),
    PRIMARY KEY (dataset_id, skill_key),
    FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE
);
