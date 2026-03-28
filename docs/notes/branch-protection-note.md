# Branch Protection Note

Date: 2026-03-26
Status: Recommended repo management baseline
Scope: pull request checks, review routing, auto labeling

## Goal

Keep merge policy narrow and predictable during submission hardening.

This note documents the minimum GitHub-side protections that fit the current CI
layout without reopening workflow scope.

## Recommended Required Checks

Require these pull request checks on `main`:

- `CI`
- `Lint`
- `Security`

Do not require these for pull requests:

- `Package`
- `Deploy Smoke`

Reason:

- `Package` is release/package sanity, not correctness gating.
- `Deploy Smoke` is intentionally slower and should not add merge friction to
  every PR.

## Ruleset Caution

Do not mark a path-filtered or conditionally-skipped workflow as a required
status check.

If a required check does not run, GitHub can block merge because the expected
status never appears.

Use stable workflow names and require only checks that always report on the PR
paths you expect to merge.

## Auto Labeling

The repo now includes:

- `.github/workflows/autolabel.yml`
- `.github/labeler.yml`

Design choices:

- uses `pull_request_target` so labels can still be written for fork PRs
- applies path-based labels only
- does not remove manually-added labels

Labels expected to exist in the repository:

- `ui`
- `client`
- `server`
- `security`
- `docs`
- `tests`
- `ci`

This is intentionally smaller than a full label taxonomy.

Size labels and other automation can be added later if there is a concrete
review need.

## CODEOWNERS

The repo now includes `.github/CODEOWNERS` for these paths:

- `.github/`
- `client/ui/`
- `server/`

This keeps review routing explicit without introducing a broader ownership map.

## Explicit Non-Goals

Not included in this baseline:

- auto-merge
- stale bot
- auto-close bots
- path-filtered required checks
- new workflow families beyond labeling

These add maintenance cost and are not needed for the current freeze baseline.
