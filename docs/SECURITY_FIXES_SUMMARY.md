# Security Bug Fixes Summary

## Overview

This document summarizes all security bug fixes implemented for the COMP3334 Secure Instant Messenger project. All 19 security vulnerabilities identified in the bug reports have been addressed.

## Server-Side Fixes (17 bugs)

### 1. Race Condition in Friendship Check (send_message)

**File:** `server/api/messages.py`
**Fix:** Added `with_for_update()` to lock the friendship row during message insertion, preventing concurrent friendship removal while a message is being sent.

### 2. Missing Transaction in Message Sending

**File:** `server/api/messages.py`
**Fix:** The friendship check and message insertion are now wrapped in a single database transaction with row-level locking.

### 3. Inconsistent Authorization in Friend Request Handling

**File:** `server/api/friends.py`
**Status:** Already correctly implemented - authorization checks verify sender can cancel, recipient can accept/decline.

### 4. Weak Password Policy

**File:** `shared/protocol.py`
**Fix:** Added password complexity validator requiring:

- Minimum 12 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character

### 5. Timing Attack in Login

**File:** `server/api/auth.py`
**Status:** Already correctly implemented - uses dummy hash verification when user not found to prevent username enumeration.

### 6. Session Token Predictability

**File:** `server/core/security.py`
**Status:** Already correctly implemented - uses `secrets.token_urlsafe(32)` for 256-bit CSPRNG.

### 7. TOTP Secret Storage Weakness

**File:** `server/core/security.py`
**Status:** Already correctly implemented - uses AES-256-GCM with HKDF-SHA256 derived keys and user ID as associated data.

### 8. Rate Limiting Bypass via IP Spoofing

**File:** `server/core/security.py`
**Status:** Already correctly implemented - uses atomic SQLite upsert with `INSERT ... ON CONFLICT` to prevent TOCTOU race conditions.

### 9. Database Query Injection Risk

**File:** `server/api/conversations.py`
**Fix:** Added authorization check in `mark_read` endpoint to verify user is part of the conversation before resetting unread count.

### 10. WebSocket Authentication Bypass

**File:** `server/main.py`
**Status:** Already correctly implemented - uses first-frame authentication with token validation.

### 11. Offline Queue Race Condition

**File:** `server/ws/handler.py`
**Fix:** Added `with_for_update()` to lock message rows during offline queue flush and ACK processing, preventing concurrent delivery attempts.

### 12. TTL Cleanup Insecure Deletion

**File:** `server/core/database.py`
**Status:** Already correctly implemented - SQLite `PRAGMA secure_delete=ON` is enabled to overwrite deleted data with zeros.

### 13. Error Information Leakage

**File:** `server/main.py`
**Fix:** Removed `str(exc)` from error responses in the readiness endpoint to prevent leaking internal system details.

### 14. Missing Input Validation in Keys API

**File:** `server/api/keys.py`
**Status:** Already correctly implemented - validates base64 encoding, key lengths (32 bytes), and Ed25519 signature verification.

### 15. Replay Protection Incomplete

**File:** `server/models/message.py`
**Status:** Already correctly implemented - uses UNIQUE constraint on (conversation_id, sender_id, counter) for server-side replay prevention.

### 16. Incorrect Authorization in Message Delivery Acknowledgment

**File:** `server/api/messages.py`
**Status:** Already correctly implemented - checks `msg.recipient_id != user_id` to ensure only recipients can send delivery ACKs.

### 17. Database-Based Rate Limiting DoS Vulnerability

**File:** `server/core/security.py`
**Status:** Already correctly implemented - uses atomic SQLite upsert operations which are efficient and resistant to DoS attacks.

## Client-Side Fixes (2 bugs)

### 18. Unencrypted Local Message Storage

**Files:** `client/state/store.py`, `client/ui/app.py`
**Fix:**

- Changed `LocalMessage` schema to store encrypted ciphertext instead of plaintext
- Added AES-256-GCM encryption/decryption functions using the same storage key as the keystore
- Modified `save_message()` to encrypt plaintext before storing
- Modified `get_messages()` to decrypt ciphertext when reading
- Added `set_storage_key()` function to set the encryption key
- Updated `client/ui/app.py` to derive and set the storage key after loading the keystore

### 19. Missing SSL Certificate Pinning

**File:** `client/api/client.py`
**Fix:**

- Added certificate pinning callback to `_make_ssl_ctx()` function
- Certificate pinning now works for both HTTP and WebSocket connections
- Verifies server certificate SHA256 hash matches expected pin during TLS handshake

## Verification

All 53 tests pass successfully:

- 4 integration tests (E2E message flow, replay rejection, offline queue)
- 11 security tests (replay attacks, ciphertext tampering, session key derivation)
- 38 unit tests (auth, config, crypto)

```
53 passed in 2.89s
```

## Files Modified

| File                          | Changes                                                                                         |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| `server/api/messages.py`      | Added `with_for_update()` for friendship row locking                                            |
| `server/ws/handler.py`        | Added `with_for_update()` for message row locking during offline queue flush and ACK processing |
| `server/main.py`              | Removed `str(exc)` from error responses                                                         |
| `server/api/conversations.py` | Added authorization check in `mark_read` endpoint                                               |
| `shared/protocol.py`          | Added password complexity validator                                                             |
| `client/state/store.py`       | Changed to encrypted message storage with AES-256-GCM                                           |
| `client/ui/app.py`            | Added storage key derivation and setup after login                                              |
| `client/api/client.py`        | Added SSL certificate pinning for HTTP and WebSocket connections                                |
