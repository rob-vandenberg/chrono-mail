import base64
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import AttachmentInfo, InlineImage, ShowResult
from chrono_mail.ui.reader import ReaderWidget, embed_inline_images, linkify, pictures_below

PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")

# --- Version ------------------------------------------------------------
__version__ = "test_ui_reader.py 3.6.357"

# --- Version history ----------------------------------------------------
# v3.6.357: a link in <...> or quotes stops at its closing character; an apostrophe inside an address stays (CR-19)
# v3.5.346: HTML mail and its pictures come through chrono-message: addresses; an HTML message over 2 MB is shown (RV-158)
# v2.0.302: a click on a file selects it, a double click opens it, a right-click on it or on a picture offers Open attachment and Save as… (D-236)
# v2.0.301: the attached pictures the text does not show are shown below it, fitted to the width, never enlarged (B-11)
# v0.0.38: CI: the PDF test writes into its own folder
# v0.0.13: Initial reading pane tests


class Probe:
    """A web server on this computer that counts the requests that really reach it."""

    def __init__(self) -> None:
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server looks for
                probe.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(PIXEL)

            @override
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.requests: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str = "/pixel.png") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def probe() -> Generator[Probe]:
    server = Probe()
    yield server
    server.stop()


@pytest.fixture
def reader(qtbot: QtBot) -> ReaderWidget:
    widget = ReaderWidget()
    qtbot.addWidget(widget)
    widget.resize(700, 500)
    widget.show()
    return widget


def shown(
    html: str | None = None,
    text: str | None = "",
    attachments: tuple[AttachmentInfo, ...] = (),
    images: tuple[InlineImage, ...] = (),
    subject: str = "A subject",
    sender: tuple[str, ...] = ("Alice <alice@example.com>",),
    to: tuple[str, ...] = ("bob@example.com",),
) -> ShowResult:
    return ShowResult(
        message_id="a" * 32, folder="Inbox", flags=(), subject=subject, sender=sender, to=to, cc=("carol@example.com",),
        date=datetime(2026, 10, 8, 10, 30, tzinfo=UTC), header_message_id="<x@y>", text_body=text, html_body=html, attachments=attachments, problems=(),
        inline_images=images, reply_to=(),
    )


def collect_links(reader: ReaderWidget) -> list[str]:
    urls: list[str] = []

    def record(url: QUrl) -> None:
        urls.append(url.toString())

    reader.link_activated.connect(record)
    return urls


def page_text(qtbot: QtBot, reader: ReaderWidget) -> str:
    result: list[str] = []
    reader.web_page.toPlainText(result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=8000)
    return result[0]


def load(qtbot: QtBot, reader: ReaderWidget, message: ShowResult) -> None:
    with qtbot.waitSignal(reader.web_page.loadFinished, timeout=10000):
        reader.show_message(message)


# --- plain text ---------------------------------------------------------------------------------------------------


def test_plain_text_is_shown_with_its_header(reader: ReaderWidget) -> None:
    reader.show_message(shown(text="Hello <b>not bold</b> & more\nsecond line"))
    assert not reader.showing_html()
    assert reader.text_view.toPlainText() == "Hello <b>not bold</b> & more\nsecond line"  # markup in plain text stays text
    assert reader._subject.text() == "A subject"  # noqa: SLF001
    assert "Alice" in reader._people.text() and "carol@example.com" in reader._people.text()  # noqa: SLF001
    assert "2026" in reader._date.text()  # noqa: SLF001


