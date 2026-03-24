# COMP3334 Secure Instant Messenger

End-to-end encrypted instant messaging application built for COMP3334. Demonstrates strong cryptographic practices, replay protection, and an honest-but-curious server model.

**Team:** 5 people | **Deadline:** April 2, 2026 16:59 | **Tests:** 45/45 passing ✅

## Features

- End-to-end encryption using X25519/AES-256-GCM
- Identity keys (Ed25519) and per-conversation sessions
- Offline message queue with ciphertext blobs only
- Argon2id password hashing and TOTP-based 2FA with QR code
- Replay protection (monotonic counter + message ID dedup)
- Key change detection with fingerprint verification
- Timed self-destruct messages (TTL in authenticated data)
- Python implementation with SQLite backend and Textual TUI client

## Repository structure

```
├── client/                # client application (TUI + crypto)
│   ├── api/               # httpx async HTTP + WebSocket client
│   ├── crypto/            # Ed25519, X25519, AES-GCM, storage
│   ├── state/             # local SQLite message store, TTL sweep
│   └── ui/                # Textual TUI screens + widgets
├── server/                # FastAPI server
│   ├── api/               # auth, keys, friends, messages, conversations
│   ├── core/              # config, database, security
│   └── ws/                # WebSocket connection manager
├── shared/                # Pydantic wire models shared by client + server
├── tests/
│   ├── unit/              # crypto, auth unit tests
│   ├── integration/       # E2E message flow, offline queue
│   ├── security/          # replay attack, ciphertext tampering
│   └── ui/                # Textual TUI smoke test
├── docs/                  # ARCHITECTURE.md, TASKS.md, DEPLOY.md
├── scripts/               # seed data helpers
├── pyproject.toml         # uv managed
├── Dockerfile.server
└── docker-compose.yml
```

## Getting started

1. **Install dependencies** (requires [uv](https://docs.astral.sh/uv/)):

   ```bash
   uv sync
   ```

2. **Start the server:**

   ```bash
   uv run python -m server.main
   ```

   The server auto-detects the environment and creates `./data/` automatically.
   In development mode, secrets are auto-generated (logged as warnings).
   TLS certs are optional locally — the server falls back to HTTP if missing.

3. **(Optional) Generate TLS certs and customise config:**

   ```bash
   bash scripts/bootstrap-env.sh dev
   ```

4. **Launch the client** (in a separate terminal):

   ```bash
   uv run python -m client.main --server https://localhost:8443
   ```

   The client stores identity keys in `~/.comp3334im/<username>/`.

## Testing

```bash
# Run all tests
uv run --extra dev pytest -v

# Unit tests only (fast, no server needed)
uv run --extra dev pytest tests/unit/ -v

# Security tests (replay, tampering)
uv run --extra dev pytest tests/security/ -v

# Integration tests (requires no running server — uses in-process fixtures)
uv run --extra dev pytest tests/integration/ -v
```

## Docker

```bash
# Start server
docker compose up --build

# Start client (separate terminal) — use --no-verify-tls since the container auto-generates a self-signed cert
uv run python -m client.main --server https://localhost:8443 --no-verify-tls
```

> **Note:** The client defaults to HTTP in `main.py`. Always pass `--server https://localhost:8443` when connecting to the Docker server. Without `--no-verify-tls`, you'll get a `ConnectionError` because the container's self-signed cert isn't trusted by the host.

![Docker client connection notes](image/README/1774329535318.png)

## Architecture & Protocol

See `docs/ARCHITECTURE.md` for the full design: session state machine, 2-DH key exchange, replay protection, secure local storage, TOTP encryption scheme, input validation policy, and trust model.

## Deployment

See `docs/DEPLOY.md` for step-by-step instructions for Windows 11 and Ubuntu.

## Troubleshooting

| Problem | Fix |
|---|---|
| `PermissionError: '/app/data'` | Stale `.env.local` with container path. Delete it or remove the `DATABASE_URL` line and restart. |
| TLS cert not found | Run `bash scripts/bootstrap-env.sh dev`, or ignore — server falls back to HTTP in dev mode. |
| `ConnectionError` / "Cannot reach server" with Docker | The container auto-generates a self-signed cert the host doesn't trust. Use `--no-verify-tls` and make sure you pass `--server https://localhost:8443` (not HTTP). |

**Data directory contract:**

| Environment | DB path | How it's set |
|---|---|---|
| Local dev (zero config) | `./data/im.db` | Auto-detected via `shared/env.py` (no `/.dockerenv`) |
| Local dev (with bootstrap) | `./data/im.db` | `bootstrap-env.sh` → `.env.local` |
| Docker / Compose | `/app/data/im.db` | Auto-detected via `shared/env.py` (`/.dockerenv` exists) |

## License

Academic project for COMP3334. Not intended for production use.
