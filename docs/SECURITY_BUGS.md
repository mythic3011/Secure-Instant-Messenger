# Security Bug Report

## Summary

Comprehensive security review of the COMP3334 Secure Instant Messenger codebase identified 19 security vulnerabilities across server APIs, authentication, rate limiting, client-side storage, and cryptographic implementations. Bugs range from authorization bypasses to information disclosure and DoS vulnerabilities.

## Findings

### Server-Side Bugs

1. **Race Condition in Friendship Check (send_message)**  
   Friendship validation occurs before message insertion but without transaction isolation, allowing messages to be sent if friendship is removed concurrently.

2. **Missing Transaction in Message Sending**  
   Friendship check and message insertion not wrapped in a single transaction, enabling race conditions.

3. **Inconsistent Authorization in Friend Request Handling**  
   Friend request actions lack proper authorization checks for edge cases.

4. **Weak Password Policy**  
   No minimum password length or complexity requirements enforced.

5. **Timing Attack in Login**  
   Password verification timing may leak information about valid usernames.

6. **Session Token Predictability**  
   Tokens generated with insufficient entropy or predictable patterns.

7. **TOTP Secret Storage Weakness**  
   Encrypted TOTP secrets may use weak encryption keys or improper key derivation.

8. **Rate Limiting Bypass via IP Spoofing**  
   IP-based rate limiting vulnerable to spoofing attacks.

9. **Database Query Injection Risk**  
   Complex queries in conversations API may be vulnerable if parameters not properly sanitized.

10. **WebSocket Authentication Bypass**  
    WebSocket connections may not properly validate authentication tokens.

11. **Offline Queue Race Condition**  
    Message delivery to offline users has race conditions in queue management.

12. **TTL Cleanup Insecure Deletion**  
    Expired message cleanup may not securely wipe data.

13. **Error Information Leakage**  
    Some error messages reveal internal system details.

14. **Missing Input Validation in Keys API**  
    Public key uploads lack comprehensive validation.

15. **Replay Protection Incomplete**  
    Server-side replay protection relies only on unique IDs, not cryptographic counters.

16. **Incorrect Authorization in Message Delivery Acknowledgment**  
    `delivery_ack` checks `sender_id == user_id` instead of `recipient_id == user_id`, allowing unauthorized status updates.

17. **Database-Based Rate Limiting DoS Vulnerability**  
    Rate limiting uses database storage instead of fast cache, vulnerable to DoS attacks overwhelming the DB.

### Client-Side Bugs

18. **Unencrypted Local Message Storage**  
    Local SQLite database stores decrypted messages in plaintext, exposing content to local attackers.

19. **Missing SSL Certificate Pinning**  
    Client connections lack certificate pinning, vulnerable to man-in-the-middle attacks.

## Recommendations

1. Implement proper transaction isolation for critical operations.
2. Add comprehensive input validation and sanitization.
3. Use fast in-memory caches for rate limiting.
4. Encrypt local storage with device-specific keys.
5. Implement SSL certificate pinning.
6. Fix authorization checks in message acknowledgment.
7. Add cryptographic replay protection beyond unique IDs.
8. Enforce strong password policies.
9. Securely wipe expired data.
10. Minimize error information leakage.

## Verification

Bugs identified through code analysis and pattern matching. No runtime testing performed. Cryptographic implementations appear correct but require peer review.
