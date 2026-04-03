# Testing Evidence Pack

Date: 2026-04-02
Status: In progress
Scope: Issue #13 submission-ready testing evidence and checklist

## Scope And Baseline

- Record the exact commit hash used for the final evidence pass
- Record the environment used for evidence capture
- Keep screenshots and logs aligned with the latest submission-facing branch
- Include Issue #44 as a concrete security hardening / regression case

## Core Submission Flows

- [ ] Register
- [ ] Login
- [ ] TOTP setup / verification
- [ ] Add friend / send friend request
- [ ] Send message
- [ ] TTL / expiry behavior
- [ ] Replay protection / duplicate handling
- [ ] Offline / reconnect behavior, if exercised in the final demo path

## Security Evidence

- [ ] Invalid peer-bundle signature is rejected
- [ ] Validly signed bundle for the wrong peer is rejected by expected-peer binding
- [ ] Rejected fetched bundle does not mutate trust/session state
- [ ] Send path maps invalid bundle to deterministic local-security handling
- [ ] History path skips invalid item and withholds the ack for that item
- [ ] No raw parse / signature exception text is shown to the user

## Verification Commands

```bash
uv run pytest tests/unit/test_crypto.py tests/unit/test_send_message_use_case.py tests/unit/test_ui_security.py tests/unit/test_trust_gate_invariants.py -q
uv run pytest tests/unit -q
uv run pytest -q
```

Latest verified result already captured:

- `237 passed, 1 warning`

Expected warning:

- `--no-verify-tls is insecure; use --ca-cert instead`

## Evidence Artifacts To Capture

- [ ] Registration success screenshot
- [ ] Login success screenshot
- [ ] TOTP setup / verification screenshot
- [ ] Friend request send screenshot
- [ ] Message send success screenshot
- [ ] TTL expiry screenshot or log snippet
- [ ] Replay rejection screenshot or log snippet
- [ ] Offline / reconnect screenshot or log snippet, if applicable
- [ ] `#44` blocked-send screenshot showing the generic security message
- [ ] Terminal capture showing the latest full-suite result

## Working Wording For #44

- Fetched peer bundles are admitted only through a centralized trust gate.
- Signature verification and expected-peer binding both must succeed before trust/session use.
- Invalid or mismatched bundles fail closed with no trust/session mutation.

## Final Checklist

- [ ] All core user flows were exercised and recorded
- [ ] Security-critical paths were verified with explicit failure cases
- [ ] Evidence is written in submission-ready prose, not raw notes
- [ ] Screenshots / logs are attached where they add proof
- [ ] Known limitations are stated clearly and precisely
