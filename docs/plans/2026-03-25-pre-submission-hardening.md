# Pre-Submission Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the existing secure-messaging submission so failure paths are fail-closed, the demo flow is deterministic, and every report claim is backed by current code and test evidence.

**Architecture:** This plan freezes feature scope and focuses on validation-first hardening. Each task starts by encoding a submission risk as a failing test or reproducible command, then applies the smallest code or documentation change needed to make the observed behavior match the report. Evidence is refreshed from the existing unit, integration, and security suites plus a final deploy/demo rehearsal.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `bandit`, `httpx`, `websockets`, `Textual`, SQLite, Docker Compose

---

## Assumptions

- This runs in a dedicated worktree before submission freeze.
- No new product features are added in this plan.
- Existing tests in `tests/unit/`, `tests/integration/`, and `tests/security/` remain the primary evidence source.
- When a static finding is in a dev-only script, either remove it or document why it is intentionally scoped to local development.

## Decision Gate

Option A:
- Fix only failures confirmed by tests/commands in this plan.
- Risk: leaves some low-signal static findings if they are not covered by behavior checks.
- Cost: lowest, best for submission freeze.

Option B:
- Proactively refactor all broad catches and insecure dev shortcuts found by static scans.
- Risk: late churn in stable paths; more regression risk before demo.
- Cost: medium.

**Recommendation**

- Choose Option A with a narrow allowance for obvious low-risk static fixes in `scripts/seed.py`, `client/crypto/storage.py`, and `server/main.py`.
- Avoid broader cleanup unless a failing test or scan output proves it matters to submission evidence.

### Task 1: Establish the Hardening Baseline

**Files:**
- Modify: `docs/BUG_REPORT.md`
- Modify: `docs/DEPLOY.md:221-271`
- Modify: `docs/TASKS.md:103-108`

**Step 1: Run the baseline checks and capture raw outputs**

Run:

```bash
uv sync --extra dev
uv run ruff check .
uv run bandit -r client server scripts
uv run --extra dev pytest -v
```

Expected:
- `ruff` and `bandit` outputs are saved in your terminal history or notes.
- `pytest` finishes with a fresh pass/fail summary you can quote later.
- Any failure becomes a blocker for the later tasks instead of being hand-waved in the report.

**Step 2: Update the evidence source notes before touching code**

```md
## Evidence Refresh

- Static checks are validated from fresh `uv run ruff check .` and `uv run bandit -r client server scripts`.
- Runtime claims are validated from fresh `uv run --extra dev pytest -v`.
- Do not quote stale test counts; always cite the latest command output.
```

**Step 3: Verify the docs still point graders at the right commands**

Run:

```bash
rg -n "pytest|bandit|ruff|--no-verify-tls|first-frame|conversation_id" docs
```

Expected:
- You know exactly which later doc sections must be updated.

**Step 4: Commit the baseline note changes**

```bash
git add docs/BUG_REPORT.md docs/DEPLOY.md docs/TASKS.md
git commit -m "docs: refresh pre-submission evidence guidance"
```

### Task 2: Fail Closed on Local Storage and Keystore Corruption

**Files:**
- Modify: `client/crypto/storage.py:185-213`
- Modify: `client/crypto/storage.py:273-320`
- Modify: `client/ui/app.py:145-156`
- Modify: `client/ui/app.py:275-292`
- Test: `tests/unit/test_store_save.py`
- Test: `tests/unit/test_ui_security.py`

**Step 1: Write the failing tests for corrupted local state**

