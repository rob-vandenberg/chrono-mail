from collections.abc import Callable
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_addressbook import CONTACT_LIST
from tests.ui.test_ui_contacts import open_contacts, seed, select_rows
from tests.ui.test_ui_tag_filters import rows
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_contact_late_answer.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial: a late answer about one contact, asked with the old tag, never takes it out of the list of every contact (D-146)


def names(page: Any) -> list[str]:  # noqa: ANN401 - the Contacts page
    return [str(getattr(row, "name")) for row in page.model.rows]  # getattr: the model's rows are the engine's contact rows


def test_an_answer_about_one_contact_asked_before_the_list_was_asked_again_is_not_used(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: "Business  (1)" in rows(page.tag_filter))
    page.tag_filter.choose("Business")
    wait(qtbot, lambda: names(page) == ["Rebecca Hoffman"])
    held: list[Callable[[], None]] = []
    ask = page._backend.ask  # noqa: SLF001

    def hold_one_contact(command: Any, parameters: Any, done: Callable[[Any], None], failed: Any, owner: Any = None) -> None:  # noqa: ANN401 - forwarded as given
        if command is CONTACT_LIST and getattr(parameters, "item", ""):  # the answer about the one contact: it comes late, as on a busy computer
            ask(command, parameters, lambda result: held.append(lambda: done(result)), failed, owner=owner)
            return
        ask(command, parameters, done, failed, owner=owner)

    monkeypatch.setattr(page._backend, "ask", hold_one_contact)  # noqa: SLF001
    select_rows(page, 0)
    page._edit_selected(remove_tag=("Business",))  # noqa: SLF001 - the Tag menu's own step
    wait(qtbot, lambda: len(held) >= 1)  # the answer about Rebecca, asked with Business chosen, is held
    page.tag_filter.choose("")  # turned off: every contact is asked again
    wait(qtbot, lambda: len(names(page)) == 3)
    for late in held:
        late()  # the answer asked with Business chosen comes after every contact was asked again
    assert len(names(page)) == 3 and "Rebecca Hoffman" in names(page)
