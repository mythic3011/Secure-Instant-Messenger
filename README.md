# COMP3334 Secure Instant Messenger

End-to-end encrypted instant messaging application built for COMP3334. Demonstrates strong cryptographic practices, replay protection, and an honest-but-curious server model.

**Team:** 5 people | **Deadline:** April 8, 2026 07:50

## Security

All 19 identified security vulnerabilities have been fixed. See [`docs/SECURITY_BUGS.md`](docs/SECURITY_BUGS.md) for the full bug report and [`docs/SECURITY_FIXES_SUMMARY.md`](docs/SECURITY_FIXES_SUMMARY.md) for the fix summary.

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
├── docs/                  # ARCHITECTURE.md, TASKS.md, DEPLOY.md, BUG_REPORT.md
├── scripts/               # seed data helpers
├── pyproject.toml         # uv managed
├── Dockerfile.server
└── docker-compose.yml
```

## Getting started

Note:
Course tutorial materials such as `docs/Tutorial/Tutorial.pdf` are not used as
the deployment baseline for this project. This repository standardizes on
Python 3.12 + `uv` for reproducible setup.

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

Avoid hard-coding a test total in submission docs. Use fresh `pytest` output as
the evidence source because the suite evolves during fixes/refactors.

## Docker

```bash
# Start server
docker compose up --build

# Start client (separate terminal) — prefer trusting the dev cert explicitly
uv run python -m client.main --server https://localhost:8443 --ca-cert ./certs/dev/server.crt
```

> **Note:** The client defaults to `https://localhost:8443` in `main.py`. Keep TLS verification enabled by default. If Docker is using a self-signed development certificate, prefer `--ca-cert <path>` to trust that certificate. Use `--no-verify-tls` only as a dev-only exception.

## Architecture & Protocol

See `docs/ARCHITECTURE.md` for the full design: session state machine, 2-DH key exchange, replay protection, secure local storage, TOTP encryption scheme, input validation policy, and trust model.

## Bug Report

See `docs/BUG_REPORT.md` for the latest bug hunting findings and code quality analysis.

## Deployment

See `docs/DEPLOY.md` for step-by-step instructions for Windows 11 and Ubuntu.

## Troubleshooting

| Problem                                               | Fix                                                                                                                                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `PermissionError: '/app/data'`                        | Stale `.env.local` with container path. Delete it or remove the `DATABASE_URL` line and restart.                                                                   |
| TLS cert not found                                    | Run `bash scripts/bootstrap-env.sh dev`, or ignore — server falls back to HTTP in dev mode.                                                                        |
| `ConnectionError` / "Cannot reach server" with Docker | The container may use a self-signed cert the host does not trust. Pass `--server https://localhost:8443` and prefer `--ca-cert <path-to-cert>`. Use `--no-verify-tls` only for local development exceptions. |

**Data directory contract:**

| Environment                | DB path           | How it's set                                             |
| -------------------------- | ----------------- | -------------------------------------------------------- |
| Local dev (zero config)    | `./data/im.db`    | Auto-detected via `shared/env.py` (no `/.dockerenv`)     |
| Local dev (with bootstrap) | `./data/im.db`    | `bootstrap-env.sh` -> `.env.local`                       |
| Docker / Compose           | `/app/data/im.db` | Auto-detected via `shared/env.py` (`/.dockerenv` exists) |

## License

Academic project for COMP3334. Not intended for production use.