```python
def test_load_keystore_corruption_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.Path, "home", lambda: tmp_path)
    username = "alice"
    storage.save_keystore(username, "Password123!", _sample_local_keys())
    keystore = tmp_path / ".comp3334im" / username / "keystore.json"
    data = json.loads(keystore.read_text())
    data["identity_priv_ct_b64"] = base64.b64encode(b"broken").decode()
    keystore.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="Wrong password or corrupted keystore"):
        storage.load_keystore(username, "Password123!")


def test_load_sessions_corrupted_entry_skips_only_broken_conversation(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(storage.Path, "home", lambda: tmp_path)
    storage.save_keystore("alice", "Password123!", _sample_local_keys())
    _write_sessions_json_with_one_valid_and_one_corrupt_entry(tmp_path, "alice")

    sessions = storage.load_sessions("alice", "Password123!")

    assert "conv-ok" in sessions
    assert "conv-bad" not in sessions
    assert any("Failed to decrypt session" in r.getMessage() for r in caplog.records)
```

**Step 2: Run only the new storage failure tests**

Run:

```bash
uv run --extra dev pytest tests/unit/test_store_save.py -k "corrupt or session" -v
```

Expected:
- FAIL because the new test scaffolding or helper coverage is not complete yet.

**Step 3: Add a UI-level fail-safe test for login/history degradation**

```python
@pytest.mark.asyncio
async def test_login_corrupted_keystore_shows_inline_error(monkeypatch):
    app = IMApp("https://example.test", "alice")
    login_screen = _fake_login_screen()
    monkeypatch.setattr(app, "screen", login_screen, raising=False)
    monkeypatch.setattr(app_module, "keystore_exists", lambda _username: True)
    monkeypatch.setattr(app_module, "load_keystore", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("Wrong password or corrupted keystore.")))

    await app.on_login_screen_login_success(SimpleNamespace(username="alice", password="Password123!"))

    assert login_screen.error.value == "Wrong password or corrupted keystore."
```

**Step 4: Implement the minimal storage/UI hardening**

```python
def load_sessions(username: str, password: str) -> dict[str, SessionState]:
    ...
    for conv_id, entry in raw.items():
        try:
            sk_raw = _aes_decrypt(...)
        except Exception as exc:
            logging.error(
                "Failed to decrypt session for user %s, conversation %s: %s",
                username,
                conv_id,
                exc,
            )
            continue
```

```python
try:
    self._local_keys = load_keystore(username, password)
    self._derive_and_set_storage_key(username, password)
except ValueError:
    login_screen.query_one("#error", Static).update("Wrong password or corrupted keystore.")
    return
```

**Step 5: Re-run the focused tests**

Run:

```bash
uv run --extra dev pytest tests/unit/test_store_save.py tests/unit/test_ui_security.py -k "corrupt or storage or history" -v
```

Expected:
- PASS, with no crash path and explicit UI/log evidence for local storage failure.

**Step 6: Commit**

```bash
git add tests/unit/test_store_save.py tests/unit/test_ui_security.py client/crypto/storage.py client/ui/app.py
git commit -m "fix: harden local storage corruption handling"
```

### Task 3: Preserve Fail-Closed Send and Incoming-Message Behavior

**Files:**
- Modify: `client/ui/app.py:158-223`
- Modify: `client/ui/app.py:700-723`
- Modify: `client/state/store.py:184-227`
- Test: `tests/unit/test_ui_security.py`
- Test: `tests/unit/test_store_save.py`

**Step 1: Write the failing tests for send-block and receive-degrade paths**

```python
@pytest.mark.asyncio
async def test_send_message_missing_storage_key_does_not_increment_counter(monkeypatch):
    app = IMApp("https://example.test", "alice")
    app._client = SimpleNamespace(send_message=_async_noop)
    app._user_id = "alice"
    app._counters["conv-1"] = 0
    monkeypatch.setattr(app, "_ensure_session", _fake_session)
    monkeypatch.setattr(app_module, "save_message", _raise_local_storage_security_error)

    result = await app.send_message_action(conversation_id="conv-1", peer_id="bob", plaintext="hello")

    assert result.status == "blocked"
    assert app._counters["conv-1"] == 0
```

