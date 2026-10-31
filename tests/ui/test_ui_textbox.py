from pytestqt.qtbot import QtBot

from chrono_mail.ui import forms
from chrono_mail.ui.folderpane import RENAME_WIDTH
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.textbox import make_text_box

# --- Version ------------------------------------------------------------
__version__ = "test_ui_textbox.py 4.2.454"

# --- Version history ----------------------------------------------------
# v4.2.454: Initial version: Rename folder opens 360 pixels wide on the owner's screen, with its texts and the folder's name (the owner)


def test_rename_folder_opens_360_pixels_wide_on_the_owners_screen(qtbot: QtBot) -> None:
    assert RENAME_WIDTH == forms.owner_width(360)  # the owner's 360 pixels, measured on his screen
    box = make_text_box(None, "Rename folder", "New name of the folder:", "Projects", RENAME_WIDTH)
    qtbot.addWidget(box)
    box.show()
    qtbot.waitExposed(box)
    assert box.width() == wpx(RENAME_WIDTH)
    assert (box.windowTitle(), box.labelText(), box.textValue()) == ("Rename folder", "New name of the folder:", "Projects")
    assert box.minimumWidth() < box.width()  # opened at that width, not held at it: it can be made narrower


def test_the_box_is_never_wider_than_its_screen(qtbot: QtBot) -> None:
    box = make_text_box(None, "Rename folder", "New name of the folder:", "", 100_000)
    qtbot.addWidget(box)
    screen = box.screen()
    assert screen is not None and box.width() <= screen.availableGeometry().width()
