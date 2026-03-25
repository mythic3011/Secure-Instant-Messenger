---
kind: knowledge
name: comp3334-secure-im-repo-workflow
scope: project
when_to_read: Read when changing GitHub workflows, branch protection, PR flow, labels, CODEOWNERS, or when a PR is blocked by expected-but-missing checks.
---

# COMP3334 Repo Workflow

## Purpose

Document the repository-side governance model that sits around the code:

- branch and PR flow
- branch protection expectations
- GitHub workflow boundaries
- auto-label and CODEOWNERS behavior
- troubleshooting for phantom required checks

## Workflow Boundaries

```text
CI            -> tests/correctness
Lint          -> linting, formatting, mypy, repo consistency scripts
Security      -> bandit, pip-audit, detect-secrets
Package       -> release/package artifact sanity
Deploy Smoke  -> slower Docker/runtime smoke validation
Auto Label    -> path-based PR labels only
```

Do not blur these concerns together.

## Branch and PR Flow

Preferred flow:

```text
main
  -> short-lived feature/fix branch
  -> focused PR
  -> review
  -> merge back to main
```

For stacked PRs:

```text
main
  -> base branch (PR A)
  -> follow-up branch on top (PR B)
```

Rules:

- keep the stack explicit in PR descriptions
- if PR B depends on PR A, say so directly
- if `main` diverges, simulate the merge in an isolated worktree before retargeting
- resolve conflicts in favor of the newer tested path unless a regression forces otherwise

## Branch Protection Guidance

Stable ideal policy:

```text
Require:
  - real check names only
  - 1 approving review
  - conversation resolution
```

Do not require:

```text
- path-filtered checks that do not always report
- workflow names that were renamed or deleted
- slow release/deploy workflows for ordinary PR merges
```

## Phantom Check Troubleshooting

When a PR shows "Expected — Waiting for status to be reported":

1. Read branch protection required contexts.
2. Read the PR head commit's legacy statuses.
3. Read the PR head commit's check runs.
4. Compare names exactly.

If the required context does not appear:

- do not keep debugging product code
- treat the protection rule as stale or mismatched
- either remove the context temporarily or restore a workflow that reports it

## Labels and Ownership

Path labels used in this repo:

```text
ui
client
server
security
docs
tests
ci
```

Ownership is intentionally narrow:

```text
.github/
client/ui/
server/
```

## PR Expectations

Every PR should say:

- what changed
- why it changed
- how it was verified
- whether workflows, labels, or protection assumptions were touched
