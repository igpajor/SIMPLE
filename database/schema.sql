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

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    statement_path TEXT,
    statement TEXT NOT NULL DEFAULT '',
    example TEXT NOT NULL DEFAULT '',
    terminal_model TEXT NOT NULL DEFAULT '',
    hints TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE
        CHECK (length(username) BETWEEN 3 AND 32),
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    must_change_password INTEGER NOT NULL DEFAULT 0
        CHECK (must_change_password IN (0, 1)),
    display_name TEXT NOT NULL DEFAULT '',
    github_username TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'rejected', 'approved')),
    content_kind TEXT NOT NULL DEFAULT 'legacy'
        CHECK (content_kind IN ('legacy', 'github_commit')),
    commit_url TEXT,
    delivery_directory TEXT,
    submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evaluation_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL UNIQUE REFERENCES submissions(id),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'completed')),
    reviewer_id INTEGER REFERENCES users(id),
    accepted_by INTEGER REFERENCES users(id),
    accepted_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    CHECK (
        (status = 'open' AND reviewer_id IS NULL AND completed_at IS NULL)
        OR
        (status = 'completed' AND reviewer_id IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS evaluation_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES evaluation_slots(id),
    reviewer_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'cancelled', 'completed')),
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at DATETIME
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL UNIQUE REFERENCES evaluation_slots(id),
    reviewer_id INTEGER NOT NULL REFERENCES users(id),
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
    feedback TEXT NOT NULL DEFAULT '',
    recipient_score INTEGER CHECK (recipient_score IS NULL OR recipient_score BETWEEN 0 AND 5),
    recipient_feedback TEXT NOT NULL DEFAULT '',
    recipient_responded_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL CHECK (type IN ('review_received', 'response_received')),
    review_id INTEGER NOT NULL REFERENCES reviews(id),
    read_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, type, review_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL CHECK (length(trim(body)) BETWEEN 1 AND 500),
    channel TEXT NOT NULL DEFAULT 'global',
    recipient_id INTEGER REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hidden_at DATETIME,
    pinned_at DATETIME
);

CREATE TABLE IF NOT EXISTS chat_reads (
    user_id INTEGER NOT NULL REFERENCES users(id),
    channel TEXT NOT NULL,
    last_read_message_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, channel)
);

CREATE TABLE IF NOT EXISTS chat_mentions (
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS chat_direct_rooms (
    user_low_id INTEGER NOT NULL REFERENCES users(id),
    user_high_id INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (user_low_id < user_high_id),
    PRIMARY KEY (user_low_id, user_high_id)
);

CREATE TABLE IF NOT EXISTS chat_reactions (
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    reaction TEXT NOT NULL CHECK (reaction IN ('👍', '❤️', '😂', '+1')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, user_id, reaction)
);

CREATE TABLE IF NOT EXISTS chat_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES chat_messages(id),
    reporter_id INTEGER NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 500),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (message_id, reporter_id)
);

CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK (amount != 0),
    entry_type TEXT NOT NULL
        CHECK (entry_type IN ('welcome', 'slot_opened', 'review_completed')),
    submission_id INTEGER REFERENCES submissions(id),
    review_id INTEGER REFERENCES reviews(id),
    details TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_point_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    admin_id INTEGER NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK (amount != 0),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identity_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    linux_uid INTEGER NOT NULL CHECK (linux_uid > 0),
    user_id INTEGER NOT NULL REFERENCES users(id),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_bindings_active_uid
    ON identity_bindings(linux_uid) WHERE is_active = 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_bindings_active_user
    ON identity_bindings(user_id) WHERE is_active = 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_points_welcome_once
    ON points_ledger(user_id)
    WHERE entry_type = 'welcome';

CREATE UNIQUE INDEX IF NOT EXISTS idx_points_slot_cost_once
    ON points_ledger(submission_id)
    WHERE entry_type = 'slot_opened';

