import json
import os
from datetime import datetime
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.engine.registry import UsageError

# --- Version ------------------------------------------------------------
__version__ = "schedule.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial times for sending later and snoozing

TOLERANCE_SECONDS: int = 60
MAXIMUM_SECONDS_AHEAD: int = 5 * 366 * 86_400


def parse_when(text: str, now: float) -> int:
    """A local time such as 2026-10-11T09:00 (or with a space) as seconds since 1970. It must not be in the past."""
    try:
        moment = datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        raise UsageError("A time is written like 2026-10-11T09:00") from None
    epoch = int(moment.astimezone().timestamp())
    if epoch < now - TOLERANCE_SECONDS:
        raise UsageError("That time has passed already")
    if epoch > now + MAXIMUM_SECONDS_AHEAD:
        raise UsageError("That time is too far ahead")
    return epoch


def describe_when(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M")


def load_snoozed(path: Path) -> dict[str, dict[str, object]]:
    """store id -> {"until": seconds, "folder": the folder it came from}. A missing or damaged file is an empty list."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("until"), int) and isinstance(value.get("folder"), str)}


def save_snoozed(path: Path, entries: dict[str, dict[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, json.dumps(entries).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError(f"The snoozed messages could not be saved: {error.strerror}") from None
