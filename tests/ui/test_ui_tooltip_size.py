import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QToolTip, QWidget
from pytestqt.qtbot import QtBot

from tests.ui.test_ui_view_sizes import _system_points
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tooltip_size.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial: a tooltip takes the size of the window the mouse is over, also while another window is the active one (D-367)


def mouse_over(monkeypatch: pytest.MonkeyPatch, widget: QWidget | None) -> None:
    def under(*where: object) -> QWidget | None:
        return widget

    monkeypatch.setattr(QApplication, "widgetAt", staticmethod(under))


def test_a_tooltip_takes_the_size_of_the_window_the_mouse_is_over(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    original = QToolTip.font()
    dialog = QDialog(window)
    button = QPushButton("Apply", dialog)
    qtbot.addWidget(dialog)
    main_size, other_size = round(_system_points() * 2.0, 2), round(_system_points() * 0.75, 2)
    try:
        window.try_interface_scale(2.0)
        window.try_windows_scale(0.75)
        monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))  # another window is the active one
        mouse_over(monkeypatch, window.tree)  # while the mouse is over the main window's folder list
        window._tooltip_font()  # noqa: SLF001 - as when that window became active
        assert QToolTip.font().pointSizeF() == main_size
        mouse_over(monkeypatch, button)
        QApplication.sendEvent(window, QEvent(QEvent.Type.Leave))  # the mouse goes over to the other window
        assert QToolTip.font().pointSizeF() == other_size
        QApplication.sendEvent(window, QEvent(QEvent.Type.Enter))  # and comes back
        assert QToolTip.font().pointSizeF() == main_size
    finally:
        monkeypatch.undo()
        window.try_interface_scale(1.0)
        window.try_windows_scale(1.0)
        QToolTip.setFont(original)
