-- Search history is local user data, independent of imported datasets.
CREATE TABLE user_recent_searches (
    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL UNIQUE CHECK (length(trim(query)) > 0),
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX user_recent_searches_used_idx
    ON user_recent_searches(search_id DESC);
