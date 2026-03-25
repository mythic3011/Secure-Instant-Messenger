# scripts/patch_log_exceptions.py
import pathlib
import re

ROOT = pathlib.Path(".")

PATTERN = re.compile(
    r"except Exception:\n(\s+)pass",
    re.MULTILINE,
)

REPLACEMENT = r"""except Exception as exc:
\1import logging
\1logging.getLogger(__name__).warning("ignored exception", exc_info=exc)
"""

for py in ROOT.rglob("*.py"):
    if "venv" in str(py):
        continue
    text = py.read_text()
    new = PATTERN.sub(REPLACEMENT, text)
    if new != text:
        py.write_text(new)
        print(f"patched: {py}")
