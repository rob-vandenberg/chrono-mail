import os
import re

from chrono_mail.core import MessageFlag, flags_from_maildir_suffix, flags_to_maildir_suffix

# --- Version ------------------------------------------------------------
__version__ = "layout.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: Initial store layout helpers

# The standard Maildir info separator ':' is not allowed in Windows file names, so every
# platform uses '!' (D-024).
SEPARATOR: str = "!"
INFO_PREFIX: str = "2,"
_NAME_PATTERN: re.Pattern[str] = re.compile(r"[0-9a-f]{32}(?:!2,(?P<info>[A-Za-z]*))?")
_KNOWN_LETTERS: frozenset[str] = frozenset(flag.maildir_letter for flag in MessageFlag)


def new_store_id() -> str:
    """A random, meaningless identity: 128 bits as 32 lower-case hex characters."""
    return os.urandom(16).hex()


def parse_name(name: str) -> tuple[str, str | None] | None:
    """(store id, info letters or None when the name has no info part), or None for a foreign file."""
    match = _NAME_PATTERN.fullmatch(name)
    if match is None:
        return None
    return name[:32], match.group("info")


def build_name(store_id: str, info: str | None) -> str:
    return store_id if info is None else f"{store_id}{SEPARATOR}{INFO_PREFIX}{info}"


def split_info(info: str) -> tuple[frozenset[MessageFlag], str]:
    """The flags we know, and the other letters (for example P or a lower-case keyword) to preserve."""
    extra = "".join(letter for letter in info if letter not in _KNOWN_LETTERS)
    return flags_from_maildir_suffix(info), extra


def join_info(flags: frozenset[MessageFlag], extra: str) -> str:
    """Maildir requires the flag letters in ASCII order."""
    return "".join(sorted(flags_to_maildir_suffix(flags) + extra))
