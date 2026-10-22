from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtGui import QColor

from chrono_mail.ui.formatting import avatar_colour, display_name, file_size, initials, long_date, short_date

# --- Version ------------------------------------------------------------
__version__ = "test_ui_formatting.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Initial UI formatting tests


@pytest.mark.parametrize(
    ("sender", "expected"),
    [("Alice Example <alice@example.com>", "Alice Example"), ('"Smith, Bob" <b@x.org>', "Smith, Bob"), ("<only@example.com>", "only@example.com"),
     ("plain@example.com", "plain@example.com"), ("  spaced <s@x>  ", "spaced"), ("", ""), ("Name <broken", "Name <broken"), ("<>", "")],
)
def test_display_name(sender: str, expected: str) -> None:
    assert display_name(sender) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Alice Example", "AE"), ("alice", "A"), ("Jean-Luc Picard", "JP"), ("  ", "?"), ("", "?"), ("é ñ", "ÉÑ"), ("Mary Jane Watson", "MW"), ("+++", "?"), ("王 小明", "王小")],
)
def test_initials(name: str, expected: str) -> None:
    assert initials(name) == expected


def test_avatar_colour_is_stable_and_case_insensitive() -> None:
    assert avatar_colour("alice@example.com") == avatar_colour("Alice@Example.COM")
    assert avatar_colour("a@x.org").isValid()
    assert len({avatar_colour(f"person{n}@example.com").name() for n in range(300)}) > 40  # real variety (72 hues)


def test_short_date_for_each_age() -> None:
    now = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    assert short_date(None, now) == ""
    assert short_date(now, now) == now.astimezone().strftime("%H:%M")  # the same day: only the time
    assert short_date(now - timedelta(days=1), now) == "Yesterday"
    assert short_date(now - timedelta(days=3), now) == (now - timedelta(days=3)).astimezone().strftime("%A")
    assert short_date(now - timedelta(days=30), now) == (now - timedelta(days=30)).astimezone().strftime("%d %b")
    assert short_date(now - timedelta(days=500), now) == (now - timedelta(days=500)).astimezone().strftime("%d %b %Y")
    assert short_date(now + timedelta(days=3), now) == (now + timedelta(days=3)).astimezone().strftime("%d %b")  # a date in the future is not "this week"


def test_long_date() -> None:
    assert long_date(None) == ""
    assert "2026" in long_date(datetime(2026, 10, 8, 10, 30, tzinfo=UTC))


@pytest.mark.parametrize(("size", "expected"), [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB"), (3 * 1024**3, "3.0 GB"), (10 * 1024**4, "10240.0 GB")])
def test_file_size(size: int, expected: str) -> None:
    assert file_size(size) == expected


def contrast_with_white(colour: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(colour.redF()) + 0.7152 * channel(colour.greenF()) + 0.0722 * channel(colour.blueF())
    return 1.05 / (luminance + 0.05)


def test_every_avatar_colour_takes_white_initials_readably() -> None:
    from chrono_mail.ui.formatting import AVATAR_HUES, AVATAR_LIGHTNESS, AVATAR_SATURATION

    for hue in range(AVATAR_HUES):  # every colour that can ever be chosen
        colour = QColor.fromHslF(hue / AVATAR_HUES, AVATAR_SATURATION, AVATAR_LIGHTNESS)
        assert contrast_with_white(colour) >= 4.5, hue
    assert all(contrast_with_white(avatar_colour(f"sender{n}@example.org")) >= 4.5 for n in range(500))
