from pathlib import Path

from chrono_mail.config.tags import first_start, give_starting_tags, load_tags, save_tags, starting_tags, TagColours

# --- Version ------------------------------------------------------------
__version__ = "test_config_starting_tags.py 4.4.581"

# --- Version history ----------------------------------------------------
# v4.4.581: Initial: the eleven starting tags of a first start, never over a person's own tags (the owner, D-333)

ELEVEN: list[str] = ["Tomato", "Flamingo", "Tangerine", "Banana", "Sage", "Basil", "Peacock", "Blueberry", "Lavender", "Grape", "Graphite"]


def test_the_starting_tags_are_the_eleven_colours_shown_in_every_section() -> None:
    tags = starting_tags()
    assert [entry.shown for entry in tags.entries] == ELEVEN
    assert tags.entry("Banana") is not None and tags.entry("Banana").colour == "#f6bf26"  # type: ignore[union-attr]
    assert all(entry.kinds == ("mail", "events", "contacts", "tasks") and not entry.shortcut for entry in tags.entries)


def test_a_first_start_gets_them_and_a_person_with_tags_never(tmp_path: Path) -> None:
    config, data = tmp_path / "config", tmp_path / "data"
    assert first_start(config, data)
    assert give_starting_tags(config / "tags.json")
    assert [entry.shown for entry in load_tags(config / "tags.json").entries] == ELEVEN
    assert not first_start(config, data)  # it ran here: never again
    own = tmp_path / "own" / "tags.json"
    own.parent.mkdir()
    save_tags(own, TagColours().with_colour("Lakeside", "#123456"))
    assert not give_starting_tags(own)
    assert [entry.shown for entry in load_tags(own).entries] == ["Lakeside"]


def test_a_data_folder_alone_is_no_first_start(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    assert not first_start(tmp_path / "config", tmp_path / "data")
