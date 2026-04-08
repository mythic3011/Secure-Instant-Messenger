# Scripts Taxonomy

This directory is zoned by runtime class.

## Public Entrypoints

- `../install.sh`
- `launch/run-client.sh`
- `launch/run-client.bat`
- `launch/run-server.sh`
- `launch/run-server.bat`

The legacy files at `scripts/run-client.*` and `scripts/run-server.*` are
compatibility shims only. They preserve exit code and argument forwarding, but
no logic should be added there.

## Launcher-Internal

- `launch/check-server-health.sh`

This file supports launcher preflight flow and is not intended for direct use.

## Install Helpers

- `install/check-prereqs.sh`
- `install/install-prereqs.sh`
- `install/bootstrap-env.sh`
- `install/bootstrap-env.bat`

These are internal orchestration helpers behind `../install.sh`.

## Shared Python Helpers

- `lib/check_server_health.py`
- `lib/bootstrap_env.py`

These support shell and batch wrappers and are not user-facing entrypoints.
