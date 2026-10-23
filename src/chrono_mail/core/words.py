# --- Version ------------------------------------------------------------
__version__ = "words.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: Initial: one way to write a count with its word ("1 message", "2 messages", "1,041 messages"), used by every text that counts
#           (the Windows computer's wording notes: "1 messages", "1041", "message(s)")


def counted(count: int, word: str, plural: str = "") -> str:
    """A count with its word as a person writes it: "1 message", "2 messages", "1,041 messages" (plural: when it is not word + "s")."""
    return f"{count:,} {word if count == 1 else plural or word + 's'}"
