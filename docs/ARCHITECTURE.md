# COMP3334 Secure IM — Architecture & Protocol Design
> Stack: Python (FastAPI + Textual + SQLite). Decided.
> 5-person team | Deadline: 2 April 2026

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Trust Boundaries & Data Flow](#2-trust-boundaries--data-flow)
3. [Cryptographic Protocol Design](#3-cryptographic-protocol-design)
4. [Message Format Specification](#4-message-format-specification)
5. [Replay Protection Design](#5-replay-protection-design)
6. [Security Requirements Mapping](#6-security-requirements-mapping)
7. [Stack Decision Matrix](#7-stack-decision-matrix)
8. [File Structure (per stack)](#8-file-structure-per-stack)
9. [Team Task Split](#9-team-task-split)
10. [Timeline (3 weeks)](#10-timeline-3-weeks)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT A                                  │
│  ┌──────────┐   ┌───────────────┐   ┌─────────────────────┐    │
│  │  UI/TUI  │──▶│  Session Mgr  │──▶│  Crypto Engine      │    │
│  └──────────┘   └───────────────┘   │  X25519 ECDH        │    │
│                                      │  AES-256-GCM        │    │
│  ┌──────────────────────────────┐   │  Ed25519            │    │
│  │  Local Encrypted Storage     │   │  HKDF-SHA256        │    │
│  │  (identity key, sessions,    │   │  Argon2id           │    │
│  │   message history, contacts) │   └─────────────────────┘    │
│  └──────────────────────────────┘                               │
└─────────────────────────┬───────────────────────────────────────┘
                          │ TLS 1.3
                          │ (WebSocket + HTTPS)
┌─────────────────────────▼───────────────────────────────────────┐
│                    SERVER (Honest-but-Curious)                   │
│                                                                   │
│  SEES:   ciphertext blobs, timestamps, sender/receiver IDs,     │
│          public keys, message sizes, online status              │
│  NEVER:  plaintext, private keys, session keys                  │
│                                                                   │
│  ┌──────────┐  ┌─────────────┐  ┌──────────────────────────┐   │
│  │  Auth    │  │  Key Store  │  │  Offline Ciphertext Queue │   │
│  │  API     │  │  (pub keys  │  │  (encrypted blobs only)   │   │
│  │  /v1/    │  │   only)     │  │                           │   │
│  └──────────┘  └─────────────┘  └──────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   SQLite / PostgreSQL                     │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Trust Boundaries & Data Flow

### 2.1 What the Server Stores (and why it's safe)

| Data | Server stores | Server sees in plaintext |
|------|--------------|--------------------------|
| User identity | username, argon2id hash, TOTP secret (encrypted) | username only |
| Identity key | Ed25519 **public** key | yes (by design) |
| Ephemeral key | X25519 **public** key (per session init) | yes (by design) |
| Messages | AES-256-GCM ciphertext + nonce + counter | ciphertext blob only |
| Associated data | sender_id, recipient_id, counter, ttl, timestamp | yes — metadata exposed |
| Friend graph | user_id pairs | yes — contact graph exposed |

**Metadata the HbC server learns (document in report §8):**
- Who talks to whom (contact graph)
- When messages are sent/received (timing)
- Approximate message sizes (after padding — consider padding to fixed sizes)
- Whether a user is online (via WebSocket connection state)
- Delivery timestamps

### 2.2 Key Data Flow — Sending a Message

```
Alice                          Server                         Bob
  │                               │                             │
  │  1. fetch Bob's pub key       │                             │
  │──────────────────────────────▶│                             │
  │◀── Ed25519_pub, X25519_pub ───│                             │
  │                               │                             │
  │  2. ECDH session setup        │                             │
  │  DH1 = X25519(Alice_id_priv, Bob_id_pub)                   │
  │  DH2 = X25519(Alice_eph_priv, Bob_id_pub)                  │
  │  session_key = HKDF(DH1 || DH2)                            │
  │                               │                             │
  │  3. encrypt message           │                             │
  │  nonce = random 12 bytes      │                             │
  │  AD = sender||recv||conv_id||counter||ttl||ts              │
  │  ct = AES-256-GCM(session_key, nonce, plaintext, AD)      │
  │                               │                             │
  │  4. POST /v1/messages         │                             │
  │──────────────────────────────▶│                             │
  │                               │  5. store ciphertext        │
  │                               │     if Bob offline          │
  │                               │                             │
  │                               │  6. WS push to Bob          │
  │                               │─────────────────────────────▶│
  │                               │                             │
  │                               │  7. Bob decrypts locally    │
  │                               │  session_key = HKDF(        │
  │                               │    X25519(Bob_id_priv, Alice_id_pub) ||  │
  │                               │    X25519(Bob_id_priv, Alice_eph_pub))  │
```

---

## 3. Cryptographic Protocol Design

### 3.1 Primitive Choices

| Purpose | Primitive | Library (Python) | Library (Go) | Why |
|---------|-----------|-----------------|--------------|-----|
| Identity keypair | Ed25519 | `cryptography` (PyCA) | `crypto/ed25519` | Fast, small keys, standard |
| Session key agreement | X25519 ECDH | `cryptography` (PyCA) | `golang.org/x/crypto/curve25519` | DH over Curve25519, no small-subgroup |
| Key derivation | HKDF-SHA256 | `cryptography` (PyCA) | `golang.org/x/crypto/hkdf` | Standard RFC 5869 |
| Message encryption | AES-256-GCM | `cryptography` (PyCA) | `crypto/aes` + `crypto/cipher` | AEAD, hardware-accelerated |
| Password hashing | Argon2id | `argon2-cffi` | `golang.org/x/crypto/argon2` | Winner of PHC, memory-hard |
| OTP | TOTP RFC 6238 | `pyotp` | `pquerna/otp` | Standard TOTP |
| Random | OS CSPRNG | `os.urandom` / `secrets` | `crypto/rand` | Never `random` module |

### 3.2 Session Establishment Protocol

This is a simplified 2-DH protocol (not full Signal X3DH, but sufficient for HbC server).

**One-time setup (at registration):**
```
1. Client generates:
   - identity_keypair  = Ed25519.generate()          # long-term, stored locally
   - dh_keypair        = X25519.generate()            # long-term DH key

2. Client uploads to server:
   - identity_pub  (Ed25519 public key)
   - dh_pub        (X25519 public key)
   - Signature over (identity_pub || dh_pub) using identity_priv
     → proves ownership, server cannot swap keys silently
```

**Session initiation (Alice → Bob, first message):**
```
1. Alice fetches from server:
   - bob_identity_pub  (Ed25519)
   - bob_dh_pub        (X25519)
   - server_signature_of_bobs_keys   ← server signs bundle at upload time

2. Alice verifies server signature (prevents key substitution by server)
   NOTE: server is HbC so it won't do this, but design is robust anyway.
   In practice: Alice computes fingerprint and user can verify out-of-band.

3. Alice generates ephemeral keypair:
   - alice_eph_priv, alice_eph_pub = X25519.generate()

4. Alice computes shared secret:
   DH1 = X25519(alice_dh_priv,  bob_dh_pub)       # static-static
   DH2 = X25519(alice_eph_priv, bob_dh_pub)        # ephemeral-static

   ikm = DH1 || DH2
   session_key = HKDF-SHA256(
     ikm    = ikm,
     salt   = "COMP3334-IM-v1",
     info   = alice_id || bob_id || conversation_id,
     length = 32
   )

5. Alice sends first message envelope containing:
   - alice_eph_pub    (so Bob can recompute session_key)
   - ciphertext
   - nonce
   - counter = 0
   - alice_identity_pub (for Bob to fingerprint)
```

**Bob receives and decrypts:**
```
1. Bob retrieves alice_eph_pub from message envelope
2. Bob computes:
   DH1 = X25519(bob_dh_priv, alice_dh_pub)         # fetched from server or cached
   DH2 = X25519(bob_dh_priv, alice_eph_pub)

   session_key = HKDF-SHA256(same params as Alice)

3. Bob decrypts with session_key
4. Bob caches session_key locally for subsequent messages
```

**Subsequent messages (same conversation):**
```
- Reuse session_key (already cached locally)
- No need to re-send alice_eph_pub
- Counter increments monotonically
- Nonce = random 12 bytes (AES-GCM nonces must NEVER repeat per key)
```

### 3.3 Key Change Detection (R6)

```
On every received message:
  1. Extract sender_identity_pub from message (only in first message per session)
  2. Compare with locally cached identity_pub for that sender
  3. If different:
     → Show warning: "⚠️  [username]'s identity key has changed."
     → Mark conversation as UNVERIFIED
     → Policy: WARN + require explicit re-verification (do not silently accept)
  4. User can view fingerprint and mark as verified
```

### 3.4 Fingerprint / Safety Number (R5)

```python
# Fingerprint = first 40 hex chars of SHA256(alice_identity_pub || bob_identity_pub)
# Display in groups of 8 for readability:
# e.g.  3a8f1c2d  9b4e7a01  cc83f210  11de5509  ab002377

def compute_fingerprint(my_pub: bytes, their_pub: bytes) -> str:
    # Sort to make fingerprint symmetric (same for both sides)
    keys = sorted([my_pub, their_pub])
    digest = SHA256(keys[0] + keys[1])
    return " ".join(digest.hex()[i:i+8] for i in range(0, 40, 8))
```

---

## 4. Message Format Specification

### 4.1 Wire Format (JSON over WebSocket / HTTP)

```json
{
  "id":              "uuid-v4",
  "type":            "message",
  "sender_id":       "alice",
  "recipient_id":    "bob",
  "conversation_id": "sha256(sorted(alice_id, bob_id))[:16]",
  "counter":         42,
  "nonce_b64":       "base64(12 random bytes)",
  "ciphertext_b64":  "base64(AES-256-GCM output)",
  "eph_pub_b64":     "base64(X25519 pub, first message only, else null)",
  "ttl_seconds":     300,
  "sent_at":         1711900000,
  "delivery_status": "sent"
}
```

### 4.2 Associated Data (AD) — bound to ciphertext, prevents tampering

```
AD = sender_id || "|" || recipient_id || "|" || conversation_id
     || "|" || counter_as_8_bytes_big_endian
     || "|" || ttl_as_4_bytes_big_endian
     || "|" || sent_at_as_8_bytes_big_endian
```

Tampering with sender, recipient, counter, TTL, or timestamp invalidates the GCM tag → detected immediately.

### 4.3 Timed Self-Destruct (R10–R12)

```
TTL enforcement:
  - ttl_seconds included in AD (cannot be altered without breaking MAC)
  - Client: on receive, schedule deletion at (received_at + ttl_seconds)
  - Client: on startup, sweep expired messages from local DB
  - Server: cron job / DB trigger to delete ciphertext after (stored_at + ttl_seconds)
  - Server: returns TTL in delivery envelope so client knows server's expiry intent

Limitation (must state in report):
  Self-destruct cannot prevent screenshots or a malicious client.
  It is a best-effort privacy feature, not a security guarantee.
```

---

## 5. Replay Protection Design

### 5.1 Problem

An attacker (or network) replays a captured ciphertext. The server re-delivers it.
Must detect and reject.

### 5.2 Solution — Counter + Nonce Tracking

```
Per-conversation state (stored locally by each client):
  last_seen_counter: int   # highest counter accepted from this sender
  seen_ids: Set[uuid]      # message IDs seen in last N messages (sliding window)

On receiving message:
  1. Check message.id NOT in seen_ids → reject if duplicate
  2. Check message.counter > last_seen_counter → reject if counter ≤ last seen
  3. Verify AES-GCM tag (includes counter in AD) → reject if tag invalid
  4. If all pass: accept, update last_seen_counter, add id to seen_ids

Server-side (additional layer):
  - Server tracks (conversation_id, counter) pairs
  - Rejects if same counter for same conversation submitted twice
  - Prevents replay even before reaching client
```

### 5.3 Out-of-order tolerance

```
Allow counter gap up to 50 (messages may arrive out of order over network).
Maintain a bitmap of seen counters in [last_seen - 50, last_seen + ∞].
Reject anything below (last_seen - 50) — outside replay window.
```

---

## 3.5 Session State Machine (required for report §6)

```
Client session states per conversation:

  ┌─────────────────────────────────────────────────────────────────┐
  │                    SESSION STATE MACHINE                         │
  └─────────────────────────────────────────────────────────────────┘

  [NO_SESSION] ──── fetch peer keys + derive_session_key_as_initiator()
       │                                                    │
       │                                              [INITIATING]
       │                                                    │
       │                                    send first message (eph_pub_b64 set)
       │                                                    │
       │◀──── receive first message + derive_session_key_as_responder() ────┐
       │                                                    │               │
       │                                             [ESTABLISHED]          │
       │                                                    │               │
       │                                    ┌───────────────┴──────────┐    │
       │                                    │                          │    │
       │                             send/receive msgs          key change?  │
       │                             (counter++)                      │    │
       │                                    │                  [UNVERIFIED]  │
       │                                    │                          │    │
       │                                    │              user re-verifies  │
       │                                    │              fingerprint       │
       │                                    │                          │    │
       │                                    └──────────────────────────┘    │
       │                                                                     │
       └─────────────────────────────────────────────────────────────────────┘

  States:
    NO_SESSION   — no shared secret exists; must do key exchange before messaging
    INITIATING   — Alice has derived session key, first message in flight
    ESTABLISHED  — both sides have session key; counter-based messaging active
    UNVERIFIED   — key change detected; warn user; block or allow with warning

  Transitions:
    NO_SESSION  → INITIATING   : initiator calls derive_session_key_as_initiator()
    INITIATING  → ESTABLISHED  : first message delivered (eph_pub_b64 received by Bob)
    NO_SESSION  → ESTABLISHED  : responder calls derive_session_key_as_responder()
    ESTABLISHED → UNVERIFIED   : KeyChangeWarning raised in IdentityKeyCache.check_and_update()
    UNVERIFIED  → ESTABLISHED  : user calls mark_verified() after out-of-band check
```

---

## 6. Security Requirements Mapping

| Req | Description | Implementation |
|-----|-------------|----------------|
| R1 | Registration | POST /v1/auth/register, argon2id, unique username |
| R2 | Login + OTP | POST /v1/auth/login, argon2id verify, pyotp TOTP check |
| R3 | Logout | DELETE /v1/auth/session, token blacklist |
| R4 | Per-device identity keypair | Ed25519 generated client-side, private key never leaves device |
| R5 | Fingerprint UI | SHA256(sorted pub keys), displayed in 5×8 hex groups |
| R6 | Key change detection | Compare cached pub key on every session init, warn if different |
| R7 | Secure session | 2-DH with X25519 + HKDF-SHA256 |
| R8 | Message encryption | AES-256-GCM with AD binding sender/receiver/counter/ttl |
| R9 | Replay protection | Message ID dedup + monotonic counter check |
| R10 | TTL in AD | ttl_seconds in AD → tamper-evident |
| R11 | Client deletion | Schedule deletion on receive; sweep on startup |
| R12 | Server deletion | Cron delete WHERE stored_at + ttl < NOW() |
| R13 | Friend request workflow | POST /v1/friends/request, GET /v1/friends/pending |
| R14 | Request lifecycle | accept/decline/cancel endpoints |
| R15 | Block/remove | PUT /v1/friends/{id}/block, DELETE /v1/friends/{id} |
| R16 | Anti-spam | Non-friends cannot send messages (DB-enforced) |
| R17 | Delivery states | sent / delivered / read (optional) |
| R18 | Delivered semantics | Option B: E2EE-protected ACK from recipient client |
| R19 | Metadata disclosure | Document: server sees timing, sizes, contact graph |
| R20 | Offline queue | Store ciphertext in messages table WHERE delivered=false |
| R21 | Retention | Delete after delivery OR after max_age (whichever first) |
| R22 | Replay robustness | Same counter/ID mechanism handles retries too |
| R23 | Conversation list | GET /v1/conversations, ordered by last_message_at DESC |
| R24 | Unread counters | unread_count column per (user, conversation) |
| R25 | Pagination | GET /v1/messages?before={id}&limit=50 (cursor-based) |

---

## 6.1 Auth Token Design

**Decision: opaque bearer tokens (not JWT)**

```
On login:
  1. Server generates token = secrets.token_urlsafe(32)   # 256-bit CSPRNG
  2. Server stores token_hash = SHA256(token) in sessions table
  3. Server returns token to client in LoginResponse.access_token
  4. Client sends token in every request: Authorization: Bearer <token>

On each request:
  1. Server computes SHA256(received_token)
  2. Looks up sessions WHERE token_hash = ? AND revoked = 0 AND expires_at > now()
  3. If found: attach user_id to request context
  4. If not found: 401 Unauthorized

On logout:
  1. Server sets sessions.revoked = 1 WHERE token_hash = ?
  2. Token is immediately invalid

Rationale for opaque tokens over JWT:
  - No need for stateless verification (server has DB anyway)
  - Instant revocation (no JWT expiry window problem)
  - Simpler implementation — no RS256 key management
  - SHA256 hash in DB means stolen DB dump cannot replay tokens
```

---

## 6.2 Secure Local Key Storage Design (R4, PDF §7)

```
Problem: private keys must survive app restarts but never be stored in plaintext.

Solution: Argon2id-derived storage key + AES-256-GCM encryption

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
    password   = user_login_password,
    salt       = argon2_salt (stored in file),
    time_cost  = 3,
    memory     = 64 MiB,
    parallelism = 1,
    length     = 32
  )

File permissions: chmod 600 (owner read/write only)

Session cache: ~/.comp3334im/<username>/sessions.json
  Same storage_key encrypts session keys and replay state.
  See client/crypto/storage.py for full implementation.

Threat coverage:
  - Disk theft / OS-level attacker: cannot decrypt without user password
  - Server compromise: server never sees private keys
  - Memory: keys are loaded only while app is running; zeroed on exit (best-effort)
```

---

## 6.3 TOTP Secret Encryption (server-side)

```
Problem: TOTP secrets stored in DB must be protected if DB is leaked.

Solution: AES-256-GCM with a server-side key derived from SERVER_SECRET env var.

On registration:
  1. Server generates totp_secret = pyotp.random_base32()
  2. Derives encryption key:
       enc_key = HKDF-SHA256(
         ikm  = SERVER_SECRET (from env, min 32 bytes),
         salt = "COMP3334-totp-v1",
         info = user_id,
         len  = 32
       )
  3. nonce = os.urandom(12)
  4. ct = AES-256-GCM(enc_key, nonce, totp_secret.encode(), ad=user_id.encode())
  5. Stores in DB: base64(nonce || ct)

On login TOTP verify:
  1. Load encrypted blob from DB
  2. Derive enc_key same way (using user_id as info)
  3. Decrypt → totp_secret
  4. pyotp.TOTP(totp_secret).verify(submitted_code)

SERVER_SECRET must be:
  - At least 32 bytes of random data
  - Stored in environment variable (never in code or DB)
  - Rotated requires re-encrypting all TOTP secrets (migration needed)
```

---

## 6.4 Input Validation & Logging Policy (PDF §7)

```
Input validation (server-side, enforced before any DB write):
  - username:       3–32 chars, [a-zA-Z0-9_-] only (Pydantic regex)
  - password:       12–128 chars
  - totp_code:      exactly 6 digits
  - message size:   ciphertext_b64 ≤ 88 KB (64 KB plaintext + GCM overhead + base64)
  - nonce_b64:      must decode to exactly 12 bytes
  - eph_pub_b64:    must decode to exactly 32 bytes (when present)
  - counter:        non-negative integer
  - ttl_seconds:    1–604800 (1 second to 7 days) or null
  - UUID fields:    must match UUID v4 regex
  - Reject any field that fails validation with 422 before touching DB

Logging policy (PDF §7 — minimal sensitive logging):
  - NEVER log: passwords, tokens, TOTP codes, plaintext, private keys, session keys
  - NEVER log: full ciphertext blobs (log message ID + size only)
  - DO log:    request method + path + status code + latency (no body)
  - DO log:    auth events (login success/fail, logout) with user_id + IP
  - DO log:    rate limit triggers with IP (no username on failed login)
  - Log level: INFO in production; DEBUG only in dev (controlled by LOG_LEVEL env var)
  - Library:   structlog (already in dependencies)
```

---

## 7. Stack Decision

**Decided: Python**

```
Server:  FastAPI + WebSockets + SQLite (aiosqlite)
Client:  Textual TUI
Crypto:  cryptography (PyCA) + argon2-cffi + pyotp + qrcode
Deploy:  Docker Compose (one command) + uv for local dev
Package: uv
```

Single language across the full team. PyCA is the gold standard Python crypto library.
Docker Compose → clean one-command deploy on Windows 11 and Ubuntu.

---

## 8. File Structure (actual)

```
project/
├── server/
│   ├── main.py                    # FastAPI app, routers, TTL cleanup loop
│   ├── api/
│   │   ├── auth.py                # R1, R2, R3 — register, login, logout
│   │   ├── keys.py                # R4 — public key upload/fetch
│   │   ├── friends.py             # R13–R16 — friend requests, block, remove
│   │   ├── messages.py            # R8/R9/R17–R22 — send, fetch, delivery ACK
│   │   └── conversations.py       # R23–R25 — list, unread, mark-read
│   ├── core/
│   │   ├── database.py            # aiosqlite, migration runner
│   │   ├── security.py            # argon2id, opaque tokens, TOTP encryption, rate limiting
│   │   └── config.py              # pydantic-settings env config
│   ├── ws/
│   │   └── handler.py             # WebSocket connection manager, offline queue flush
│   └── migrations/
│       └── 001_init.sql
├── client/
│   ├── main.py                    # CLI entrypoint
│   ├── crypto/
│   │   ├── session.py             # Ed25519, X25519 2-DH, HKDF-SHA256, AES-256-GCM,
│   │   │                          # replay protection, key change detection
│   │   └── storage.py             # Argon2id-derived storage key, AES-GCM keystore
│   ├── api/
│   │   └── client.py              # httpx async HTTP client + WebSocket listener
│   ├── state/
│   │   └── store.py               # local SQLite message store, TTL sweep
│   └── ui/
│       ├── app.py                 # Textual TUI App, wires screens + crypto + WS
│       ├── screens/
│       │   ├── login.py
│       │   ├── register.py        # includes TOTP QR code display
│       │   ├── conversations.py   # conversation list + unread counters
│       │   ├── chat.py            # chat view, key change warning, TTL display
│       │   ├── friends.py         # friend request management
│       │   └── settings.py        # fingerprint display (R5), TTL config (R10)
│       └── widgets/
├── shared/
│   └── protocol.py                # Pydantic wire models, make_conversation_id(), enums
├── tests/
│   ├── unit/
│   │   ├── test_crypto.py         # Ed25519, X25519, AES-GCM, replay, fingerprint (22 tests)
│   │   └── test_auth.py           # argon2id, bearer tokens, TOTP encryption (10 tests)
│   ├── integration/
│   │   ├── test_e2e_message.py    # register→login→friend→send→decrypt + replay (2 tests)
│   │   └── test_offline_queue.py  # offline queue store-and-forward + replay (2 tests)
│   ├── security/
│   │   └── test_replay_attack.py  # replay, tampering, session key derivation (13 tests)
│   └── ui/
│       └── test_textual.py        # Textual TUI smoke test
├── scripts/
│   └── seed.py                    # seed data helper
├── docs/
│   ├── ARCHITECTURE.md            # this file
│   ├── TASKS.md                   # task breakdown + status
│   └── DEPLOY.md                  # step-by-step deploy guide
├── docker-compose.yml
├── Dockerfile.server
├── .env.example
├── pyproject.toml                 # uv managed
└── README.md
```

---

## 9. Team Task Split

**Split for 5 people:**

```
Person 1 — Server Auth & Key Infrastructure
  Owns: server/api/auth.py, server/api/keys.py, server/core/security.py
  Covers: R1, R2, R3, R4
  Deliverables:
    - POST /v1/auth/register (argon2id, uniqueness check, rate limit)
    - POST /v1/auth/login (argon2id + TOTP verify, issue opaque bearer token)
    - POST /v1/auth/logout (token revocation)
    - POST /v1/keys/upload (store Ed25519 + X25519 pub key)
    - GET  /v1/keys/{user_id} (fetch pub keys for session init)
    - DB schema: users, sessions, public_keys tables
    - Rate limiting middleware

Person 2 — Server Messaging & WebSocket
  Owns: server/api/messages.py, server/api/conversations.py, server/ws/handler.py
  Covers: R13–R25
  Deliverables:
    - WebSocket connection manager (online/offline tracking)
    - POST /v1/messages (receive ciphertext, store, push or queue)
    - GET  /v1/messages (paginated fetch, cursor-based)
    - POST /v1/friends/request, GET /v1/friends/pending, PUT /accept/decline
    - GET  /v1/conversations (ordered by last_message_at, unread counts)
    - POST /v1/conversations/{id}/read (mark-read, reset unread counter)
    - Delivery ACK handling
    - TTL cleanup cron job
    - DB schema: messages, conversations, friend_requests, blocks tables

Person 3 — Client Crypto Engine
  Owns: client/crypto/, client/state/, shared/protocol.py
  Covers: R4, R5, R6, R7, R8, R9
  Deliverables:
    - Ed25519 keypair generation + secure local storage
    - X25519 session establishment (2-DH + HKDF)
    - AES-256-GCM encrypt/decrypt with AD binding
    - Key change detection + warning logic
    - Fingerprint computation + display
    - Replay protection (counter tracking, seen_ids)
    - Encrypted local DB for sessions + message history

Person 4 — Client UI & Integration
  Owns: client/ui/, client/api/client.py, client/main.py
  Covers: R5, R10, R11, R13–R25 (client side)
  Deliverables:
    - httpx async API client (auth, keys, messages, friends)
    - WebSocket listener + message dispatch
    - TUI screens: login, register, conversation list, chat view, friends, settings
    - Unread counter display
    - TTL countdown + auto-delete from UI
    - Key change warning display
    - Friend request UI (send, pending, accept/decline)
    - TOTP QR code display on registration

Person 5 — Testing, Deployment & Report
  Owns: tests/, DEPLOY.md, docs/
  Covers: §8 (testing), §9 (deployment)
  Deliverables:
    - Integration + security test suite maintenance
    - DEPLOY.md — clean install on Windows 11 + Ubuntu
    - Report §10 (Deployment & Setup Guide)
    - Presentation video coordination
```

**Cross-team integration points (must agree on interface early):**
```
P1 ↔ P3:  Bearer token format (opaque), /v1/keys API response schema
P2 ↔ P3:  Message wire format (see §4.1), WebSocket push schema
P2 ↔ P4:  Conversation list response schema, pagination cursor format
P3 ↔ P4:  crypto module API — encrypt(session_key, plaintext, ad) → envelope
```

---

## 10. Timeline (3 Weeks)

```
Week 1 (Mar 10–16) — Foundation ✅ DONE
  All:  Stack agreed (Python). Repo, Docker, shared protocol.py set up.
  P1:   DB schema done. Register + login + TOTP working.
  P2:   WebSocket hub. Message POST/GET. Friend request endpoints.
  P3:   Ed25519 + X25519 keygen. AES-GCM encrypt/decrypt unit tested.
  P4:   httpx client. Login + register screens working.
  P5:   Test scaffolding. Unit + integration tests passing.

Week 2 (Mar 17–23) — Core Features (current)
  P1:   Rate limiting. Token revocation. [DONE]
  P2:   Offline queue. Delivery ACK. TTL cleanup cron. [DONE]
        mark-read endpoint (POST /v1/conversations/{id}/read). [TODO]
  P3:   Full session establishment. Key change detection.
        Replay protection. Encrypted local storage. [DONE]
  P4:   Chat screen. Friend request UI. Conversation list + unread. [DONE]
        TTL countdown in UI. TOTP QR code display. [TODO]
  P5:   Offline queue integration test. Security test suite. [DONE]

Week 3 (Mar 24–Apr 1) — Polish, Deploy, Report
  All:  End-to-end manual test (two terminals, Alice+Bob).
  P2:   Fix any remaining edge cases in messaging/friends.
  P3:   Audit nonce handling. Verify AD binding.
  P4:   DEPLOY.md — test clean install on Windows 11 + Ubuntu.
  P5:   Presentation video recording.
  All:  Report writing (split by section, merge Friday Apr 1).

Apr 2:  Submit by 16:59.
```

---

## Appendix: Database Schema (SQLite)

```sql
-- migrations/001_init.sql

CREATE TABLE users (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    username    TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,          -- argon2id hash
    totp_secret TEXT NOT NULL,          -- encrypted at rest
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    deleted_at  INTEGER
);

CREATE TABLE public_keys (
    user_id         TEXT PRIMARY KEY REFERENCES users(id),
    identity_pub    TEXT NOT NULL,      -- base64 Ed25519 public key
    dh_pub          TEXT NOT NULL,      -- base64 X25519 public key
    key_sig         TEXT NOT NULL,      -- Ed25519 sig over (identity_pub || dh_pub)
    uploaded_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT NOT NULL REFERENCES users(id),
    token_hash  TEXT NOT NULL,
    expires_at  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE friend_requests (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    sender_id       TEXT NOT NULL REFERENCES users(id),
    recipient_id    TEXT NOT NULL REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/accepted/declined/cancelled
    created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE(sender_id, recipient_id)
);

CREATE TABLE friendships (
    user_a_id   TEXT NOT NULL REFERENCES users(id),
    user_b_id   TEXT NOT NULL REFERENCES users(id),
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (user_a_id, user_b_id),
    CHECK (user_a_id < user_b_id)   -- enforce canonical order
);

CREATE TABLE blocks (
    blocker_id  TEXT NOT NULL REFERENCES users(id),
    blocked_id  TEXT NOT NULL REFERENCES users(id),
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE TABLE messages (
    id              TEXT PRIMARY KEY,               -- client-generated UUID v4
    conversation_id TEXT NOT NULL,
    sender_id       TEXT NOT NULL REFERENCES users(id),
    recipient_id    TEXT NOT NULL REFERENCES users(id),
    counter         INTEGER NOT NULL,
    nonce_b64       TEXT NOT NULL,
    ciphertext_b64  TEXT NOT NULL,
    eph_pub_b64     TEXT,                           -- non-null on first message only
    ttl_seconds     INTEGER,
    sent_at         INTEGER NOT NULL,
    stored_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    delivered_at    INTEGER,
    expires_at      INTEGER GENERATED ALWAYS AS (
                        CASE WHEN ttl_seconds IS NOT NULL
                        THEN stored_at + ttl_seconds
                        ELSE NULL END
                    ) VIRTUAL,
    UNIQUE(conversation_id, sender_id, counter)     -- replay prevention
);

CREATE INDEX idx_messages_recipient_delivered
    ON messages(recipient_id, delivered_at)
    WHERE delivered_at IS NULL;

CREATE INDEX idx_messages_expires
    ON messages(expires_at)
    WHERE expires_at IS NOT NULL;

CREATE TABLE conversations (
    id                  TEXT PRIMARY KEY,
    user_a_id           TEXT NOT NULL,
    user_b_id           TEXT NOT NULL,
    last_message_at     INTEGER,
    unread_count_a      INTEGER NOT NULL DEFAULT 0,
    unread_count_b      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_a_id, user_b_id),
    CHECK (user_a_id < user_b_id)
);
```

---

*Threat model: HbC server + network attacker + malicious users*
*Crypto: X25519 + HKDF-SHA256 + AES-256-GCM + Ed25519 + Argon2id + TOTP*
*All primitives from well-reviewed libraries — no custom crypto*
