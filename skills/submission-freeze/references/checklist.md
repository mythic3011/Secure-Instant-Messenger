# Submission Freeze Checklist

Use this reference when running a submission freeze or merge-to-main handoff.

## Merge Checklist

- Confirm the real merge candidate with `git log`, `merge-base`, and `rev-list`
- Confirm whether the branch carries earlier prerequisite branch history
- Triage dirty workspace changes into:
  - must merge now
  - can defer
  - do not merge
- Move only the must-merge set into an isolated merge-prep branch if needed
- Run fresh verification on the exact merge-prep branch:
  - `uv run --extra dev pytest -q`
  - `uv run --extra dev mypy client server`
  - `uv run python scripts/check_silent_excepts.py`
  - `uv run python scripts/check_stale_security_claims.py`
- Fast-forward `main` only after syncing `origin/main`
- If `--ff-only` fails, stop and inspect why
- Tag the frozen state if needed, for example `submission-base`

## Team Sync Template

```text
main 已更新，之後請全部人改用 main 測試。
今次 main 已包含最近一輪 submission / security branch 歷史，不只係單一 fix commit。

如有本地舊 branch，先：
git fetch origin
git switch main
git pull origin main

之後先再測關鍵流程。
如果再報 bug，請附上：
- branch name
- commit hash
- OS
- 重現步驟
- screenshot / error log
```

## Changelog Template

```text
Submission base:
Main updated to:

Included in this merge batch:
- [critical fix 1]
- [critical fix 2]
- [critical fix 3]

Verification at merge time:
- pytest:
- mypy:
- check_silent_excepts.py:
- check_stale_security_claims.py:

Notes:
- this merge includes [single commit / prior branch stack]
- post-submission follow-up:
```
