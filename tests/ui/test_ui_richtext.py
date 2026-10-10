import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui.richeditor import clean_html, safe_link
from tests.ui.test_ui_composer import new_composer
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_richtext.py 0.0.24"

# --- Version history ----------------------------------------------------
# v0.0.24: Initial rich text composing tests


@pytest.mark.parametrize(
    ("address", "safe"),
    [("https://example.org", True), ("http://a.b/c?d=e", True), ("mailto:a@b.org", True), ("javascript:alert(1)", False), ("file:///etc/passwd", False), ("data:text/html,x", False), ("example.org", False), ("", False), ("https://x\n.org", False), ("https://" + "a" * 2100, False)],
)
def test_only_web_and_mail_links_are_safe(address: str, safe: bool) -> None:
    assert safe_link(address) is safe


def test_clean_html_removes_pictures_editor_fonts_and_unsafe_links() -> None:
    html = '<html><head><meta name="qrichtext" content="1" /></head><body style="font-family:Noto; font-size:9pt"><p>Hi <img src="http://tracker/x.gif"> <a href="javascript:x()">bad</a> <a href="https://ok.org">ok</a></p></body></html>'
    cleaned = clean_html(html)
    assert "<img" not in cleaned and "qrichtext" not in cleaned and "font-family" not in cleaned and "<body>" in cleaned
    assert 'href=""' in cleaned and "javascript" not in cleaned and 'href="https://ok.org"' in cleaned


def test_switching_keeps_the_text_and_warns_before_dropping_formatting(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.body.setPlainText("Hello there")
    window.rich_button.click()
    assert window.is_rich() and window.rich.toPlainText() == "Hello there"
    window.rich_button.click()  # nothing formatted: no question
    assert not window.is_rich() and window.body.toPlainText() == "Hello there" and not world.dialogs.shown
    window.rich_button.click()
    window.rich.edit.selectAll()
    window.rich.bold.trigger()
    assert window.rich.has_formatting()
    world.dialogs.answer = QMessageBox.StandardButton.No
    window.rich_button.click()
    assert window.is_rich() and window.rich_button.isChecked()
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    window.rich_button.click()
    assert not window.is_rich() and window.body.toPlainText() == "Hello there"
    window._finished = True  # noqa: SLF001
    window.close()


def test_formatting_counts_as_a_change(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.body.setPlainText("text")
    window._saved_snapshot = window._snapshot()  # noqa: SLF001
    window.rich_button.click()
    window._saved_snapshot = window._snapshot()  # noqa: SLF001
    assert not window.is_modified()
    window.rich.edit.selectAll()
    window.rich.italic.trigger()
    assert window.is_modified()
    window._finished = True  # noqa: SLF001
    window.close()


def test_a_rich_message_is_sent_as_text_and_html(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.to.setText("you@example.com")
    window.subject.setText("Rich")
    window.rich_button.click()
    window.rich.setPlainText("Hello world")
    window.rich.edit.selectAll()
    window.rich.bold.trigger()
    assert window.rich.make_link("https://example.org") is True
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    data = world.smtp.received[0].data
    assert b"multipart/alternative" in data and b"text/plain" in data and b"text/html" in data
    assert b"font-weight" in data and b"https://example.org" in data and b"<img" not in data


def test_an_unsafe_link_is_refused(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.rich_button.click()
    window.rich.setPlainText("x")
    window.rich.edit.selectAll()
    assert window.rich.make_link("javascript:alert(1)") is False
    assert any("Only web addresses" in text for _, _, text in world.dialogs.shown) and "javascript" not in window.rich.outgoing_html()
    window._finished = True  # noqa: SLF001
    window.close()


def test_lists_and_clear_formatting(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.rich_button.click()
    window.rich.setPlainText("one")
    window.rich.bullets.trigger()
    assert "<ul" in window.rich.outgoing_html()
    window.rich.bullets.trigger()  # a second click ends the list
    assert "<ul" not in window.rich.outgoing_html()
    window.rich.numbers.trigger()
    assert "<ol" in window.rich.outgoing_html()
    window.rich.edit.selectAll()
    window.rich.bold.trigger()
    window.rich.plain.trigger()
    assert "font-weight:700" not in window.rich.outgoing_html().replace(" ", "")
    window._finished = True  # noqa: SLF001
    window.close()


def test_a_rich_draft_comes_back_as_rich_text(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window.to.setText("you@example.com")
    window.rich_button.click()
    window.rich.setPlainText("Formatted")
    window.rich.edit.selectAll()
    window.rich.bold.trigger()
    window.save_button.click()
    wait(qtbot, lambda: window.stored_as() is not None and not window.is_modified())
    window._finished = True  # noqa: SLF001
    window.close()
    saved = world.call("message.list", account=world.account_id, folder="Drafts").messages[0]
    opened = world.call("draft.open", account=world.account_id, message=saved.message_id)
    again = new_composer(world, qtbot, opened)
    assert again.is_rich() and again.rich_button.isChecked() and again.rich.has_formatting() and not again.is_modified()
    again._finished = True  # noqa: SLF001
    again.close()


def test_the_signature_is_left_alone_in_rich_text_when_the_sender_changes(world: World, qtbot: QtBot) -> None:
    world.call("account.edit", account=world.account_id, signature="Sig")
    window = new_composer(world, qtbot)
    window.rich_button.click()
    window.rich.edit.selectAll()
    window.rich.bold.trigger()
    before = window.rich.toPlainText()
    window._account_changed()  # noqa: SLF001
    assert window.rich.toPlainText() == before
    window._finished = True  # noqa: SLF001
    window.close()
