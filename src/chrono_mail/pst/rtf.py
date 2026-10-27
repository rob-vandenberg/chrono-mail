import re

from chrono_mail.pst.properties import codec_for

# --- Version ------------------------------------------------------------
__version__ = "rtf.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: compressed RTF (MS-OXRTFCP), HTML kept inside RTF (MS-OXRTFEX), and plain text from RTF

MAXIMUM_RTF_BYTES: int = 64 * 1024 * 1024
MAXIMUM_TEXT_CHARACTERS: int = 64 * 1024 * 1024

_COMPRESSED = 0x75465A4C  # "LZFu"
_UNCOMPRESSED = 0x414C454D  # "MELA"
_PREBUFFER = (
    b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern \\fscript \\fdecor MS Sans SerifSymbolArial"
    b"Times New RomanCourier{\\colortbl\\red0\\green0\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx"
)
_WINDOW = 4096


class RtfError(ValueError):
    """The compressed RTF is damaged."""


def decompress(data: bytes) -> bytes:
    """The RTF of a PR_RTF_COMPRESSED value: a 16-byte header (sizes, kind, CRC), then LZ77 runs over a 4096-byte window that starts
    with a fixed text, or the RTF as it is ("MELA")."""
    if len(data) < 16:
        raise RtfError("too short")
    compressed_size = int.from_bytes(data[0:4], "little")
    raw_size = int.from_bytes(data[4:8], "little")
    kind = int.from_bytes(data[8:12], "little")
    if raw_size > MAXIMUM_RTF_BYTES:
        raise RtfError("too large")
    end = min(len(data), compressed_size + 4) if compressed_size >= 12 else len(data)
    if kind == _UNCOMPRESSED:
        return data[16 : 16 + raw_size]
    if kind != _COMPRESSED:
        raise RtfError("unknown kind")
    window = bytearray(_WINDOW)
    window[: len(_PREBUFFER)] = _PREBUFFER
    write = len(_PREBUFFER)
    out = bytearray()
    position = 16
    while position < end:
        control = data[position]
        position += 1
        for bit in range(8):
            if position >= end:
                break
            if control & (1 << bit):
                if position + 1 >= end:
                    raise RtfError("cut off")
                reference = (data[position] << 8) | data[position + 1]
                position += 2
                offset = reference >> 4
                length = (reference & 0x0F) + 2
                if offset == write:
                    return bytes(out[:raw_size]) if raw_size else bytes(out)
                for step in range(length):
                    byte = window[(offset + step) % _WINDOW]
                    out.append(byte)
                    window[write] = byte
                    write = (write + 1) % _WINDOW
            else:
                byte = data[position]
                position += 1
                out.append(byte)
                window[write] = byte
                write = (write + 1) % _WINDOW
            if len(out) > MAXIMUM_RTF_BYTES:
                raise RtfError("too large")
    return bytes(out[:raw_size]) if raw_size else bytes(out)


def is_html(rtf: bytes) -> bool:
    """The RTF holds an HTML body (\\fromhtml1 in its header, MS-OXRTFEX 2.1.3.1)."""
    return b"\\fromhtml1" in rtf[:1024]


_SKIPPED_DESTINATIONS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "headerl", "headerr", "footerl", "footerr", "listtable",
    "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "themedata", "colorschememapping", "datastore", "latentstyles", "object",
    "objdata", "filetbl", "revtbl", "pgdsctbl", "mmathPr", "fldinst", "bkmkstart", "bkmkend",
})
_SYMBOLS: dict[str, str] = {
    "tab": "\t", "emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022", "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c",
    "rdblquote": "\u201d", "emspace": "\u2003", "enspace": "\u2002", "qmspace": "\u2005", "cell": "\t",
}
_CONTROL = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?")
_HEX = re.compile(rb"[0-9A-Fa-f]{2}")


class _State:
    __slots__ = ("in_tag", "rtf_only", "skip", "skip_count")

    def __init__(self, skip: bool = False, rtf_only: bool = False, in_tag: bool = False, skip_count: int = 1) -> None:
        self.skip = skip
        self.rtf_only = rtf_only
        self.in_tag = in_tag
        self.skip_count = skip_count

    def copy(self) -> "_State":
        return _State(self.skip, self.rtf_only, self.in_tag, self.skip_count)


