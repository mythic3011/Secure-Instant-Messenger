# Git And PR Workflow Note

Date: 2026-03-26
Status: Active repository workflow baseline
Scope: branch flow, stacked PR handling, review and merge expectations

## Branch Flow

Default flow:

```text
main
  -> feature/fix branch
  -> focused PR
  -> review
  -> merge
```

Rules:

- keep branches short-lived
- keep PR scope narrow
- do not mix repo-governance work with unrelated product changes unless there is
  a direct merge blocker

## Stacked PR Flow

If one PR depends on another:

```text
main
  -> PR A base branch
  -> PR B follow-up branch
```

Required discipline:

- state the dependency in the PR body
- do not assume PR B can be retargeted safely without merge simulation
- use an isolated worktree to test `main` integration before pushing branch
  rewrites or retargeting

## Merge Order

Preferred order:

1. Merge the base PR if possible.
2. Rebase or merge `main` into the follow-up branch.
3. Re-run verification.
4. Retarget the follow-up PR only when the branch is already integration-safe.

## Canonical References

- `knowledge/projects/comp3334-secure-im/REPO_WORKFLOW.md`
- `docs/notes/branch-protection-note.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
