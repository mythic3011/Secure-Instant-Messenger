# Focused Review Checklist

Date: 2026-03-26
Status: Submission hardening checklist
Scope: review only, not new architecture work

## Review Goal

Confirm that the frozen UI contract and render cleanup still preserve:

- security visibility
- state clarity
- interaction safety
- presentation-only widget boundaries

This checklist is for final review, not for reopening design scope.

## Review Targets

- `client/ui/screens/chat.py`
- `client/ui/screens/settings.py`
- `client/ui/screens/conversations.py`
- `client/ui/widgets/banner.py`
- `client/ui/widgets/security_badge.py`

## 1. Contract Freeze Check

- `client/ui/contracts.py` shape has not changed.
- controller result semantics have not changed.
- no screen has reintroduced raw trust/security decision logic.
- widgets remain presentation-only.
- no new source of truth has been created in UI code.

## 2. Security Visibility Check

- `verified` remains visible where trust decisions matter.
- `key_changed` remains visible and visually stronger than non-security warnings.
- `requires_action` remains visible in both detail and summary surfaces.
- `local_history_unavailable` remains visible and is not mistaken for a normal empty state.
- degraded or blocked states are not styled like success states.
- summary indicators do not hide detail-level trust warnings.

## 3. State Clarity Check

- loading state is distinct.
- empty state is distinct from degraded state.
- degraded state is distinct from blocked state.
- blocked state is distinct from generic error state.
- chat message content is visually separated from metadata.
- TTL, delivery state, and trust cues remain readable.
- warning bars still render as real warnings, not decorative text.

## 4. Interaction Safety Check

- blocked send keeps draft.
- failed send keeps draft.
- successful send clears input only after success.
- verify action does not optimistic-update trust display.
- persistent warnings do not disappear on refresh or reopen unless state is actually resolved.
- disabled actions are visibly disabled, not merely inactive.

## 5. Widget Boundary Check

### Banner

- accepts render props only
- does not decide priority
- does not map exceptions
- does not infer security meaning

### SecurityBadge

- accepts render props only
- does not inspect raw trust state
- does not decide trust priority
- does not create a second logic path

## 6. Style Drift Check

- chat, settings, and conversations still feel like one visual system
- status colors mean the same thing across screens
- banner severity hierarchy is visually consistent
- badge semantics are visually consistent
- no screen-specific styling weakens security-critical cues
- increased information density has not reduced legibility

## 7. Evidence Check

For any reviewed or changed UI surface, confirm:

- before/after screenshot or terminal capture exists
- changed screen is named explicitly
- reviewer can point to where security-relevant state is shown
- no hidden regression appears in degraded or blocked paths

## 8. Test Check

Confirm these remain green:

- `tests/unit/test_ui_security.py`
- `tests/unit/test_ui_widgets.py`
- full `tests/unit`
- current baseline summary: `84 passed, 1 warning`

If any review change touches UI behavior, rerun the affected tests before approval.

## 9. Stop Conditions

Stop review and do not continue polishing if any of the following happens:

- contract shape would need to change
- widget starts absorbing logic
- a screen needs to infer trust/security state locally
- a style tweak makes a warning less visible
- scope expands into new abstractions without a concrete duplication or bug

## Final Review Question

After the review, the answer to all of the following should still be “yes”:

- Is the UI clearer under degraded conditions?
- Are security-relevant states still obvious?
- Is blocked behavior still honest to the user?
- Are widgets still dumb presenters only?
- Is the client easier to interpret, not just prettier?
