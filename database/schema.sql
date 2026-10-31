PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,

    delivery_directory TEXT,
    delivery_files TEXT,
    allowed_functions TEXT,

    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'archived')),

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
