# --- Version ------------------------------------------------------------
__version__ = "words.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: digits: only 0 to 9 (isdigit also takes "²", which int() refuses)
# v2.4.333: Initial: one way to write a count with its word ("1 message", "2 messages", "1,041 messages"), used by every text that counts
#           (the Windows computer's wording notes: "1 messages", "1041", "message(s)")


def digits(text: str) -> bool:
    """Whether text is one or more of the digits 0 to 9 only, so int() takes it: str.isdigit() also takes "²" and other digits of other
    scripts, which int() refuses (a server's or a file's text could stop a whole check with a ValueError)."""
    return text.isascii() and text.isdigit()


def counted(count: int, word: str, plural: str = "") -> str:
    """A count with its word as a person writes it: "1 message", "2 messages", "1,041 messages" (plural: when it is not word + "s")."""
    return f"{count:,} {word if count == 1 else plural or word + 's'}"
