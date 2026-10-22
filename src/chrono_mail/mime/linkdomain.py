import ipaddress
from functools import cache
from urllib.parse import unquote, urlsplit

from publicsuffixlist import PublicSuffixList

# --- Version ------------------------------------------------------------
__version__ = "linkdomain.py 1.5.257"

# --- Version history ----------------------------------------------------
# v1.5.257: Initial: whether a link in a message leads to another main domain than its sender's (D-215)

# Caps for text that comes from the message itself (AGENTS.md section 4.2).
MAXIMUM_LINK_LENGTH: int = 8192
MAXIMUM_HOST_LENGTH: int = 253  # the longest name DNS allows


@cache
def _suffixes() -> PublicSuffixList:
    """The Public Suffix List built into the package: read once, never fetched from the internet."""
    return PublicSuffixList()


def main_domain(host: str) -> str | None:
    """The main domain of a host name, the part someone registers ("alianz.nl" for "business.alianz.nl", "example.co.uk" for
    "www.example.co.uk"), in plain letters (ASCII) and lower case; None when it has none (a bare ending such as "co.uk", a
    single name such as "localhost", a name that is not valid)."""
    name = host.strip().rstrip(".").lower()
    if not name or len(name) > MAXIMUM_HOST_LENGTH:
        return None
    try:
        ascii_name = name.encode("idna").decode("ascii")  # look-alike letters are compared in their plain-letter form
    except UnicodeError:
        return None
    return _suffixes().privatesuffix(ascii_name)


def _is_address_number(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def link_domain(link: str) -> str | None:
    """The domain a link leads to: the server of a web link, the domain of a mailto address; None for a link without one
    (a jump inside the message, an embedded picture)."""
    if len(link) > MAXIMUM_LINK_LENGTH:
        link = link[:MAXIMUM_LINK_LENGTH]
    try:
        parts = urlsplit(link.strip())
        if parts.scheme.lower() == "mailto":
            address = unquote(parts.path.split("?", 1)[0].split(",", 1)[0])
            return address.rpartition("@")[2].strip() or None if "@" in address else None
        host = parts.hostname
    except ValueError:  # a damaged address, for example a port that is no number or an unclosed [
        return None
    return host or None


def leads_elsewhere(link: str, sender: str) -> bool:
    """Whether a link should carry a warning: it leads to another main domain than the sender's address, to a bare number
    address, or to a place whose main domain cannot be told; also when the sender has no usable address (same domain cannot
    be confirmed). A link without a domain never does."""
    domain = link_domain(link)
    if domain is None:
        return False
    if _is_address_number(domain):
        return True
    target = main_domain(domain)
    own = main_domain(sender.rpartition("@")[2]) if "@" in sender else None
    return target is None or own is None or target != own
