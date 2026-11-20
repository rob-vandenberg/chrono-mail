from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import scale
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_view_sizes.py 4.6.651"

# --- Version history ----------------------------------------------------
# v4.6.651: the row above the message list (Sorted by, Filter, the count) follows the Message list slider, not the Main window slider (D-367)


def _row_sizes(header: QWidget) -> list[float]:
    return [header.font().pointSizeF()] + [part.font().pointSizeF() for part in header.findChildren(QWidget) if part.isVisibleTo(header)]


def test_the_row_above_the_message_list_follows_the_message_list_slider(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    header = window.messages.list_header
    texts = [window.messages.sort_button.text(), window.messages.count_label.text()]
    assert texts[0].startswith("Sorted by") and "message" in texts[1]
    standard = window.list_view.font().pointSizeF()
    try:
        window.try_list_scale(1.5)
        large = window.list_view.font().pointSizeF()
        assert large > standard
        assert set(_row_sizes(header)) == {large}  # the button, the Filter, the count: all at the list's size
        window.try_interface_scale(0.75)  # the Main window slider leaves the row alone
        assert set(_row_sizes(header)) == {large}
        margins = window.messages.header_layout.contentsMargins()
        assert (margins.left(), margins.top()) == (scale.lpx(10), scale.lpx(6))
    finally:
        window.try_interface_scale(1.0)
        window.try_list_scale(1.0)
    assert set(_row_sizes(header)) == {standard}
