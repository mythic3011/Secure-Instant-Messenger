# Deployment Guide — COMP3334 Secure IM

This guide shows the recommended way to run the project on a clean machine.

Use the Quick Start first.
Only read the later sections if something fails.

---

## Quick Start (Recommended)

Run from the project root.

Supported platforms for this guide:

- Ubuntu / Linux via `install.sh` and `scripts/launch/*.sh`
- Windows 11 via `install.bat` and `scripts/launch/*.bat`
- `install.sh` and `install.bat` implement the same installer contract on
  different platforms

### 1. Prepare host and project environment

```bash
sh ./install.sh --fix
```

Windows `cmd.exe` / PowerShell:

```bat
install.bat --fix
```

This checks external prerequisites, bootstraps the local project environment,
and prepares demo TLS files when missing.

### 2. Start the server

```bash
docker compose up --build
```

Server URL:

```text
https://localhost:8443
```

### 3. Start the client in a new terminal

```bash
sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
```

That is the standard local/demo run path for this project.
The canonical client entrypoint is `python -m client.main`; the UI source files
live under `client/ui/` and `client/ui/screens/`.

> **TLS note:** Bootstrap-generated demo certs include SAN entries for
> `localhost` and `127.0.0.1`, so `--ca-cert ./certs/server.crt` is the
> preferred local trust path. Use `--no-verify-tls` only as a temporary local
> fallback on machines that still reject the cert.

### Standard path summary

```text
install.sh --fix -> docker compose -> run-client.sh preflight -> client connects to https://localhost:8443 with --ca-cert ./certs/server.crt
```

Do not mix Docker mode and ad-hoc direct server runs unless you are debugging.

---

## 1. Prerequisites

### Ubuntu Linux

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip git curl openssl \
    docker.io docker-compose-plugin
sudo systemctl enable --now docker
# Allow running Docker without sudo (re-login after this)
sudo usermod -aG docker $USER
```

### Windows 11

Install the following in order:

1. **Python 3.12** — https://www.python.org/downloads/
   During install: check **"Add Python to PATH"**

2. **Git for Windows** — https://git-scm.com/download/win

3. **Docker Desktop** — https://www.docker.com/products/docker-desktop/
   After install: start Docker Desktop and wait for the engine to be running

4. **OpenSSL** (required for TLS cert generation) — included with Git for Windows.
   Ensure `openssl` is available in `PATH` before running the bootstrap script.

### Install `uv` (Python package manager)

**Ubuntu / macOS:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or restart terminal
```

**Windows (PowerShell):**

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## 2. Get the Code

```bash
git clone <repo-url> COMP3334_Project
cd COMP3334_Project
```

Or extract the submitted zip:

```bash
unzip TeamID.zip
cd TeamID/code
```

---

## 3. Install Python Dependencies

`install.sh` on Linux/macOS and `install.bat` on Windows are the canonical
dependency/bootstrap entrypoints for this project. They run project-local setup
including `uv sync`.

> **For running tests**, install dev dependencies too:
>
> ```bash
> uv sync --extra dev
> ```

If `uv sync` fails on a clean machine:

- confirm you are using Python 3.12: `python --version`
- rerun `uv sync` from the project root
- use `uv sync --extra dev` only if you need tests
- if Windows reports build or PATH issues, reopen the terminal after installing Python / `uv`
- if runtime import errors still appear later, rerun `uv sync` before debugging application code

---

## 4. Configure Environment

Use the canonical installer entrypoint:

```bash
sh ./install.sh --check
sh ./install.sh --fix
```

Windows `cmd.exe` / PowerShell:

```bat
install.bat --check
install.bat --fix
```

`./install.sh --check` is read-only and validates host prerequisites plus
project readiness. `./install.sh --fix` performs project-local bootstrap via
`uv sync`, ensures `.env.local` exists, and prepares demo TLS files if missing.

`install.bat` provides the Windows peer entrypoint for the same installer
contract. Use it from `cmd.exe` or PowerShell instead of trying to invoke
`install.sh` directly from plain Windows shells.

---

## 5. Generate a Self-Signed TLS Certificate (Manual Fallback)

`install.sh --fix` on Linux/macOS and `install.bat --fix` on Windows are the
primary paths.
Use this only if certificate auto-generation failed.

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:4096 \
    -keyout certs/server.key \
    -out certs/server.crt \
    -days 365 -nodes \
    -subj "/CN=localhost"
