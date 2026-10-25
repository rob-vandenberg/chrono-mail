import dataclasses
import re
from datetime import datetime
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "render.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: json_safe: the --json output writes DEL and the C1 control characters as \\u escapes (RV-129)
# v3.6.349: printable for the progress and error lines too (RV-203)
# v3.4.344: control characters from mail and servers are printed as "?": a terminal never runs their escape codes (RV-129)
# v0.0.12: Initial text output

_INDENT = "  "
_CONTROL: re.Pattern[str] = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # every control character but tab and line break


_JSON_CONTROL: re.Pattern[str] = re.compile("[\x7f-\x9f]")  # what json.dumps leaves as it is among the control characters


def json_safe(text: str) -> str:
    """JSON for a terminal: DEL and the C1 control characters (U+0080 to U+009F, such as the 8-bit CSI) written as \\u escapes, as
    json.dumps already writes the others; the data stays the same for a program that reads it (RV-129)."""
    return _JSON_CONTROL.sub(lambda found: f"\\u{ord(found.group()):04x}", text)


def printable(text: str) -> str:
    """Text from a mail or a server, safe for a terminal on one line (the progress and error lines of the command line, RV-203): no escape
    codes, no line breaks that would make up lines, no marks that turn the order of the text around."""
    return _ONE_LINE.sub(" ", _printable(text))


_ONE_LINE: re.Pattern[str] = re.compile("[\n\r\u2028\u2029\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _printable(text: str) -> str:
    """Text from a mail or a server is hostile input: its escape codes could change the terminal or what it shows (RV-129)."""
    return _CONTROL.sub("?", text)


def _scalar(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, Enum):
        return _printable(str(value.value))
    return _printable(str(value))


def _lines(value: object, depth: int) -> list[str]:
    pad = _INDENT * depth
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        lines: list[str] = []
        for parameter_field in dataclasses.fields(value):
            item = getattr(value, parameter_field.name)
            label = parameter_field.name.replace("_", " ")
            if isinstance(item, (tuple, list)) and item:
                lines.append(f"{pad}{label}:")
                lines.extend(_lines(item, depth + 1))
            elif isinstance(item, (tuple, list)):
                lines.append(f"{pad}{label}: (none)")
            elif dataclasses.is_dataclass(item):
                lines.append(f"{pad}{label}:")
                lines.extend(_lines(item, depth + 1))
            elif isinstance(item, str) and "\n" in item:
                lines.append(f"{pad}{label}:")
                lines.extend(f"{pad}{_INDENT}{_printable(line)}" for line in item.splitlines())
            else:
                lines.append(f"{pad}{label}: {_scalar(item)}")
        return lines
    if isinstance(value, (tuple, list)):
        lines = []
        for position, item in enumerate(value):
            if dataclasses.is_dataclass(item) and not isinstance(item, type):
                if position:
                    lines.append("")
                lines.extend(_lines(item, depth))
            else:
                lines.append(f"{pad}{_scalar(item)}")
        return lines
    return [f"{pad}{_scalar(value)}"]


def render_text(result: object) -> str:
    """A command result as readable text."""
    if result is None or (isinstance(result, (tuple, list)) and not result):
        return "(nothing)"
    return "\n".join(_lines(result, 0))
