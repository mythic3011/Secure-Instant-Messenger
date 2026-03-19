-- migrations/001_init.sql
-- Run once on fresh DB. Never modify after merging.
-- SQLite 3.35+ required (for GENERATED columns).

PRAGMA journal_mode = WAL;       -- concurrent reads during writes
PRAGMA foreign_keys = ON;        -- enforce FK constraints
PRAGMA secure_delete = ON;       -- zero-fill deleted pages (TTL data)

-- -----------------------------------------------------------------------
-- Users
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    username    TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pw_hash     TEXT NOT NULL,              -- argon2id output (includes salt)
    totp_secret TEXT NOT NULL,              -- encrypted with server-side key
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    deleted_at  INTEGER                     -- soft delete
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- -----------------------------------------------------------------------
-- Public key bundles
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public_keys (
    user_id         TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    identity_pub    TEXT NOT NULL,          -- base64 Ed25519 public key (32 bytes)
    dh_pub          TEXT NOT NULL,          -- base64 X25519 public key (32 bytes)
    key_sig         TEXT NOT NULL,          -- base64 Ed25519 sig over (identity_pub||dh_pub)
    uploaded_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

-- -----------------------------------------------------------------------
-- Sessions / tokens
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,       -- SHA256 of the bearer token
    expires_at  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    revoked     INTEGER NOT NULL DEFAULT 0  -- 1 = revoked (logout)
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id, revoked);

-- -----------------------------------------------------------------------
-- Friend requests
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS friend_requests (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    sender_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','accepted','declined','cancelled')),
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE(sender_id, recipient_id)
);

CREATE INDEX IF NOT EXISTS idx_fr_recipient ON friend_requests(recipient_id, status);
CREATE INDEX IF NOT EXISTS idx_fr_sender    ON friend_requests(sender_id, status);

-- -----------------------------------------------------------------------
-- Friendships (mutual, canonical order: user_a_id < user_b_id)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS friendships (
    user_a_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (user_a_id, user_b_id),
    CHECK (user_a_id < user_b_id)
);

-- -----------------------------------------------------------------------
-- Blocks
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS blocks (
    blocker_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (blocker_id, blocked_id)
);

-- -----------------------------------------------------------------------
-- Messages (server stores ciphertext only)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,           -- client-generated UUID v4
    conversation_id TEXT NOT NULL,
    sender_id       TEXT NOT NULL REFERENCES users(id),
    recipient_id    TEXT NOT NULL REFERENCES users(id),
    counter         INTEGER NOT NULL,
    nonce_b64       TEXT NOT NULL,
    ciphertext_b64  TEXT NOT NULL,
    eph_pub_b64     TEXT,                       -- non-null on session-initiating message only
    conv_dh_pub_b64 TEXT,                       -- per-conversation DH pub, first message only
    chain_index     INTEGER NOT NULL DEFAULT 0, -- ratchet chain index for this message
    ttl_seconds     INTEGER,                    -- NULL = no expiry
    sent_at         INTEGER NOT NULL,           -- client clock (unix)
    stored_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    delivered_at    INTEGER,                    -- NULL = not yet delivered
    expires_at      INTEGER GENERATED ALWAYS AS (
                        CASE WHEN ttl_seconds IS NOT NULL
                        THEN stored_at + ttl_seconds
                        ELSE NULL END
                    ) VIRTUAL,

    -- Replay prevention: same counter per conversation+sender cannot be inserted twice
    UNIQUE(conversation_id, sender_id, counter)
);

CREATE INDEX IF NOT EXISTS idx_msg_recipient_undelivered
    ON messages(recipient_id, delivered_at)
    WHERE delivered_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_msg_conversation
    ON messages(conversation_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_msg_expires
    ON messages(expires_at)
    WHERE expires_at IS NOT NULL;

-- -----------------------------------------------------------------------
-- Conversations (metadata cache — server learns this, document in report)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id                  TEXT PRIMARY KEY,       -- server-assigned random ID (secrets.token_hex(16))
    user_a_id           TEXT NOT NULL REFERENCES users(id),
    user_b_id           TEXT NOT NULL REFERENCES users(id),
    last_message_at     INTEGER,
    unread_count_a      INTEGER NOT NULL DEFAULT 0,
    unread_count_b      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_a_id, user_b_id),
    CHECK (user_a_id < user_b_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_user_a ON conversations(user_a_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conv_user_b ON conversations(user_b_id, last_message_at DESC);

-- -----------------------------------------------------------------------
-- Rate limiting (simple token bucket per IP / user)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rate_limits (
    key         TEXT PRIMARY KEY,   -- e.g. "login:192.168.1.1" or "register:username"
    attempts    INTEGER NOT NULL DEFAULT 1,
    window_start INTEGER NOT NULL DEFAULT (unixepoch()),
    locked_until INTEGER              -- NULL = not locked
);
