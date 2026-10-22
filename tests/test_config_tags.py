import pytest

from chrono_mail.config import TAG_KINDS, TagColours, parse_tags, tags_to_text
from chrono_mail.core import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "test_config_tags.py 0.5.135"

# --- Version history ----------------------------------------------------
# v0.5.135: Initial tests: the order of the tags, their shortcuts and what they are for


def three() -> TagColours:
    return TagColours().with_colour("Home", "#ff0000").with_colour("Work", "#00ff00").with_colour("School", "#0000ff")


def names(tags: TagColours) -> list[str]:
    return [entry.shown for entry in tags.entries]


def test_tags_keep_the_order_they_were_made_in_and_a_new_colour_keeps_the_place() -> None:
    tags = three().with_colour("work", "#123456")
    assert names(tags) == ["Home", "work", "School"] and tags.colour_of("WORK") == "#123456"
    assert names(parse_tags(tags_to_text(tags))) == ["Home", "work", "School"]


def test_a_tag_moves_up_and_down_and_to_a_place() -> None:
    tags = three()
    assert names(tags.moved("School", -1)) == ["Home", "School", "Work"]
    assert names(tags.moved("Home", -1)) == ["Home", "Work", "School"]  # at the top it stays
    assert names(tags.placed("School", 0)) == ["School", "Home", "Work"]
    assert names(tags.placed("Home", 99)) == ["Work", "School", "Home"]


def test_a_shortcut_belongs_to_one_tag_and_kinds_are_checked() -> None:
    tags = three().with_details("Home", "Ctrl+Alt+1", ("mail", "tasks")).with_details("Work", "Ctrl+Alt+1", TAG_KINDS)
    home, work = tags.entry("home"), tags.entry("work")
    assert home is not None and work is not None
    assert (home.shortcut, home.kinds, work.shortcut) == ("", ("mail", "tasks"), "Ctrl+Alt+1")
    with pytest.raises(ConfigurationError):
        tags.with_details("Home", "Ctrl+Q", TAG_KINDS)
    with pytest.raises(ConfigurationError):
        tags.with_details("Home", "", ())
    with pytest.raises(ConfigurationError):
        tags.with_details("Nobody", "", TAG_KINDS)


def test_a_renamed_tag_keeps_its_place_shortcut_and_kinds() -> None:
    tags = three().with_details("Work", "Ctrl+Alt+2", ("mail",)).renamed("work", "Job")
    entry = tags.entry("job")
    assert names(tags) == ["Home", "Job", "School"] and entry is not None and (entry.shortcut, entry.kinds, entry.colour) == ("Ctrl+Alt+2", ("mail",), "#00ff00")


def test_the_details_are_saved_and_a_file_without_them_still_loads() -> None:
    tags = three().with_details("Home", "Ctrl+Alt+3", ("events",))
    again = parse_tags(tags_to_text(tags))
    entry = again.entry("home")
    assert entry is not None and (entry.shortcut, entry.kinds) == ("Ctrl+Alt+3", ("events",))
    old = parse_tags('{"format": 1, "tags": [{"name": "Old", "colour": "#abcdef"}]}')
    kept = old.entry("old")
    assert kept is not None and (kept.colour, kept.shortcut, kept.kinds) == ("#abcdef", "", TAG_KINDS)
