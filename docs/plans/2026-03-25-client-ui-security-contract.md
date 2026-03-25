# Client UI Security Contract

Date: 2026-03-25
Status: Approved planning baseline
Scope: `client/ui/contracts.py`, `client/ui/app.py`, `client/ui/screens/*`

## Goal

Modernize the client UI without weakening security meaning.

This contract exists to prevent each screen from inventing its own security and
error handling rules. The UI must become clearer, not more permissive.

Priority order:

1. correct
2. secure
3. clear
4. pretty

Modern UI definition for this project:

- readable > fancy
- state clarity > animation
- security visibility > minimal UI
- no misleading success states

## Hard Boundary

### UI layer

Responsibilities:

- render current state
- dispatch user intent
- show security and degraded-state feedback

Must not:

- decide trust or crypto outcomes
- silently fall back and pretend success
- swallow storage, decrypt, or trust errors
- clear persistent security warnings on refresh or reopen

### Controller layer (`client/ui/app.py`)

Responsibilities:

- catch domain, security, storage, and transport exceptions
- map them into a stable UI contract
- choose banner priority, screen state, and disabled actions
- preserve persistent security state across screen transitions

### Store and crypto layers

Responsibilities:

- remain source of truth for trust, session, storage, and message integrity
- expose facts and failures, not presentation decisions

### Persistent state ownership

Persistent security state source:

- trust state comes from persisted session or store state
- `key_changed` is a persisted trust flag, not a UI-only warning
- storage availability is derived from runtime failures and may be cached as a
  critical controller state, but UI does not persist it

Rules:

- UI contract does not define state ownership
- UI does not persist security state
- controller does not become the source of truth for trust correctness

## Non-Negotiable Rules

- UI must never silently swallow a security failure.
- UI must never show a stale success state after storage or decrypt failure.
- `key_changed` for a verified contact must remain visible until explicitly resolved.
- blocked send must keep the draft intact.
- settings verify action must update persistent trust state, not only a label.
- backend and crypto behavior are out of scope for phase 1.

## Shared Types

Phase 1 should standardize on the following contract shapes in
`client/ui/contracts.py`.

```python
from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["info", "warning", "error"]
UiStateKind = Literal["ok", "empty", "loading", "degraded", "blocked", "error"]

UiErrorCode = Literal[
    "local_storage_unavailable",
    "local_history_unavailable",
    "message_send_blocked",
    "message_send_failed",
    "key_changed",
    "trust_unverified",
    "ciphertext_tampered",
    "replay_rejected",
    "network_unavailable",
    "server_error",
]

MessageRecord = dict[str, Any]


@dataclass(frozen=True)
class UIBanner:
    code: UiErrorCode
    severity: Severity
    title: str
    message: str
    persistent: bool = False
    dismissible: bool = True


@dataclass(frozen=True)
class UIScreenState:
    kind: UiStateKind
    banners: tuple[UIBanner, ...] = ()
    primary_banner: UIBanner | None = None
    disabled_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatHistoryResult:
    state: UIScreenState
    messages: tuple[MessageRecord, ...] = ()


@dataclass(frozen=True)
class SendResult:
    ok: bool
    state: UIScreenState
    sent_at: int | None = None


@dataclass(frozen=True)
class TrustViewModel:
    fingerprint: str
    verified: bool
    key_changed: bool
    requires_action: bool
    last_verified_at: int | None
    banner: UIBanner | None = None
```

## Screen Responsibilities

### Chat screen

Must render:

- messages
- compose box
- message send status
- conversation-level warning banner
- `loading`, `empty`, `degraded`, `blocked`, and `error` states

Must not:

- decide decrypt or integrity outcomes
- mutate trust state on its own
- handle storage recovery logic

### Settings screen

Must render:

- fingerprint
- verified or unverified state
- `key_changed`
- TTL presets or manual input
- verify action result

Must not:

- decide whether a new key is trustworthy
- auto-clear a persistent key warning

### Conversation list

Must render:

- unread badge
- selected state
- last activity
- conversation security indicator such as key warning

Must not:

- resolve key changes
- recover history

