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

Fallback (if `uv` is unavailable):
```bash
pip install -e .
```

---

## 4. Configure Environment

```bash
cp .env.example .env.local
```

Generate two random secret keys and paste them into `.env.local`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# Run twice — one value for TOKEN_SECRET_KEY, one for TOTP_ENCRYPTION_KEY
```

Edit `.env.local` and replace the placeholder values:

```
TOKEN_SECRET_KEY=<paste first value here>
TOTP_ENCRYPTION_KEY=<paste second value here>
```

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
5. A TOTP provisioning URI will appear — scan it with an authenticator app:
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
# Unit tests (no server needed)
uv run pytest tests/unit/ -v

# Integration tests (uses in-memory test server)
uv run pytest tests/integration/ -v

# Security tests (replay attack, tampering)
uv run pytest test_replay_attack.py -v

# All tests
uv run pytest -v
```

---

## 10. Troubleshooting

| Problem | Solution |
|---------|----------|
| Port 8443 already in use | Change `PORT=8443` in `.env.local` to another port, e.g. `8444` |
| TLS certificate errors in client | Expected for self-signed certs — client uses `verify=False` in dev mode |
| TOTP code rejected | Ensure your system clock is accurate. Ubuntu: `timedatectl set-ntp true`. Windows: Settings → Time & Language → Sync now |
| Docker permission denied (Linux) | Run `sudo usermod -aG docker $USER` then log out and back in |
| `uv: command not found` | Restart terminal after installing `uv`, or use `pip install -e .` instead |
| `ModuleNotFoundError` | Run `uv sync` from the project root to install all dependencies |
| Database locked error | Stop any other running server instance before starting a new one |
| Keystore not found on login | You must register on this device first — keys are stored locally in `~/.comp3334im/<username>/` |

---

## 11. File Locations

| File | Purpose |
|------|---------|
| `.env.local` | Server configuration (secrets, ports) — never commit |
| `certs/server.crt` | TLS certificate |
| `certs/server.key` | TLS private key |
| `~/.comp3334im/<username>/keystore.json` | Encrypted local key storage (client) |
| `~/.comp3334im/<username>/sessions.json` | Encrypted session state (client) |
| `~/.comp3334im/<username>/messages.db` | Local message history (client) |
| `/app/data/im.db` (Docker) | Server SQLite database |
