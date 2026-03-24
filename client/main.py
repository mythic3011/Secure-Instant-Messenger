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
    parser.add_argument(
        "--verify-tls", dest="verify_tls", action="store_true", default=True,
        help="Verify server TLS certificate (default: enabled)",
    )
    parser.add_argument(
        "--no-verify-tls", dest="verify_tls", action="store_false",
        help="Disable TLS certificate verification (insecure; use --ca-cert instead)",
    )
    parser.add_argument(
        "--ca-cert", default=None,
        help="Path to CA cert or self-signed cert to trust (use instead of --no-verify-tls)",
    )
    parser.add_argument(
        "--pin-cert", default=None,
        help="Expected SHA256 fingerprint of server cert (hex, 64 chars) for cert pinning",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir) if args.log_dir else Path("logs")
    _setup_logging(log_dir, verbose=args.verbose)

    server = args.server or os.environ.get("SERVER_URL") or "https://localhost:8443"

    # Auto-detect scheme: if user passed bare host or http://, warn and upgrade
    if server.startswith("http://"):
        log = logging.getLogger(__name__)
        log.warning(
            "Server URL uses http:// — upgrading to https:// "
            "(the server requires TLS). Use --server https://... to suppress this warning."
        )
        server = server.replace("http://", "https://", 1)

    username = args.username or os.environ.get("IM_USERNAME") or ""

    # Auto-disable TLS verification for localhost self-signed certs in dev
    verify_tls = args.verify_tls
    ca_cert = args.ca_cert
    if verify_tls and not ca_cert and "localhost" in server:
        # Check if the server cert is self-signed (untrusted) before connecting
        import socket
        import ssl
        from urllib.parse import urlparse
        parsed = urlparse(server)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8443
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=3) as sock:
                with ctx.wrap_socket(sock, server_hostname=host):
                    pass  # cert is trusted, no action needed
        except ssl.SSLCertVerificationError:
            log = logging.getLogger(__name__)
            log.warning(
                "Server at %s uses a self-signed certificate. "
                "Auto-disabling TLS verification for dev convenience. "
                "Use --ca-cert to trust a specific cert instead.",
                server,
            )
            verify_tls = False
        except (TimeoutError, OSError):
            pass  # server not reachable yet, let the app handle it

    log = logging.getLogger(__name__)
    log.info("Starting client — server=%s username=%s verify_tls=%s", server, username or "(none)", verify_tls)

    from client.ui.app import IMApp
    try:
        IMApp(
            server_url=server,
            username=username,
            verify_tls=verify_tls,
            ca_cert=ca_cert,
            pin_sha256=args.pin_cert,
        ).run()
    except KeyboardInterrupt:
        log.info("Client shut down by user (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
