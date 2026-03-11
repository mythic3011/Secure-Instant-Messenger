# COMP3334 Secure Instant Messenger

This repository contains the code for a secure, end‑to‑end encrypted instant messaging application developed as part of the COMP3334 project. The system is designed to demonstrate strong cryptographic practices, replay protection and an honest‑but‑curious server model.

## Features

- End‑to‑end encryption using X25519/AES‑256‑GCM
- Identity keys (Ed25519) and per‑conversation sessions
- Offline message queue with ciphertext blobs only
- Argon2id password hashing and TOTP-based 2FA
- Python implementation with SQLite backend (server) and a lightweight client
- Test suite covering unit and integration scenarios

## Repository structure

```
├── client/                # client application code (UI + crypto)
├── server/                # server application and API handlers
├── shared/                # protocol definitions shared between client/server
├── tests/                 # unit & integration tests
│   ├── unit/
│   └── integration/
├── Tutorial/              # notebooks and learning material
├── docs/                  # architecture & protocol documentation
├── pyproject.toml         # Python project configuration
├── Dockerfile.server      # container image for the server
├── docker-compose.yml     # development environment configuration
└── README.md              # this file
```

## Getting started

1. **Copy the environment file**: `cp .env.example .env.local` and fill in secrets
   generated with `python -c "import secrets; print(secrets.token_hex(32))"`.

2. **Create a virtual environment**:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. **Run database migrations** (server currently uses SQLite or Postgres configured
   via `DATABASE_URL`):

   ```bash
   # simple sqlite migration
   sqlite3 server/data/im.db < server/migrations/001_init.sql
   ```

4. **Start the server** (use the `uv` shortcut if installed):

   ```bash
   # uv is a lightweight entry point for uvicorn (install with `pip install uv`)
   uv server.main:app --reload --host 0.0.0.0 --port 8443
   ```

5. **Launch the client** (open `client/main.py` or run via UI):

   ```bash
   python client/main.py
   ```

   The client stores identity keys and session state in an encrypted local file.

## Testing

Execute the full test suite with:

```bash
pytest -q
```

## Architecture & Protocol

Detailed design decisions, protocol flows, and security mappings are described in
`docs/ARCHITECTURE.md`. That document includes diagrams, cryptographic choices,
and the trust model used for the honest‑but‑curious server.

## Development workflow

- Use `docker-compose up` for a containerised development stack.
- The client and server are written in Python; keep dependencies pinned in
  `pyproject.toml`.
- Add new features with corresponding unit tests and update integration tests
  under `tests/integration`.

## Contribution guidelines

1. Create a new branch for each feature or bugfix.
2. Run the existing tests and add new ones for any changes.
3. When the implementation is complete and tests pass, open a pull request and
   ensure at least one teammate reviews the changes.

## License

This project is for academic purposes and is not intended for production use.
Please refer to your course guidelines regarding licensing and attribution.
