PRAGMA foreign_keys = ON;

CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_key TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    rating REAL NOT NULL DEFAULT 1000,
    rating_deviation REAL NOT NULL DEFAULT 350,
    volatility REAL NOT NULL DEFAULT 0.06,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    games_played INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE matches (
    id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    winner TEXT NOT NULL CHECK (winner IN ('red', 'blue')),
    red_players_json TEXT NOT NULL,
    blue_players_json TEXT NOT NULL,
    played_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE match_players (
    match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    team TEXT NOT NULL CHECK (team IN ('red', 'blue')),
    won INTEGER NOT NULL CHECK (won IN (0, 1)),
    rating_before REAL NOT NULL,
    rating_after REAL NOT NULL,
    deviation_before REAL NOT NULL,
    deviation_after REAL NOT NULL,
    PRIMARY KEY (match_id, player_id)
);

CREATE INDEX match_players_player_id_index ON match_players(player_id);
CREATE INDEX matches_played_at_index ON matches(played_at DESC);
CREATE INDEX players_leaderboard_index ON players(rating DESC, wins DESC);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
