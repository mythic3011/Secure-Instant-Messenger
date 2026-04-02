# Final Evidence Pack

Date: 2026-03-26
Status: Submission-ready evidence summary
Scope: submission evidence for frozen UI contract and Issue #44 trust-boundary hardening

## Submission State

The client UI remains in submission-ready freeze state, and the peer-bundle
trust boundary hardening from Issue #44 is complete.

Frozen areas:

- Phase 1 contract
- Phase 2 render cleanup
- minimal widget extraction

Security hardening evidence now includes:

- centralized peer-bundle trust gate
- signature verification plus expected-peer binding
- fail-closed rejection of invalid or mismatched bundles

No open focused-review findings remain in the frozen UI surfaces.

## Security Evidence

Issue #44 is validated locally with the following behaviors:

- invalid signature bundles are rejected
- validly signed bundles for the wrong peer are rejected
- rejected bundles do not mutate trust or session state
- history-path failures skip the item and withhold the ack for that item
- send-path failures map to deterministic local-security handling

## Frozen Surfaces

Core UI surfaces frozen for submission:

- `client/ui/screens/chat.py`
- `client/ui/screens/settings.py`
- `client/ui/screens/conversations.py`

Frozen minimal widget set:

- `client/ui/theme.py`
- `client/ui/widgets/banner.py`
- `client/ui/widgets/security_badge.py`

Supporting artifacts:

- `docs/notes/submission-ui-freeze-note.md`
- `docs/notes/focused-review-checklist.md`
- `docs/notes/focused-review-result-2026-03-26.md`

## Key Freeze Guarantees

- contract is frozen
- render cleanup is frozen
- widgets are dumb presenters only
- no screen may reintroduce raw trust/security decision logic
- no further UI abstraction work unless a concrete duplication or bug is demonstrated

## Security/UX Guarantees Preserved

- blocked send keeps draft intact
- successful send clears input only after success
- verify action does not optimistic-update persistent trust display
- `key_changed` remains visible and now maps to honest blocked behavior
- degraded state is distinct from empty and success states
- summary-level security indicators remain consistent with detail-level warnings

## Focused Review Outcome

Focused review covered:

- `chat.py`
- `settings.py`
- `conversations.py`
- `Banner`
- `SecurityBadge`

Initial focused-review findings:

- P1: `key_changed` warning did not block send
- P2: conversation summary refresh could drift into contradictory state

Final result:

- P1 fixed
- P2 fixed
- no open focused-review findings remain

Reference:

- `docs/notes/focused-review-result-2026-03-26.md`

## Test Evidence

Validated locally:

- `tests/unit/test_crypto.py`
- `tests/unit/test_send_message_use_case.py`
- `tests/unit/test_ui_security.py`
- `tests/unit/test_trust_gate_invariants.py`
- full `pytest`

Exact verification commands:

```bash
uv run pytest tests/unit/test_crypto.py tests/unit/test_send_message_use_case.py tests/unit/test_ui_security.py tests/unit/test_trust_gate_invariants.py -q
uv run pytest -q
```

Latest local result:

- `237 passed, 1 warning`

Expected warning:

- `--no-verify-tls is insecure; use --ca-cert instead`

This warning is expected and not treated as a submission blocker.

## Before/After Evidence

Required submission evidence to attach or reference:

- before screenshot or terminal capture
- after screenshot or terminal capture
- affected screens list
- note confirming no security-relevant state became less visible

Suggested capture targets:

- chat screen with normal state
- chat screen with blocked/warning state
- settings screen with `requires_action`
- conversation list showing security indicator

The blocked-send capture is especially important because it demonstrates that a
critical trust break now maps to both:

- visible warning
- disabled send action

## Release-Style Summary

This submission freezes the client UI around a controller-driven security
contract, while also closing the fetched peer-bundle trust boundary with
centralized signature verification and expected-peer binding.

The final state preserves security visibility, fails closed on invalid or
mismatched bundles, and prevents misleading UI or trust-state behavior under
blocked or degraded conditions.

## Post-Freeze Rule

Only the following are allowed after this point:

- submission hardening
- evidence packaging
- bug fixes with clear reproduction

Post-freeze controller wiring fixes are allowed only when they restore frozen
semantics, not when they redefine them.

## Freeze Protection Note

If a GitHub submission or mainline branch is used after this freeze, it should
require:

- approving review before merge
- required status checks to pass before merge

This is intended to prevent post-freeze scope creep from bypassing the frozen
UI contract and submission baseline.
