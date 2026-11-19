from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.richeditor import RichEditor
from chrono_mail.ui.signaturesdialog import SignatureEditor
from tests.ui.test_ui_signatures import close, composer, keep, pick_signature
from tests.ui.world import World, add_window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_toolbar.py 4.6.641"

# --- Version history ----------------------------------------------------
# v4.6.641: Paint Format stands between U and A
# v4.6.640: Font Style (A) stands after U
# v4.6.639: Initial: the bar of the reference client in the New message window and in the window of one text (D-363)

MORE: list[str] = ["Save", "Format", "", "Cut", "Copy", "Paste", "Remove Format", "Undo", "Redo", "Outdent", "Indent", "Insert Link", "Insert Image"]
EDITING: list[str] = ["Cut", "Copy", "Paste", "Remove Format", "Undo", "Redo", "Outdent", "Indent", "Insert Link", "Insert Image"]


def on_bar(editor: RichEditor) -> list[str]:
    """What the bar shows, left to right: each button's name, "|" a divider, "" the room that pushes the three dots to the right."""
    names: list[str] = []
    for action in editor.toolbar.actions():
        widget = editor.toolbar.widgetForAction(action)
        if action.isSeparator():
            names.append("|")
        elif widget is editor.more:
            names.append("More")
        elif isinstance(widget, QToolButton):
            names.append(widget.defaultAction().text() if widget.defaultAction() is not None else widget.text())
        else:
            names.append("")
    return names


def menu_names(editor: RichEditor) -> list[str]:
    return [action.text() for action in editor.more_menu.actions()]


