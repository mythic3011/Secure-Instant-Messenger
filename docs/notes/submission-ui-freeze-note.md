# Submission UI Freeze Note

Date: 2026-03-26
Status: Frozen for submission hardening
Scope: client UI contract, render cleanup, minimal widget extraction

## Freeze Decision

The client UI has reached the intended submission-ready state for this phase.

The following are now frozen:

- UI contract shape
- controller result semantics
- screen state ownership
- Phase 2 render cleanup on core screens
- minimal widget extraction set

## Frozen Rules

- contract is frozen
- render cleanup is frozen
- widgets are dumb presenters only
- no screen may reintroduce raw trust/security decision logic
- no further UI abstraction work unless a concrete duplication or bug is demonstrated
- post-freeze controller wiring fixes are allowed only when they restore frozen semantics, not when they redefine them

## What Is Frozen

### Contract

The UI contract is frozen.

Do not change:

- `client/ui/contracts.py` shape
- controller result semantics in `client/ui/app.py`
- screen state ownership boundaries

Exception only if:

- a proven bug exists
- a required state cannot be expressed and a test demonstrates the gap

### Render Cleanup

The following screens have completed the intended render/state cleanup for this phase:

- `client/ui/screens/chat.py`
- `client/ui/screens/settings.py`
- `client/ui/screens/conversations.py`

### Widget Extraction

The current minimal useful widget set is frozen:

- `client/ui/theme.py`
- `client/ui/widgets/banner.py`
- `client/ui/widgets/security_badge.py`

Widgets must remain presentation-only.

## Non-Negotiable Rules

- UI must not silently swallow security-relevant failure states.
- blocked send must keep draft intact.
- verify action must not optimistic-update persistent trust display.
- `key_changed` visibility must not be weakened by later styling changes.
- degraded state must not be rendered like success or normal empty state.

## Current State Summary

### Phase Status

- Phase 1 contract: complete
- Phase 2 render cleanup: core complete
- widget extraction: minimum useful set reached

### Test Summary

At freeze time, the following UI-focused test sets are green:

- `tests/unit/test_ui_security.py`
- `tests/unit/test_ui_widgets.py`
- full `tests/unit` pass

Latest local summary at freeze point:

- `84 passed, 1 warning`

Expected warning:

- `--no-verify-tls is insecure; use --ca-cert instead`

This warning is intentional and not treated as a freeze blocker.

## Allowed Work After Freeze

Allowed:

- focused review
- style drift checks
- before/after evidence capture
- documentation updates
- bug fixes with clear reproduction
- very small presentation-only polish that does not weaken visibility

Not allowed:

- new UI abstraction layers
- new widget families without demonstrated need
- controller/state ownership changes
- contract shape changes
- reintroduction of screen-local trust/security logic

## Evidence Requirement For Any Post-Freeze UI PR

Any UI PR after this freeze must include:

- before/after screenshot or terminal capture
- affected screen list
- note on whether any security-relevant state became more or less visible
- confirmation that existing UI security tests still pass
- confirmation that no security-relevant state became less visible

## Review Priority

If any further review happens before submission, focus only on:

- `chat.py`
- `settings.py`
- `conversations.py`
- `Banner`
- `SecurityBadge`

Do not expand scope beyond this list unless a concrete bug requires it.
