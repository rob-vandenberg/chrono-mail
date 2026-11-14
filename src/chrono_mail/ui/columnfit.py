import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from chrono_mail.ui.scale import lpx, message_list
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "columnfit.py 4.4.615"

# --- Version history ----------------------------------------------------
# v4.4.615: fit takes a smallest width per text column (the Size column's is its widest text); the others keep MINIMUM_TEXT; drag
#           takes the same smallest widths, so a dragged line stops there too; the rounding of what the text columns give goes only to
#           the ones that can still give (it pushed a column at its smallest width a pixel or two below it)
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


def fit(order: Sequence[str], icon: Sequence[bool], layout: ColumnLayout, defaults: Sequence[int], available: int,
        minimums: Mapping[str, int] | None = None) -> list[int]:
    """The widths of the columns (in their visual order) that fill exactly the available width: icon columns keep their pixels,
    the text columns share what is left. A column without a saved width starts from its default. A text column is never narrower
    than its smallest width: the one given in minimums (pixels, by column id), else MINIMUM_TEXT. When the room is too small
    for every column at its smallest, each column gets its smallest width."""
    widths = [lpx(max(MINIMUM_ICON, layout.icons.get(column_id, default))) if is_icon else 0 for column_id, is_icon, default in zip(order, icon, defaults)]
    texts = [position for position, is_icon in enumerate(icon) if not is_icon]
    if not texts:
        return widths
    text_defaults = sum(defaults[position] for position in texts) or 1
    wanted = {position: layout.shares.get(order[position], defaults[position] / text_defaults) for position in texts}
    room = available - sum(widths)
    smallest = _smallest(order, texts, minimums)
    for position, width in _share_out(room, wanted, smallest).items():
        widths[position] = width
    return widths


def _smallest(order: Sequence[str], texts: list[int], minimums: Mapping[str, int] | None) -> dict[int, int]:
    """The smallest width of each text column (by visual position): the one given in minimums (pixels, by column id), at least MINIMUM_TEXT."""
    return {position: max(lpx(MINIMUM_TEXT), (minimums or {}).get(order[position], 0) if position < len(order) else 0) for position in texts}


def drag(icon: Sequence[bool], widths: Sequence[int], section: int, wanted: int, order: Sequence[str] = (),
         minimums: Mapping[str, int] | None = None) -> list[int]:
    """The widths after the line right of the column at this visual position was dragged so far that the column would be
    `wanted` wide (the line moved by the difference). order and minimums: the column ids in visual order and their smallest
    widths, as fit takes them.
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
    smallest = _smallest(order, texts, minimums)
    if icon[left] or icon[right]:
        if icon[left] and icon[right]:
            texts_after = any(position > right for position in texts)
            changing = left if texts_after or not texts else right
        else:
            changing = left if icon[left] else right
        change = moved if changing == left else -moved
        return _change_icon(widths, changing, change, texts, smallest)
    after = [position for position in texts if position > left]
    room = sum(widths[position] - smallest[position] for position in after)
    change = max(smallest[left] - widths[left], min(moved, max(room, 0)))
    if change == 0 or not after:
        return result
    result[left] += change
    _give(result, widths, after, change, smallest)
    return result


def _change_icon(widths: Sequence[int], changing: int, change: int, texts: list[int], smallest: dict[int, int]) -> list[int]:
    """An icon column wider or narrower; every text column gives or takes in proportion, none below its smallest width."""
    result = list(widths)
    room = sum(widths[position] - smallest[position] for position in texts)
    change = max(lpx(MINIMUM_ICON) - widths[changing], min(change, max(room, 0)))
    if change == 0 or not texts:
        return result
    result[changing] += change
    _give(result, widths, texts, change, smallest)
    return result


def _give(result: list[int], widths: Sequence[int], positions: list[int], change: int, smallest: dict[int, int]) -> None:
    """These text columns give (change > 0) or take (change < 0) the change: they give in proportion to what each can still
    give above its smallest width (the rounding goes to the ones that can still give, never below a smallest width), and take
    in proportion to their width."""
    if change > 0:
        give = {position: float(max(widths[position] - smallest[position], 0)) for position in positions}
        if sum(give.values()) > 0:
            givers = {position: amount for position, amount in give.items() if amount > 0}
            total = sum(givers.values())
            parts = {position: int(change * amount / total) for position, amount in givers.items()}
            rest = sorted(givers, key=lambda position: change * givers[position] / total - parts[position], reverse=True)
            for position in rest[: change - sum(parts.values())]:  # fewer than the givers: one pixel each, still within what each can give
                parts[position] += 1
        else:
            parts = _split(change, {position: float(widths[position]) for position in positions})
        for position, part in parts.items():
            result[position] = widths[position] - part
    else:
        total = sum(widths[position] for position in positions) - change
        for position, width in _split(total, {position: float(widths[position]) for position in positions}).items():
            result[position] = width


def _share_out(room: int, wanted: dict[int, float], smallest: dict[int, int]) -> dict[int, int]:
    """Split the room over the positions in proportion to what they want, none below its smallest; the total is exactly the room
    (unless the room is too small for all at their smallest)."""
    fixed: dict[int, int] = {}
    free = dict(wanted)
    while free:
        left = room - sum(fixed.values())
        weights = sum(free.values()) or 1.0
        too_small = [position for position, share in free.items() if left * share / weights < smallest[position]]
        if not too_small:
            break
        for position in too_small:
            fixed[position] = smallest[position]
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