```python
@pytest.mark.asyncio
async def test_incoming_message_store_failure_sets_ui_error(monkeypatch):
    app = _configured_app_with_session()
    chat = ChatScreen("conv-1", "bob", "bob", "alice")
    monkeypatch.setattr(app, "screen", chat, raising=False)
    monkeypatch.setattr(app_module, "save_message", _raise_local_storage_security_error)

    await app._handle_incoming_message(_valid_payload("conv-1"))

    assert chat.query_one("#warning").value == "Local chat history is unavailable. Re-login to unlock it."
```

**Step 2: Run the focused UI security tests**

Run:

```bash
uv run --extra dev pytest tests/unit/test_ui_security.py -k "send_message or incoming_message_store_failure" -v
```

Expected:
- FAIL until the failure-path assertions match the current implementation.

**Step 3: Keep the implementation fail-closed**

```python
except (LocalStorageSecurityError, SecurityError) as exc:
    log.warning(
        "send_message_blocked",
        conversation_id=conversation_id,
        peer_id=peer_id,
        error=type(exc).__name__,
    )
    return SendResult(status="blocked", sent_at=sent_at, error=LOCAL_KEY_UNAVAILABLE)
```

```python
except (LocalStorageSecurityError, SecurityError) as exc:
    log.warning(
        "incoming_message_store_failed",
        conversation_id=conv_id,
        message_id=envelope.id,
        error=type(exc).__name__,
    )
    if active_chat and active_chat.conversation_id == conv_id:
        active_chat.set_ui_error(LOCAL_HISTORY_UNAVAILABLE)
    return
```

**Step 4: Verify storage-layer logging remains explicit**

Run:

```bash
uv run --extra dev pytest tests/unit/test_store_save.py::test_missing_storage_key_raises -v
```

Expected:
- PASS, confirming no silent local-storage failure.

**Step 5: Commit**

```bash
git add tests/unit/test_ui_security.py tests/unit/test_store_save.py client/ui/app.py client/state/store.py
git commit -m "fix: keep storage failures fail-closed in ui flows"
```

### Task 4: Prove Replay and Tamper Rejection in Real Flows

**Files:**
- Modify: `tests/security/test_replay_attack.py:226-359`
- Modify: `tests/integration/test_e2e_message.py:253-325`
- Modify: `tests/integration/test_offline_queue.py:191-240`
- Modify: `server/api/messages.py:81-162`

**Step 1: Extend the security tests to cover the exact attack claims**

```python
def test_tampered_sender_id_rejected(self, session_keys):
    env = self._fresh_envelope(alice_send, conv_id)
    env.sender_id = "mallory"

    with pytest.raises(IntegrityError):
        decrypt_envelope(
            recv_chain=bob_recv,
            envelope=env,
            replay_protector=ReplayProtector(),
        )
```

```python
@pytest.mark.asyncio
async def test_replay_rejection(app_client: AsyncClient):
    ...
    resp2 = await client.post("/v1/messages", json={"envelope": envelope.model_dump()}, headers=_auth(alice_token))
    assert resp2.status_code == 409, resp2.text
```

**Step 2: Run the attack-focused suites**

Run:

```bash
uv run --extra dev pytest tests/security/test_replay_attack.py tests/integration/test_e2e_message.py tests/integration/test_offline_queue.py -k "replay or tampered or ttl or counter" -v
```

Expected:
- PASS if current behavior already matches the report.
- FAIL only if a real mismatch exists between server replay enforcement, AD binding, and integration handling.

**Step 3: Narrow any server-side exception handling if the test exposes false positives**

```python
except IntegrityError as exc:
    log.warning(
        "replay_rejected",
        msg_id=env.id,
        sender=env.sender_id,
        counter=env.counter,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate message (replay rejected)")
```

**Step 4: Re-run the full security slice**

Run:

```bash
uv run --extra dev pytest tests/security/ tests/integration/test_e2e_message.py tests/integration/test_offline_queue.py -v
```

Expected:
- PASS with concrete evidence for replay rejection, tamper rejection, and offline queue behavior.

**Step 5: Commit**

