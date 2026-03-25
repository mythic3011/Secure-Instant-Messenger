# Branch Protection Note

Date: 2026-03-26
Status: Active repo management baseline with temporary protection fallback
Scope: pull request checks, review routing, auto labeling, phantom-check troubleshooting

## Goal

Keep merge policy narrow and predictable during submission hardening.

This note documents the minimum GitHub-side protections that fit the current CI
layout without reopening workflow scope.

## Recommended Required Checks

Stable target state for `main`:

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

## Temporary State

The repo hit a real phantom required-check problem during submission hardening.

Observed issue:

- branch protection required `Security`, but the PR only reported
  `GitGuardian Security Checks`
- branch protection later required `CI`, but the PR head commit reported no
  matching status context

Temporary operational state:

- keep review requirement enabled
- keep conversation resolution enabled
- do not require a status context that is not actually reported on the PR head
  commit

Restore required checks only after the workflow names and reported contexts are
confirmed stable.

## Ruleset Caution

Do not mark a path-filtered or conditionally-skipped workflow as a required
status check.

If a required check does not run, GitHub can block merge because the expected
status never appears.

Use stable workflow names and require only checks that always report on the PR
paths you expect to merge.

## Phantom Check Troubleshooting

When a PR shows `Expected — Waiting for status to be reported`:

1. inspect branch protection required contexts
2. inspect the PR head commit statuses
3. inspect the PR head commit check runs
4. compare the names exactly

If the required context does not appear:

- treat it as a stale or mismatched protection rule
- do not keep debugging product code as if the failure were real
- either remove the requirement temporarily or rename it to a real reporting
  check

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

## Canonical References

- `knowledge/projects/comp3334-secure-im/REPO_WORKFLOW.md`
- `skills/repo-governance/SKILL.md`
- `docs/notes/git-pr-workflow-note.md`
