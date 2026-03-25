#!/usr/bin/env python3
"""
scripts/seed.py — Seed mock users, friendships, and messages for dev/demo.

Usage:
    uv run python scripts/seed.py                        # default server
    uv run python scripts/seed.py --server https://localhost:8443
    uv run python scripts/seed.py --server https://localhost:8443 --clean

Custom users: edit SEED_USERS below.

TOTP QR codes are saved to /tmp/comp3334_totp_<username>.md
Credentials summary saved to /tmp/comp3334_seed_credentials.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# ── Custom user config ────────────────────────────────────────────────────────
# Edit this list to add/remove seed users.
# password: plain text (will be sent to /register, stored as argon2id hash)
# friends:  list of usernames this user should be friends with (mutual)
# messages: list of (recipient_username, message_text) tuples

SEED_USERS: list[dict] = [
    {
        "username": "alice",
        "password": "Alice@12345",
        "display_name": "Alice Wong",
        "friends": ["bob", "charlie"],
        "messages_to": [
            ("bob", "Hey Bob! This is Alice. Can you hear me? 🔒"),
            ("bob", "E2EE is working great on this system!"),
            ("charlie", "Hi Charlie, welcome to the secure channel."),
        ],
    },
    {
        "username": "bob",
        "password": "Bob@12345",
        "display_name": "Bob Chan",
        "friends": ["alice", "charlie"],
        "messages_to": [
            ("alice", "Alice! Loud and clear. The encryption looks solid."),
            ("charlie", "Charlie, did you get Alice's message?"),
        ],
    },
    {
        "username": "charlie",
        "password": "Charlie@12345",
        "display_name": "Charlie Lee",
        "friends": ["alice", "bob"],
        "messages_to": [
            ("alice", "Got it Alice! Keys exchanged successfully."),
            ("bob", "Bob, all messages verified. Replay protection is on."),
        ],
    },
    {
        "username": "dave",
        "password": "Dave@12345",
        "display_name": "Dave Ng",
        "friends": ["alice"],
        "messages_to": [
            ("alice", "Alice, this is Dave. Just joined the network."),
        ],
    },
]

# ── Globals ───────────────────────────────────────────────────────────────────
TMP_DIR = Path("/tmp")
CRED_FILE = TMP_DIR / "comp3334_seed_credentials.md"
TOTP_STATE_FILE = TMP_DIR / "comp3334_seed_totp_state.json"

# ssl context — accepts self-signed certs
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _load_totp_secrets() -> dict[str, str]:
    """Load persisted TOTP secrets from previous runs (needed to re-login existing users)."""
    if TOTP_STATE_FILE.exists():
        try:
            return json.loads(TOTP_STATE_FILE.read_text())
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("unexpected error", exc_info=exc)
            raise
    return {}


def _save_totp_secrets(secrets: dict[str, str]) -> None:
    TOTP_STATE_FILE.write_text(json.dumps(secrets, indent=2))


# ── Colours ───────────────────────────────────────────────────────────────────
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"


def info(msg: str) -> None:
    print(f"{GREEN}[seed]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[seed]{NC} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[seed]{NC} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}── {msg} ──{NC}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────
@dataclass
class Session:
    username: str
    password: str
    token: str = ""
    user_id: str = ""
    totp_secret: str = ""


async def api(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str = "",
    **kwargs: Any,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await client.request(method, path, headers=headers, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} → HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


# ── QR code helper ────────────────────────────────────────────────────────────
def _totp_uri(username: str, secret: str, issuer: str = "COMP3334-IM") -> str:
    return f"otpauth://totp/{issuer}:{username}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"


def save_totp_md(username: str, secret: str) -> Path:
    """Save TOTP secret + QR URI as a markdown file in /tmp."""
    uri = _totp_uri(username, secret)
    out = TMP_DIR / f"comp3334_totp_{username}.md"

    qr_block = ""
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        import io

        buf = io.StringIO()
        qr.print_ascii(out=buf)
        qr_block = f"\n```\n{buf.getvalue()}```\n"
    except ImportError:
        qr_block = "\n_(install `qrcode` for ASCII QR: `uv add qrcode`)_\n"

    md = f"""# TOTP Setup — {username}

Scan this in Google Authenticator / Aegis / any TOTP app.

**Secret:** `{secret}`

**URI:**
```
{uri}
```
{qr_block}
> Generated by `scripts/seed.py` on {time.strftime("%Y-%m-%d %H:%M:%S")}
"""
    out.write_text(md)
    return out


# ── Crypto helpers (minimal — uploads Ed25519 + X25519 keys) ─────────────────
def _gen_key_bundle() -> dict:
    """
    Generate a minimal key bundle for seed users.
    Returns dict with identity_pub_b64, dh_pub_b64, key_sig_b64.
    Uses the same crypto as client/crypto/session.py.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ik = Ed25519PrivateKey.generate()
    dh = X25519PrivateKey.generate()

    ik_pub_bytes = ik.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    dh_pub_bytes = dh.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    # Sign (identity_pub || dh_pub) with identity key
    sig = ik.sign(ik_pub_bytes + dh_pub_bytes)

    return {
        "identity_pub_b64": base64.b64encode(ik_pub_bytes).decode(),
        "dh_pub_b64": base64.b64encode(dh_pub_bytes).decode(),
        "key_sig_b64": base64.b64encode(sig).decode(),
    }