```

---

## 6. Run the Server

### Option A — Docker Compose (recommended)

```bash
docker compose up --build
```

The server starts on `https://localhost:8443`.
To run in the background: `docker compose up --build -d`
To stop: `docker compose down`

### Option B — Direct (development, no Docker)

Linux / macOS / Git Bash:

```bash
chmod +x scripts/launch/run-server.sh
./scripts/launch/run-server.sh
```

Windows CMD / PowerShell:

```bat
scripts\launch\run-server.bat
```

Manual equivalent:

```bash
uv run python -m server.main
```

> **Note:** In development mode (`app_env=development`), if `TOKEN_SECRET_KEY` or
> `TOTP_ENCRYPTION_KEY` are missing from `.env.local`, the server auto-generates
> temporary values and logs a warning. **Never rely on this in production.**

### Database initialization / import

For a normal first-time deployment, the application will initialize the local
SQLite database automatically when the server starts.

The authoritative schema path is the ORM model set created by
`server.core.database.init_db()` at startup. This repo does not rely on
`server/migrations/001_init.sql` as the deployment authority.

---

## 7. Run the Client

Open a **new terminal** in the project directory.

For local testing and demo, the standard path is:

```bash
sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
```

The launcher performs a server `/health` preflight by default. To bypass that
guard explicitly:

```bash
sh scripts/launch/run-client.sh --no-health-check https://localhost:8443 --ca-cert ./certs/server.crt
```

Manual equivalent:

```bash
uv run python -m client.main --server https://localhost:8443 --ca-cert ./certs/server.crt
```

Course tutorial materials (for example `docs/Tutorial/Tutorial.pdf`) are not
the deployment baseline for this project. This project uses Python 3.12 +
`uv` for reproducible environments.

Linux / macOS / Git Bash:

```bash
sh scripts/launch/run-client.sh https://localhost:8443 --ca-cert ./certs/server.crt
```

Windows CMD / PowerShell:

```bat
scripts\launch\run-client.bat https://localhost:8443 --ca-cert .\certs\server.crt
```

If the launcher reports missing prerequisites or an unprepared environment on
Windows, run:

```bat
install.bat --fix
```

To bypass the default server health preflight explicitly:

```bat
scripts\launch\run-client.bat --no-health-check https://localhost:8443 --ca-cert .\certs\server.crt
```

Manual equivalent:

```bash
uv run python -m client.main --server https://localhost:8443 --ca-cert ./certs/server.crt
```

You will be prompted for your username if not provided via `--username`.

---

## 8. First-Time Setup Walkthrough

### Register a new account

1. At the login screen, click **"Register instead"**
2. Enter a username (3–32 chars, letters/digits/`_`/`-`)
3. Enter a password (minimum 12 characters) and confirm it
4. Click **Register**
5. A TOTP provisioning URI (and ASCII QR code, if `qrcode` is installed) will appear — scan it with an authenticator app:
   - **Google Authenticator** (Android/iOS)
   - **Authy** (Android/iOS/Desktop)
   - **Microsoft Authenticator**
6. The app will return to the login screen

### Login

1. Enter your username and password
2. Open your authenticator app and enter the current 6-digit code
3. Click **Login**

### Add a friend

1. From the conversation list, click **Friends**
2. Enter the other user's username and click **Send Request**
3. The other user must login and accept the request from their Friends screen

### Send a message

1. Select a conversation from the list
2. Type your message and press **Enter** or click **Send**
3. Messages are end-to-end encrypted — the server only sees ciphertext

### Set a self-destruct timer (optional)

1. In a chat, click the **⚙** button
2. Enter a TTL in seconds (e.g. `300` for 5 minutes)
3. Click **Save TTL** — subsequent messages will auto-delete after that duration

### Verify a contact's identity key

1. In a chat, click **⚙**
2. Compare the **Safety Number** with your contact out-of-band (call, in person)
3. If it matches, click **Mark as Verified ✓**

---

## 9. Running Tests

```bash
# Install dev dependencies first (required for pytest)
uv sync --extra dev

# Unit tests only (no server needed, fast)
uv run --extra dev pytest tests/unit/ -v

# Integration tests (uses in-memory test server, no real DB)
uv run --extra dev pytest tests/integration/ -v

# Security tests (replay attack, ciphertext tampering)
uv run --extra dev pytest tests/security/ -v

# Repository guardrails (submission freeze)
UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_silent_excepts.py
UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_stale_security_claims.py

# Type check
./.venv/bin/mypy client server

# All tests
uv run --extra dev pytest -v
```

