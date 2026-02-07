CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    referred_by BIGINT,
    tokens BIGINT DEFAULT 0,
    is_banned BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS token_history (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    change_amount BIGINT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL DEFAULT 'registration',
    rarity TEXT NOT NULL DEFAULT 'Normal',
    reward_tokens BIGINT NOT NULL DEFAULT 15000,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS user_tasks (
    user_id BIGINT NOT NULL,
    task_id INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    enabled BOOLEAN DEFAULT TRUE,
    completed_at TIMESTAMP,
    PRIMARY KEY (user_id, task_id)
);

CREATE TABLE IF NOT EXISTS mandatory_channels (
    id SERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    channel_title TEXT,
    channel_username TEXT
);

CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    media_type TEXT,
    media_url TEXT,
    button_text TEXT,
    button_url TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings (key, value)
VALUES
  ('token_rate', '1000=0.1'),
  ('support_link', 'https://t.me/support')
ON CONFLICT (key) DO NOTHING;