### Auth screens

Must render:

- inline validation
- login, register, and TOTP failure states
- network and TLS-related failure messaging

Must not:

- define TLS policy
- create storage keys or trust state

## Error Mapping Table

| Domain error or state | UI code | Severity | Screen effect | Required action |
| --- | --- | ---: | --- | --- |
| `LocalStorageSecurityError` on history load | `local_history_unavailable` | warning | degraded empty-state and banner | hide messages, keep chat open |
| `LocalStorageSecurityError` on send | `local_storage_unavailable` | error | blocked send | disable send until state recovers |
| `TrustState.key_changed=True` for verified contact | `key_changed` | error | persistent banner | disable send or block trust transition |
| unverified contact key changed | `trust_unverified` | warning | persistent banner | allow continue, show verify action |
| replay reject | `replay_rejected` | warning | no crash, optional banner or status | drop message |
| tamper or authentication fail | `ciphertext_tampered` | error | no crash, banner or status | drop message |
| network unreachable | `network_unavailable` | warning | banner and retry hint | disable send transiently |
| generic send failure | `message_send_failed` | error | inline composer error | keep draft |
| explicit policy block | `message_send_blocked` | error | blocked state | explain reason |

`degraded` means the screen remains usable, but a required capability is
missing or incomplete. Actions may still be allowed, but the reason must stay
visible.

## Persistent vs Transient

### Persistent

These must survive refresh, reopen, and screen changes until explicitly resolved:

- `key_changed` for verified contact
- `trust_unverified` if unresolved
- `local_storage_unavailable` when the storage key is truly unavailable

### Transient

These may be temporary:

- `network_unavailable`
- `message_send_failed`
- `server_error`

## Banner Priority

If multiple conditions exist at once, apply a fixed priority:

1. `key_changed` for verified contact
2. `local_storage_unavailable`
3. `ciphertext_tampered`
4. `network_unavailable`
5. `trust_unverified`
6. generic send or server error

Security trust breakage always outranks transient transport issues.

## Controller Contract

Screens must not receive raw `SecurityError` instances.

`client/ui/app.py` should become the single mapping boundary and expose helpers
with shapes like:

```python
def map_exception_to_banner(exc: Exception) -> UIBanner: ...

async def load_chat_history_state(conversation_id: str) -> ChatHistoryResult: ...

async def send_message_state(
    conversation_id: str,
    peer_id: str,
    plaintext: str,
) -> SendResult: ...

def trust_state_to_view_model(conversation_id: str) -> TrustViewModel | None: ...
```

Rules:

- all screens consume only `ChatHistoryResult`, `SendResult`, `UIScreenState`,
  `UIBanner`, and `TrustViewModel`
- all exception and trust mapping stays in `app.py`
- controller decides disabled actions such as `("send",)` or `("verify",)`
- screens render `primary_banner` and may optionally expose `banners` as a
  detail view later

## First Implementation Target

Phase 1 should stop at these files:

### `client/ui/contracts.py`

Add:

- `Severity`
- `UiStateKind`
- `UiErrorCode`
- `UIBanner`
- `UIScreenState`
- `ChatHistoryResult`
- `SendResult`
- `TrustViewModel`

Keep compatibility only if needed to avoid a large diff in one step.

### `client/ui/app.py`

Add:

- `map_exception_to_banner()`
- `load_chat_history_state()`
- `send_message_state()`
- `trust_state_to_view_model()`

### `client/ui/screens/chat.py`

Change only enough to ensure:

- it no longer handles raw storage or security exceptions
- it renders `ChatHistoryResult` and `SendResult`
- it respects `UIScreenState.disabled_actions`

## Done Definition

Phase 1 is done only when:

- every security-relevant state is visible: `verified`, `key_changed`, `ttl`,
  storage failure, and local history failure
- no screen silently ignores storage, decrypt, or trust failures
- chat remains stable under degraded history or send-blocked states
- blocked send keeps the user draft
- multiple simultaneous conditions resolve to a deterministic primary banner
- chat, conversations, settings, and auth consume one shared contract model
- no backend change is required to ship the first UI-contract pass
