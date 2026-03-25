# Bug Report

## Summary

As the bug hunter for the COMP3334 Secure Instant Messenger project, I conducted a pre-submission hardening review focused on zero crash, zero silent security failure, and report-to-code consistency. The repository now includes automated guardrails that fail the build if silent broad exception swallowing or stale security claims are reintroduced.

## Findings

### 1. Misplaced Test File (FIXED)

**File:** `tests/ui/test_textual.py` (moved to `scripts/demo_textual.py`)
**Issue:** This file contained a `TestApp` class that inherited from `textual.app.App` but had no test methods. Pytest could not collect it as a test class because it had an `__init__` constructor, resulting in a warning:
`PytestCollectionWarning: cannot collect test class 'TestApp' because it has a __init__ constructor`

**Impact:** Cluttered test output with warnings. The file was a demo application rather than a test.
**Fix Applied:** Moved the file to `scripts/demo_textual.py` to remove it from test collection. Tests now run cleanly with no warnings.

### 2. Repository Guardrails for Silent Failure (FIXED)

Two repository-level checks were added to protect the submission freeze:

- `scripts/check_silent_excepts.py` — AST-based scan for broad `except` blocks with silent bodies (`pass`, `continue`, `return`, or pure logging + swallow)
- `scripts/check_stale_security_claims.py` — scan for stale security wording such as hashed `conversation_id` claims or outdated delivery-status semantics

Both checks are enforced in:

- `scripts/pre-commit`
- `.github/workflows/lint.yml`

**Impact:** The repo now blocks two high-risk regression classes:

- silent security failure caused by swallowed exceptions
- report/code mismatch caused by stale security prose

**Fix Applied:** Silent `except ...: pass` cases in `scripts/seed.py` were removed, broad catches were triaged into either narrower exception handling or explicit `# allow-silent-except` best-effort paths, and stale documentation claims were corrected.

### 3. Type Checking Issues (MyPy)

MyPy reported 178 errors, primarily:

- Missing type annotations for functions and parameters
- Missing type parameters for generic types like `dict` (should be `dict[str, Any]`)
- Calls to untyped functions in typed contexts
- Returning `Any` from typed functions

**Impact:** Potential runtime type errors, reduced IDE support, harder debugging.  
**Files affected:** `shared/protocol.py`, `client/crypto/session.py`, `server/core/config.py`, test files, etc.

**Recommendation:** Add proper type annotations throughout the codebase to match the `strict = true` MyPy configuration.

### 4. Potential Runtime Issues

- The application uses cryptographic operations extensively; type issues could lead to unexpected behavior.
- No runtime bugs were observed in focused hardening checks, but MyPy cleanup remains future work.

## Verification

- Guard scripts pass:
  - `UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_silent_excepts.py`
  - `UV_CACHE_DIR=$PWD/.uv-cache uv run python scripts/check_stale_security_claims.py`
- Guard-script unit tests pass:
  - `uv run --extra dev pytest tests/unit/test_check_silent_excepts.py tests/unit/test_check_stale_security_claims.py -v`
- UI hardening regression tests pass:
  - `uv run --extra dev pytest tests/unit/test_ui_security.py -v`
- Use the fresh `uv run --extra dev pytest -v` summary as the authoritative test evidence instead of a hard-coded count.

## Assurance Summary

We applied a pre-submission hardening gate focused on reliability and consistency rather than new feature work. The final assurance chain combines static analysis, repository-level invariant checks, type checking, and runtime tests: `ruff` for code-quality regressions, `bandit` for security smells, `check_silent_excepts.py` to block silent broad exception swallowing, `check_stale_security_claims.py` to block outdated security wording, `mypy` to catch boundary-level type drift and `None`/return-shape issues, and `pytest` for behavioral verification. This gives layered evidence that the system fails closed, that report claims remain aligned with the codebase, and that demo-critical paths are less likely to break due to hidden regressions.

In particular, we enforced two submission-specific invariants at repository level: no silent security failure and no stale security claims. Silent broad `except` patterns are rejected unless explicitly justified, while documentation and source comments are scanned for stale statements such as outdated `conversation_id` derivation or delivery-status wording. Together with the existing replay, tamper, TLS, and UI degradation tests, this hardening pass improves confidence that the delivered system is consistent across report, code, and observed behavior.

## Next Steps

1. ✅ Fixed the misplaced test file (moved to `scripts/demo_textual.py`).
2. ✅ Added repository guardrails for silent exception swallowing and stale security claims.
3. Re-run full submission gate before hand-in: `ruff`, `bandit`, both guard scripts, and `pytest`.
4. Gradually add type annotations to resolve MyPy errors.
5. Manually fix remaining non-security lint issues if time permits.

## Environment

- Python 3.12.11
- uv package manager
- macOS
