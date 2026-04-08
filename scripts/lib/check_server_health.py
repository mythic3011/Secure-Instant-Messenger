from __future__ import annotations

import argparse
import json
import ssl
import sys
from urllib import error, parse, request


def _health_url(server_url: str) -> str:
    parsed = parse.urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Malformed server URL.")
    path = parsed.path.rstrip("/")
    target_path = f"{path}/health" if path else "/health"
    return parse.urlunparse((parsed.scheme, parsed.netloc, target_path, "", "", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check server /health endpoint.")
    parser.add_argument("--server", required=True, help="Server base URL")
    parser.add_argument("--ca-cert", default=None, help="Custom CA certificate")
    parser.add_argument("--timeout", type=float, default=3.0, help="Timeout in seconds")
    args = parser.parse_args()

    try:
        health_url = _health_url(args.server)
    except ValueError as exc:
        print(f"[health] {exc}", file=sys.stderr)
        return 2

    context = None
    if health_url.startswith("https://"):
        if args.ca_cert:
            context = ssl.create_default_context(cafile=args.ca_cert)
        else:
            context = ssl.create_default_context()

    req = request.Request(health_url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with request.urlopen(req, timeout=args.timeout, context=context) as resp:  # noqa: S310
            status = resp.getcode()
            body = resp.read()
    except error.HTTPError as exc:
        print(f"[health] Health endpoint returned HTTP {exc.code}.", file=sys.stderr)
        return 1
    except ssl.SSLError as exc:
        print(f"[health] TLS verification failed: {exc}.", file=sys.stderr)
        return 1
    except error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            print(f"[health] TLS verification failed: {reason}.", file=sys.stderr)
            return 1
        print(f"[health] Server unreachable: {reason}.", file=sys.stderr)
        return 1

    if status != 200:
        print(f"[health] Health endpoint returned HTTP {status}.", file=sys.stderr)
        return 1

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("[health] Health endpoint returned non-JSON response.", file=sys.stderr)
        return 1

    if payload.get("status") != "ok":
        print(f"[health] Unexpected health payload: {payload!r}.", file=sys.stderr)
        return 1

    print(f"[health] ok: {health_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