```bash
git add tests/security/test_replay_attack.py tests/integration/test_e2e_message.py tests/integration/test_offline_queue.py server/api/messages.py
git commit -m "test: lock replay and tamper evidence to runtime flows"
```

### Task 5: Validate Key-Change Persistence and Re-Verification

**Files:**
- Modify: `client/crypto/session.py:534-641`
- Modify: `client/ui/app.py:526-576`
- Test: `tests/unit/test_crypto.py:434-484`
- Test: `tests/unit/test_ui_security.py:187-225`

**Step 1: Add the persistence-focused failing test**

```python
def test_verified_key_change_persists_across_serialise_roundtrip():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    cache.mark_verified("alice", pub1)

    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("alice", pub2)

    restored = IdentityKeyCache.from_dict(cache.as_dict())
    trust = restored.get_trust_state("alice")
    assert trust is not None
    assert trust.verified is True
    assert trust.key_changed is True
    assert restored.get("alice") == pub1
```

**Step 2: Add the UI re-verification test**

```python
def test_verified_warning_persists_until_reverified():
    cache = IdentityKeyCache()
    pub1 = b"a" * 32
    pub2 = b"b" * 32
    cache.check_and_update("bob", pub1)
    cache.mark_verified("bob", pub1)

    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("bob", pub2)

    restored = IdentityKeyCache.from_dict(cache.as_dict())
    assert restored.get_trust_state("bob").key_changed is True

    restored.mark_verified("bob", pub2)
    assert restored.get_trust_state("bob").key_changed is False
```

**Step 3: Run the key-change slice**

Run:

```bash
uv run --extra dev pytest tests/unit/test_crypto.py tests/unit/test_ui_security.py -k "key_change or verified_warning" -v
```

Expected:
- PASS if the verified-contact warning survives app restart and clears only after manual re-verification.

**Step 4: Tighten implementation only if the tests fail**

```python
if state.verified:
    state.key_changed = True
    self._trust_states[peer_id] = state
    raise KeyChangeWarning(peer_id, cached, received_pub, verified=True)
```

```python
state.identity_key_cache.mark_verified(chat.peer_id, peer_pub)
self._persist_sessions()
chat.clear_key_warning()
```

**Step 5: Commit**

```bash
git add tests/unit/test_crypto.py tests/unit/test_ui_security.py client/crypto/session.py client/ui/app.py
git commit -m "test: prove verified key-change warning persistence"
```

### Task 6: Harden TLS and Dev-Only Exceptions Without Changing Defaults

**Files:**
- Modify: `client/api/client.py:43-127`
- Modify: `tests/unit/test_client_tls.py`
- Modify: `scripts/seed.py:81-88`
- Modify: `scripts/seed.py:459-468`
- Modify: `server/main.py:139-168`
- Modify: `docs/DEPLOY.md:245-255`
- Modify: `docs/TASKS.md:103-108`

**Step 1: Write the failing TLS policy tests**

```python
def test_tls_default_verifies_cert() -> None:
    verify, ws_ssl = client_module.apply_tls_policy()
    assert verify is True
    assert ws_ssl is None


def test_tls_insecure_flag_disables_verify_with_warning(monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(client_module.log, "warning", lambda *args, **kwargs: warnings.append((args, kwargs)))

    verify, ws_ssl = client_module.apply_tls_policy(verify_tls=False)

    assert verify is False
    assert isinstance(ws_ssl, ssl.SSLContext)
    assert warnings
```

**Step 2: Run the TLS tests**

Run:

```bash
uv run --extra dev pytest tests/unit/test_client_tls.py -v
```

Expected:
- PASS for client defaults before touching anything else.

**Step 3: Remove insecure dev-script defaults or make them explicit**

```python
parser.add_argument(
    "--no-verify-tls",
    action="store_true",
    help="Disable TLS verification for local self-signed demo use only",
)
...
async with httpx.AsyncClient(
    base_url=server,
    verify=False if args.no_verify_tls else _SSL_CTX,
    timeout=15.0,
) as client:
```

