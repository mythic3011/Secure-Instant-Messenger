# Secure Instant Messenger

End-to-end encrypted instant messaging application. Demonstrates strong cryptographic practices, replay protection, and an honest-but-curious server model.

**Team:** 5 people | **Deadline:** April 9, 2026 07:50

## Features

- End-to-end encryption using X25519/AES-256-GCM
- Identity keys (Ed25519) and per-conversation sessions
- Offline message queue with ciphertext blobs only
- Argon2id password hashing and TOTP-based 2FA with QR code
- Replay protection (monotonic counter + message ID dedup)
- Key change detection with fingerprint verification
- Centralized peer-bundle trust gate with signature verification, expected-peer binding, and fail-closed rejection of invalid or mismatched bundles
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

Client source-of-truth paths:

- entrypoint: `python -m client.main`
- app controller: `client/ui/app.py`
- Textual screens: `client/ui/screens/`

## Quick start

Choose one setup path only. For teammates and graders, use the standard path
below first and only read later sections if something fails.

### Platform support

- Linux / macOS: supported via `./install.sh` plus `scripts/launch/*.sh`
- Windows 11: supported via `install.bat` plus `scripts/launch/*.bat`
- Installer contract note: `install.sh` and `install.bat` implement the same
  installer contract on different platforms

### Standard run path

1. **Prepare host and project environment**:

   ```bash
   sh ./install.sh --fix
   ```

   On Windows `cmd.exe`, use:

   ```bat
   install.bat --fix
   ```

   This checks external prerequisites, bootstraps the local project environment,
   and prepares demo TLS files when missing.

2. **Start the server**:

   ```bash
   docker compose up --build
   ```

   The standard server URL is:

   ```text
   https://localhost:8443
   ```

3. **Start the client in a new terminal**:

   ```bash
   sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
   ```

   This is the standard local/demo path. The launcher does a server health
   preflight by default; use `--no-health-check` only when you explicitly want
   to bypass that guard.

Do not mix Docker mode and ad-hoc direct local server runs in the same session
unless you are explicitly debugging config or database paths.

### What this project expects

- Python 3.12
- `uv`
- Docker with Compose support
- Local bootstrap via `./install.sh --fix`

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

## Repo workflow

The repository now uses lightweight governance automation for reviewability:

- PRs should use the built-in PR template and state behavior / contract impact explicitly.
- PR auto-labeling applies path labels such as `client`, `server`, `ui`,
  `security`, `docs`, `testing`, `ci`, and `mixed-scope`.
- Issue auto-labeling applies a narrower inferred label set for triage.

If app behavior or a wire / state contract changes, update or add the related
tests before requesting review again.

## Alternative run paths

### Docker mode

Docker Compose starts the **server only**. The client still runs from the host
terminal.

```bash
docker compose up --build
sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
```

### Direct server run (debug only)

```bash
uv run python -m server.main
sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
```

This is not the primary grading/demo path.

## Architecture & Protocol

See `docs/ARCHITECTURE.md` for the full design: session state machine, 2-DH key exchange, replay protection, secure local storage, TOTP encryption scheme, input validation policy, and trust model.

## Deployment

See `docs/DEPLOY.md` for step-by-step instructions for Windows 11 and Ubuntu.

## Troubleshooting

| Problem                                               | Fix                                                                                                                                           |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `PermissionError: '/app/data'`                        | Stale `.env.local` with container path. Delete it or remove the `DATABASE_URL` line and restart.                                            |
| TLS cert files missing                                | Linux/macOS: run `sh ./install.sh --fix`. Windows: run `install.bat --fix`, then follow any remaining manual TLS steps in `docs/DEPLOY.md` if needed. |
| `ConnectionError` / "Cannot reach server" with Docker | Confirm Docker is running, then use `https://localhost:8443` with `--ca-cert ./certs/server.crt` as the standard local client path. |
| `--ca-cert` or `--pin-cert` still fails locally       | Local trust should work with the SAN-enabled demo certs. If a specific machine still rejects them, use `--no-verify-tls` only as a temporary local fallback and capture the failure details. |

**Data directory contract:**

| Environment                | DB path           | How it's set                                             |
| -------------------------- | ----------------- | -------------------------------------------------------- |
| Local dev (zero config)    | `./data/im.db`    | Auto-detected via `shared/env.py` (no `/.dockerenv`)     |
| Local dev (with bootstrap) | `./data/im.db`    | `install.sh` / `install.bat` -> `.env.local` / local project bootstrap |
| Docker / Compose           | `/app/data/im.db` | Auto-detected via `shared/env.py` (`/.dockerenv` exists) |

## License

Not intended for production use.
