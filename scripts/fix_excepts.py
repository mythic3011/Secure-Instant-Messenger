# scripts/fix_excepts.py
import pathlib
import re

ROOT = pathlib.Path(".")

PATTERN = re.compile(
    r"except Exception:\n(\s+)pass",
    re.MULTILINE,
)

REPLACEMENT = r"""except Exception as exc:
\1import logging
\1logging.getLogger(__name__).error("unexpected error", exc_info=exc)
\1raise"""


def process_file(path: pathlib.Path):
    text = path.read_text()
    new = PATTERN.sub(REPLACEMENT, text)
    if new != text:
        path.write_text(new)
        print(f"fixed: {path}")


for py in ROOT.rglob("*.py"):
    if "venv" in str(py):
        continue
    process_file(py)