```python
try:
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
except TimeoutError:
    await websocket.close(code=4001)
    return
except WebSocketDisconnect:
    return
```

**Step 4: Re-run static checks against the touched security paths**

Run:

```bash
uv run ruff check client/api/client.py server/main.py scripts/seed.py tests/unit/test_client_tls.py
uv run bandit -r client server scripts
```

Expected:
- No new TLS regressions.
- Any remaining `verify=False` is justified and isolated to explicit local-development flags.

**Step 5: Commit**

```bash
git add client/api/client.py tests/unit/test_client_tls.py scripts/seed.py server/main.py docs/DEPLOY.md docs/TASKS.md
git commit -m "fix: keep tls secure by default across client and dev tooling"
```

### Task 7: Align Report Claims With Code and Refresh Demo Evidence

**Files:**
- Modify: `docs/ARCHITECTURE.md:201-365`
- Modify: `docs/DEPLOY.md:221-271`
- Modify: `docs/TASKS.md:33-55`
- Modify: `docs/BUG_REPORT.md`
- Modify: `scripts/test-deploy.sh:45-118`

**Step 1: Locate every claim that can drift from code**

Run:

```bash
rg -n "query param|token=|first-frame|SHA256\\(sorted|conversation_id|No Per-Message Forward Secrecy|delivery_status|metadata" docs
```

Expected:
- A finite list of doc lines to update.

**Step 2: Replace stale prose with code-matching statements**

```md
- WebSocket authentication uses first-frame JSON auth after the TLS handshake; bearer tokens are not sent in the URL query string.
- `conversation_id` is server-issued and validated on message submit; clients must use the server conversation row instead of deriving their own ID.
- Replay protection is dual-layer: client-side `ReplayProtector` plus server-side `UNIQUE(conversation_id, sender_id, counter)`.
- Delivery semantics are `sent` -> `delivered`; `read` remains reserved for future UX.
```

**Step 3: Rehearse the demo and deploy flow from commands, not memory**

Run:

```bash
./scripts/test-deploy.sh --no-docker
uv run --extra dev pytest tests/integration/test_e2e_message.py tests/integration/test_offline_queue.py -v
```

Expected:
- A fresh smoke-test result and a deterministic normal-flow/offline-flow evidence set.

**Step 4: Write the final demo script checkpoints into the bug report or handoff notes**

```md
1. Register Alice and Bob.
2. Add friend and confirm the conversation appears.
3. Send one normal message and show `delivered`.
4. Disconnect Bob, send offline, reconnect, and show delivery.
5. Show fingerprint screen and explain key-change warning persistence.
6. Mention replay/tamper rejection with the corresponding automated test names.
```

**Step 5: Commit**

```bash
git add docs/ARCHITECTURE.md docs/DEPLOY.md docs/TASKS.md docs/BUG_REPORT.md scripts/test-deploy.sh
git commit -m "docs: align submission report with verified behavior"
```

### Task 8: Final Submission Gate

**Files:**
- Modify: `docs/BUG_REPORT.md`

**Step 1: Run the final gate exactly once from a clean state**

Run:

```bash
uv sync --extra dev
uv run ruff check .
uv run bandit -r client server scripts
uv run --extra dev pytest -v
./scripts/test-deploy.sh --no-docker
```

Expected:
- All commands succeed, or any failure becomes a stop-ship item.

**Step 2: Complete the submission checklist in one place**

```md
- [ ] No UI crash in login, send, receive, or history load paths
- [ ] No backend crash in WebSocket auth or message submit paths
- [ ] No silent security failure for local storage, replay, tamper, or key change
- [ ] TLS defaults remain secure
- [ ] Report, code, and runtime behavior match
- [ ] Demo flow has been rehearsed from current commands
```

**Step 3: Commit the gate evidence**

```bash
git add docs/BUG_REPORT.md
git commit -m "docs: record final submission gate evidence"
```
