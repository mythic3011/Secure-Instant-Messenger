# Plan: Caddy Infrastructure Upgrade

**Date:** 2026-03-24
**Status:** Pending implementation

## Goal

Replace uvicorn's self-managed TLS with Caddy as a reverse proxy. Caddy handles TLS termination, HTTP→HTTPS redirect, and serves on standard ports (80/443). uvicorn runs plain HTTP internally.

## New Topology

```
Client (https://localhost or https://<domain>)
    │  :443 HTTPS / WSS
    ▼
[Caddy]  ← TLS termination, HTTP→HTTPS redirect, auto cert
    │  plain HTTP :8000 (internal Docker network only)
    ▼
[uvicorn / FastAPI]
    │
    ▼
[SQLite volume]
```

## Files to Change

### 1. `Caddyfile` (new)
- `local_certs` global option → Caddy internal CA for localhost dev
- `localhost { reverse_proxy server:8000 }` — proxies all HTTP + WebSocket
- HTTP :80 redirect to HTTPS is automatic, no config needed

### 2. `docker-compose.yml`
- Add `caddy` service: image `caddy:2.9-alpine`, ports `80:80` and `443:443`, mounts `Caddyfile` + a `caddy_data` volume for cert persistence
- `server` service: remove `ports` (no longer exposed externally), change internal port to `8000`, remove OrbStack TLS label
- Both services on `internal` network so Caddy can reach `server:8000`

### 3. `Dockerfile.server`
- Remove `openssl` apt install (no longer needed)
- Remove `TLS_CERT_FILE` / `TLS_KEY_FILE` env vars
- Change `EXPOSE` from `8443` to `8000`
- Healthcheck hits `http://localhost:8000/health` (plain HTTP)

### 4. `scripts/docker-entrypoint.sh`
- Remove all cert generation logic
- Keep only `mkdir -p /app/data` + `exec "$@"`

### 5. `server/core/config.py`
- Remove `tls_cert_file` / `tls_key_file` fields
- Change default `port` to `8000`

### 6. `server/main.py`
- Remove TLS cert detection block in `__main__`
- `uvicorn.run(...)` always without `ssl_certfile`/`ssl_keyfile`

### 7. `client/main.py`
- Change default server URL from `https://localhost:8443` → `https://localhost`
- Auto-disable-verify-tls probe: update port from `8443` → `443`

### 8. `.env.example`
- `PORT=8000`
- `SERVER_URL=https://localhost`
- Remove `TLS_CERT_FILE` / `TLS_KEY_FILE` lines

### 9. `docs/DEPLOY.md`
- Remove "Generate a Self-Signed TLS Certificate" section (step 5)
- Update server URL references from `:8443` → no port
- Update client run command default URL
- Add note: Caddy auto-issues cert via internal CA; client still needs `--no-verify-tls` or to trust Caddy's root CA for localhost

### 10. `docs/ARCHITECTURE.md`
- Update §11 Stack to mention Caddy
- Update §13.2/13.3 port references
- Update Docker section to reflect Caddy topology

## Client TLS Note

Caddy's internal CA cert isn't in the system trust store by default. For localhost dev, two options:
1. Keep existing `--no-verify-tls` auto-detect in `client/main.py` (already handles `SSLCertVerificationError`) — no change needed
2. Export Caddy's root CA and pass `--ca-cert` — better but requires extra setup step

The existing auto-detect logic already handles this gracefully, so no client crypto code changes needed.

## What Doesn't Change

- All E2E crypto (X25519, AES-GCM, etc.)
- Auth token flow
- WebSocket first-frame auth
- All API routes
- Test suite (integration tests spin up their own in-process server, bypass Docker entirely)