def _convert(rtf: bytes, html: bool) -> str:
    """The text of an RTF document. With html, the HTML kept inside it: the \\*\\htmltag groups and the text outside \\htmlrtf."""
    out: list[str] = []
    pending = bytearray()
    codec = "cp1252"
    state = _State()
    stack: list[_State] = []
    group_start = False
    starred = False
    to_skip: int = 0  # characters after \uN that stand for it in older readers
    high: list[int] = []  # the first half of a surrogate pair, waiting for its second half
    size = 0
    line_break = "\r\n" if html else "\n"

    def shown() -> bool:
        return not state.skip and (state.in_tag or not state.rtf_only)

    def flush() -> None:
        nonlocal size
        if pending and high:
            high.clear()
            if shown():
                out.append("\ufffd")
        if pending:
            text = bytes(pending).decode(codec, errors="replace")
            pending.clear()
            if shown():
                out.append(text)
                size += len(text)

    def emit(text: str) -> None:
        nonlocal size
        flush()
        if shown():
            out.append(text)
            size += len(text)

    position = 0
    length = len(rtf)
    while position < length and size < MAXIMUM_TEXT_CHARACTERS:
        byte = rtf[position]
        if byte == 0x7B:  # {
            flush()
            stack.append(state.copy())
            if len(stack) > 1000:
                break
            group_start, starred = True, False
            position += 1
            continue
        if byte == 0x7D:  # }
            flush()
            if stack:
                state = stack.pop()
            group_start, starred = False, False
            position += 1
            continue
        if byte == 0x5C:  # backslash
            following = rtf[position + 1 : position + 2]
            if following == b"'":
                digits = rtf[position + 2 : position + 4]
                if not _HEX.fullmatch(digits):
                    position += 2  # a damaged escape: what follows is read as text
                    continue
                value = int(digits, 16)
                position += 4
                if to_skip:
                    to_skip -= 1
                elif not state.skip:
                    pending.append(value)
                group_start = False
                continue
            if following == b"*":
                starred = True
                position += 2
                continue
            if following in (b"\\", b"{", b"}"):
                if to_skip:
                    to_skip -= 1
                elif not state.skip:
                    pending.extend(following)
                position += 2
                group_start = False
                continue
            if following in (b"\r", b"\n"):
                emit(line_break)
                position += 2
                continue
            if following == b"~":
                emit("\u00a0")
                position += 2
                continue
            if following in (b"-", b"_", b":", b"|"):
                if following == b"_":
                    emit("-")
                position += 2
                continue
            match = _CONTROL.match(rtf, position)
            if match is None:
                position += 2
                continue
            position = match.end()
            word = match.group(1).decode("ascii")
            number_text = match.group(2)
            number = int(number_text) if number_text is not None else None
            first = group_start
            group_start = False
            if first and starred:
                flush()
                starred = False
                if html and word == "htmltag":
                    state.in_tag, state.skip = True, False
                else:
                    state.skip = True
                continue
            if first and word in _SKIPPED_DESTINATIONS:
                flush()
                state.skip = True
                continue
            if word == "ansicpg" and number is not None:
                flush()
                codec = codec_for(number)
            elif word == "uc" and number is not None:
                state.skip_count = max(0, min(number, 10))
            elif word == "u" and number is not None:
                code = number % 65536
                if 0xDC00 <= code < 0xE000 and high:
                    code = 0x10000 + ((high.pop() - 0xD800) << 10) + (code - 0xDC00)
                    emit(chr(code))
                else:
                    if high:
                        high.clear()
                        emit("\ufffd")
                    if 0xD800 <= code < 0xDC00:
                        flush()
                        high.append(code)
                    else:
                        emit("\ufffd" if 0xDC00 <= code < 0xE000 else chr(code))
                to_skip = state.skip_count
            elif word == "bin" and number is not None:
                flush()
                position += max(0, number)  # raw bytes (a picture, an object): never text, never a brace
            elif word == "htmlrtf":
                flush()
                state.rtf_only = number != 0
            elif word in ("par", "line", "row", "sect", "page"):
                emit(line_break)
            elif word in _SYMBOLS:
                emit(_SYMBOLS[word])
            continue
        if byte in (0x0D, 0x0A):
            position += 1
            continue
        group_start = False
        if to_skip:
            to_skip -= 1
        elif not state.skip:
            pending.append(byte)
        position += 1
    flush()
    if high:
        emit("\ufffd")
    return "".join(out)


def html_from_rtf(rtf: bytes) -> str | None:
    """The HTML an HTML mail was turned into RTF from; None when the RTF holds no HTML."""
    return _convert(rtf, html=True) if is_html(rtf) else None


def text_from_rtf(rtf: bytes) -> str:
    """The text of an RTF document, without its formatting."""
    return _convert(rtf, html=False).strip("\r\n")
