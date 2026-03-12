# Deployment Guide — COMP3334 Secure IM

Step-by-step guide to deploy and run the application from a clean **Windows 11** or **Ubuntu Linux** machine. No pre-installed software is assumed.

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

4. **OpenSSL** (for TLS cert generation) — included with Git for Windows.
   Open **Git Bash** for all commands below.

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

```bash
uv sync
```

> **For running tests**, install dev dependencies too:
>
> ```bash
> uv sync --extra dev
> ```

Fallback (if `uv` is unavailable):

```bash
pip install -e .
```

---

## 4. Configure Environment

Run the bootstrap script — it copies `.env.example` to `.env.local` and **automatically generates cryptographically random secrets** for `TOKEN_SECRET_KEY` and `TOTP_ENCRYPTION_KEY`:

**Ubuntu / macOS / Git Bash (Windows):**

```bash
chmod +x scripts/bootstrap-env.sh
./scripts/bootstrap-env.sh
```

**Windows (PowerShell, if Git Bash is unavailable):**

```powershell
python -c "
import shutil, secrets, re, pathlib
src = pathlib.Path('.env.example').read_text()
src = src.replace('REPLACE_WITH_RANDOM_64_HEX_CHARS', '__REPLACE__', 1)
src = src.replace('REPLACE_WITH_RANDOM_64_HEX_CHARS', '__REPLACE__', 1)
src = src.replace('__REPLACE__', secrets.token_hex(32), 1)
src = src.replace('__REPLACE__', secrets.token_hex(32), 1)
pathlib.Path('.env.local').write_text(src)
print('Wrote .env.local')
"
```

> If `.env.local` already exists, the script will exit without overwriting it.
> To regenerate secrets: `rm .env.local && ./scripts/bootstrap-env.sh`

---

## 5. Generate a Self-Signed TLS Certificate (Development)

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

```bash
uv run uvicorn server.main:app --host 0.0.0.0 --port 8443
```

> **Note:** In development mode (`app_env=development`), if `TOKEN_SECRET_KEY` or
> `TOTP_ENCRYPTION_KEY` are missing from `.env.local`, the server auto-generates
> temporary values and logs a warning. **Never rely on this in production.**

---

## 7. Run the Client

Open a **new terminal** in the project directory:

```bash
uv run python -m client.main --server https://localhost:8443
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

# All tests
uv run --extra dev pytest -v
```

Expected output: **45 tests passed**.

---

## 10. Troubleshooting

| Problem                               | Solution                                                                                                                 |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Port 8443 already in use              | Change `PORT=8443` in `.env.local` to another port, e.g. `8444`                                                          |
| TLS certificate errors in client      | Expected for self-signed certs — client uses `verify=False` in dev mode                                                  |
| TOTP code rejected                    | Ensure your system clock is accurate. Ubuntu: `timedatectl set-ntp true`. Windows: Settings → Time & Language → Sync now |
| Docker permission denied (Linux)      | Run `sudo usermod -aG docker $USER` then log out and back in                                                             |
| `uv: command not found`               | Restart terminal after installing `uv`, or use `pip install -e .` instead                                                |
| `ModuleNotFoundError`                 | Run `uv sync` (add `--extra dev` for tests) from the project root                                                        |
| Database locked error                 | Stop any other running server instance before starting a new one                                                         |
| Keystore not found on login           | You must register on this device first — keys are stored locally in `~/.comp3334im/<username>/`                          |
| `.env.local` already exists           | Delete it first: `rm .env.local`, then re-run `./scripts/bootstrap-env.sh`                                               |
| `bootstrap-env.sh: Permission denied` | Run `chmod +x scripts/bootstrap-env.sh` first                                                                            |

---

## 11. File Locations

| File                                     | Purpose                                                  |
| ---------------------------------------- | -------------------------------------------------------- |
| `scripts/bootstrap-env.sh`               | Auto-generates `.env.local` with random secrets          |
| `.env.local`                             | Server configuration (secrets, ports) — **never commit** |
| `certs/server.crt`                       | TLS certificate                                          |
| `certs/server.key`                       | TLS private key                                          |
| `~/.comp3334im/<username>/keystore.json` | Encrypted local key storage (client)                     |
| `~/.comp3334im/<username>/sessions.json` | Encrypted session state (client)                         |
| `~/.comp3334im/<username>/messages.db`   | Local message history (client)                           |
| `/app/data/im.db` (Docker)               | Server SQLite database                                   |
