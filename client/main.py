"""
client/main.py — CLI entrypoint for the COMP3334 Secure IM client.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="COMP3334 Secure IM Client")
    parser.add_argument("--server", default=None, help="Server base URL (or set SERVER_URL env)")
    parser.add_argument("--username", default=None, help="Username (prompted if omitted or from USERNAME env)")
    args = parser.parse_args()

    # allow environment overrides
    server = args.server or os.environ.get("SERVER_URL") or "https://localhost:8443"
    username = args.username or os.environ.get("USERNAME")
    if not username:
        username = input("Username: ").strip()
        if not username:
            print("Username required.")
            sys.exit(1)

    from client.ui.app import IMApp
    try:
        IMApp(server_url=server, username=username).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
