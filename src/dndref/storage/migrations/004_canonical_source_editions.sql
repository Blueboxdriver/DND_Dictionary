-- Source edition was already stored as display text by migration 002.
-- Canonicalize known historical labels while preserving unknown/custom values.
UPDATE sources
SET edition = CASE edition
    WHEN '2014 rules' THEN '2014'
    WHEN '2024 rules' THEN '2024'
    ELSE edition
END
WHERE edition IN ('2014 rules', '2024 rules');