def test_the_bar_stands_above_the_text_in_the_order_of_the_reference_client(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    assert on_bar(window.rich) == ["Attach a file", "Signature", "Template", "|", "B", "I", "U", "Paint Format", "A", "Bulleted list", "Numbered list", "", "More"]
    assert menu_names(window.rich) == MORE
    assert window.rich.toolbar.isVisible() and window.rich.toolbar.y() < window.editor_stack.y()  # also in plain text, above the text
    assert window.rich.more.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup and window.rich.more.toolTip() == "More"
    close(window)


def test_the_old_buttons_below_the_text_are_gone_and_remove_stays(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    texts = [button.text() for button in window.findChildren(QPushButton) if button.isVisible()]
    assert "Attach a file…" not in texts and "Plain text" not in texts and "Rich text" not in texts and "Signature" not in texts
    assert "Remove" in texts
    close(window)


def test_signature_replaces_the_signature_and_template_adds_at_the_cursor(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Offer", "<p>Our offer</p>"))
    window = composer(world, qtbot)
    signatures, templates = window.signature_button.menu(), window.template_button.menu()
    assert signatures is not None and templates is not None
    assert [action.text() for action in signatures.actions()] == ["Formal", "Offer"]  # the same list on both, the keys on Template only
    assert [action.text() for action in templates.actions()] == ["Formal\tCtrl+1", "Offer\tCtrl+2"]
    window.body.setPlainText("Hello")
    pick_signature(window, 0)
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert "Our offer" in text and "Kind regards" not in text and text.count("-- \n") == 1  # the second signature replaced the first
    cursor = window.body.textCursor()
    cursor.setPosition(len("Hello"))
    window.body.setTextCursor(cursor)
    templates.actions()[0].trigger()
    templates.actions()[0].trigger()
    text = window.body.toPlainText()
    assert text.startswith("HelloKind regardsKind regards") and text.count("Our offer") == 1  # added twice at the cursor, the signature stays
    close(window)


def test_ctrl_1_adds_the_first_text_at_the_cursor_and_replaces_nothing(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Offer", "<p>Our offer</p>"))
    window = composer(world, qtbot)
    window.rich_action.trigger()
    pick_signature(window, 1)
    cursor = window.rich.edit.textCursor()
    cursor.setPosition(0)
    window.rich.edit.setTextCursor(cursor)
    keyed = [action for action in window.actions() if action.shortcut() == QKeySequence("Ctrl+1")]
    assert len(keyed) == 1
    keyed[0].trigger()
    text = window.rich.toPlainText()
    assert text.startswith("Kind regards") and "Our offer" in text
    close(window)


def test_in_plain_text_what_only_rich_text_can_do_is_greyed_never_hidden(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"))
    window = composer(world, qtbot)
    assert not window.is_rich()
    rich_only = [window.rich.bold, window.rich.italic, window.rich.underline, window.rich.bullets, window.rich.numbers, window.rich.plain,
                 window.rich.outdent, window.rich.indent, window.rich.link, window.rich.picture]
    always = [window.attach_action, window.save_action, window.rich.cut, window.rich.copy, window.rich.paste, window.rich.undo, window.rich.redo]
    assert not any(action.isEnabled() for action in rich_only) and all(action.isEnabled() for action in always)
    assert window.signature_button.isEnabled() and window.template_button.isEnabled()
    assert all(window.rich.toolbar.widgetForAction(action).isVisible() for action in (window.rich.bold, window.rich.bullets))  # greyed, not hidden
    window.rich_action.trigger()
    assert all(action.isEnabled() for action in rich_only)
    window.plain_action.trigger()
    assert not window.rich.bold.isEnabled()
    close(window)


def test_undo_in_the_three_dots_acts_on_the_text_in_use(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    QTest.keyClicks(window.body, "Plain words")
    window.rich.undo.trigger()
    assert window.body.toPlainText() == ""
    window.rich_action.trigger()
    QTest.keyClicks(window.rich.edit, "Rich words")
    window.rich.undo.trigger()
    assert "Rich words" not in window.rich.toPlainText()
    close(window)


def test_save_in_the_three_dots_does_what_save_draft_does(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    saved: list[bool] = []
    window.save_button.clicked.connect(lambda: saved.append(True))
    window.save_action.trigger()
    assert saved == [True]
    close(window)


def test_the_window_of_one_text_has_the_formatting_but_not_the_message_buttons(qtbot: QtBot) -> None:
    editor = SignatureEditor(None, None, lambda name: False)
    add_window(qtbot, editor)
    rich = editor.findChild(RichEditor)
    assert rich is not None
    assert on_bar(rich) == ["B", "I", "U", "Paint Format", "A", "Bulleted list", "Numbered list", "", "More"]
    assert menu_names(rich) == EDITING
    assert all(action.isEnabled() for action in rich.formatting)
    assert not [action for action in rich.findChildren(QAction) if action.text() in ("Save", "Format", "Attach a file", "Signature", "Template")]




def test_indent_moves_one_paragraph_and_the_reader_sees_it_as_a_margin(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    QTest.keyClicks(window.rich.edit, "Indented")
    window.rich.indent.trigger()
    window.rich.indent.trigger()
    assert window.rich.edit.textCursor().blockFormat().indent() == 2
    sent = window.rich.outgoing_html()
    paragraph = sent[sent.rindex("<p", 0, sent.index("Indented")):sent.index("Indented")]
    assert "margin-left:80px" in paragraph and "-qt-block-indent:2" in paragraph
    window.rich.outdent.trigger()
    window.rich.outdent.trigger()
    window.rich.outdent.trigger()  # never past the margin
    assert window.rich.edit.textCursor().blockFormat().indent() == 0
    close(window)


def test_indent_in_a_list_moves_only_that_item_and_outdent_brings_it_back(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    edit = window.rich.edit
    window.rich.bullets.trigger()
    QTest.keyClicks(edit, "First")
    edit.textCursor().insertBlock()
    QTest.keyClicks(edit, "Second")
    edit.textCursor().insertBlock()
    QTest.keyClicks(edit, "Third")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.PreviousBlock)
    edit.setTextCursor(cursor)
    window.rich.indent.trigger()
    document = edit.document()
    levels = [(document.findBlockByNumber(number).text(), document.findBlockByNumber(number).textList().format().indent()) for number in range(3)]  # type: ignore[union-attr]
    assert levels == [("First", 1), ("Second", 2), ("Third", 1)]
    assert all(document.findBlockByNumber(number).blockFormat().indent() == 0 for number in range(3))  # the list level alone
    sent = window.rich.outgoing_html()
    assert "-qt-block-indent:1" not in sent and "margin-left:40px" not in sent  # in the reader as deep as in the editor
    assert sent.index("<ul") < sent.index("First") < sent.index("<ul", sent.index("First")) < sent.index("Second")  # a list inside the list
    window.rich.outdent.trigger()
    assert document.findBlockByNumber(1).textList() is document.findBlockByNumber(0).textList()  # back in the first list
    close(window)