CREATE UNIQUE INDEX IF NOT EXISTS idx_points_review_reward_once
    ON points_ledger(review_id)
    WHERE entry_type = 'review_completed';

CREATE INDEX IF NOT EXISTS idx_submissions_user_exercise
    ON submissions(user_id, exercise_id, status);

CREATE INDEX IF NOT EXISTS idx_slots_status
    ON evaluation_slots(status, created_at);

CREATE INDEX IF NOT EXISTS idx_evaluation_requests_slot_status
    ON evaluation_requests(slot_id, status);
CREATE INDEX IF NOT EXISTS idx_evaluation_requests_reviewer_status
    ON evaluation_requests(reviewer_id, status);

CREATE INDEX IF NOT EXISTS idx_chat_mentions_user_message
    ON chat_mentions(user_id, message_id);

CREATE INDEX IF NOT EXISTS idx_admin_adjustments_user
    ON admin_point_adjustments(user_id, created_at);

CREATE TRIGGER IF NOT EXISTS trg_users_initial_point
AFTER INSERT ON users
BEGIN
    INSERT INTO points_ledger (user_id, amount, entry_type, details)
    VALUES (NEW.id, 1, 'welcome', 'Crédito inicial');
END;

DROP TRIGGER IF EXISTS trg_submission_validate;

CREATE TRIGGER trg_submission_validate
BEFORE INSERT ON submissions
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM exercises
            WHERE id = NEW.exercise_id AND status = 'available'
        ) THEN RAISE(ABORT, 'Exercício indisponível')
        WHEN COALESCE((
            SELECT SUM(amount) FROM points_ledger WHERE user_id = NEW.user_id
        ), 0) + COALESCE((
            SELECT SUM(amount) FROM admin_point_adjustments WHERE user_id = NEW.user_id
        ), 0) < 1 THEN RAISE(ABORT, 'Saldo insuficiente para abrir um slot')
        WHEN EXISTS (
            SELECT 1 FROM submissions
            WHERE user_id = NEW.user_id
              AND exercise_id = NEW.exercise_id
              AND status IN ('pending', 'approved')
        ) THEN RAISE(ABORT, 'Já existe uma submissão ativa ou aprovada para este exercício')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_submission_open_slot
AFTER INSERT ON submissions
BEGIN
    INSERT INTO points_ledger (
        user_id, amount, entry_type, submission_id, details
    ) VALUES (
        NEW.user_id, -1, 'slot_opened', NEW.id, 'Abertura de slot de avaliação'
    );

    INSERT INTO evaluation_slots (submission_id)
    VALUES (NEW.id);
END;

CREATE TRIGGER IF NOT EXISTS trg_review_validate
BEFORE INSERT ON reviews
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM evaluation_slots
            WHERE id = NEW.slot_id AND status = 'open'
        ) THEN RAISE(ABORT, 'Slot de avaliação indisponível')
        WHEN NEW.reviewer_id = (
            SELECT s.user_id
            FROM submissions s
            JOIN evaluation_slots slot ON slot.submission_id = s.id
            WHERE slot.id = NEW.slot_id
        ) THEN RAISE(ABORT, 'Não é permitido avaliar a própria submissão')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_review_complete
AFTER INSERT ON reviews
BEGIN
    UPDATE evaluation_slots
    SET status = 'completed',
        reviewer_id = NEW.reviewer_id,
        completed_at = CURRENT_TIMESTAMP
    WHERE id = NEW.slot_id;

    UPDATE submissions
    SET status = CASE WHEN NEW.score >= 3 THEN 'approved' ELSE 'rejected' END
    WHERE id = (
        SELECT submission_id FROM evaluation_slots WHERE id = NEW.slot_id
    );

    INSERT INTO points_ledger (
        user_id, amount, entry_type, review_id, details
    ) VALUES (
        NEW.reviewer_id, 1, 'review_completed', NEW.id, 'Avaliação concluída'
    );
END;
