import dataclasses
import re
from datetime import datetime
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "render.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: control characters from mail and servers are printed as "?": a terminal never runs their escape codes (RV-129)
# v0.0.12: Initial text output

_INDENT = "  "
_CONTROL: re.Pattern[str] = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # every control character but tab and line break


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
