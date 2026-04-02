# COMP3334 Project — Task Breakdown

## Status: Submission Freeze / Maintainer Follow-up

**Team:** 5 people (P1–P5)
**Course Deadline:** April 8, 2026 07:50

---

## Completed ✅

### Foundation (Done)

- `shared/protocol.py` — Pydantic wire models and protocol enums
- `shared/__init__.py`
- `server/models/*` + `server/core/database.py` — authoritative ORM schema and startup initialization

### Server (Done)

- `server/main.py` — FastAPI app, routers, WebSocket endpoint, TTL cleanup loop
- `server/core/config.py` — pydantic-settings env config
- `server/core/database.py` — aiosqlite, startup schema initialization
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
- `tests/integration/test_e2e_message.py` — full E2E: register->login->friend->send->decrypt + replay rejection (2 tests)
- `tests/integration/test_offline_queue.py` — offline queue store-and-forward + replay rejection (2 tests)
- `tests/ui/test_textual.py` — Textual TUI smoke test

### Docs (Done)

- `ARCHITECTURE.md` — full design doc with session state machine, secure storage design, TOTP encryption scheme, input validation policy, logging policy, auth token design
- `DEPLOY.md` — step-by-step deploy guide for Windows 11 + Ubuntu

---

## Current Priorities 🔲

### Submission-facing deliverables

| #   | Task                                                 | Owner    | Status |
| --- | ---------------------------------------------------- | -------- | ------ |
| 1   | Finalize testing evidence pack and checklist (`#13`) | Assigned | Open   |
| 2   | Finalize demo video script / recording package (`#14`) | Assigned | Open |
| 3   | Finalize report package (`#15`)                      | Assigned | Open   |

### Post-freeze follow-ups

| #   | Task                                                                       | Owner      | Status |
| --- | -------------------------------------------------------------------------- | ---------- | ------ |
| 4   | Client-side friendship-change push handling + conversation refresh (`#28`) | Unassigned | Open   |
| 5   | Server-side requester notification after friend acceptance (`#29`)         | Unassigned | Open   |
| 6   | Avoid noisy KeyboardInterrupt traceback on Windows client shutdown (`#21`) | Unassigned | Open   |

### Operational note

The old mixed-scope friend-refresh issue was closed and replaced by `#28` and
`#29` so server notification and client refresh logic can be reviewed
separately.

---

## Report Section Assignments

| Section | Content                                                                  | Owner |
| ------- | ------------------------------------------------------------------------ | ----- |
| 1–3     | Abstract, Introduction, Team info                                        | All   |
| 4       | Threat Model & Assumptions                                               | P1    |
| 5       | Architecture (trust boundaries, data flow)                               | P2    |
| 6       | Protocol Design (session state machine, message format, replay)          | P3    |
| 7       | Cryptographic Choices & Rationale                                        | P3    |
| 8       | Security Analysis (server can't decrypt, metadata exposure, limitations) | P1    |
| 9       | Testing & Evaluation (demo + 2 security test cases)                      | P2    |
| 10      | Deployment & Setup Guide                                                 | P5    |
| 11–12   | Future Works, References                                                 | All   |

---

## Known Issues / Risks

1. ~~**`client/ui/app.py` event handler naming**~~ — ✅ All handlers verified correct (`on_login_screen_login_success`, `on_conversation_list_screen_conversation_selected`, etc.).
2. ~~**`make_key_signature` import**~~ — ✅ Imported directly from `client/crypto/session.py` in `app.py:31`. No issue.
3. **TLS in dev** — verification stays enabled by default. Standard local/demo path is `--ca-cert ./certs/server.crt`; use `--no-verify-tls` only as a temporary local fallback when a specific machine still rejects the generated cert.
4. **pytest requires `--extra dev`** — run as `uv run --extra dev pytest` (pytest is in `[project.optional-dependencies].dev`).
5. **Submission guardrails enabled** — `scripts/check_silent_excepts.py` and `scripts/check_stale_security_claims.py` run in pre-commit and `lint.yml`.

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
uv run python -m client.main --server https://localhost:8443 --ca-cert ./certs/server.crt

# Run all tests
uv run --extra dev pytest -v

# Run only unit tests (fast, no server)
uv run --extra dev pytest tests/unit/ -v

# Run security tests
uv run --extra dev pytest tests/security/ -v

# Guardrails for submission freeze
UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_silent_excepts.py
UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_stale_security_claims.py

# Type check
./.venv/bin/mypy client server

# Run integration tests
uv run --extra dev pytest tests/integration/ -v

# Docker
docker compose up --build
```

## Final Assurance Story

Before submission, development moved from feature work into hardening mode. The final gate is layered: `ruff`, `bandit`, two custom repository checks, `mypy`, and then the full `pytest` suite. The custom checks enforce two important invariants: silent broad exception swallowing is blocked, and stale security claims in docs or comments are blocked. MyPy is used in a non-strict, boundary-focused way to catch `None` crashes and wrong return shapes across client, storage, and API boundaries. The result is not just “the tests pass”, but that static checks, invariant enforcement, type checks, and runtime tests all support the same reliability and security claims.

### Short Presentation Version

We stopped feature work and moved into hardening mode. Our final assurance gate combines `ruff`, `bandit`, two custom repository checks, `mypy`, and `pytest`. The custom checks block silent exception swallowing and stale security claims, while MyPy catches boundary-level crashes like `None` misuse and wrong return shapes. So our claim is not just that tests pass, but that static checks, invariant enforcement, type checks, and runtime behavior all align.
