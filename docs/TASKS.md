# COMP3334 Project — Task Breakdown

## Status: In Progress — 34/34 tests passing ✅
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
- `server/api/conversations.py` — R23/R24 conversation list, unread counters
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
- `client/ui/screens/register.py` — registration form
- `client/ui/screens/conversations.py` — conversation list (R23/R24)
- `client/ui/screens/chat.py` — chat view, key change warning, TTL display
- `client/ui/screens/friends.py` — friend request management
- `client/ui/screens/settings.py` — fingerprint display (R5), TTL config (R10)

### Tests (Done)
- `test_replay_attack.py` — security test: replay resistance + ciphertext tampering
- `tests/unit/test_crypto.py` — Ed25519, X25519, AES-GCM, replay protector, fingerprint
- `tests/unit/test_auth.py` — Argon2id, bearer tokens, TOTP encryption
- `tests/integration/test_e2e_message.py` — full E2E: register→login→friend→send→decrypt + replay rejection

### Docs (Done)
- `ARCHITECTURE.md` — full design doc with session state machine, secure storage design, TOTP encryption scheme, input validation policy, logging policy, auth token design
- `DEPLOY.md` — step-by-step deploy guide for Windows 11 + Ubuntu

---

## Remaining Tasks 🔲

### Week 2 Priority (Mar 17–23)

| # | Task | Owner | Req |
|---|------|-------|-----|
| 1 | **Wire up `client/ui/app.py` event handlers** — test login→conversation→chat flow end-to-end manually | P4 | All |
| 2 | **`client/ui/widgets/message_list.py`** — optional: extract MessageItem widget for reuse | P4 | R23 |
| 3 | **TTL countdown timer in ChatScreen** — schedule `sweep_expired()` every 30s while chat is open | P4 | R11 |
| 4 | **TOTP QR code display** — show QR code image or URI on registration (currently just notifies) | P4 | R2 |
| 5 | **`server/api/conversations.py` mark-read endpoint** — `POST /v1/conversations/{id}/read` resets unread | P2 | R24 |
| 6 | **Offline queue test** — integration test: send while Bob offline, Bob connects, gets message | P2 | R20 |

### Week 3 Priority (Mar 24–Apr 1)

| # | Task | Owner | Req |
|---|------|-------|-----|
| 7 | **End-to-end manual test** — two terminals, Alice+Bob full conversation | All | All |
| 8 | **Windows 11 deploy test** — follow DEPLOY.md on clean Windows VM | P4 | §9 |
| 9 | **Ubuntu deploy test** — follow DEPLOY.md on clean Ubuntu VM | P4 | §9 |
| 10 | **Report writing** — split by section (see below), merge Apr 1 | All | §8 |
| 11 | **Presentation video** — 10 min, record by Apr 1 | All | §10 |

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
| 10–11 | Future Works, References | All |

---

## Known Issues / Risks

1. **`client/ui/app.py` event handler naming** — Textual uses `on_<screen_class>_<message_class>` naming. Verify all handler names match exactly (snake_case of class names).
2. **`make_key_signature` import in `client/crypto/storage.py`** — imported from `session.py` but not re-exported. Verify import path in `app.py`.
3. **`anyio` backend for integration tests** — `pyproject.toml` sets `asyncio_mode = "auto"`. Integration tests use `anyio` fixture — may need `pytest-anyio` or adjust to `asyncio` only.
4. **TLS in dev** — client uses `verify=False`. For production, use real certs and set `verify=True`.
5. **TOTP QR code** — currently only shows `otpauth://` URI as a notification. Consider adding `qrcode` library to render ASCII QR in terminal.

---

## Quick Commands

```bash
# Install deps
uv sync

# Run server (dev)
uv run uvicorn server.main:app --host 0.0.0.0 --port 8443

# Run client
uv run python -m client.main --server https://localhost:8443

# Run all tests
uv run pytest -v

# Run only unit tests (fast, no server)
uv run pytest tests/unit/ -v

# Run security tests
uv run pytest test_replay_attack.py -v

# Docker
docker compose up --build
```
