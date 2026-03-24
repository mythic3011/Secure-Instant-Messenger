# Bug Report

## Summary

As the bug hunter for the COMP3334 Secure Instant Messenger project, I conducted a systematic review of the codebase to identify potential issues. The project has passing tests (53/53), but several code quality and structural issues were found.

## Findings

### 1. Misplaced Test File (FIXED)

**File:** `tests/ui/test_textual.py` (moved to `scripts/demo_textual.py`)
**Issue:** This file contained a `TestApp` class that inherited from `textual.app.App` but had no test methods. Pytest could not collect it as a test class because it had an `__init__` constructor, resulting in a warning:
`PytestCollectionWarning: cannot collect test class 'TestApp' because it has a __init__ constructor`

**Impact:** Cluttered test output with warnings. The file was a demo application rather than a test.
**Fix Applied:** Moved the file to `scripts/demo_textual.py` to remove it from test collection. Tests now run cleanly with no warnings.

### 2. Code Quality Issues (Ruff Linting) (PARTIALLY FIXED)

Ruff initially identified 184 errors across the codebase. After running `uv run ruff check --fix`, 67 errors were auto-fixed, leaving 118 remaining.

**Fixed Issues:**

- Import organization issues (65 fixed)
- Some unused imports and deprecated imports

**Remaining Issues:**

- Line length violations (E501) - multiple files exceed 100 characters
- Security issues (e.g., `try`-`except`-`continue` without logging in `client/crypto/storage.py`)
- Other style violations

**Impact:** Code readability and maintainability improved, but further manual fixes needed for line lengths.
**Recommendation:** Manually break long lines and address security concerns.

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
- No runtime bugs were observed in quick tests, but the linting errors suggest areas for improvement.

## Verification

- All unit, integration, and security tests pass cleanly (53/53) with no warnings.
- No functional bugs detected in test suite.
- Code quality improved: one structural issue fixed, linting errors reduced from 184 to 118.
- Type checking issues (MyPy) remain for future improvement.

## Next Steps

1. ✅ Fixed the misplaced test file (moved to `scripts/demo_textual.py`).
2. ✅ Ran `ruff check --fix` to auto-correct style issues (67 fixed, 118 remaining).
3. Gradually add type annotations to resolve MyPy errors.
4. Manually fix remaining line length violations.
5. Re-run checks to ensure no regressions.

## Environment

- Python 3.12.11
- uv package manager
- macOS
