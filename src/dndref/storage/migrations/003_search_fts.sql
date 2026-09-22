CREATE VIRTUAL TABLE entry_search USING fts5(
    entry_id UNINDEXED,
    dataset_id UNINDEXED,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX entries_kind_name_idx
    ON entries(kind, normalized_name, dataset_id, local_key);

INSERT INTO entry_search (entry_id, dataset_id, body)
SELECT
    e.id,
    e.dataset_id,
    trim(
        e.name || ' ' || e.description || ' ' ||
        COALESCE((
            SELECT group_concat(es.heading || ' ' || es.body, ' ')
            FROM entry_sections AS es
            WHERE es.entry_id = e.id
        ), '') || ' ' ||
        CASE e.kind
            WHEN 'spell' THEN
                COALESCE((SELECT school || ' ' || casting_time || ' ' || range || ' ' || duration || ' ' ||
                    COALESCE(material_description, '') || ' ' || COALESCE(higher_level_effects, '')
                    FROM spells WHERE entry_id = e.id), '')
            WHEN 'item' THEN
                COALESCE((SELECT item_type || ' ' || COALESCE(subtype, '') || ' ' || COALESCE(rarity, '') || ' ' ||
                    COALESCE(attunement_prerequisite, '') FROM items WHERE entry_id = e.id), '') || ' ' ||
                COALESCE((SELECT group_concat(ip.name || ' ' || ip.description || ' ' || COALESCE(ipl.value, ''), ' ')
                    FROM item_property_links AS ipl
                    JOIN item_properties AS ip ON ip.id = ipl.property_id
                    WHERE ipl.item_id = e.id), '') || ' ' ||
                COALESCE((SELECT damage_expression || ' ' || damage_type || ' ' || range || ' ' ||
                    COALESCE(versatile_damage, '') || ' ' || COALESCE(mastery, '')
                    FROM weapon_details WHERE entry_id = e.id), '') || ' ' ||
                COALESCE((SELECT armor_category || ' ' || ac_expression FROM armor_details WHERE entry_id = e.id), '')
            WHEN 'feat' THEN
                COALESCE((SELECT category || ' ' || COALESCE(prerequisite, '') || ' ' ||
                    COALESCE(ability_increase, '') FROM feats WHERE entry_id = e.id), '')
            WHEN 'class' THEN
                COALESCE((SELECT primary_ability || ' ' || saving_throw_proficiencies || ' ' || skill_choices || ' ' ||
                    weapon_proficiencies || ' ' || armor_proficiencies || ' ' || tool_proficiencies || ' ' ||
                    starting_equipment || ' ' || multiclassing || ' ' || COALESCE(spellcasting_ability, '')
                    FROM classes WHERE entry_id = e.id), '') || ' ' ||
                COALESCE((SELECT group_concat(title || ' ' || description, ' ')
                    FROM class_features WHERE class_id = e.id), '') || ' ' ||
                COALESCE((SELECT group_concat(name || ' ' || introduction, ' ')
                    FROM subclasses WHERE class_id = e.id), '') || ' ' ||
                COALESCE((SELECT group_concat(sf.title || ' ' || sf.description, ' ')
                    FROM subclass_features AS sf
                    JOIN subclasses AS s ON s.id = sf.subclass_id
                    WHERE s.class_id = e.id), '') || ' ' ||
                COALESCE((SELECT group_concat(label, ' ')
                    FROM class_progression_columns WHERE class_id = e.id), '')
            ELSE ''
        END
    )
FROM entries AS e;
