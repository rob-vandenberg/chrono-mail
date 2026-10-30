from email.utils import parseaddr

# --- Version ------------------------------------------------------------
__version__ = "names.py 4.2.430"

# --- Version history ----------------------------------------------------
# v4.2.430: displayed_address: the address as a person reads it, the quotes left out; written_address: a name with special characters (an @, a comma) in quotes, so the address can be read back
# v0.5.166: Initial: the name of a sender as the From column shows it, one rule for the index and the window (D-153)


def shown_name(sender: str) -> str:
    """The name part of 'Name <address>' (without quotes), or the address when there is no name: what the From column
    shows, and what sorting and grouping by sender use."""
    if "<" in sender and sender.rstrip().endswith(">"):
        name = sender[: sender.rindex("<")].strip().strip('"')
        return name or sender[sender.rindex("<") + 1 : -1].strip()
    return sender.strip()


_SPECIALS: frozenset[str] = frozenset('()<>@,;:"[]')  # a name holding one of these cannot be read back unquoted (a dot can: "J. Smith" stays as it is)


def written_address(name: str, address: str) -> str:
    """'Name <address>' as it can be read back: a name with a comma, an @ or another special character in quotes (a name that is itself an
    address, "Doe, John"); without a name the address alone."""
    if not name:
        return address
    if any(character in _SPECIALS for character in name):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{address}>"


def displayed_address(text: str) -> str:
    """An address written by written_address as a person reads it: 'Doe, John <john@example.org>', the quotes left out (the reader's and the
    print-out's lines; the list keeps its names the same way)."""
    name, address = parseaddr(text)
    if not address:
        return text
    return f"{name} <{address}>" if name else address
