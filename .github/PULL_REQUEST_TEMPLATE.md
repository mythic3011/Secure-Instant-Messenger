## Summary

- what changed
- what did not change

## Why

- why this PR exists
- what problem it solves

## Change / Contract Impact

- [ ] no behavior change
- [ ] changes app behavior / expected flow
- [ ] changes implementation or use-case contract
- [ ] changes source of truth

If any box other than "no behavior change" applies, explain:

```text
What changed?
What is now the source of truth?
What old assumption / contract is no longer valid?
```

## Scope

- [ ] single bug / single scope
- [ ] stacked / dependent on another issue or PR

If this PR is tied to a bug report, confirm:

- [ ] issue scope is clean and matches this implementation
- [ ] no mixed root causes remain in the issue body
- [ ] outdated speculation / analysis has been removed

## Verification

- [ ] related tests were updated or added if behavior / contract changed
- [ ] all related tests pass
- [ ] test commands actually run are listed below
- [ ] `git diff --check`

List any additional commands actually run:

```text
<command output summary>
```

## Merge Readiness

- [ ] ready to merge
- [ ] not merge-ready yet

A PR is not merge-ready if:

- app behavior / contract changed but related tests were not updated
- source of truth changed but was not made explicit
- CI is still failing

## Workflow / Protection Impact

- [ ] no GitHub workflow or protection impact
- [ ] changes GitHub workflows
- [ ] changes labels or CODEOWNERS
- [ ] changes branch protection assumptions

If any box other than "no impact" applies, explain:

```text
<what changed and what reviewers should check>
```

## Stacked PR Note

- [ ] not stacked
- [ ] stacked on another PR

If stacked, link the base PR and state merge order:

```text
Base PR: #
Merge order:
```
