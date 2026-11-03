import ipaddress
import re
from functools import cache
from urllib.parse import unquote, urlsplit

from publicsuffixlist import PublicSuffixList

# --- Version ------------------------------------------------------------
__version__ = "linkdomain.py 4.3.503"

# --- Version history ----------------------------------------------------
# v4.3.503: a mailto link is judged by every address it writes to, also those after its ?; the person's own accounts' never count (the owner, D-215)
# v1.5.257: Initial: whether a link in a message leads to another main domain than its sender's (D-215)

# Caps for text that comes from the message itself (AGENTS.md section 4.2).
MAXIMUM_LINK_LENGTH: int = 8192
MAXIMUM_HOST_LENGTH: int = 253  # the longest name DNS allows


@cache
def _suffixes() -> PublicSuffixList:
    """The Public Suffix List built into the package: read once, never fetched from the internet."""
    return PublicSuffixList()


def main_domain(host: str) -> str | None:
    """The main domain of a host name, the part someone registers ("example.nl" for "business.example.nl", "example.co.uk" for
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


def mailto_addresses(link: str) -> frozenset[str]:
    """Every address a mailto link writes to, in lower case: those before the ? (also after mailto://) and those of its to, cc and bcc,
    separated by , or ;; empty for any other link or a damaged one."""
    try:
        parts = urlsplit(link[:MAXIMUM_LINK_LENGTH].strip())
        if parts.scheme.lower() != "mailto":
            return frozenset()
        path, _, query = parts.path.partition("?")
        query = query or parts.query
        fields = (field.partition("=") for field in query.split("&"))  # by hand: a + in an address stays a + (parse_qsl makes it a space)
        lists = [unquote(parts.netloc), unquote(path), *(unquote(value) for name, _, value in fields if unquote(name).lower() in ("to", "cc", "bcc"))]  # mailto://… too
    except ValueError:
        return frozenset()
    return frozenset(address for text in lists for piece in re.split("[,;]", text) if (address := piece.strip().strip("/").lower()))  # , or ;


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


def leads_elsewhere(link: str, sender: str, own: frozenset[str] = frozenset()) -> bool:
    """Whether a link should carry a warning: it leads to another main domain than the sender's address, to a bare number
    address, or to a place whose main domain cannot be told; also when the sender has no usable address (same domain cannot
    be confirmed). A mailto link is judged by every address it writes to (also those after its ?), and those of the person's own
    accounts (own, in lower case) never count: a quoted signature in nearly every reply made the warning meaningless (the owner,
    D-215). A link without a domain never warns."""
    senders = main_domain(sender.rpartition("@")[2]) if "@" in sender else None
    if mailto_addresses(link) or link.strip().lower().startswith("mailto:"):
        if len(link) > MAXIMUM_LINK_LENGTH or not _readable(link):
            return True  # what lies past the cap is never read, and a damaged link cannot be read: an address could hide there
        others = [address for address in mailto_addresses(link) if address not in own and "@" in address]  # one without a domain leads nowhere, as before
        if any(address.count("@") > 1 or any(character.isspace() for character in address) for address in others):
            return True  # no single address: what it writes to cannot be told
        return any(_warns(address.rpartition("@")[2], senders) for address in others)
    domain = link_domain(link)
    return domain is not None and _warns(domain, senders)


def _readable(link: str) -> bool:
    try:
        urlsplit(link.strip())
    except ValueError:
        return False
    return True


def _warns(domain: str, senders: str | None) -> bool:
    """Whether a place of this domain gets the warning, for a sender of this main domain (None: none that can be told)."""
    if not domain or _is_address_number(domain):
        return True
    target = main_domain(domain)
    return target is None or senders is None or target != senders
