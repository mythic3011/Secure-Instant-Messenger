# Submission Readiness Checklist

Use this checklist to prepare the final `TeamID.zip` submission.
It is submission-facing and should stay aligned with `docs/Project.pdf`.

## TeamID.zip Structure

- [ ] Create a folder named `TeamID`
- [ ] Put all runnable submission code inside `TeamID/code`
- [ ] Compress the folder as `TeamID.zip`

## Code Package

- [ ] `TeamID/code` contains the project source code only
- [ ] `TeamID/code` does not contain internal development-only context or tooling files
- [ ] `TeamID/code` includes either an importable database artifact or a documented runtime database initialization path
- [ ] `TeamID/code` includes a step-by-step deployment and usage document

## Functional / Security Coverage

- [ ] E2EE 1:1 private chat
- [ ] Timed self-destruct messages
- [ ] Password + OTP login
- [ ] Friend request / contact management
- [ ] Offline ciphertext queue
- [ ] Sent / Delivered message status
- [ ] Conversation list and unread counters
- [ ] Replay protection / de-duplication
- [ ] Key change visibility
- [ ] Fetched peer bundles are admitted only through the centralized trust gate
- [ ] Invalid or mismatched fetched bundles fail closed without trust/session mutation
- [ ] Wrong-peer valid bundle rejection is captured as a submission evidence case
- [ ] TLS remains enabled for client-server transport

## Deployment / Usage Evidence

- [ ] `docs/DEPLOY.md` can be followed from a clean Windows 11 machine
- [ ] `docs/DEPLOY.md` can be followed from a clean Ubuntu machine
- [ ] The deployment guide explicitly tells the reader how the database is initialized or imported
- [ ] The deployment guide does not assume pre-installed project-specific tools beyond what it instructs the user to install
- [ ] `README.md` and `docs/DEPLOY.md` agree on the standard local/demo run path
- [ ] Any local TLS fallback wording is clearly marked as troubleshooting-only, not the standard path

## Report Review

- [ ] Report includes team ID, names, and student IDs
- [ ] Report includes abstract and introduction
- [ ] Report includes threat model and assumptions
- [ ] Report includes architecture and trust boundaries
- [ ] Report includes protocol design
- [ ] Report describes the centralized peer-bundle trust gate with signature verification and expected-peer binding
- [ ] Report includes cryptographic choices, rationale, libraries, and versions
- [ ] Report includes security analysis and limitations
- [ ] Report states that formal revocation / invalidation lifecycle is future work, not implemented scope
- [ ] Report includes testing and evaluation
- [ ] Report includes at least 2 security test cases
- [ ] Report includes references

## Video Review

- [ ] Video demonstrates the key system design and security behavior
- [ ] Video length matches the project brief expectation

## Final Gate

- [ ] Submission-facing code batch is committed
- [ ] Fresh test / verification command outputs are captured as evidence instead of relying on stale hard-coded counts
- [ ] Architecture, README, deploy guide, and evidence notes all match the same trust-boundary contract
- [ ] Remaining post-freeze issues are tracked with clean issue / PR scope
- [ ] Submission zip contains code only and matches the intended final contents
- [ ] `TeamID.zip` is built from the intended final contents
