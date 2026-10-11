import base64
import binascii
import quopri
import re
from collections.abc import Iterator
from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "vcard.py 0.0.57"

# --- Version history ----------------------------------------------------
# v0.0.57: Initial vCard content lines: parsing (2.1, 3.0, 4.0), escaping, folding

MAXIMUM_TEXT_BYTES: int = 32 * 1024 * 1024  # more than this is refused: a vCard with a photo is a few megabytes at most
MAXIMUM_LINES_PER_CARD: int = 20_000
MAXIMUM_CARDS: int = 200_000
MAXIMUM_LINE_CHARACTERS: int = 12 * 1024 * 1024  # an inline photo is one long line
FOLD_OCTETS: int = 75

_NAME_CHARACTERS = re.compile(r"[A-Za-z0-9-]+")


@dataclass(frozen=True, slots=True)
class ContentLine:
    """One property of a card: a group (Apple's item1), a name, parameters, and the value as it was written (escapes not yet undone)."""

    name: str
    value: str
    params: tuple[tuple[str, tuple[str, ...]], ...] = ()
    group: str = ""

    def param(self, name: str) -> tuple[str, ...]:
        """The values of a parameter (names are case-insensitive); 2.1's bare parameters (TEL;HOME;VOICE) count as TYPE."""
        wanted = name.upper()
        return tuple(value for key, values in self.params if key == wanted for value in values)

    def types(self) -> tuple[str, ...]:
        """The TYPE values in lower case, whether written as TYPE=work,voice or as bare words, and with the PREF marker as a type in 4.0."""
        found: list[str] = []
        for value in self.param("TYPE"):
            found.extend(part.strip().lower() for part in value.split(",") if part.strip())
        return tuple(found)


def unfold(text: str) -> Iterator[str]:
    """The logical lines of a text: lines that start with a space or a tab continue the line before. Quoted-printable soft breaks (a line ending in =) are joined too."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    current: list[str] = []
    for line in lines:
        if line[:1] in (" ", "\t") and current:
            current.append(line[1:])
            continue
        if current:
            yield "".join(current)
        current = [line]
    if current:
        yield "".join(current)


def _join_quoted_printable(lines: list[str]) -> list[str]:
    joined: list[str] = []
    pending = ""
    for line in lines:
        text = pending + line
        if text.endswith("=") and "QUOTED-PRINTABLE" in text.split(":", 1)[0].upper():
            pending = text[:-1]
            continue
        pending = ""
        joined.append(text)
    if pending:
        joined.append(pending)
    return joined


def _split_params(text: str) -> list[str]:
    """The parameters of a line, split at ; outside double quotes."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for character in text:
        if character == '"':
            quoted = not quoted
            current.append(character)
        elif character == ";" and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return parts


def _split_values(text: str) -> tuple[str, ...]:
    """The comma-separated values of a parameter, the quotes taken off."""
    values: list[str] = []
    current: list[str] = []
    quoted = False
    for character in text:
        if character == '"':
            quoted = not quoted
        elif character == "," and not quoted:
            values.append("".join(current))
            current = []
        else:
            current.append(character)
    values.append("".join(current))
    return tuple(values)


def parse_line(line: str) -> ContentLine | None:
    """One logical line, or None when it is not a property (a blank line, junk). Never raises."""
    if len(line) > MAXIMUM_LINE_CHARACTERS:
        return None
    quoted = False
    colon = -1
    for position, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            colon = position
            break
    if colon <= 0:
        return None
    head, value = line[:colon], line[colon + 1 :]
    pieces = _split_params(head)
    name_part = pieces[0].strip()
    group = ""
    if "." in name_part:
        group, _, name_part = name_part.rpartition(".")
    if _NAME_CHARACTERS.fullmatch(name_part) is None or (group and _NAME_CHARACTERS.fullmatch(group) is None):
        return None
    params: list[tuple[str, tuple[str, ...]]] = []
    for piece in pieces[1:]:
        piece = piece.strip()
        if not piece:
            continue
        if "=" in piece:
            key, _, raw = piece.partition("=")
            params.append((key.strip().upper(), _split_values(raw)))
        else:
            params.append(("TYPE", (piece,)))  # vCard 2.1: TEL;HOME;VOICE
    line_value = value
    if any(key in ("ENCODING", "TYPE") and any(item.upper() == "QUOTED-PRINTABLE" for item in values) for key, values in params):  # also the bare 2.1 form
        charset = next((item for key, values in params if key == "CHARSET" for item in values), "utf-8")
        try:
            line_value = quopri.decodestring(value.encode("ascii", "replace")).decode(charset, "replace")
        except (LookupError, ValueError):
            line_value = value
        cleaned = [(key, tuple(item for item in values if item.upper() != "QUOTED-PRINTABLE")) for key, values in params if key not in ("ENCODING", "CHARSET")]
        params = [(key, values) for key, values in cleaned if values]
    return ContentLine(name_part.upper(), line_value, tuple(params), group)


