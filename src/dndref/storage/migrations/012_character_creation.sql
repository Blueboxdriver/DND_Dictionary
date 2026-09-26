ALTER TABLE user_characters
    ADD COLUMN creation_name_confirmed INTEGER NOT NULL DEFAULT 1
    CHECK (creation_name_confirmed IN (0, 1));

ALTER TABLE user_characters
    ADD COLUMN ability_score_method TEXT
    CHECK (ability_score_method IS NULL OR ability_score_method IN
        ('standard_array', 'point_buy', 'manual'));

CREATE TABLE user_character_currency (
    currency_id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES user_characters(character_id) ON DELETE CASCADE,
    currency_key TEXT NOT NULL CHECK (currency_key IN ('cp', 'sp', 'ep', 'gp', 'pp')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    provenance_kind TEXT NOT NULL DEFAULT 'other',
    provenance_label TEXT NOT NULL DEFAULT 'Character currency',
    source_owner_dataset_id TEXT,
    source_owner_type TEXT,
    source_owner_key TEXT,
    source_choice_key TEXT,
    source_option_key TEXT,
    source_rule TEXT,
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_type IS NULL)),
    CHECK ((source_owner_dataset_id IS NULL) = (source_owner_key IS NULL)),
    CHECK (source_choice_key IS NULL OR source_owner_dataset_id IS NOT NULL)
);
CREATE INDEX user_character_currency_character_idx
    ON user_character_currency(character_id, currency_key);
