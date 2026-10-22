from PySide6.QtCore import QUrl
from pytestqt.qtbot import QtBot

from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_reader import load, reader, shown  # noqa: F401 - fixture
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_linkhint.py 1.5.257"

# --- Version history ----------------------------------------------------
# v1.5.257: Initial: the address of the link under the pointer in the bar at the bottom, with the warning triangle for another main domain (D-215)


def hovered(reader: ReaderWidget) -> list[tuple[str, bool]]:
    seen: list[tuple[str, bool]] = []
    reader.link_hovered.connect(lambda address, elsewhere: seen.append((address, elsewhere)))
    return seen


def test_the_reader_tells_the_link_under_the_pointer_in_html_and_in_plain_text(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    seen = hovered(reader)
    load(qtbot, reader, shown(html='<a href="https://shop.example.com/a">shop</a> <a href="https://evil.test/">x</a>', sender=("Alice <alice@example.com>",)))
    reader.web_page.linkHovered.emit("https://shop.example.com/a")
    reader.web_page.linkHovered.emit("https://evil.test/")
    reader.web_page.linkHovered.emit("")
    assert seen == [("https://shop.example.com/a", False), ("https://evil.test/", True), ("", False)]
    reader.show_message(shown(text="See https://example.org/x", sender=("Alice <alice@example.com>",)))
    reader.text_view.highlighted.emit(QUrl("https://example.org/x"))
    assert seen[-1] == ("https://example.org/x", True)
    reader.clear()  # the message goes while the pointer is on its link: the address goes too
    assert seen[-1] == ("", False)


def test_the_message_window_shows_where_a_link_leads_with_the_triangle(world: World, qtbot: QtBot) -> None:
    window = MessageWindow(world.backend, world.account_id, "Inbox", "a" * 32, shown(text="See https://example.org/x"), lambda *args: None, lambda url: None, lambda reader: None)
    qtbot.addWidget(window)
    window.show()
    line = window.status_line
    assert line.currentWidget() is line.normal
    window.reader.text_view.highlighted.emit(QUrl("https://www.example.com/page"))
    assert line.currentWidget() is line.link and line.address.text() == "https://www.example.com/page" and line.warning.isHidden()
    window.reader.text_view.highlighted.emit(QUrl("https://example.org/x"))
    assert line.address.text() == "https://example.org/x" and not line.warning.isHidden() and not line.warning.pixmap().isNull()
    window.reader.text_view.highlighted.emit(QUrl())
    assert line.currentWidget() is line.normal


def test_the_reading_pane_shows_the_link_and_the_status_text_comes_back(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.show_status("Moved 3 messages")
    window.reader.web_page.linkHovered.emit("https://evil.test/<b>bold</b>")
    line = window.status_line
    assert line.currentWidget() is line.link and line.address.text() == "https://evil.test/<b>bold</b>"  # plain text, never markup
    assert not line.warning.isHidden()
    window.reader.web_page.linkHovered.emit("")
    wait(qtbot, lambda: line.currentWidget() is line.normal)
    assert window.status_label.text() == "Moved 3 messages"
