from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path


WEAK_VALUES = {
    "replace_with_random_64_hex_chars",
    "changeme",
    "secret",
    "password",
    "default",
    "example",
}


def update_assignment(text: str, name: str, value: str) -> str:
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, replacement, text, flags=re.M)
    return text.rstrip() + f"\n{replacement}\n"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    env_path = Path(".env.local")
    text = env_path.read_text(encoding="utf-8")

    current = dict(re.findall(r"^([A-Z_][A-Z0-9_]*)=(.*)$", text, flags=re.M))

    for name in ("TOKEN_SECRET_KEY", "TOTP_ENCRYPTION_KEY"):
        value = current.get(name, "")
        if mode == "production" or not value or len(value) < 64 or value.lower() in WEAK_VALUES:
            text = update_assignment(text, name, secrets.token_hex(32))

    app_env = "production" if mode == "production" else current.get("APP_ENV", "development") or "development"
    text = update_assignment(text, "APP_ENV", app_env)
    text = update_assignment(text, "DATABASE_URL", "sqlite+aiosqlite:///./data/im.db")
    text = update_assignment(text, "TLS_CERT_FILE", "./certs/server.crt")
    text = update_assignment(text, "TLS_KEY_FILE", "./certs/server.key")

    env_path.write_text(text.strip() + "\n", encoding="utf-8")
    print(".env.local updated for", mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
