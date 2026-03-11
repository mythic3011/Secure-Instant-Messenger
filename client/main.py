"""
client/main.py — CLI entrypoint for the COMP3334 Secure IM client.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path


def _setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Configure root logger: DEBUG to rotating file, WARNING to stderr."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "client.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Rotating file handler — keeps last 5 × 1 MB files
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=1 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root.addHandler(fh)

    # Console handler — WARNING by default, DEBUG if --verbose
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(sh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    log = logging.getLogger(__name__)
    log.debug("Logging initialised — file: %s", log_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="COMP3334 Secure IM Client")
    parser.add_argument("--server", default=None, help="Server base URL (or set SERVER_URL env)")
    parser.add_argument("--username", default=None, help="Username (prompted if omitted or from USERNAME env)")
    parser.add_argument("--log-dir", default=None, help="Directory for log files (default: ./logs)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print DEBUG logs to stderr as well")
    args = parser.parse_args()

    log_dir = Path(args.log_dir) if args.log_dir else Path("logs")
    _setup_logging(log_dir, verbose=args.verbose)

    server = args.server or os.environ.get("SERVER_URL") or "http://localhost:8443"
    username = args.username or os.environ.get("IM_USERNAME")
    if not username:
        username = input("Username: ").strip()
        if not username:
            print("Username required.")
            sys.exit(1)

    log = logging.getLogger(__name__)
    log.info("Starting client — server=%s username=%s", server, username)

    from client.ui.app import IMApp
    try:
        IMApp(server_url=server, username=username).run()
    except KeyboardInterrupt:
        log.info("Client shut down by user (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
