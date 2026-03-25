# Phase 2 Render/Style-Only Checklist

Date: 2026-03-26
Status: Active review checklist
Scope: render, style, layout, and widget extraction only

## Rule

Contract is frozen.

Do not change:

- `client/ui/contracts.py` shape
- controller result semantics
- screen state ownership

Exception:

- a proven bug
- a missing required state that tests demonstrate cannot be expressed

Render-only changes must not reduce visibility of any security-relevant state.

## Purpose

Use this checklist before and after any presentation-layer change.

Targets:

- `client/ui/screens/chat.py`
- `client/ui/screens/settings.py`
- `client/ui/screens/conversations.py`
- `client/ui/screens/login.py`
- `client/ui/screens/register.py`

This checklist is a guardrail against visual improvements that weaken security
meaning, state clarity, or interaction safety.

## Review Evidence

For any Phase 2 UI PR, include:

- before and after screenshot or terminal capture
- which screens were reviewed against this checklist
- whether any security-relevant state became more visible, less visible, or unchanged

## 1. Security Visibility

- `verified` is visible where the user needs to make trust decisions.
- `key_changed` is visible and visually stronger than non-security warnings.
- `requires_action` is visible in both detail and summary views.
- `local_history_unavailable` remains visible and is not visually mistaken for a normal empty state.
- storage/security failure banners remain visible until their underlying state changes.
- banner priority is preserved visually, not only in code.
- degraded security states are never styled like success states.
- summary indicators in conversation lists do not hide detail-level trust warnings.

## 2. State Rendering

- loading state is distinct and readable.
- empty state is distinct from degraded state.
- degraded state is distinct from blocked state.
- blocked state is distinct from generic error state.
- message metadata is visually separate from message content.
- TTL, delivery state, and trust cues are readable without crowding the message body.
- warning banners do not collapse into decorative text.
- a rejected or unavailable state is explained with a reason, not only a color.
- no render change may compress TTL, trust, or failure indicators into low-legibility text.

## 3. Interaction Safety

- blocked send keeps draft.
- failed send keeps draft.
- verify action does not optimistic-update trust UI before controller confirmation.
- warnings do not disappear on refresh or reopen unless the underlying state is resolved.
- screens do not silently hide or downgrade security-relevant errors.
- disabled actions remain visibly disabled, not just non-functional.
- retryable failures are visually distinct from non-retryable policy blocks.

## 4. Visual Consistency

- status colors mean the same thing across screens.
- banner hierarchy is consistent across screens.
- badge semantics are consistent across screens.
- title, spacing, and section hierarchy are consistent across screens.
- security-critical cues are not weakened by screen-specific styling.
- emphasis is driven by meaning first, decoration second.

## 5. Review Pass By Screen

### Chat

- trust warning is visible while composing and reading messages
- degraded history state does not look like “no messages yet”
- send-blocked state is obvious without losing draft
- metadata separation improves readability without hiding delivery or TTL state

### Settings

- trust state is readable without requiring inference
- `requires_action` is obvious
- fingerprint remains clearly associated with verification
- verify action result only reflects controller-confirmed state

### Conversations

- security indicator is visible in summary rows
- unread and security indicators do not compete ambiguously
- summary state does not attempt to resolve trust issues

### Auth

- login/register/TOTP failures are clear and non-ambiguous
- transport/network failures are not styled like validation errors
- auth screens remain visually aligned with the rest of the client

## 6. Extraction Gate

Do not extract a shared widget unless all of the following are true:

- the same semantic role appears in at least two screens
- the rendering rules are already stable under this checklist
- extraction will reduce duplication without hiding state meaning
- the widget API can stay presentation-only

Candidate widgets after review:

- `Banner`
- `SecurityBadge`
- `StatusRow`
- `SectionHeader`

Avoid extracting large shells or layout containers until repeated usage is
proven stable.

## 7. Definition Of Safe Phase 2 Work

A render/style change is safe only if:

- all relevant contract state remains visible
- the change does not introduce a new source of truth
- tests for existing invariants still pass
- the screen is easier to interpret under degraded and blocked conditions, not
  just prettier in the happy path
