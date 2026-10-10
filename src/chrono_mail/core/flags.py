from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "flags.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial message flags


class MessageFlag(Enum):
    """A flag on a message. Value: (Maildir letter, IMAP name)."""

    DRAFT = ("D", "\\Draft")
    FLAGGED = ("F", "\\Flagged")
    REPLIED = ("R", "\\Answered")
    SEEN = ("S", "\\Seen")
    TRASHED = ("T", "\\Deleted")

    @property
    def maildir_letter(self) -> str:
        return self.value[0]

    @property
    def imap_name(self) -> str:
        return self.value[1]


_FLAG_BY_LETTER: dict[str, MessageFlag] = {flag.maildir_letter: flag for flag in MessageFlag}


def flags_to_maildir_suffix(flags: frozenset[MessageFlag]) -> str:
    """The Maildir flag letters for these flags, in the ASCII order Maildir requires."""
    return "".join(sorted(flag.maildir_letter for flag in flags))


def flags_from_maildir_suffix(suffix: str) -> frozenset[MessageFlag]:
    """The flags for these Maildir letters. Unknown letters (for example P) are ignored."""
    return frozenset(_FLAG_BY_LETTER[letter] for letter in suffix if letter in _FLAG_BY_LETTER)
