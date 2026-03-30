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

## Quick start

Choose one setup path only. For teammates and graders, use the standard path
below first and only read later sections if something fails.

### Standard run path

1. **Install dependencies**:

   ```bash
   uv sync
   ```

2. **Generate local config and demo TLS files**:

   Linux / macOS / Git Bash:

   ```bash
   chmod +x scripts/bootstrap-env.sh
   ./scripts/bootstrap-env.sh
   ```

   Windows CMD / PowerShell:

   ```bat
   scripts\bootstrap-env.bat
   ```

3. **Start the server**:

   ```bash
   docker compose up --build
   ```

   The standard server URL is:

   ```text
   https://localhost:8443
   ```

4. **Start the client in a new terminal**:

   ```bash
   uv run python -m client.main --server https://localhost:8443 --no-verify-tls
   ```

   This is the current reliable local/demo path. Local certificate trust via
   `--ca-cert` is tracked separately in issue `#16`.

Do not mix Docker mode and ad-hoc direct local server runs in the same session
unless you are explicitly debugging config or database paths.

### What this project expects

- Python 3.12
- `uv`
- Docker with Compose support
- Local bootstrap via `scripts/bootstrap-env.*`

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

## Alternative run paths

### Docker mode

Docker Compose starts the **server only**. The client still runs from the host
terminal.

```bash
docker compose up --build
uv run python -m client.main --server https://localhost:8443 --no-verify-tls
```

### Direct server run (debug only)

```bash
uv run python -m server.main
uv run python -m client.main --server https://localhost:8443 --no-verify-tls
```

This is not the primary grading/demo path.

## Architecture & Protocol

See `docs/ARCHITECTURE.md` for the full design: session state machine, 2-DH key exchange, replay protection, secure local storage, TOTP encryption scheme, input validation policy, and trust model.

## Bug Report

See `docs/BUG_REPORT.md` for the latest bug hunting findings and code quality analysis.

## Deployment

See `docs/DEPLOY.md` for step-by-step instructions for Windows 11 and Ubuntu.

## Troubleshooting

| Problem                                               | Fix                                                                                                                                           |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `PermissionError: '/app/data'`                        | Stale `.env.local` with container path. Delete it or remove the `DATABASE_URL` line and restart.                                            |
| TLS cert files missing                                | Run `bash scripts/bootstrap-env.sh` or `scripts\bootstrap-env.bat`, then restart the server.                                                 |
| `ConnectionError` / "Cannot reach server" with Docker | Confirm Docker is running, then use `https://localhost:8443`. For local/demo use, the current reliable client path is `--no-verify-tls`.    |
| `--ca-cert` or `--pin-cert` still fails locally       | Known local/demo cert issue: bootstrap-generated cert trust is tracked in issue `#16`. Use `--no-verify-tls` only as the current workaround. |

**Data directory contract:**

| Environment                | DB path           | How it's set                                             |
| -------------------------- | ----------------- | -------------------------------------------------------- |
| Local dev (zero config)    | `./data/im.db`    | Auto-detected via `shared/env.py` (no `/.dockerenv`)     |
| Local dev (with bootstrap) | `./data/im.db`    | `bootstrap-env.sh` -> `.env.local`                       |
| Docker / Compose           | `/app/data/im.db` | Auto-detected via `shared/env.py` (`/.dockerenv` exists) |

## License

Academic project for COMP3334. Not intended for production use.