def parse_cards(text: str | bytes) -> list[list[ContentLine]]:
    """The cards in a text, each as its content lines (BEGIN, END and VERSION included only when they are inside a card). Lenient: a card that is
    not closed is taken as far as it goes, text outside cards is ignored, and nothing a server sends makes this raise."""
    if isinstance(text, bytes):
        if len(text) > MAXIMUM_TEXT_BYTES:
            return []
        text = text.decode("utf-8", "replace")
    elif len(text) > MAXIMUM_TEXT_BYTES:
        return []
    text = text.lstrip("\ufeff")
    cards: list[list[ContentLine]] = []
    current: list[ContentLine] | None = None
    for line in _join_quoted_printable(list(unfold(text))):
        parsed = parse_line(line)
        if parsed is None:
            continue
        if parsed.name == "BEGIN" and parsed.value.strip().upper() == "VCARD":
            if current:
                cards.append(current)  # the one before was not closed
            current = []
            continue
        if current is None:
            continue
        if parsed.name == "END" and parsed.value.strip().upper() == "VCARD":
            cards.append(current)
            current = None
            if len(cards) >= MAXIMUM_CARDS:
                break
            continue
        if len(current) < MAXIMUM_LINES_PER_CARD:
            current.append(parsed)
    if current:
        cards.append(current)
    return cards


# --- values ----------------------------------------------------------------------------------------------------------------------------


def unescape(value: str) -> str:
    """Undo the escapes of a text value: \\n and \\N are line breaks, \\, ; and \\\\ stand for themselves."""
    if "\\" not in value:
        return value
    out: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "\\" and position + 1 < len(value):
            following = value[position + 1]
            out.append("\n" if following in "nN" else following)
            position += 2
        else:
            out.append(character)
            position += 1
    return "".join(out)


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def split_unescaped(value: str, separator: str) -> list[str]:
    """Split at a separator that is not escaped; the parts are still escaped."""
    parts: list[str] = []
    current: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "\\" and position + 1 < len(value):
            current.append(value[position : position + 2])
            position += 2
        elif character == separator:
            parts.append("".join(current))
            current = []
            position += 1
        else:
            current.append(character)
            position += 1
    parts.append("".join(current))
    return parts


def structured(value: str) -> list[str]:
    """A value of components split at ; (N, ADR, ORG), each unescaped."""
    return [unescape(part) for part in split_unescaped(value, ";")]


def listed(value: str) -> list[str]:
    """A value that is a list split at , (NICKNAME, CATEGORIES), each unescaped and without empty items."""
    return [item for item in (unescape(part).strip() for part in split_unescaped(value, ",")) if item]


def decode_binary(line: ContentLine, limit: int = 8 * 1024 * 1024) -> tuple[str, bytes] | None:
    """The media type and the bytes of an inline value (ENCODING=b, or a data: URI). None for anything else, or for something too large or damaged."""
    media = next(iter(line.param("MEDIATYPE")), "")
    value = line.value.strip()
    if value.lower().startswith("data:"):
        header, _, payload = value[5:].partition(",")
        if ";base64" not in header.lower():
            return None
        media = header.split(";", 1)[0] or media
        value = payload
    elif not any(item.lower() in ("b", "base64") for item in line.param("ENCODING")):
        return None
    if len(value) > limit * 4 // 3 + 16:
        return None
    try:
        data = base64.b64decode("".join(value.split()), validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(data) > limit:
        return None
    if not media:
        kind = next(iter(line.types()), "jpeg")
        media = kind if "/" in kind else f"image/{kind}"
    return media, data


# --- writing ---------------------------------------------------------------------------------------------------------------------------


def _quote_param(value: str) -> str:
    cleaned = value.replace('"', "'").replace("\n", " ").replace("\r", " ")
    return f'"{cleaned}"' if any(character in cleaned for character in ";:,") else cleaned


def fold(line: str) -> str:
    """A line cut into pieces of at most 75 bytes (UTF-8 characters are never split), each continuation starting with a space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= FOLD_OCTETS:
        return line
    pieces: list[str] = []
    current = ""
    size = 0
    limit = FOLD_OCTETS
    for character in line:
        width = len(character.encode("utf-8"))
        if size + width > limit:
            pieces.append(current)
            current, size, limit = "", 1, FOLD_OCTETS - 1  # the leading space of a continuation counts
        current += character
        size += width
    pieces.append(current)
    return "\r\n ".join(pieces)


def format_line(line: ContentLine) -> str:
    head = f"{line.group}.{line.name}" if line.group else line.name
    for key, values in line.params:
        head += f";{key}=" + ",".join(_quote_param(value) for value in values)
    return fold(f"{head}:{line.value}")


def format_card(lines: list[ContentLine], version: str = "3.0") -> str:
    """A card as text: BEGIN, VERSION, the lines, END, with CRLF line ends."""
    body = [format_line(line) for line in lines if line.name not in ("VERSION", "BEGIN", "END")]
    return "\r\n".join(["BEGIN:VCARD", f"VERSION:{version}", *body, "END:VCARD"]) + "\r\n"
