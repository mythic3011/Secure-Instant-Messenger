# COMP3334 Project — Task Breakdown

## Status: In Progress — 45/45 tests passing ✅
**Team:** 5 people (P1–P5)
**Deadline:** April 2, 2026 16:59

---

## Completed ✅

### Foundation (Done)
- `shared/protocol.py` — Pydantic wire models, `make_conversation_id()`, all enums
- `shared/__init__.py`
- `001_init.sql` → `server/migrations/001_init.sql` — full DB schema

### Server (Done)
- `server/main.py` — FastAPI app, routers, WebSocket endpoint, TTL cleanup loop
- `server/core/config.py` — pydantic-settings env config
- `server/core/database.py` — aiosqlite, migration runner
- `server/core/security.py` — Argon2id, opaque bearer tokens, TOTP encryption, rate limiting
- `server/api/auth.py` — R1 register, R2 login (password+TOTP), R3 logout
- `server/api/keys.py` — R4 public key upload/fetch with self-signature verification
- `server/api/friends.py` — R13–R16 friend requests, accept/decline/cancel, block, remove
- `server/api/messages.py` — R8/R9/R16/R17/R18/R20/R22 send, fetch (paginated), delivery ACK
- `server/api/conversations.py` — R23/R24 conversation list, unread counters, mark-read endpoint
- `server/ws/handler.py` — WebSocket connection manager, offline queue flush

### Client Crypto (Done)
- `client/crypto/session.py` — Ed25519, X25519 2-DH, HKDF-SHA256, AES-256-GCM, replay protection, key change detection
- `client/crypto/storage.py` — Argon2id-derived storage key, AES-GCM encrypted keystore + session cache

### Client App (Done)
- `client/api/client.py` — httpx async HTTP client + WebSocket listener with auto-reconnect
- `client/state/store.py` — local SQLite message store, TTL sweep
- `client/main.py` — CLI entrypoint
- `client/ui/app.py` — main Textual TUI app, wires all screens + crypto + WS
- `client/ui/screens/login.py` — login form
- `client/ui/screens/register.py` — registration form + TOTP QR code display (R2)
- `client/ui/screens/conversations.py` — conversation list (R23/R24)
- `client/ui/screens/chat.py` — chat view, key change warning, TTL countdown + sweep every 30s (R10/R11)
- `client/ui/screens/friends.py` — friend request management
- `client/ui/screens/settings.py` — fingerprint display (R5), TTL config (R10)

### Tests (Done)
- `tests/security/test_replay_attack.py` — replay resistance, ciphertext tampering, session key derivation (13 tests)
- `tests/unit/test_crypto.py` — Ed25519, X25519, AES-GCM, replay protector, fingerprint, envelope (22 tests)
- `tests/unit/test_auth.py` — Argon2id, bearer tokens, TOTP encryption (10 tests)
- `tests/integration/test_e2e_message.py` — full E2E: register→login→friend→send→decrypt + replay rejection (2 tests)
- `tests/integration/test_offline_queue.py` — offline queue store-and-forward + replay rejection (2 tests)
- `tests/ui/test_textual.py` — Textual TUI smoke test

### Docs (Done)
- `ARCHITECTURE.md` — full design doc with session state machine, secure storage design, TOTP encryption scheme, input validation policy, logging policy, auth token design
- `DEPLOY.md` — step-by-step deploy guide for Windows 11 + Ubuntu

---

## Remaining Tasks 🔲

### Week 2 (Mar 17–23) — mostly done

| # | Task | Owner | Req |
|---|------|-------|-----|
| 1 | **Wire up `client/ui/app.py` event handlers** — test login→conversation→chat flow end-to-end manually | P4 | All |
| 2 | **`client/ui/widgets/message_list.py`** — optional: extract MessageItem widget for reuse | P4 | R23 |
| 3 | **P5 task assignment** — assign P5 to a remaining task or report section | All | — |

### Week 3 Priority (Mar 24–Apr 1)

| # | Task | Owner | Req |
|---|------|-------|-----|
| 4 | **End-to-end manual test** — two terminals, Alice+Bob full conversation | All | All |
| 5 | **Windows 11 deploy test** — follow DEPLOY.md on clean Windows VM | P5 | §9 |
| 6 | **Ubuntu deploy test** — follow DEPLOY.md on clean Ubuntu VM | P5 | §9 |
| 7 | **Report writing** — split by section (see below), merge Apr 1 | All | §8 |
| 8 | **Presentation video** — 10 min, record by Apr 1 | All | §10 |

---

## Report Section Assignments

| Section | Content | Owner |
|---------|---------|-------|
| 1–3 | Abstract, Introduction, Team info | All |
| 4 | Threat Model & Assumptions | P1 |
| 5 | Architecture (trust boundaries, data flow) | P2 |
| 6 | Protocol Design (session state machine, message format, replay) | P3 |
| 7 | Cryptographic Choices & Rationale | P3 |
| 8 | Security Analysis (server can't decrypt, metadata exposure, limitations) | P1 |
| 9 | Testing & Evaluation (demo + 2 security test cases) | P2 |
| 10 | Deployment & Setup Guide | P5 |
| 11–12 | Future Works, References | All |

---

## Known Issues / Risks

1. **`client/ui/app.py` event handler naming** — Textual uses `on_<screen_class>_<message_class>` naming. Verify all handler names match exactly (snake_case of class names).
2. **`make_key_signature` import in `client/crypto/storage.py`** — imported from `session.py` but not re-exported. Verify import path in `app.py`.
3. **TLS in dev** — client uses `verify=False`. For production, use real certs and set `verify=True`.
4. **pytest requires `--extra dev`** — run as `uv run --extra dev pytest` (pytest is in `[project.optional-dependencies].dev`).

---

## Quick Commands

```bash
# Install deps
uv sync

# Install dev deps (needed for tests)
uv sync --extra dev

# Run server (dev)
uv run uvicorn server.main:app --host 0.0.0.0 --port 8443

# Run client
uv run python -m client.main --server https://localhost:8443

# Run all tests
uv run --extra dev pytest -v

# Run only unit tests (fast, no server)
uv run --extra dev pytest tests/unit/ -v

# Run security tests
uv run --extra dev pytest tests/security/ -v

# Run integration tests
uv run --extra dev pytest tests/integration/ -v

# Docker
docker compose up --build
```
