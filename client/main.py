"""
client/main.py — CLI entrypoint for the COMP3334 Secure IM client.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="COMP3334 Secure IM Client")
    parser.add_argument("--server", default="https://localhost:8443", help="Server base URL")
    parser.add_argument("--username", default=None, help="Username (prompted if omitted)")
    args = parser.parse_args()

    username = args.username
    if not username:
        username = input("Username: ").strip()
        if not username:
            print("Username required.")
            sys.exit(1)

    from client.ui.app import IMApp
    try:
        IMApp(server_url=args.server, username=username).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
