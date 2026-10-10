import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from chrono_mail.core import MessageFlag
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.addresscompleter import AddressCompleter, split_last_address
from tests.mailfactory import raw_message
from tests.ui.test_ui_composer import new_composer
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_completer.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: Initial address completion tests


def fill_addresses(world: World) -> None:
    with open_mailbox(world.engine, world.account_id) as mailbox:
        for number, (sender, to) in enumerate([("Ann Archer <ann@x.org>", "me@example.com"), ("me@example.com", '"Brown, Bob" <bob@x.org>, Bea <bea@x.org>')]):
            raw = raw_message(subject=f"S{number}", sender=sender, to=to, message_id=f"<a{number}@x>")
            mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset({MessageFlag.SEEN}))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("", "")),
        ("bo", ("", "bo")),
        ("ann@x.org, bo", ("ann@x.org,", "bo")),
        ("ann@x.org,  bo", ("ann@x.org,", "bo")),
        ("a@x.org; b", ("a@x.org;", "b")),
        ('"Brown, Bob" <bob@x.org>, c', ('"Brown, Bob" <bob@x.org>,', "c")),
        ('"Brown, Bo', ("", '"Brown, Bo')),
        ("Bob <b,c@x.org", ("", "Bob <b,c@x.org")),
        ("a@x.org, ", ("a@x.org,", "")),
    ],
)
def test_the_address_being_typed_is_found(text: str, expected: tuple[str, str]) -> None:
    assert split_last_address(text) == expected


def test_typing_offers_suggestions_and_choosing_one_keeps_the_others(world: World, qtbot: QtBot) -> None:
    fill_addresses(world)
    window = new_composer(world, qtbot)
    completer = window.completers[0]
    window.to.setFocus()
    QTest.keyClicks(window.to, "ann@x.org, br")
    wait(qtbot, lambda: completer._suggestions.rowCount() == 1)  # noqa: SLF001
    assert completer._suggestions.stringList() == ['"Brown, Bob" <bob@x.org>']  # noqa: SLF001
    index = completer._suggestions.index(0, 0)  # noqa: SLF001
    assert completer.pathFromIndex(index) == 'ann@x.org, "Brown, Bob" <bob@x.org>, '
    assert completer.splitPath("ann@x.org, br") == ["br"]
    window._finished = True  # noqa: SLF001
    window.close()


def test_short_fragments_ask_nothing_and_old_answers_are_dropped(world: World, qtbot: QtBot) -> None:
    fill_addresses(world)
    window = new_composer(world, qtbot)
    completer = window.completers[1]
    window.cc.setFocus()
    QTest.keyClicks(window.cc, "b")
    qtbot.wait(300)
    assert completer._suggestions.rowCount() == 0  # noqa: SLF001
    QTest.keyClicks(window.cc, "e")
    wait(qtbot, lambda: completer._suggestions.rowCount() == 1)  # noqa: SLF001
    QTest.keyClicks(window.cc, "zzz")  # no longer matches anything
    wait(qtbot, lambda: completer._suggestions.rowCount() == 0)  # noqa: SLF001
    window._finished = True  # noqa: SLF001
    window.close()


def test_all_three_address_fields_have_a_completer(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    assert [field.completer() is not None for field in (window.to, window.cc, window.bcc)] == [True, True, True]
    assert len(window.completers) == 3 and all(isinstance(item, AddressCompleter) for item in window.completers)
    assert not isinstance(window.subject, QLineEdit) or window.subject.completer() is None  # the subject has none
    window._finished = True  # noqa: SLF001
    window.close()


def test_the_completer_survives_a_failing_command(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import StorageError
    from chrono_mail.index import Index

    fill_addresses(world)

    def broken(self: Index, text: str, limit: int = 8) -> object:
        raise StorageError("the index is gone")

    monkeypatch.setattr(Index, "suggest", broken)
    window = new_composer(world, qtbot)
    QTest.keyClicks(window.to, "ann")
    qtbot.wait(400)
    assert window.completers[0]._suggestions.rowCount() == 0 and window.to.text() == "ann"  # noqa: SLF001
    monkeypatch.undo()
    QTest.keyClicks(window.to, "@")
    wait(qtbot, lambda: window.completers[0]._suggestions.rowCount() == 1)  # noqa: SLF001 - it works again afterwards
    window._finished = True  # noqa: SLF001
    window.close()
