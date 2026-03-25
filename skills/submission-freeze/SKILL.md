---
name: submission-freeze
description: Use when preparing a branch for submission or main-branch freeze, especially when you must separate submission-critical fixes from CI/tooling noise, verify the exact merge set, communicate source-of-truth changes, and merge safely into main.
---

# Submission Freeze

## Overview

Use this skill when the repo is near submission, release freeze, or report
freeze and you need disciplined control over what reaches `main`, including the
git branch and merge mechanics needed to prove what will actually land.

Core rule:

```text
Content freeze first. Tooling cleanup second.
```

Do not let CI polish, agent-context files, or local workspace noise delay
submission-critical fixes.

## When to Use

- Freezing a branch for submission or report handoff
- Deciding whether a branch is safe to merge into `main`
- Auditing git branch ancestry and the true merge set before fast-forwarding
- Separating critical product/security fixes from workflow or hygiene changes
- Auditing a dirty workspace before merge
- Writing freeze notes, sync messages, or merged-change summaries

## Required Inputs

Inspect these first:

```text
git status --short --branch
git branch --all --verbose --no-abbrev
git log --graph --oneline --decorate --all --max-count=40
git merge-base main <candidate-branch>
git rev-list --left-right --count main... <candidate-branch>
git diff --stat main.. <candidate-branch>
git diff --name-only
git ls-files --others --exclude-standard
```

If the current workspace is dirty, do not merge from it directly. Use an
isolated worktree.

## Git Branch Reality Check

Before planning any merge, answer these questions with git evidence:

- What branch am I actually on?
- How many commits is the candidate branch ahead of `main`?
- Is the candidate branch already carrying earlier prerequisite branches?
- Are the changes I care about committed on the branch, or only sitting dirty
  in the workspace?

Useful commands:

```bash
git merge-base main <candidate-branch>
git rev-list --left-right --count main...<candidate-branch>
git log --oneline main..<candidate-branch>
git diff --name-status main..<candidate-branch>
```

If the answer is "this branch includes more than the last fix commit", say that
explicitly in the freeze note and changelog.

## Freeze Workflow

### 1. Identify the real merge candidate

Confirm:

- current branch
- commits ahead of `main`
- whether prerequisite branches are already included
- whether local uncommitted changes are outside the candidate branch

Do not assume the last commit is the whole change set. Verify the full branch
history that will fast-forward into `main`.

If the branch already contains earlier security or review branches, treat the
entire reachable stack as the merge payload.

### 2. Separate committed history from dirty workspace state

Classify workspace changes into three buckets:

```text
Must merge now:
  submission-critical product, security, correctness, or user-facing fixes

Can defer:
  CI rollout, lint cleanup, docs polish, comments, formatting-only changes

Do not merge:
  temp files, local-only config, agent mirrors, half-done experiments
```

If the branch is already broadly correct but the workspace is dirty, move only
the must-merge set into an isolated merge-prep branch.

## Isolated Merge Practice

When the working tree is dirty, use two worktrees:

```text
merge-prep worktree:
  inspect, patch, verify, and stage the candidate branch safely

main worktree:
  fast-forward main, merge with --ff-only, tag, and push
```

This avoids polluting the original workspace and prevents accidental inclusion
of unstaged files.

### 3. Keep commit boundaries intentional

Preferred split:

```text
Commit A:
  product/security fixes that must reach main

Commit B:
  workflow, CI, packaging, or hygiene rollout
```

Do not mix:

- app behavior changes
- workflow policy changes
- agent context files
- personal/local configuration

### 4. Verify before any merge claim

Run fresh verification on the exact merge-prep branch, not on memory:

```bash
uv run --extra dev pytest -q
uv run --extra dev mypy client server
uv run python scripts/check_silent_excepts.py
uv run python scripts/check_stale_security_claims.py
```

If a new lint workflow fails on legacy debt, treat that as rollout policy to
fix separately. Do not block submission-critical content on broad style debt
unless the repo explicitly requires it.

### 5. Merge safely

Prefer:

```bash
git switch main
git fetch origin
git pull --ff-only origin main
git merge --ff-only <merge-prep-branch>
```

Safer when the original workspace is dirty:

```bash
git worktree add /tmp/<repo>-main-merge main
cd /tmp/<repo>-main-merge
git fetch origin
git merge --ff-only origin/main
git merge --ff-only <merge-prep-branch>
```

If `--ff-only` fails:

- stop
- inspect new commits on `main`
- do not silently switch to `--no-ff`

Use an isolated worktree for `main` if the original workspace is dirty.

### 6. Tag and communicate source of truth

After merge, create a stable tag when appropriate:

```bash
git tag submission-base
git push origin main --tags
```

Then send a short sync note making `main` the source of truth.

Minimum message:

```text
main has been updated; test from main only.
This merge includes the latest submission/security branch history, not just one final fix commit.
If you still have an old branch, fetch origin and pull main before testing.
```

## Release-Control Heuristics

- A green product/security verification set beats a perfect lint rollout near submission.
- Fast-forward cleanliness matters, but only after you confirm the branch
  ancestry and actual merge payload.
- Communication is part of release control, not optional cleanup.
- If teammates are likely to test stale branches, write the freeze note immediately.
- Record whether `main` includes only one fix commit or an entire prior branch stack.

## Common Mistakes

- Treating the last commit as the full merge scope
- Assuming "merge this branch" means "merge only the last thing I touched"
- Merging from a dirty workspace
- Using `--no-ff` immediately after `--ff-only` fails instead of inspecting why
- Blocking submission on broad legacy lint debt
- Mixing agent context or local config into a submission branch
- Forgetting to tell the team that `main` is now the testing baseline
- Claiming "ready to merge" without fresh verification evidence