# ── Seed logic ────────────────────────────────────────────────────────────────
async def register_user(
    client: httpx.AsyncClient,
    user: dict,
    totp_secrets: dict[str, str],
) -> Session:
    """
    Register (if needed) and login a seed user.
    totp_secrets is a mutable dict used to persist TOTP secrets across re-runs.
    """
    username = user["username"]
    password = user["password"]
    totp_secret = ""

    # Try register — includes key bundle in the same request
    bundle = _gen_key_bundle()
    try:
        resp = await api(
            client,
            "POST",
            "/v1/auth/register",
            json={
                "username": username,
                "password": password,
                **bundle,
            },
        )
        # Server returns totp_provisioning_uri, extract secret from it
        uri = resp.get("totp_provisioning_uri", "")
        # URI format: otpauth://totp/ISSUER:user?secret=BASE32SECRET&...
        import urllib.parse

        qs = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
        totp_secret = qs.get("secret", [""])[0]
        totp_secrets[username] = totp_secret
        totp_file = save_totp_md(username, totp_secret)
        info(f"  Registered {username!r} | TOTP → {totp_file}")
    except RuntimeError as exc:
        if "409" in str(exc) or "already" in str(exc).lower():
            warn(f"  {username!r} already exists — skipping register")
            totp_secret = totp_secrets.get(username, "")
            if not totp_secret:
                error(
                    f"  No saved TOTP secret for {username!r} — cannot login. Run with --clean to reset."
                )
                raise RuntimeError(
                    f"No TOTP secret for existing user {username!r}"
                ) from exc
        else:
            raise

    # Generate TOTP code for login
    try:
        import pyotp

        totp_code = pyotp.TOTP(totp_secret).now()
    except ImportError:
        error("pyotp is required for login. Run: uv add pyotp")
        raise

    resp = await api(
        client,
        "POST",
        "/v1/auth/login",
        json={
            "username": username,
            "password": password,
            "totp_code": totp_code,
        },
    )
    token = resp["access_token"]
    info(f"  Logged in  {username!r}")

    # Fetch user_id from keys endpoint (server doesn't return it on login)
    keys_resp = await api(client, "GET", f"/v1/keys/{username}", token=token)
    user_id = keys_resp.get("user_id", "")
    info(f"  Resolved   {username!r} → user_id={user_id[:8]}…")

    return Session(
        username=username,
        password=password,
        token=token,
        user_id=user_id,
        totp_secret=totp_secret,
    )


async def setup_friendships(
    client: httpx.AsyncClient,
    sessions: dict[str, Session],
    users: list[dict],
) -> None:
    """Send + auto-accept all friend requests."""
    done: set[frozenset] = set()

    for user in users:
        uname = user["username"]
        sess = sessions[uname]
        for friend in user.get("friends", []):
            pair = frozenset({uname, friend})
            if pair in done:
                continue
            done.add(pair)

            if friend not in sessions:
                warn(f"  Friend {friend!r} not in seed list — skipping")
                continue

            fsess = sessions[friend]

            # Send request
            try:
                resp = await api(
                    client,
                    "POST",
                    "/v1/friends/request",
                    token=sess.token,
                    json={"recipient_username": friend},
                )
                req_id = resp.get("id", "")
                info(f"  Friend request {uname!r} → {friend!r} (id={req_id[:8]}…)")

                # Auto-accept from the other side
                await api(
                    client,
                    "PUT",
                    f"/v1/friends/request/{req_id}",
                    token=fsess.token,
                    json={"action": "accept"},
                )
                info(f"  Accepted by {friend!r}")
            except RuntimeError as exc:
                if "409" in str(exc) or "already" in str(exc).lower():
                    warn(f"  {uname!r} ↔ {friend!r} already friends")
                else:
                    warn(f"  Friendship {uname!r}↔{friend!r} skipped: {exc}")


def _encrypt_seed_message(text: str) -> tuple[str, str]:
    """
    Encrypt seed message text with AES-256-GCM using a random key.
    Returns (nonce_b64, ciphertext_b64).
    This is real encryption but with a throwaway key — seed data only.
    """
    import base64
    import os as _os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _os.urandom(32)
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, text.encode(), None)
    return base64.b64encode(nonce).decode(), base64.b64encode(ct).decode()


