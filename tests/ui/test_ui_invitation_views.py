from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import delete

from chrono_mail.ui.invitationcalendar import InvitationDelegateDialog
from chrono_mail.ui.invitationwindow import CalendarSource
from chrono_mail.ui.readingpane import ReadingPane
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import shown

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_views.py 4.4.554"

# --- Version history ----------------------------------------------------
# v4.4.554: Delegate asks its calendar first: the dialog test goes to _delegate (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.517: The stand-in of the reading pane has _renew_blocks, which the pane calls since 4.4.515
# v4.4.514: every dialog of an invite belongs to the main window (D-310)
# v4.4.511: Initial tests: every open window of a mail follows an answer, and Choose a calendar opens with the card window gone (D-310)

WHERE: tuple[str, str, str] = ("a", "Inbox", "m1")


def test_every_other_open_window_of_that_mail_shows_the_new_card(qtbot: QtBot) -> None:
    """P11: Accept in a message window left the card in the reading pane as it was."""
    pane, same, other = ReaderWidget(), ReaderWidget(), ReaderWidget()
    for reader in (pane, same, other):
        qtbot.addWidget(reader)
        reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    answered = replace(shown(html="<p></p>"), invitation=replace(CARD, stored_in="Calendar (Work)"))

    def ask(_: object, __: object, done: Any, *___: object, **____: object) -> None:  # noqa: ANN401 - the answer's type is the command's
        done(answered)

    host = SimpleNamespace(_card_windows=[], _renew_blocks=lambda *_: False, shown=WHERE, reader=pane, _backend=SimpleNamespace(ask=ask),
                           message_windows=[SimpleNamespace(where=WHERE, reader=same), SimpleNamespace(where=("a", "Inbox", "m2"), reader=other)])
    acting = ReaderWidget()
    qtbot.addWidget(acting)
    ReadingPane._refresh_others(cast(ReadingPane, host), acting, CalendarSource(*WHERE))  # noqa: SLF001
    assert pane.invitation is not None and pane.invitation.stored_in == "Calendar (Work)"
    assert same.invitation is not None and same.invitation.stored_in == "Calendar (Work)"
    assert other.invitation is not None and other.invitation.stored_in == ""


def test_a_late_refresh_leaves_the_pane_alone_when_another_mail_was_chosen(qtbot: QtBot) -> None:
    pane, acting = ReaderWidget(), ReaderWidget()
    for reader in (pane, acting):
        qtbot.addWidget(reader)
    pane.show_message(replace(shown(html="<p></p>"), invitation=CARD))

    def ask(_: object, __: object, done: Any, *___: object, **____: object) -> None:  # noqa: ANN401 - the answer's type is the command's
        host.shown = ("a", "Inbox", "m2")  # the same invitation in another mail, chosen while the answer was on its way
        done(replace(shown(html="<p></p>"), invitation=replace(CARD, stored_in="Calendar (Work)")))

    host = SimpleNamespace(_card_windows=[], _renew_blocks=lambda *_: False, shown=WHERE, reader=pane, _backend=SimpleNamespace(ask=ask), message_windows=[])
    ReadingPane._refresh_others(cast(ReadingPane, host), acting, CalendarSource(*WHERE))  # noqa: SLF001
    assert pane.invitation is not None and pane.invitation.stored_in == ""


def test_an_answer_finishes_when_its_window_was_closed_meanwhile(qtbot: QtBot) -> None:
    """Review: with the window deleted the card could no longer be read, and the answer was never asked."""
    closed = ReaderWidget()
    delete(closed)
    asked: list[object] = []

    def ask(command: object, *_: object, **__: object) -> None:
        asked.append(command)

    def others(*_: object) -> None:
        asked.append("others")

    host = SimpleNamespace(_backend=SimpleNamespace(ask=ask), _refresh_others=others)
    ReadingPane._send_answer(cast(ReadingPane, host), closed, CalendarSource(*WHERE), "ACCEPTED", "c1", CARD)  # noqa: SLF001
    assert len(asked) == 1
    ReadingPane._show_again(cast(ReadingPane, host), closed, CalendarSource(*WHERE))  # noqa: SLF001
    assert asked[-1] == "others"  # nothing more: there is no window to show it in


def test_every_dialog_of_an_invite_belongs_to_the_main_window_whatever_window_was_clicked(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 10: the action belongs to the program: closing a window never loses a click."""
    main, card = ReaderWidget(), ReaderWidget()
    qtbot.addWidget(main)
    qtbot.addWidget(card)
    parents: list[QWidget | None] = []

    def refused(dialog: InvitationDelegateDialog) -> int:
        parents.append(dialog.parentWidget())
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(InvitationDelegateDialog, "exec", refused)
    ReadingPane._delegate(cast(ReadingPane, SimpleNamespace(_parent=main)), card, CalendarSource(*WHERE), False, "")
    assert parents == [main]
