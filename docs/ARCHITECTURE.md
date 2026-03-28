# COMP3334 Secure IM — Architecture & Protocol Design

> Stack: Python (FastAPI + Textual + SQLite). Implemented and tested.
> 5-person team | Deadline: 2 April 2026 | Implemented and tested

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Trust Boundaries & Data Flow](#2-trust-boundaries--data-flow)
3. [Cryptographic Protocol Design](#3-cryptographic-protocol-design)
4. [Message Format Specification](#4-message-format-specification)
5. [Replay Protection Design](#5-replay-protection-design)
6. [Security Requirements Mapping](#6-security-requirements-mapping)
7. [Auth Token Design](#7-auth-token-design)
8. [Secure Local Key Storage](#8-secure-local-key-storage)
9. [TOTP Secret Encryption](#9-totp-secret-encryption)
10. [Input Validation & Logging Policy](#10-input-validation--logging-policy)
11. [Stack & File Structure](#11-stack--file-structure)
12. [Database Schema](#12-database-schema)
13. [CI / Deploy](#13-ci--deploy)
14. [Security Limitations & Trade-offs](#14-security-limitations--trade-offs)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT A                                  │
│  ┌──────────┐   ┌───────────────┐   ┌─────────────────────┐    │
│  │  TUI     │──▶│  Session Mgr  │──▶│  Crypto Engine      │    │
│  │ Textual  │   │  (app.py)     │   │  X25519 ECDH        │    │
│  └──────────┘   └───────────────┘   │  AES-256-GCM        │    │
│                                      │  Ed25519            │    │
│  ┌──────────────────────────────┐   │  HKDF-SHA256        │    │
│  │  Local Encrypted Storage     │   │  Argon2id           │    │
│  │  keystore.json  (Argon2id +  │   └─────────────────────┘    │
│  │  AES-GCM), sessions.json,    │                               │
│  │  messages.db (SQLite)        │                               │
│  └──────────────────────────────┘                               │
└─────────────────────────┬───────────────────────────────────────┘
                          │ TLS 1.3 (HTTPS + WSS)
┌─────────────────────────▼───────────────────────────────────────┐
│                    SERVER (Honest-but-Curious)                   │
│                                                                   │
│  SEES:   ciphertext blobs, timestamps, sender/receiver IDs,     │
│          public keys, message sizes, online status              │
│  NEVER:  plaintext, private keys, session keys                  │
│                                                                   │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────────────────┐   │
│  │  Auth    │  │  Key Store  │  │  Offline Ciphertext Queue │   │
│  │  /v1/    │  │  (pub keys) │  │  (encrypted blobs only)   │   │
│  └──────────┘  └─────────────┘  └──────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              SQLite (aiosqlite, WAL mode)                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Trust Boundaries & Data Flow

### 2.1 What the Server Stores

| Data            | Stored                                                   | Server sees plaintext       |
| --------------- | -------------------------------------------------------- | --------------------------- |
| User identity   | username, argon2id hash, TOTP secret (AES-GCM encrypted) | username only               |
| Identity key    | Ed25519 public key                                       | yes (by design)             |
| Ephemeral key   | X25519 public key (first message only)                   | yes (by design)             |
| Messages        | AES-256-GCM ciphertext + nonce + counter                 | ciphertext blob only        |
| Associated data | sender_id, recipient_id, counter, ttl, timestamp         | yes — metadata exposed      |
| Friend graph    | user_id pairs                                            | yes — contact graph exposed |

**Metadata the HbC server learns (report §8):**

- Who talks to whom (contact graph)
- When messages are sent/received (timing)
- Approximate message sizes
- Whether a user is online (WebSocket connection state)
- Delivery timestamps

### 2.2 Key Data Flow — Sending a Message

```
Alice                          Server                         Bob
  │  1. GET /v1/keys/{bob}        │                             │
  │──────────────────────────────▶│                             │
  │◀── Ed25519_pub, X25519_pub ───│                             │
  │                               │                             │
  │  2. 2-DH session setup        │                             │
  │  DH1 = X25519(alice_dh_priv,  bob_dh_pub)                  │
  │  DH2 = X25519(alice_eph_priv, bob_dh_pub)                  │
  │  session_key = HKDF-SHA256(DH1||DH2, info=ids+conv_id)    │
  │                               │                             │
  │  3. AES-256-GCM encrypt       │                             │
  │  AD = sender||recv||conv_id||counter||ttl||sent_at         │
  │  ct = AES-256-GCM(session_key, nonce, plaintext, AD)      │
  │                               │                             │
  │  4. POST /v1/messages         │                             │
  │──────────────────────────────▶│                             │
  │                               │  5. store ciphertext        │
  │                               │     if Bob offline          │
  │                               │  6. WS push to Bob          │
  │                               │────────────────────────────▶│
  │                               │                             │
  │                               │  7. Bob derives session_key │
  │                               │  DH1=X25519(bob_dh_priv, alice_dh_pub)
  │                               │  DH2=X25519(bob_dh_priv, alice_eph_pub)
  │                               │  -> decrypt locally          │
```

---

## 3. Cryptographic Protocol Design

### 3.1 Primitive Choices

| Purpose               | Primitive     | Library                  | Why                                           |
| --------------------- | ------------- | ------------------------ | --------------------------------------------- |
| Identity keypair      | Ed25519       | `cryptography` (PyCA)    | Fast, small keys, standard                    |
| Session key agreement | X25519 ECDH   | `cryptography` (PyCA)    | DH over Curve25519, no small-subgroup attacks |
| Key derivation        | HKDF-SHA256   | `cryptography` (PyCA)    | RFC 5869 standard                             |
| Message encryption    | AES-256-GCM   | `cryptography` (PyCA)    | AEAD, hardware-accelerated                    |
| Password hashing      | Argon2id      | `argon2-cffi`            | PHC winner, memory-hard                       |
| OTP                   | TOTP RFC 6238 | `pyotp`                  | Standard TOTP, ±1 window                      |
| Random                | OS CSPRNG     | `os.urandom` / `secrets` | Never `random` module                         |

### 3.2 Session Establishment (2-DH)

**Registration — one-time key upload:**

```
1. Client generates:
   identity_keypair = Ed25519.generate()   # long-term, stored locally encrypted
   dh_keypair       = X25519.generate()    # long-term DH key

2. Client uploads to server:
   identity_pub  (Ed25519 public key)
   dh_pub        (X25519 public key)
   key_sig       = Ed25519.sign(identity_priv, identity_pub || dh_pub)
   -> proves ownership; server cannot swap keys silently
```

**Session initiation (Alice -> Bob, first message):**

```
1. Alice fetches bob_identity_pub + bob_dh_pub from server
2. Alice generates ephemeral keypair: alice_eph_priv, alice_eph_pub
3. Alice computes:
   DH1 = X25519(alice_dh_priv,  bob_dh_pub)    # static-static
   DH2 = X25519(alice_eph_priv, bob_dh_pub)    # ephemeral-static
   ikm = DH1 || DH2
   session_key = HKDF-SHA256(
     ikm  = ikm,
     salt = b"COMP3334-IM-v1",
     info = alice_id || bob_id || conversation_id,
     len  = 32
   )
4. First message envelope includes alice_eph_pub so Bob can recompute
```

**Bob receives and decrypts:**

```
DH1 = X25519(bob_dh_priv, alice_dh_pub)
DH2 = X25519(bob_dh_priv, alice_eph_pub)   # from envelope
session_key = HKDF-SHA256(same params)
-> decrypt; cache session_key locally
```

**Subsequent messages:** reuse cached session_key, counter increments, new random nonce per message.

### 3.3 Session State Machine

```
[NO_SESSION] ──── derive_session_key_as_initiator() ────▶ [INITIATING]
     │                                                           │
     │◀── derive_session_key_as_responder() ──────────── first message received
     │                                                           │
     └──────────────────────────────────────────────▶ [ESTABLISHED]
                                                            │
                                          KeyChangeWarning raised
                                                            │
                                                     [UNVERIFIED]
                                                            │
                                          user calls mark_verified()
                                                            │
                                                     [ESTABLISHED]
```

States:

- `NO_SESSION` — no shared secret; key exchange required before messaging
- `INITIATING` — Alice derived session key, first message in flight
- `ESTABLISHED` — both sides have session key; counter-based messaging active
- `UNVERIFIED` — key change detected; warn user; require explicit re-verification

### 3.4 Key Change Detection (R6)

On every received message with `eph_pub_b64` set (re-keying):

1. Fetch sender's current `identity_pub` from server
2. Compare with locally cached value in `IdentityKeyCache`
3. If different -> raise `KeyChangeWarning` -> show `⚠ key changed` banner
4. User views fingerprint in ⚙ settings and clicks "Mark as Verified ✓"

### 3.5 Fingerprint / Safety Number (R5)

```python
# SHA256(sorted(alice_pub, bob_pub)) — symmetric, same on both sides
# Display: first 40 hex chars in 5 groups of 8
# e.g.  3a8f1c2d  9b4e7a01  cc83f210  11de5509  ab002377
def compute_fingerprint(my_pub: bytes, their_pub: bytes) -> str:
    keys = sorted([my_pub, their_pub])
    digest = SHA256(keys[0] + keys[1])
    return " ".join(digest.hex()[i:i+8] for i in range(0, 40, 8))
```

---

## 4. Message Format Specification

### 4.1 Wire Format (JSON over HTTPS / WebSocket)

```json
{
  "id": "uuid-v4",
  "sender_id": "alice_user_id",
  "recipient_id": "bob_user_id",
  "conversation_id": "server-issued random conversation ID",
  "counter": 42,
  "nonce_b64": "base64(12 random bytes)",
  "ciphertext_b64": "base64(AES-256-GCM output)",
  "eph_pub_b64": "base64(X25519 pub) — first message only, else null",
  "ttl_seconds": 300,
  "sent_at": 1711900000,
  "delivery_status": "sent"
}
```

### 4.2 Associated Data (AD)

Bound to the ciphertext — tampering any field breaks the GCM tag:

```
AD = sender_id || "|" || recipient_id || "|" || conversation_id
     || "|" || counter (8 bytes big-endian)
     || "|" || ttl_seconds (4 bytes big-endian, or b"\x00\x00\x00\x00")
     || "|" || sent_at (8 bytes big-endian)
```

### 4.3 Timed Self-Destruct (R10–R12)

```
- ttl_seconds is in AD -> cannot be altered without breaking MAC
- Client: sweep expired messages from local SQLite on startup + every 30s in chat
- Server: TTL cleanup loop runs every 300s (configurable)
  DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at <= unixepoch()
- Server: hard retention cap — max_message_age_days (default 30)

Limitation: self-destruct cannot prevent screenshots or a malicious client.
It is a best-effort privacy feature, not a security guarantee.
```

---

## 5. Replay Protection Design

### 5.1 Client-side (ReplayProtector)

```
Per-conversation state (persisted in sessions.json):
  last_counter: int         # highest counter accepted from this sender
  seen_ids: Set[uuid]       # message IDs in sliding window

On receiving a message:
  1. Check message.id NOT in seen_ids -> reject if duplicate
  2. Check message.counter > (last_counter - window) -> reject if outside window
  3. Verify AES-GCM tag (counter is in AD) -> reject if tampered
  4. Accept -> update last_counter, add id to seen_ids
```

Out-of-order tolerance: window of 50 counters. Anything below `last_counter - 50` is rejected.

### 5.2 Server-side (service + DB)

```sql
UNIQUE(conversation_id, sender_id, counter)
```

The server enforces replay resistance in two steps:

1. `MessageService` checks the sender's last seen counter for the conversation
   and rejects stale or regressed counters.
2. The DB constraint rejects exact duplicate `(conversation_id, sender_id, counter)`
   tuples before they reach the client.

---

## 6. Security Requirements Mapping

| Req | Description                 | Implementation                                                          |
| --- | --------------------------- | ----------------------------------------------------------------------- |
| R1  | Registration                | `POST /v1/auth/register` — Argon2id hash, unique username, rate-limited |
| R2  | Login + OTP                 | `POST /v1/auth/login` — Argon2id verify + pyotp TOTP (±1 window)        |
| R3  | Logout                      | `POST /v1/auth/logout` — token revoked in DB immediately                |
| R4  | Per-device identity keypair | Ed25519 generated client-side; private key never leaves device          |
| R5  | Fingerprint UI              | SHA256(sorted pub keys), 5×8 hex groups in ⚙ settings screen            |
| R6  | Key change detection        | `IdentityKeyCache.check_and_update()` on every re-keying message        |
| R7  | Secure session              | 2-DH: X25519 static-static + ephemeral-static, HKDF-SHA256              |
| R8  | Message encryption          | AES-256-GCM, AD binds sender/receiver/counter/ttl/timestamp             |
| R9  | Replay protection           | Message ID dedup + monotonic counter + DB UNIQUE constraint             |
| R10 | TTL in AD                   | `ttl_seconds` in AD — tamper-evident; countdown shown in UI             |
| R11 | Client deletion             | Sweep on startup + 30s interval timer in chat screen                    |
| R12 | Server deletion             | TTL cleanup loop every 300s; hard cap at `max_message_age_days`         |
| R13 | Friend request              | `POST /v1/friends/request`                                              |
| R14 | Request lifecycle           | accept / decline / cancel endpoints                                     |
| R15 | Block / remove              | `PUT /v1/friends/{id}/block`, `DELETE /v1/friends/{id}`                 |
| R16 | Anti-spam                   | Non-friends cannot send messages (DB friendship check)                  |
| R17 | Delivery states             | `sent` -> `delivered`; `read` is reserved for future UX                 |
| R18 | Delivered semantics         | server-assisted ACK via `POST /v1/messages/ack`                         |
| R19 | Metadata disclosure         | Documented: server sees timing, sizes, contact graph                    |
| R20 | Offline queue               | Ciphertext stored in `messages` table; flushed on WS reconnect          |
| R21 | Retention                   | Deleted on TTL expiry or age cap; delivery alone does not delete ciphertext |
| R22 | Replay robustness           | Same counter/ID mechanism handles retransmission                        |
| R23 | Conversation list           | `GET /v1/conversations` ordered by `last_message_at DESC`               |
| R24 | Unread counters             | `unread_count_a` / `unread_count_b` columns per conversation            |
| R25 | Pagination                  | `GET /v1/messages?before={id}&limit=50` (cursor-based)                  |

---

## 7. Auth Token Design

**Decision: opaque bearer tokens (not JWT)**

```
On login:
  1. token = secrets.token_urlsafe(32)   # 256-bit CSPRNG
  2. Store SHA256(token) in sessions table — raw token never persisted
  3. Return token to client in LoginResponse.access_token
  4. Client sends: Authorization: Bearer <token>
     WebSocket auth: first-frame JSON auth after TLS handshake

On each request:
  1. Compute SHA256(received_token)
  2. SELECT WHERE token_hash = ? AND revoked = 0 AND expires_at > now()
  3. Found -> attach user_id to request context
  4. Not found -> 401 Unauthorized

On logout:
  SET revoked = 1 WHERE token_hash = ?   # immediate invalidation

Token lifetime: 900 seconds (15 minutes), configurable via TOKEN_EXPIRY_SECONDS.

Rationale over JWT:
  - Instant revocation (no expiry window problem)
  - SHA256 in DB — stolen DB dump cannot replay tokens
  - No RS256 key management needed
  - Server has DB anyway — stateless verification adds no benefit here
```

---

## 8. Secure Local Key Storage

```
File: ~/.comp3334im/<username>/keystore.json
{
  "argon2_salt_b64":         "<16 random bytes>",
  "identity_priv_nonce_b64": "<12 random bytes>",
  "identity_priv_ct_b64":    "<AES-GCM(storage_key, identity_priv)>",
  "dh_priv_nonce_b64":       "<12 random bytes>",
  "dh_priv_ct_b64":          "<AES-GCM(storage_key, dh_priv)>",
  "identity_pub_b64":        "<Ed25519 pub — not secret>",
  "dh_pub_b64":              "<X25519 pub — not secret>",
  "key_sig_b64":             "<Ed25519 sig — not secret>"
}

Key derivation:
  storage_key = Argon2id(
    password    = user_login_password,
    salt        = argon2_salt (stored in file),
    time_cost   = 3,
    memory      = 64 MiB,
    parallelism = 1,
    length      = 32
  )

File permissions: 600 (owner read/write only)

Session cache: ~/.comp3334im/<username>/sessions.json
  Same storage_key encrypts session keys, ratchet state, and ReplayProtector state.

Threat coverage:
  - Disk theft: cannot decrypt without user password
  - Server compromise: server never sees private keys
  - Memory: keys loaded only while app is running
```

---

## 9. TOTP Secret Encryption

```
On registration:
  1. totp_secret = pyotp.random_base32()
  2. enc_key = HKDF-SHA256(
       ikm  = TOTP_ENCRYPTION_KEY (env var, 32+ bytes),
       salt = b"COMP3334-totp-v1",
       info = user_id.encode(),
       len  = 32
     )
  3. nonce = os.urandom(12)
  4. ct = AES-256-GCM(enc_key, nonce, totp_secret.encode(), ad=user_id.encode())
  5. Store in DB: base64(nonce || ct)

On login TOTP verify:
  1. Load encrypted blob from DB
  2. Derive enc_key same way (user_id as info)
  3. Decrypt -> totp_secret
  4. pyotp.TOTP(totp_secret).verify(code, valid_window=1)

AD binding to user_id: decrypting with a different user_id fails authentication.
TOTP_ENCRYPTION_KEY must be stored in env — never in code or DB.
```

---

## 10. Input Validation & Logging Policy

**Validation (enforced by Pydantic before any DB write):**

| Field          | Rule                                              |
| -------------- | ------------------------------------------------- |
| username       | 3–32 chars, `[a-zA-Z0-9_-]` only                  |
| password       | 12–128 chars                                      |
| totp_code      | exactly 6 digits                                  |
| ciphertext_b64 | ≤ 88 KB (64 KB plaintext + GCM overhead + base64) |
| nonce_b64      | must decode to exactly 12 bytes                   |
| eph_pub_b64    | must decode to exactly 32 bytes (when present)    |
| counter        | non-negative integer                              |
| ttl_seconds    | 1–604800 (1s to 7 days) or null                   |
| UUID fields    | must match UUID v4 format                         |

**Logging policy (structlog):**

```
NEVER log: passwords, tokens, TOTP codes, plaintext, private keys, session keys
NEVER log: full ciphertext blobs (log message ID + size only)
DO log:    request method + path + status code + latency (no body)
DO log:    auth events (login success/fail, logout) with user_id + IP
DO log:    rate limit triggers with IP (no username on failed login)
Log level: INFO in production; DEBUG in dev (LOG_LEVEL env var)
```

**Rate limiting (DB-backed, per IP):**

| Endpoint               | Max attempts | Window |
| ---------------------- | ------------ | ------ |
| POST /v1/auth/login    | 5            | 300s   |
| POST /v1/auth/register | 3            | 3600s  |

Key is `"login:<ip>"` — not username, to avoid user enumeration.
Constant-time dummy Argon2id hash on unknown username to prevent timing attacks.

---

## 11. Stack & File Structure

**Stack:**

```
Server:  FastAPI + aiosqlite (SQLite WAL) + uvicorn
Client:  Textual TUI + httpx (async HTTP/WS)
Crypto:  cryptography (PyCA) + argon2-cffi + pyotp + qrcode
Deploy:  Docker Compose (one command) + uv for local dev
```

**File structure:**

```
project/
├── server/
│   ├── main.py                    # FastAPI app, routers, TTL cleanup loop, health/ready
│   ├── api/
│   │   ├── auth.py                # R1–R3: register, login, logout
│   │   ├── keys.py                # R4: public key upload/fetch + self-sig verify
│   │   ├── friends.py             # R13–R16: friend requests, accept/decline/cancel, block
│   │   ├── messages.py            # R8/R9/R17–R22: send, fetch, delivery ACK
│   │   └── conversations.py       # R23–R25: list, unread counters, mark-read
│   ├── core/
│   │   ├── config.py              # pydantic-settings, lru_cache, dev auto-keygen
│   │   ├── database.py            # aiosqlite, WAL+FK+secure_delete, startup schema init
│   │   └── security.py            # Argon2id, opaque tokens, TOTP encryption, rate limiting
│   ├── services/
│   │   ├── auth_service.py        # auth/session workflow logic
│   │   ├── conversation_service.py# unread counters and conversation metadata
│   │   └── message_service.py     # replay checks, persistence, ACK flow
│   ├── ws/
│   │   └── handler.py             # WebSocket connection manager, offline queue flush
├── client/
│   ├── main.py                    # CLI entrypoint (--server, --username flags)
│   ├── crypto/
│   │   ├── session.py             # Ed25519, X25519 2-DH, HKDF, AES-GCM, ReplayProtector
│   │   └── storage.py             # Argon2id-derived storage key, AES-GCM keystore/sessions
│   ├── api/
│   │   └── client.py              # httpx async HTTP client + WebSocket listener
│   ├── state/
│   │   └── store.py               # local SQLite message store, TTL sweep
│   └── ui/
│       ├── app.py                 # IMApp: wires all screens + crypto + WS events
│       └── screens/
│           ├── login.py
│           ├── register.py        # TOTP QR code display
│           ├── conversations.py   # conversation list + unread counters
│           ├── chat.py            # chat view, MessageItem, key change warning, TTL countdown
│           ├── friends.py         # friend request management
│           └── settings.py        # fingerprint display (R5), TTL config (R10)
├── shared/
│   └── protocol.py                # Pydantic wire models and protocol enums
├── tests/
│   ├── unit/
│   │   ├── test_crypto.py         # 22 tests: Ed25519, X25519, AES-GCM, replay, fingerprint
│   │   └── test_auth.py           # 10 tests: Argon2id, bearer tokens, TOTP encryption
│   ├── integration/
│   │   ├── test_e2e_message.py    # 2 tests: register->login->friend->send->decrypt + replay
│   │   └── test_offline_queue.py  # 2 tests: offline queue store-and-forward + replay
│   ├── security/
│   │   └── test_replay_attack.py  # 13 tests: replay, ciphertext tampering, session key
│   └── ui/
│       └── test_textual.py        # TUI smoke test
├── scripts/
│   ├── bootstrap-env.sh           # auto-generates .env.local + TLS cert
│   ├── docker-entrypoint.sh       # auto-generates self-signed TLS cert at container start
│   ├── test-deploy.sh             # local deploy smoke test (mirrors CI)
│   └── seed.py                    # seeds alice/bob/charlie/dave with friendships + messages
├── .github/workflows/ci.yml       # CI: unit+integration tests + Docker build + health check
├── docker-compose.yml
├── Dockerfile.server              # multi-stage, non-root, python:3.12.9-slim
├── .env.example
├── pyproject.toml                 # uv managed, Python 3.12+
└── docs/
    ├── ARCHITECTURE.md            # this file
    ├── TASKS.md
    └── DEPLOY.md
```

---

## 12. Database Schema

The authoritative schema is the SQLAlchemy ORM model set under `server/models/`
and the startup initialization path in `server/core/database.py`.

For submission and deployment purposes, a fresh local database is created by the
application at startup rather than by applying `server/migrations/001_init.sql`.

**Key design decisions:**

- `users.id` is `lower(hex(randomblob(16)))` — 128-bit random, not sequential (no enumeration)
- `PRAGMA secure_delete = ON` — zero-fills deleted pages (relevant for TTL data)
- `PRAGMA journal_mode = WAL` — concurrent reads while server writes
- `PRAGMA foreign_keys = ON` — referential integrity enforced at DB level
- `expires_at` is a virtual generated column — no application logic needed for TTL queries

---

## 13. CI / Deploy

### 13.1 GitHub Actions (`.github/workflows/ci.yml`)

Two jobs run on every push/PR to `main`:

| Job            | What it does                                                                                          |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `test`         | `uv sync --extra dev` -> `pytest -v --tb=short`                                                        |
| `docker-build` | bootstrap env -> `docker compose up --build -d` -> wait for healthcheck -> `curl /health` -> teardown |

### 13.2 Local Deploy Test (`scripts/test-deploy.sh`)

Mirrors CI locally. Checks prerequisites, runs tests, builds Docker image, waits for health, smoke-tests `/health`, tears down.

```bash
./scripts/test-deploy.sh           # full test including Docker
./scripts/test-deploy.sh --no-docker   # tests only, skip Docker
```

### 13.3 Server Config (env vars)

| Variable               | Required | Default               | Notes                                        |
| ---------------------- | -------- | --------------------- | -------------------------------------------- |
| `TOKEN_SECRET_KEY`     | prod     | auto-generated in dev | 64 hex chars (32 bytes)                      |
| `TOTP_ENCRYPTION_KEY`  | prod     | auto-generated in dev | 64 hex chars (32 bytes)                      |
| `APP_ENV`              | no       | `development`         | `production` disables docs, enforces secrets |
| `PORT`                 | no       | `8443`                |                                              |
| `TOKEN_EXPIRY_SECONDS` | no       | `900`                 | 15 minutes                                   |
| `MAX_MESSAGE_AGE_DAYS` | no       | `30`                  | Hard retention cap                           |
| `TLS_CERT_FILE`        | no       | `./certs/server.crt`  | Auto-generated by bootstrap script           |
| `TLS_KEY_FILE`         | no       | `./certs/server.key`  | Auto-generated by bootstrap script           |

### 13.4 Docker

- Base image: `python:3.12.9-slim` (pinned — no `latest`)
- Multi-stage build: `deps` stage resolves packages, `runtime` stage copies only the venv
- Non-root user: `appuser:appgroup`
- TLS cert auto-generated at container startup via `docker-entrypoint.sh` if not mounted
- SQLite data persisted via named volume `server_data:/app/data`
- Health check: HTTPS GET `https://localhost:8443/health` with SSL verify disabled (self-signed)

---

_Threat model: HbC server + network attacker + malicious users_
_Crypto: X25519 + HKDF-SHA256 + AES-256-GCM + Ed25519 + Argon2id + TOTP_
_All primitives from well-reviewed libraries — no custom crypto_
_Last updated: 2026-03-13_

---

## 14. Security Limitations & Trade-offs

This section documents known limitations. These are intentional trade-offs made for implementation simplicity within the project scope, not oversights.

### 14.1 No Full Signal-Style Double Ratchet

The design uses 2-DH to establish a root key, then a symmetric ratchet to derive
fresh per-message keys. This gives per-message forward secrecy for message keys,
but it does not provide the full recovery and post-compromise properties of a
Signal-style Double Ratchet with DH-ratchet turns.

**Mitigation in a production system:** Implement a full Double Ratchet with DH
ratchet steps and stronger post-compromise recovery semantics.

### 14.2 Static DH Key Reuse Across Conversations

The X25519 DH keypair is long-term and used as the static-static component (DH1) in every conversation. If this key is compromised, the DH1 component is broken for all conversations. The ephemeral-static component (DH2) still provides per-session uniqueness, but the blast radius of a static key compromise is all conversations.

**Mitigation in a production system:** Use per-conversation one-time pre-keys (as in Signal's X3DH) so that compromising one pre-key affects only one conversation.

### 14.3 Metadata Exposure to Honest-but-Curious Server

The server learns:

- **Contact graph:** who talks to whom (friendship table + message routing)
- **Timing:** when messages are sent, delivered, and acknowledged
- **Message sizes:** approximate plaintext length (ciphertext size minus GCM overhead)
- **Online status:** WebSocket connection state
- **Delivery ACKs:** which specific messages were successfully decrypted (ACK is plaintext; see `DeliveryAck` docstring in `shared/protocol.py` for rationale)
- **Conversation IDs:** random server-assigned identifiers. The server still
  learns the contact graph through friendship rows and message routing metadata.

**Mitigation in a production system:** Metadata-resistant messaging requires techniques like sealed sender (Signal), onion routing, or private information retrieval — all far beyond the scope of this project.

### 14.4 No Key Rotation Mechanism

There is no way to rotate the long-term identity key (Ed25519) or DH key (X25519) without re-registering. If a key is suspected compromised, the user must create a new account.

**Mitigation in a production system:** Implement key rotation with a signed key update message that chains the old identity to the new one, allowing contacts to verify the transition.

### 14.5 Self-Destruct Limitations

The TTL-based self-destruct (R10–R12) is best-effort:

- Cannot prevent screenshots or screen recording
- A malicious client can ignore TTL and retain messages indefinitely
- Server-side deletion runs on a configurable interval (default 300s), so messages may persist briefly past expiry
- Client-side deletion depends on the app running; messages on disk persist until next launch

### 14.6 Delivery ACK Trade-off

The system uses a server-assisted acknowledgement path (assignment Option A).
When the recipient client acknowledges delivery, the server learns that delivery
occurred and when it occurred. This is simpler to implement and easier to demo,
but exposes delivery timing metadata and provides weaker authenticity semantics
than an end-to-end protected acknowledgement.

**Mitigation in a production system:** Implement Option B, where delivery ACKs
are bound to the session and protected end-to-end.