def test_links_in_plain_text_are_clickable_and_reported(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(shown(text="See https://example.com/a?b=1&c=2 or mail me@example.org or mailto:me@example.org now"))
    document = reader.text_view.toHtml()
    assert 'href="https://example.com/a?b=1&amp;c=2"' in document and 'href="mailto:me@example.org"' in document
    activated = collect_links(reader)
    with qtbot.waitSignal(reader.link_activated, timeout=2000):
        reader.text_view.anchorClicked.emit(QUrl("https://example.com/a"))
    assert activated == ["https://example.com/a"]


def test_linkify() -> None:
    assert linkify("a <b> & c") == "a &lt;b&gt; &amp; c"
    assert linkify("x https://e.com/p?q=1&r=2 y") == 'x <a href="https://e.com/p?q=1&amp;r=2">https://e.com/p?q=1&amp;r=2</a> y'
    assert 'href="http://a.b"' in linkify("http://a.b")
    assert linkify("javascript:alert(1)") == "javascript:alert(1)"  # only web and mail addresses become links
    assert linkify("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert linkify("") == ""


def test_linkify_stops_a_link_at_its_closing_bracket_or_quote() -> None:
    """CR-19: a link in <...> or quotes never takes the closing character into its address."""
    assert linkify("<https://example.com/a>") == '&lt;<a href="https://example.com/a">https://example.com/a</a>&gt;'
    assert linkify('"https://example.com/b"') == '&quot;<a href="https://example.com/b">https://example.com/b</a>&quot;'
    assert linkify("'https://example.com/c'") == '&#x27;<a href="https://example.com/c">https://example.com/c</a>&#x27;'
    assert linkify("<mailto:me@example.org>") == '&lt;<a href="mailto:me@example.org">mailto:me@example.org</a>&gt;'
    assert linkify("<https://e.com/p?q=1&r=2> & <b>") == '&lt;<a href="https://e.com/p?q=1&amp;r=2">https://e.com/p?q=1&amp;r=2</a>&gt; &amp; &lt;b&gt;'
    # an apostrophe inside an address belongs to it, unless the address opened with one
    assert linkify("https://example.com/wiki/O'Brien x") == '<a href="https://example.com/wiki/O&#x27;Brien">https://example.com/wiki/O&#x27;Brien</a> x'
    assert linkify("'https://example.com/wiki/O'Brien'") == '&#x27;<a href="https://example.com/wiki/O">https://example.com/wiki/O</a>&#x27;Brien&#x27;'
    assert linkify("'https://'") == "&#x27;https://&#x27;"
    # an address right after one that closed with an apostrophe is found too
    assert linkify("'http://a'http://b x") == '&#x27;<a href="http://a">http://a</a>&#x27;<a href="http://b">http://b</a> x'
    assert linkify("'http://'https://b.org") == '&#x27;http://&#x27;<a href="https://b.org">https://b.org</a>'


def test_linkify_leaves_the_punctuation_of_the_sentence_out_of_a_link() -> None:
    """A full stop, comma, closing quote or bracket right after an address ends the sentence, not the address."""
    assert linkify("See https://example.com/a.") == 'See <a href="https://example.com/a">https://example.com/a</a>.'
    assert linkify("'see https://example.com'") == '&#x27;see <a href="https://example.com">https://example.com</a>&#x27;'
    assert linkify("(at https://example.com/b), or mailto:me@example.org!") == (
        '(at <a href="https://example.com/b">https://example.com/b</a>), or <a href="mailto:me@example.org">mailto:me@example.org</a>!'
    )
    wiki = "https://en.wikipedia.org/wiki/Mercury_(planet)"
    assert linkify(f"({wiki}).") == f'(<a href="{wiki}">{wiki}</a>).'  # a bracket that belongs to the address stays
    assert linkify("https://...") == "https://..."  # nothing but the scheme is left: plain text
    assert linkify("Write to mailto:.") == "Write to mailto:."  # the scheme's own colon is never cut
    assert linkify("(mailto:)") == "(mailto:)"
    assert linkify("Go to HTTPS://Example.com/A.") == 'Go to <a href="HTTPS://Example.com/A">HTTPS://Example.com/A</a>.'  # a scheme in capitals is a scheme
    assert linkify("MAILTO:.") == "MAILTO:."
    assert linkify("https://example.com/" + ")" * 200_000).startswith('<a href="https://example.com/">')  # one pass, also over hostile text


def test_embed_inline_images() -> None:
    addresses = {"logo@x": "chrono-message:/1/part/0"}
    assert embed_inline_images('<img src="cid:logo@x">', addresses) == '<img src="chrono-message:/1/part/0">'
    assert embed_inline_images('<img src="CID:LOGO@X"> url(cid:logo@x)', addresses) == '<img src="chrono-message:/1/part/0"> url(chrono-message:/1/part/0)'
    assert embed_inline_images('<img src="cid:unknown">', addresses) == '<img src="cid:unknown">'
    assert embed_inline_images("no pictures here", addresses) == "no pictures here"


# --- HTML: what it can and cannot do -----------------------------------------------------------------------------------------


def test_html_is_rendered(reader: ReaderWidget, qtbot: QtBot) -> None:
    load(qtbot, reader, shown(html="<html><body><h1>Big news</h1><p>Some <b>bold</b> words</p></body></html>"))
    assert reader.showing_html()
    text = page_text(qtbot, reader)
    assert "Big news" in text and "Some bold words" in text


def test_the_web_view_is_locked_down(reader: ReaderWidget) -> None:
    settings = reader.web_page.settings()
    for attribute in (
        QWebEngineSettings.WebAttribute.JavascriptEnabled, QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
        QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, QWebEngineSettings.WebAttribute.LocalStorageEnabled,
        QWebEngineSettings.WebAttribute.PluginsEnabled, QWebEngineSettings.WebAttribute.WebGLEnabled,
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
        QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled,
        QWebEngineSettings.WebAttribute.PdfViewerEnabled, QWebEngineSettings.WebAttribute.ScreenCaptureEnabled,
    ):
        assert not settings.testAttribute(attribute), attribute
    assert reader.web_page.profile().isOffTheRecord()


def test_javascript_does_not_run(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    script = f"<script>document.title = 'JS RAN'; document.body.innerHTML = 'JS CHANGED'; new Image().src = '{probe.url('/from-script')}';</script>"
    load(qtbot, reader, shown(html=f"<html><head><title>Plain</title></head><body><p id='t'>static text</p>{script}<img src='x' onerror=\"document.title='ONERROR'\"></body></html>"))
    qtbot.wait(500)
    text = page_text(qtbot, reader)
    assert "static text" in text and "JS CHANGED" not in text
    assert reader.web_page.title() != "JS RAN" and "ONERROR" not in reader.web_page.title()
    assert probe.requests == []


def test_remote_content_is_blocked_until_the_person_allows_it(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    document = f"<html><body><p>Text</p><img src='{probe.url('/tracker.png')}'><img src='{probe.url('/two.png')}'></body></html>"
    load(qtbot, reader, shown(html=document))
    qtbot.waitUntil(reader.banner_visible, timeout=5000)
    qtbot.wait(300)
    assert probe.requests == [] and reader.blocker.blocked >= 2  # nothing left the computer
    with qtbot.waitSignal(reader.web_page.loadFinished, timeout=10000):
        reader._load_remote.click()  # noqa: SLF001
    qtbot.waitUntil(lambda: len(probe.requests) >= 2, timeout=8000)
    assert not reader.banner_visible()
    assert sorted(set(probe.requests)) == ["/tracker.png", "/two.png"]


def test_the_next_message_is_blocked_again(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    document = f"<html><body><img src='{probe.url('/a.png')}'></body></html>"
    load(qtbot, reader, shown(html=document))
    qtbot.waitUntil(reader.banner_visible, timeout=5000)
    with qtbot.waitSignal(reader.web_page.loadFinished, timeout=10000):
        reader._load_remote.click()  # noqa: SLF001
    qtbot.waitUntil(lambda: len(probe.requests) >= 1, timeout=8000)
    seen_before = len(probe.requests)
    load(qtbot, reader, shown(html=f"<html><body><img src='{probe.url('/b.png')}'></body></html>"))
    qtbot.waitUntil(reader.banner_visible, timeout=5000)
    qtbot.wait(300)
    assert len(probe.requests) == seen_before  # permission was for the first message only


def test_stylesheets_fonts_frames_and_redirects_cannot_reach_out(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    document = (
        f"<html><head><link rel='stylesheet' href='{probe.url('/style.css')}'><style>@import url('{probe.url('/import.css')}');"
        f"@font-face {{ font-family: x; src: url('{probe.url('/font.woff')}'); }} body {{ background: url('{probe.url('/bg.png')}'); }}</style>"
        f"<meta http-equiv='refresh' content='0;url={probe.url('/refresh')}'></head>"
        f"<body><iframe src='{probe.url('/frame.html')}'></iframe><video src='{probe.url('/video.mp4')}'></video>"
        f"<object data='{probe.url('/object')}'></object><embed src='{probe.url('/embed')}'>"
        f"<form action='{probe.url('/form')}'><input type=submit></form><a href='{probe.url('/link')}'>link</a></body></html>"
    )
    load(qtbot, reader, shown(html=document))
    qtbot.wait(1500)
    assert probe.requests == []


def test_a_clicked_link_is_reported_and_never_followed(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    load(qtbot, reader, shown(html="<html><body>x</body></html>"))
    activated = collect_links(reader)
    page = reader.web_page
    link = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert page.acceptNavigationRequest(QUrl(probe.url("/x")), link, True) is False
    assert activated == [probe.url("/x")]
    assert page.acceptNavigationRequest(QUrl("https://elsewhere.example/"), QWebEnginePage.NavigationType.NavigationTypeTyped, True) is False
    assert page.acceptNavigationRequest(QUrl("https://elsewhere.example/"), QWebEnginePage.NavigationType.NavigationTypeRedirect, True) is False
    assert page.acceptNavigationRequest(QUrl("file:///etc/passwd"), QWebEnginePage.NavigationType.NavigationTypeTyped, True) is False
    assert page.acceptNavigationRequest(QUrl("data:text/html,hello"), QWebEnginePage.NavigationType.NavigationTypeTyped, True) is True
    assert probe.requests == []


def test_a_link_that_wants_a_new_window_is_reported_and_no_window_opens(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    load(qtbot, reader, shown(html="<html><body>x</body></html>"))
    activated = collect_links(reader)
    catcher = reader.web_page.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)
    assert catcher.acceptNavigationRequest(QUrl(probe.url("/blank")), QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True) is False
    assert activated == [probe.url("/blank")] and probe.requests == []


def test_inline_pictures_of_the_message_are_shown_without_the_network(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    encoded = base64.b64encode(PIXEL).decode("ascii")
    message = shown(html="<html><body><p>Look</p><img src='cid:logo@example.com'></body></html>", images=(InlineImage("logo@example.com", "image/png", encoded),))
    load(qtbot, reader, message)
    qtbot.wait(500)
    assert "chrono-message:/" in reader._current_html and "cid:" not in reader._current_html  # noqa: SLF001
    qtbot.waitUntil(lambda: reader.web_page.contentsSize().height() > 0)
    assert reader.blocker.blocked == 0 and not reader.banner_visible() and probe.requests == []


def test_markup_in_the_subject_and_the_sender_stays_text(reader: ReaderWidget) -> None:
    reader.show_message(shown(text="x", subject="<b>bold</b><img src=x onerror=alert(1)>", sender=("<script>alert(1)</script> <a@b.c>",), to=("<i>to</i>@x.org",)))
    assert reader._subject.textFormat() == Qt.TextFormat.PlainText  # noqa: SLF001
    assert reader._subject.text() == "<b>bold</b><img src=x onerror=alert(1)>"  # shown as typed, not interpreted
    people = reader._people.text()  # noqa: SLF001
    assert "<script>" not in people and "&lt;script&gt;" in people and "<i>" not in people and "&lt;i&gt;" in people


# --- attachments and states -----------------------------------------------------------------------------------------------------


def test_attachment_buttons(reader: ReaderWidget) -> None:
    attachments = (
        AttachmentInfo("logo.png", "image/png", 10, True, "logo.png"),
        AttachmentInfo("report.pdf", "application/pdf", 2048, False, "report.pdf"),
        AttachmentInfo(None, "text/plain", 5, False, "attachment-3.txt"),
    )
    reader.show_message(shown(text="x", attachments=attachments))
    assert not reader._attachments.isHidden()  # noqa: SLF001
    buttons = reader._attachments.findChildren(type(reader._load_remote))  # noqa: SLF001
    assert [button.text() for button in buttons] == ["📎 report.pdf (2.0 KB)", "📎 attachment-3.txt (5 B)"]  # inline pictures are not listed
    requested: list[tuple[int, str]] = []

    def record(position: int, name: str) -> None:
        requested.append((position, name))

    reader.attachment_save_requested.connect(record)
    buttons[1].click()
    assert requested == [] and buttons[1].isChecked()  # a click only selects the file (D-236)
    buttons[0].click()
    assert buttons[0].isChecked() and not buttons[1].isChecked()  # one at a time
    reader.show_message(shown(text="no attachments"))
    assert reader._attachments.isHidden()  # noqa: SLF001


def test_clear_empties_the_pane(reader: ReaderWidget) -> None:
    reader.show_message(shown(text="x"))
    reader.clear()
    assert reader._header.isHidden() and reader._attachments.isHidden() and not reader.banner_visible()  # noqa: SLF001


def test_a_pdf_of_an_html_message_for_review(reader: ReaderWidget, qtbot: QtBot, tmp_path: Path) -> None:
    html = ("<html><body style='font-family:sans-serif'><h2>Newsletter</h2><p>Hello <b>Rob</b>, here is <i>formatted</i> mail with a "
            "<a href='https://example.com'>link</a>.</p><table border=1 cellpadding=6><tr><th>Item</th><th>Price</th></tr><tr><td>One</td><td>1.00</td></tr></table></body></html>")
    load(qtbot, reader, shown(html=html, subject="A formatted newsletter"))
    qtbot.wait(300)
    done: list[bool] = []

    def finished(path: str, ok: bool) -> None:
        done.append(ok)

    reader.web_page.pdfPrintingFinished.connect(finished)
    reader.web_page.printToPdf(str(tmp_path / "html-message.pdf"))
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    assert done == [True] and (tmp_path / "html-message.pdf").stat().st_size > 1000


def picture(width: int, height: int) -> InlineImage:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QImageWriter

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x3366CC)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert QImageWriter(buffer, b"png").write(image)  # QImage.save: the type hints and PySide disagree on the format's type
    return InlineImage("", "image/png", base64.b64encode(bytes(buffer.data().data())).decode("ascii"))


def picture_widths(reader: ReaderWidget) -> list[float]:
    document = reader.text_view.document()
    widths: list[float] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            shape = iterator.fragment().charFormat()
            if shape.isImageFormat():
                widths.append(shape.toImageFormat().width())
            iterator += 1
        block = block.next()
    return widths


def test_the_pictures_of_a_plain_text_message_are_shown_below_it_as_wide_as_the_pane_at_most(reader: ReaderWidget, qtbot: QtBot) -> None:
    """B-11: an attached picture is shown below the text, shrunk to the width, never enlarged; also after the pane changes size."""
    from dataclasses import replace

    reader.show_message(replace(shown(text="See the pictures."), pictures=(picture(3000, 300), picture(40, 20))))
    assert not reader.showing_html() and "See the pictures." in reader.text_view.toPlainText()
    room = reader.text_view.viewport().width() - 2 * reader.text_view.document().documentMargin()
    wide, small = picture_widths(reader)
    assert 0 < wide <= room < 3000 and small == 40
    reader.resize(400, 500)
    qtbot.waitUntil(lambda: picture_widths(reader)[0] < wide)
    assert picture_widths(reader)[0] <= reader.text_view.viewport().width()


def test_the_pictures_of_an_html_message_come_below_its_text_as_wide_as_the_page_at_most() -> None:
    page = pictures_below("<html><body><p>Hello</p></body></html>", ["chrono-message:/1/part/0"])
    assert page.index("Hello") < page.index('src="chrono-message:/1/part/0"') < page.index("</body>") and "max-width:100%" in page
    assert pictures_below("<p>No body tag</p>", ["chrono-message:/1/part/0"]).endswith('margin-top:12px">')
    assert pictures_below("<p>Nothing</p>", []) == "<p>Nothing</p>"


def test_a_double_click_opens_a_file_and_a_right_click_offers_open_and_save(reader: ReaderWidget, qtbot: QtBot) -> None:
    """The owner (D-236): as in a file manager; Save as… opens the save window, Open attachment the program for the kind of file."""
    from PySide6.QtTest import QTest

    reader.show_message(shown(text="x", attachments=(AttachmentInfo("report.pdf", "application/pdf", 2048, False, "report.pdf"),)))
    opened: list[tuple[int, str]] = []
    saved: list[tuple[int, str]] = []

    def record_open(position: int, name: str) -> None:
        opened.append((position, name))

    def record_save(position: int, name: str) -> None:
        saved.append((position, name))

    reader.attachment_open_requested.connect(record_open)
    reader.attachment_save_requested.connect(record_save)
    (chip,) = reader.chips()
    QTest.mouseDClick(chip, Qt.MouseButton.LeftButton)
    assert opened == [(0, "report.pdf")] and saved == [] and chip.isChecked()
    menu = reader.attachment_menu(0)
    assert [action.text() for action in menu.actions()] == ["Open attachment", "Save as…"]
    menu.actions()[1].trigger()
    menu.actions()[0].trigger()
    assert saved == [(0, "report.pdf")] and opened == [(0, "report.pdf"), (0, "report.pdf")]
    menu.close()
    chip.customContextMenuRequested.emit(QPoint(2, 2))  # a right-click on the file: its menu, and the file is the selected one
    shown_menu = next(widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QMenu) and widget.isVisible())
    assert [action.text() for action in shown_menu.actions()] == ["Open attachment", "Save as…"]
    shown_menu.close()


def test_a_right_click_on_a_picture_offers_open_and_save_and_on_the_text_the_text_menu(reader: ReaderWidget, qtbot: QtBot) -> None:
    from dataclasses import replace

    attachments = (AttachmentInfo("photo.png", "image/png", 100, False, "photo.png"),)
    reader.show_message(replace(shown(text="See the picture.", attachments=attachments), pictures=(replace(picture(200, 100), position=0),)))
    view = reader.text_view
    requests: list[int] = []

    def record(position: int, where: QPoint) -> None:
        requests.append(position)

    view.picture_menu_requested.connect(record)
    layout = view.document().documentLayout()
    block = view.document().lastBlock()
    while block.isValid() and not layout.blockBoundingRect(block).height():
        block = block.previous()
    inside = layout.blockBoundingRect(block).center().toPoint() - QPoint(view.horizontalScrollBar().value(), view.verticalScrollBar().value())
    left = QPoint(int(view.document().documentMargin()) + 5, inside.y())
    assert view.picture_at(left) == 0
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, left, view.mapToGlobal(left)))
    assert requests == [0]
    assert view.picture_at(QPoint(int(view.document().documentMargin()) + 5, 5)) == -1  # the text: its own menu (Copy, Select All)
    html_message = replace(shown(html="<p>Hi</p>", text=None, attachments=attachments), pictures=(InlineImage("", "image/png", "iVBORw==", 0),))
    reader.show_message(html_message)
    (address, position), = reader._picture_positions.items()  # noqa: SLF001 - how a right-click on it is known
    assert position == 0 and address.startswith("chrono-message:/") and QUrl(address).toString() == address  # the right-click's address is this one


def test_an_html_message_over_two_megabytes_is_shown(reader: ReaderWidget, qtbot: QtBot) -> None:
    """RV-158: a newsletter with large inlined pictures showed an empty page (setHtml takes at most 2 MB)."""
    filler = "<p>" + "x" * 1000 + "</p>"
    message = shown(html=f"<html><body><p>The start</p>{filler * 3000}<p>The end</p></body></html>")
    load(qtbot, reader, message)
    text = page_text(qtbot, reader)
    assert "The start" in text and "The end" in text

