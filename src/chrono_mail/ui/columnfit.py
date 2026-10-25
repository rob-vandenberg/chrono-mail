import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from chrono_mail.ui.scale import lpx, message_list
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "columnfit.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v0.3.108: Icon columns and the smallest widths follow the size factor of the message list; icon widths are kept as the width at the standard size
# v0.1.89: Initial column fit: the columns always fill the width of the list, text columns as shares, icon columns in pixels

MINIMUM_TEXT: int = 50  # a text column is never narrower
MINIMUM_ICON: int = 24  # an icon column is never narrower (the icon and a few pixels on each side)


@dataclass
class ColumnLayout:
    """What is remembered: icon columns in pixels, text columns as shares of the width left after the icon columns."""

    icons: dict[str, int] = field(default_factory=dict)
    shares: dict[str, float] = field(default_factory=dict)

    def to_text(self) -> str:
        return json.dumps({"icons": self.icons, "shares": {key: round(value, 6) for key, value in self.shares.items()}})


def read_layout(text: str) -> ColumnLayout | None:
    """A saved layout; None when there is none or it cannot be used. Unusable entries are left out."""
    try:
        data: object = load_json(text) if text else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    icons: object = data.get("icons", {})
    shares: object = data.get("shares", {})
    if not isinstance(icons, dict) or not isinstance(shares, dict):
        return None
    layout = ColumnLayout()
    for key, value in icons.items():
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and MINIMUM_ICON <= value <= 400:
            layout.icons[key] = value
    for key, value in shares.items():
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1:
            layout.shares[key] = float(value)
    return layout


def layout_from_pixels(order: Sequence[str], icon: Sequence[bool], widths: Sequence[int]) -> ColumnLayout:
    """The layout that gives these widths (also used to take over the pixel widths saved by older versions)."""
    layout = ColumnLayout()
    text_total = sum(width for width, is_icon in zip(widths, icon) if not is_icon)
    for column_id, is_icon, width in zip(order, icon, widths):
        if is_icon:
            layout.icons[column_id] = max(MINIMUM_ICON, round(width / message_list()))  # kept as the width at the standard size
        elif text_total > 0:
            layout.shares[column_id] = max(width, 1) / text_total
    return layout


def fit(order: Sequence[str], icon: Sequence[bool], layout: ColumnLayout, defaults: Sequence[int], available: int) -> list[int]:
    """The widths of the columns (in their visual order) that fill exactly the available width: icon columns keep their pixels,
    the text columns share what is left. A column without a saved width starts from its default. When the room is too small
    for every column at its smallest, each column gets its smallest width."""
    widths = [lpx(max(MINIMUM_ICON, layout.icons.get(column_id, default))) if is_icon else 0 for column_id, is_icon, default in zip(order, icon, defaults)]
    texts = [position for position, is_icon in enumerate(icon) if not is_icon]
    if not texts:
        return widths
    text_defaults = sum(defaults[position] for position in texts) or 1
    wanted = {position: layout.shares.get(order[position], defaults[position] / text_defaults) for position in texts}
    room = available - sum(widths)
    for position, width in _share_out(room, wanted, lpx(MINIMUM_TEXT)).items():
        widths[position] = width
    return widths


def drag(icon: Sequence[bool], widths: Sequence[int], section: int, wanted: int) -> list[int]:
    """The widths after the line right of the column at this visual position was dragged so far that the column would be
    `wanted` wide (the line moved by the difference).
    Next to an icon column, that icon column changes and all text columns give or take the difference in proportion; other
    icon columns keep their width. Between two icon columns the one farther from the text columns changes, so the line stays
    under the pointer. Between two text columns the left one changes and the text columns to its right give or take.
    The drag stops at the smallest widths."""
    result = list(widths)
    if not 0 <= section < len(widths) - 1:
        return result
    moved = wanted - widths[section]
    left, right = section, section + 1
    texts = [position for position, is_icon in enumerate(icon) if not is_icon]
    if icon[left] or icon[right]:
        if icon[left] and icon[right]:
            texts_after = any(position > right for position in texts)
            changing = left if texts_after or not texts else right
        else:
            changing = left if icon[left] else right
        change = moved if changing == left else -moved
        return _change_icon(widths, changing, change, texts)
    after = [position for position in texts if position > left]
    room = sum(widths[position] - lpx(MINIMUM_TEXT) for position in after)
    change = max(lpx(MINIMUM_TEXT) - widths[left], min(moved, max(room, 0)))
    if change == 0 or not after:
        return result
    result[left] += change
    _give(result, widths, after, change)
    return result


def _change_icon(widths: Sequence[int], changing: int, change: int, texts: list[int]) -> list[int]:
    """An icon column wider or narrower; every text column gives or takes in proportion."""
    result = list(widths)
    room = sum(widths[position] - lpx(MINIMUM_TEXT) for position in texts)
    change = max(lpx(MINIMUM_ICON) - widths[changing], min(change, max(room, 0)))
    if change == 0 or not texts:
        return result
    result[changing] += change
    _give(result, widths, texts, change)
    return result


def _give(result: list[int], widths: Sequence[int], positions: list[int], change: int) -> None:
    """These text columns give (change > 0) or take (change < 0) the change: they give in proportion to what each can still
    give, and take in proportion to their width."""
    if change > 0:
        give = {position: float(max(widths[position] - lpx(MINIMUM_TEXT), 0)) for position in positions}
        shares = give if sum(give.values()) > 0 else {position: float(widths[position]) for position in positions}
        for position, part in _split(change, shares).items():
            result[position] = widths[position] - part
    else:
        total = sum(widths[position] for position in positions) - change
        for position, width in _split(total, {position: float(widths[position]) for position in positions}).items():
            result[position] = width


def _share_out(room: int, wanted: dict[int, float], smallest: int) -> dict[int, int]:
    """Split the room over the positions in proportion to what they want, none below the smallest; the total is exactly the room
    (unless the room is too small for all at their smallest)."""
    fixed: dict[int, int] = {}
    free = dict(wanted)
    while free:
        left = room - sum(fixed.values())
        weights = sum(free.values()) or 1.0
        too_small = [position for position, share in free.items() if left * share / weights < smallest]
        if not too_small:
            break
        for position in too_small:
            fixed[position] = smallest
            del free[position]
    left = room - sum(fixed.values())
    if free and left > 0:
        fixed.update(_split(left, free))
    return fixed


def _split(amount: int, weights: dict[int, float]) -> dict[int, int]:
    """Whole numbers in proportion to the weights that add up to exactly the amount (the last position takes the rounding)."""
    total = sum(weights.values()) or 1.0
    positions = sorted(weights)
    parts = {position: int(amount * weights[position] / total) for position in positions}
    parts[positions[-1]] += amount - sum(parts.values())
    return parts
