import base64
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import AttachmentInfo, InlineImage, ShowResult
from chrono_mail.ui.reader import ReaderWidget, embed_inline_images, linkify

PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")

# --- Version ------------------------------------------------------------
__version__ = "test_ui_reader.py 0.0.38"

# --- Version history ----------------------------------------------------
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


def test_embed_inline_images() -> None:
    images = {"logo@x": ("image/png", "QUJD")}
    assert embed_inline_images('<img src="cid:logo@x">', images) == '<img src="data:image/png;base64,QUJD">'
    assert embed_inline_images('<img src="CID:LOGO@X"> url(cid:logo@x)', images) == '<img src="data:image/png;base64,QUJD"> url(data:image/png;base64,QUJD)'
    assert embed_inline_images('<img src="cid:unknown">', images) == '<img src="cid:unknown">'
    assert embed_inline_images("no pictures here", images) == "no pictures here"


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
    assert "data:image/png;base64," in reader._current_html and "cid:" not in reader._current_html  # noqa: SLF001
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
    assert requested == [(2, "attachment-3.txt")]
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