Use the fresh `pytest` summary as the evidence source instead of relying on a
hard-coded test count in this document. Use the guard-script outputs as
evidence that silent broad exception swallowing and stale security claims are
repository-enforced invariants.

---

## 10. Troubleshooting

Check these in order.

1. Is Docker running?

```bash
docker compose ps
```

2. Is the server up?

```bash
docker compose logs --tail=100
```

3. Did bootstrap complete?

Confirm these files exist:

- `.env.local`
- `certs/server.crt`
- `certs/server.key`

### Common Problems

| Problem                               | Solution                                                                                                                   |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Port 8443 already in use              | Change `PORT=8443` in `.env.local` to another port, e.g. `8444`                                                            |
| TLS certificate errors in client      | Preferred local path is `--ca-cert ./certs/server.crt`. If that still fails on a specific machine, fall back to `--no-verify-tls` for local/demo only and capture the exact failure for follow-up. |
| TOTP code rejected                    | Ensure your system clock is accurate. Ubuntu: `timedatectl set-ntp true`. Windows: Settings -> Time & Language -> Sync now |
| Docker permission denied (Linux)      | Run `sudo usermod -aG docker $USER` then log out and back in                                                               |
| `uv: command not found`               | Install `uv` using the supported steps above, restart the terminal, then rerun the canonical installer or `uv sync`.      |
| `ModuleNotFoundError` or missing library import | Linux/macOS: run `sh ./install.sh --fix`. Windows: run `install.bat --fix`. Use `uv sync --extra dev` only for tests.                             |
| `scripts\\launch\\run-server.bat` or `scripts\\launch\\run-client.bat` exits immediately | Read the printed prerequisite message, then run `install.bat --check` or `install.bat --fix` as appropriate. |
| `./scripts/launch/run-server.sh` or `./scripts/launch/run-client.sh` says dependencies are missing | Run `sh ./install.sh --fix` from the repo root before debugging application code.                                        |
| Database locked error                 | Stop any other running server instance before starting a new one                                                           |
| Keystore not found on login           | You must register on this device first — keys are stored locally in `~/.comp3334im/<username>/`                            |
| `.env.local` already exists           | Delete `.env.local` only if you intentionally want to regenerate local config, then rerun `install.sh` / `install.bat --fix` for your platform. |
| `bootstrap-env.sh: Permission denied` | Run via the public entrypoint `sh ./install.sh --fix` instead of invoking helper scripts directly.                                                       |

---

## 11. File Locations

| File                                     | Purpose                                                  |
| ---------------------------------------- | -------------------------------------------------------- |
| `client/main.py`                         | Canonical client entrypoint                              |
| `client/ui/app.py`                       | Main Textual app controller                              |
| `client/ui/screens/`                     | Authoritative Textual screen implementations             |
| `install.sh` / `install.bat`            | Canonical setup entrypoint for prerequisite checks and project-local bootstrap |
| `scripts/install/check-prereqs.sh`      | Read-only external prerequisite validation               |
| `scripts/install/bootstrap-env.sh`      | Project-local bootstrap (`.env.local`, demo TLS files, `uv sync`) |
| `scripts/launch/run-server.sh` / `scripts/launch/run-server.bat` | Starts the direct development server with prerequisite checks |
| `scripts/lib/check_server_health.py` / `scripts/launch/check-server-health.sh` | Shared server `/health` probe used by launchers |
| `scripts/launch/run-client.sh` / `scripts/launch/run-client.bat` | Starts the client with prerequisite checks |
| `.env.local`                             | Server configuration (secrets, ports) — **never commit** |
| `certs/server.crt`                       | TLS certificate                                          |
| `certs/server.key`                       | TLS private key                                          |
| `~/.comp3334im/<username>/keystore.json` | Encrypted local key storage (client)                     |
| `~/.comp3334im/<username>/sessions.json` | Encrypted session state (client)                         |
| `~/.comp3334im/<username>/messages.db`   | Local message history (client)                           |
| `/app/data/im.db` (Docker)               | Server SQLite database                                   |

---

## Support Rule

If someone says "it does not work", ask for these exact outputs first:

```bash
docker compose ps
docker compose logs --tail=100
```

Without those two outputs, there is not enough signal to debug the run path.
