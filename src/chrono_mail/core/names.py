# --- Version ------------------------------------------------------------
__version__ = "names.py 0.5.166"

# --- Version history ----------------------------------------------------
# v0.5.166: Initial: the name of a sender as the From column shows it, one rule for the index and the window (D-153)


def shown_name(sender: str) -> str:
    """The name part of 'Name <address>' (without quotes), or the address when there is no name: what the From column
    shows, and what sorting and grouping by sender use."""
    if "<" in sender and sender.rstrip().endswith(">"):
        name = sender[: sender.rindex("<")].strip().strip('"')
        return name or sender[sender.rindex("<") + 1 : -1].strip()
    return sender.strip()