async def send_mock_messages(
    client: httpx.AsyncClient,
    sessions: dict[str, Session],
    users: list[dict],
) -> None:
    """
    Send mock messages via the API using the correct MessageEnvelope format.
    Messages are encrypted with a throwaway key (seed data — not real E2EE).
    """
    import uuid

    # Build a lookup of (user_a_id, user_b_id) → conv_id from server
    conv_id_cache: dict[tuple[str, str], str] = {}
    for uname, sess in sessions.items():
        try:
            resp = await api(client, "GET", "/v1/conversations", token=sess.token)
            for conv in resp.get("conversations", []):
                a, b = sorted([sess.user_id, conv["peer_id"]])
                conv_id_cache[(a, b)] = conv["id"]
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("unexpected error", exc_info=exc)
            raise

    # Per-pair counters so replay protection doesn't reject sequential messages
    counters: dict[tuple[str, str], int] = {}

    for user in users:
        uname = user["username"]
        sess = sessions.get(uname)
        if sess is None:
            continue

        for recipient, text in user.get("messages_to", []):
            rsess = sessions.get(recipient)
            if rsess is None:
                warn(f"  Recipient {recipient!r} not in seed list — skipping")
                continue

            a, b = sorted([sess.user_id, rsess.user_id])
            conv_id = conv_id_cache.get((a, b))
            if conv_id is None:
                warn(f"  No conversation for {uname!r}↔{recipient!r} — skipping")
                continue

            pair_key = (sess.user_id, rsess.user_id)
            counter = counters.get(pair_key, 0)
            counters[pair_key] = counter + 1

            nonce_b64, ciphertext_b64 = _encrypt_seed_message(text)
            now = int(time.time())

            envelope = {
                "id": str(uuid.uuid4()),
                "type": "message",
                "sender_id": sess.user_id,
                "recipient_id": rsess.user_id,
                "conversation_id": conv_id,
                "counter": counter,
                "nonce_b64": nonce_b64,
                "ciphertext_b64": ciphertext_b64,
                "eph_pub_b64": None,
                "ttl_seconds": None,
                "sent_at": now,
            }

            try:
                await api(
                    client,
                    "POST",
                    "/v1/messages",
                    token=sess.token,
                    json={"envelope": envelope},
                )
                info(f"  Message {uname!r} → {recipient!r}: {text[:40]!r}")
            except RuntimeError as exc:
                warn(f"  Message failed {uname!r}→{recipient!r}: {exc}")


def save_credentials(users: list[dict], sessions: dict[str, Session]) -> Path:
    lines = [
        "# COMP3334 Seed Credentials\n",
        f"> Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        "| Username | Password | TOTP file | User ID |\n",
        "|----------|----------|-----------|----------|\n",
    ]
    for user in users:
        u = user["username"]
        s = sessions.get(u)
        totp_file = (
            f"`/tmp/comp3334_totp_{u}.md`" if s and s.totp_secret else "_(existing)_"
        )
        uid = s.user_id[:12] + "…" if s else "?"
        lines.append(f"| `{u}` | `{user['password']}` | {totp_file} | `{uid}` |\n")

    CRED_FILE.write_text("".join(lines))
    return CRED_FILE


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mock data for COMP3334 IM")
    parser.add_argument(
        "--server", default="https://localhost:8443", help="Server base URL"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Drop + reinit DB before seeding (requires docker)",
    )
    args = parser.parse_args()

    server = args.server.rstrip("/")

    if args.clean:
        warn("--clean: stopping containers, removing volumes, restarting…")
        os.system("docker compose down -v && docker compose up -d")
        info("Waiting 5s for server to start…")
        await asyncio.sleep(5)

    print(f"\n{BOLD}COMP3334 Seed Script{NC}")
    print(f"Server : {server}")
    print(f"Users  : {', '.join(u['username'] for u in SEED_USERS)}")

    async with httpx.AsyncClient(
        base_url=server,
        verify=False,
        timeout=15.0,
    ) as client:
        # Health check
        try:
            await api(client, "GET", "/health")
            info("Server reachable ✓")
        except Exception as exc:
            error(f"Server not reachable at {server}: {exc}")
            sys.exit(1)

        sessions: dict[str, Session] = {}
        # Persists TOTP secrets across re-runs (needed to login existing users)
        totp_secrets: dict[str, str] = _load_totp_secrets()

        header("Registering users")
        for user in SEED_USERS:
            try:
                sess = await register_user(client, user, totp_secrets)
                sessions[user["username"]] = sess
            except Exception as exc:
                error(f"  Failed to register {user['username']!r}: {exc}")

        _save_totp_secrets(totp_secrets)

        header("Setting up friendships")
        await setup_friendships(client, sessions, SEED_USERS)

        header("Sending mock messages")
        await send_mock_messages(client, sessions, SEED_USERS)

        header("Saving credentials")
        cred_path = save_credentials(SEED_USERS, sessions)
        info(f"Credentials → {cred_path}")
        for u in SEED_USERS:
            totp_path = TMP_DIR / f"comp3334_totp_{u['username']}.md"
            if totp_path.exists():
                info(f"TOTP QR     → {totp_path}")

    print(f"\n{GREEN}{BOLD}Seed complete!{NC}")
    print("\nLogin with any seed user:")
    for u in SEED_USERS:
        print(f"  {u['username']:<12} / {u['password']}")
    print(f"\nCredentials: {CRED_FILE}")
    print("TOTP files:  /tmp/comp3334_totp_<username>.md\n")


if __name__ == "__main__":
    asyncio.run(main())
