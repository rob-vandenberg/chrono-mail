from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "flags.py 4.4.519"

# --- Version history ----------------------------------------------------
# v4.4.519: INVITE_SENT: a request for the latest version answered, on the mail (D-310)
# v4.4.517: Answer marks of an invitation: declined and delegated, kept on the mail
# v0.0.4: Initial message flags


class MessageFlag(Enum):
    """A flag on a message. Value: (Maildir letter, IMAP name)."""

    DRAFT = ("D", "\\Draft")
    FLAGGED = ("F", "\\Flagged")
    REPLIED = ("R", "\\Answered")
    SEEN = ("S", "\\Seen")
    TRASHED = ("T", "\\Deleted")
    INVITE_DECLINED = ("J", "$InviteDeclined")  # you declined the invitation of this mail: nothing of it is left in a calendar to say so
    INVITE_DELEGATED = ("K", "$InviteDelegated")  # you passed it on
    INVITE_SENT = ("L", "$InviteSent")  # you sent the latest version an attendee asked for

    @property
    def maildir_letter(self) -> str:
        return self.value[0]

    @property
    def imap_name(self) -> str:
        return self.value[1]


ANSWER_MARKS: frozenset[MessageFlag] = frozenset({MessageFlag.INVITE_DECLINED, MessageFlag.INVITE_DELEGATED, MessageFlag.INVITE_SENT})  # keywords on a server: a folder that keeps none keeps them here only (OR-21)
_FLAG_BY_LETTER: dict[str, MessageFlag] = {flag.maildir_letter: flag for flag in MessageFlag}


def flags_to_maildir_suffix(flags: frozenset[MessageFlag]) -> str:
    """The Maildir flag letters for these flags, in the ASCII order Maildir requires."""
    return "".join(sorted(flag.maildir_letter for flag in flags))


def flags_from_maildir_suffix(suffix: str) -> frozenset[MessageFlag]:
    """The flags for these Maildir letters. Unknown letters (for example P) are ignored."""
    return frozenset(_FLAG_BY_LETTER[letter] for letter in suffix if letter in _FLAG_BY_LETTER)
