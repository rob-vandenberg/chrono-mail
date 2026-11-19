from datetime import UTC, datetime

from PySide6.QtGui import QTextBlock
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ComposeResult
from chrono_mail.engine.compose import ComposeKind, build_draft
from chrono_mail.mime import Address, ParsedMessage
from chrono_mail.ui.composer import ComposeWindow
from tests.ui.test_ui_pictures import rich_answer

from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_quote_styles.py 4.6.631"

# --- Version history ----------------------------------------------------
# v4.6.631: Initial tests: a quote keeps the original's own style rules, which reach only the original; the own lines have no space
#           around them, in the editor and in what is sent (D-360)

RED = "#ff0000"
STYLED = ('<html><head><style><!-- p.MsoNormal {margin:0cm; color:#ff0000} --></style></head>'
          '<body><p class="MsoNormal">Dag Ann,</p><p class="MsoNormal">Zoals besproken komen we morgen.</p></body></html>')


def styled_answer(kind: ComposeKind = "reply") -> ComposeResult:
    """The program's answer to Reply on a mail whose own style rules put its lines tight together and make them red."""
    original = ParsedMessage(
        subject="Offerte", from_addresses=(Address("Bert Peeters", "bert@example.com"),), to_addresses=(Address("", "me@example.com"),),
        cc_addresses=(), reply_to_addresses=(), date=datetime(2026, 10, 27, 13, 33, tzinfo=UTC), message_id="<m1@x>", references=(),
        text_body=None, html_body=STYLED, attachments=(),
    )
    draft = build_draft(original, kind, "me@example.com")
    return ComposeResult(draft.kind, draft.to, draft.cc, draft.subject, draft.body, draft.in_reply_to, draft.references, draft.attachment_names,
                         None, draft.quote_intro, draft.quote_original)


def block_with(window: ComposeWindow, text: str) -> QTextBlock:
    block = window.rich.edit.document().begin()
    while block.isValid():
        if text in block.text():
            return block
        block = block.next()
    raise AssertionError(f"no line with {text!r}")


def spacing(block: QTextBlock) -> tuple[float, float]:
    return block.blockFormat().topMargin(), block.blockFormat().bottomMargin()


def colour(block: QTextBlock) -> str:
    return block.begin().fragment().charFormat().foreground().color().name()


def test_the_quoted_original_keeps_its_own_line_spacing_and_colour(world: World, qtbot: QtBot) -> None:
    window = rich_answer(world, qtbot, styled_answer())
    for text in ("Dag Ann,", "Zoals besproken"):
        assert spacing(block_with(window, text)) == (0.0, 0.0)
        assert colour(block_with(window, text)) == RED


def test_the_originals_style_rules_never_reach_the_own_lines(world: World, qtbot: QtBot) -> None:
    window = rich_answer(world, qtbot, styled_answer())
    intro = block_with(window, "wrote:")
    assert spacing(intro) == (0.0, 0.0) and colour(intro) != RED
    own = window.rich.edit.document().begin()  # the own empty line above the quote, where the cursor stands
    assert own.text() == "" and spacing(own) == (0.0, 0.0)


def test_a_line_typed_in_a_reply_is_one_line_high_and_is_sent_so(world: World, qtbot: QtBot) -> None:
    window = rich_answer(world, qtbot, styled_answer())
    cursor = window.rich.edit.textCursor()
    cursor.insertText("Prima, tot morgen.")
    cursor.insertBlock()
    cursor.insertText("Bert")
    for text in ("Prima, tot morgen.", "Bert"):
        assert spacing(block_with(window, text)) == (0.0, 0.0) and colour(block_with(window, text)) != RED
    sent = window.rich.outgoing_html()
    for text in ("Prima, tot morgen.", "Dag Ann,"):
        paragraph = sent[sent.rindex("<p", 0, sent.index(text)):sent.index(text)]
        assert "margin-top:0px; margin-bottom:0px;" in paragraph


def test_a_forward_keeps_the_originals_style_and_its_header_lines_tight(world: World, qtbot: QtBot) -> None:
    window = rich_answer(world, qtbot, styled_answer("forward"))
    assert spacing(block_with(window, "Dag Ann,")) == (0.0, 0.0) and colour(block_with(window, "Dag Ann,")) == RED
    header = block_with(window, "Forwarded message")
    assert spacing(header) == (0.0, 0.0) and colour(header) != RED
