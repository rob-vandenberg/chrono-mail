import json
from typing import Any

# --- Version ------------------------------------------------------------
__version__ = "jsontext.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: Initial: one way to read JSON that a file, a backup or a server gives; nested without end it is damaged JSON, never a crash


def load_json(text: str | bytes | bytearray) -> Any:  # noqa: ANN401 - Any as json.loads gives: every caller checks what it got, as before
    """JSON from text this program did not write itself this moment (a file, a backup, a setting). Raises ValueError for anything that is not
    JSON, also for JSON nested so deep that reading it would run out of stack (Python's reader raises RecursionError there, which no caller
    expects: a crafted backup or file stopped the program, AGENTS.md 4.2)."""
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("The JSON is nested too deeply") from None
