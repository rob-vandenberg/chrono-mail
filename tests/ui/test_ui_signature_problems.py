import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.config.signatures import MAXIMUM_HTML_BYTES
from chrono_mail.engine.commands_signatures import EXPORT_LIST
from chrono_mail.ui.signaturesdialog import SignaturesPanel
from tests.ui.test_ui_signatures import answer_editor, keep, ok, open_page
from tests.ui.world import World, answer_save_file, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_signature_problems.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial: a text that could not be read is named on a line of its own that no later message replaces, until it is given a
#           new text; Save As is greyed on it; Export leaves it out and says so (it wrote it empty)


def with_an_unreadable_text(world: World) -> None:
    """Huge, whose file is too large to read, and Work."""
    big, _ = keep(world, ("Huge", "<p>h</p>"), ("Work", "<p>Kind regards</p>"))
    (world.engine.directories.signatures_directory / f"{big}.html").write_bytes(b"x" * (MAXIMUM_HTML_BYTES + 1))


def select(panel: SignaturesPanel, row: int) -> None:
    item = panel.items.topLevelItem(row)
    assert item is not None
    panel.items.setCurrentItem(item)


def test_save_as_is_greyed_on_a_text_that_could_not_be_read(world: World, qtbot: QtBot) -> None:
    with_an_unreadable_text(world)
    dialog, panel = open_page(world, qtbot)
    select(panel, 0)
    assert not panel.save_as_button.isEnabled() and panel.save_as_button.toolTip() == "This text could not be read"
    assert panel.edit_button.isEnabled() and panel.delete_button.isEnabled()  # it can be given a new text, or deleted
    select(panel, 1)
    assert panel.save_as_button.isEnabled() and panel.save_as_button.toolTip() == ""
    dialog.reject()


def test_export_leaves_out_a_text_that_could_not_be_read_and_says_so(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_an_unreadable_text(world)
    dialog, panel = open_page(world, qtbot)
    target = tmp_path / "all.json"
    answer_save_file(monkeypatch, target)
    panel.export_button.click()
    wait(qtbot, lambda: target.exists() and panel.status.isVisibleTo(panel))
    assert [item["name"] for item in json.loads(target.read_text("utf-8"))[EXPORT_LIST]] == ["Work"]
    assert panel.status.text() == "Could not be read, so not exported: Huge"
    assert panel.unreadable.isVisibleTo(panel) and "Huge" in panel.unreadable.text()  # a later message never replaces it
    dialog.reject()


def test_the_line_about_a_text_that_could_not_be_read_stays_until_it_has_a_new_text(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_an_unreadable_text(world)
    dialog, panel = open_page(world, qtbot)
    select(panel, 1)
    (tmp_path / "a file").write_text("x", "utf-8")
    answer_save_file(monkeypatch, tmp_path / "a file" / "Work.html")
    panel.save_as_button.click()  # fails (a file where a folder should be): a message of its own on the other line
    wait(qtbot, lambda: panel.status.isVisibleTo(panel) and panel.status.text() != "")
    assert panel.unreadable.isVisibleTo(panel) and panel.unreadable.text() == "Could not be read, so left out of your messages: Huge"
    select(panel, 0)
    answer_editor(monkeypatch, "Huge", "<p>A new text</p>")
    panel.edit_button.click()
    assert not panel.unreadable.isVisibleTo(panel) and panel.save_as_button.isEnabled()
    ok(dialog, qtbot)
