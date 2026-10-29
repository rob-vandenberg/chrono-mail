from email.parser import BytesHeaderParser
from email.utils import parseaddr

# --- Version ------------------------------------------------------------
__version__ = "headers.py 4.2.417"

# --- Version history ----------------------------------------------------
# v4.2.417: header_values moved here from engine/rule_engine.py (the index needs it too, D-220); delivery_addresses: the addresses the
#           receiving server wrote the message was delivered to (the Account column, D-292; Reply's account)

MAXIMUM_HEADER_BYTES: int = 256 * 1024
DELIVERY_HEADERS: tuple[str, ...] = ("Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To")  # written by the receiving server


def _headers_values(raw: bytes, names: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """Every value of each of these headers, the header block read once. Never raises."""
    end = raw.find(b"\r\n\r\n")
    end = raw.find(b"\n\n") if end == -1 else end
    head = raw[: end if end != -1 else MAXIMUM_HEADER_BYTES][:MAXIMUM_HEADER_BYTES]
    try:
        message = BytesHeaderParser().parsebytes(head)
        return tuple(tuple(str(value).encode("utf-8", "surrogateescape").decode("utf-8", "replace") for value in message.get_all(name, [])) for name in names)
    except Exception:  # a hostile header must never stop the mail from arriving
        return tuple(() for _ in names)


def header_values(raw: bytes, name: str) -> tuple[str, ...]:
    """Every value of a header of the message (a header may occur several times). Never raises."""
    return _headers_values(raw, (name,))[0]


def delivery_addresses(raw: bytes) -> tuple[str, ...]:
    """The addresses (lower case) the receiving server wrote it delivered the message to, each once, in the order found. Never raises."""
    found: list[str] = []
    for values in _headers_values(raw, DELIVERY_HEADERS):
        for value in values:
            try:
                address = parseaddr(value)[1].strip().lower()
            except Exception:  # a hostile header must never stop the mail from arriving
                continue
            if address and address not in found:
                found.append(address)
    return tuple(found)
