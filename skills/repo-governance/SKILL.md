---
name: repo-governance
description: Use when configuring or troubleshooting GitHub PR flow, branch protection, required checks, CODEOWNERS, labels, PR templates, or stacked PR merge discipline in this repo.
---

# Repo Governance

## Overview

Use this skill when the task is about repository workflow rather than product
behavior: branch protection, PR metadata, CODEOWNERS, label automation,
required checks, or troubleshooting blocked merges.

## When to Use

- Adding or updating PR templates
- Adding or updating CODEOWNERS
- Adding path-based label automation
- Changing branch protection guidance
- Troubleshooting PRs blocked by missing or stale required checks
- Handling stacked PRs where the base branch diverged from `main`

## Core Rules

```text
1. Use stable check names before making them required.
2. Required checks must correspond to checks that actually report on PRs.
3. CODEOWNERS should stay narrow and intentional.
4. PR templates should force verification evidence, not filler.
5. Do not add bots or automation layers without a concrete review problem.
6. Prefer documenting temporary exceptions instead of hiding them.
```

## Phantom Required Check Troubleshooting

When a PR is blocked with "Expected — Waiting for status to be reported":

1. Compare branch protection required contexts with the PR head commit's
   reported statuses and check runs.
2. If the required name does not appear, treat it as a stale or mismatched
   protection rule.
3. Do not keep a phantom required check in place during submission hardening.
4. Either:
   - remove the required context temporarily, or
   - rename protection to match the real reporting check, or
   - restore a workflow that reports the expected name

Never assume a green third-party check means the required context is satisfied.
The names must match.

## Stacked PR Rule

If PR B sits on top of PR A and PR A diverges heavily from `main`:

```text
- do not blindly patch PR A or retarget PR B without checking merge shape
- simulate merges in an isolated worktree first
- keep the newer tested branch versions when resolving conflicts against older main-path files, unless a regression says otherwise
- re-run targeted and full verification before retargeting
```

## Minimum Repo Baseline

For this repo, the minimum useful governance set is:

```text
- path-based auto-labeling
- narrow CODEOWNERS
- branch protection note
- PR template with verification and workflow-impact sections
```

Non-goals by default:

```text
- auto-merge
- stale bot
- auto-close bots
- broad status-check sprawl
```
