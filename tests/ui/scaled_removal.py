"""Run in a process of its own with QT_SCALE_FACTOR set (Qt reads it only before the program's first window): two rows leave the message
list, and the list on screen must look exactly as when it is painted whole. Exits 0 when it does, 1 when it does not."""

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chrono_mail.ui.messagelistview import MessageListView

# --- Version ------------------------------------------------------------
__version__ = "scaled_removal.py 4.2.393"

# --- Version history ----------------------------------------------------
# v4.2.393: Initial: two rows removed at a scale where they are not whole screen pixels (D-266)


def on_screen(view: MessageListView) -> bytes:
    """What the window shows of the list: the window's own pixels, not a fresh paint."""
    screen = QApplication.primaryScreen()
    assert screen is not None
    image = screen.grabWindow(view.winId()).toImage()
    return bytes(image.constBits())


def main() -> int:
    application = QApplication(sys.argv)
    model = QStandardItemModel()
    for number in range(40):
        model.appendRow([QStandardItem(f"Note {number:05d}"), QStandardItem(f"Sender {number}")])
    view = MessageListView()
    view.setModel(model)
    view.resize(400, 400)
    view.show()
    if not QTest.qWaitForWindowExposed(view, 10000):
        print("the list never came on screen")
        return 2
    view.viewport().repaint()  # painted once: from now on a removal moves the rows below it
    model.removeRows(3, 2)
    QCoreApplication.sendPostedEvents()  # the paint the removal asked for
    shown = on_screen(view)
    view.repaint_all()
    view.viewport().repaint()
    whole = on_screen(view)
    view.close()
    application.processEvents()
    ratio = view.viewport().devicePixelRatioF()
    print(f"scale {ratio}, removed {2 * view.visualRect(model.index(0, 0)).height()} points")
    return 0 if shown == whole else 1


if __name__ == "__main__":
    sys.exit(main())
