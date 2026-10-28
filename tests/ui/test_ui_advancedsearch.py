from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog, SearchScope, build_query
from chrono_mail.engine.commands_view import OrderParameters
from chrono_mail.ui.messagelist import MessageListModel, ViewSpec
from chrono_mail.ui.searchfield import SearchField
from tests.mailfactory import raw_message
from tests.ui.test_ui_window import select, subjects
from tests.ui.world import account_node, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_advancedsearch.py 4.2.406"

# --- Version history ----------------------------------------------------
# v4.2.406: Scope has the search field's three choices in its order; a search with This folder + subfolders runs from the dialog (D-287)
# v2.0.298: the folders under an account in the owner's new order (D-232)
# v2.0.292: the folders under an account in the owner's order (D-232)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: A view with dates or sizes asks the order for them (the list has one kind of view, R-15)
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.19: Initial advanced search tests


def test_build_query_spreads_alternatives_over_the_other_terms() -> None:
    assert build_query() == ""
    assert build_query("x y") == "x y"
    assert build_query("x", any_words="a b") == "x a OR x b"
    assert build_query("x", "big cat", "a b", "dog", sender="ann", subject="plan b") == 'x "big cat" from:ann subject:plan subject:b a OR x "big cat" from:ann subject:plan subject:b b -dog'
    assert build_query(none_words="spam junk") == "-spam -junk"
    assert build_query('say "hi"', phrase='"quoted" OR NOT') == 'say hi "quoted"'


def test_the_dialog_collects_its_fields(qtbot: QtBot) -> None:
    dialog = AdvancedSearchDialog()
    qtbot.addWidget(dialog)
    assert dialog.search() == AdvancedSearch()
    dialog.all_words.setText("budget")
    dialog.from_words.setText("ann")
    dialog.none_words.setText("draft")
    dialog.use_from_date.setChecked(True)
    dialog.from_date.setDate(QDate(2026, 2, 1))
    dialog.use_to_date.setChecked(True)
    dialog.to_date.setDate(QDate(2026, 3, 31))
    dialog.minimum_size.setValue(2)
    dialog.maximum_size.setValue(500)
    dialog.unread.setChecked(True)
    dialog.scope.setCurrentIndex(2)
    assert "from:ann" in dialog.preview.text()
    found = dialog.search()
    assert found == AdvancedSearch("budget from:ann -draft", SearchScope.ALL, True, False, False, "2026-02-01", "2026-03-31", 2048, 512000)
    assert found.extra_filters and not AdvancedSearch("x").extra_filters


def test_a_view_with_dates_or_sizes_asks_for_them(world: World) -> None:
    model = MessageListModel(world.backend)
    for spec in (ViewSpec("a", "Inbox", date_from="2026-01-01"), ViewSpec("a", "Inbox", date_to="2026-01-01"), ViewSpec("a", "Inbox", minimum_size=1), ViewSpec("a", "Inbox", maximum_size=1)):
        asked = model._order_parameters(spec, True, [])  # noqa: SLF001
        assert isinstance(asked, OrderParameters) and (asked.date_from, asked.date_to, asked.minimum_size, asked.maximum_size) == (spec.date_from, spec.date_to, spec.minimum_size, spec.maximum_size)


def put(world: World, subject: str, body: str, moment: datetime) -> None:
    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject=subject, body=body + "\n", message_id=f"<{subject}@x>", date=moment), frozenset())


def test_the_window_runs_an_advanced_search_and_clears_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    put(world, "Old cats", "cats cats", datetime(2025, 1, 5, 9, 0, tzinfo=UTC))
    put(world, "New cats", "cats and dogs", datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    put(world, "New dogs", "dogs only", datetime(2026, 8, 6, 9, 0, tzinfo=UTC))
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 6)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self.any_words.setText("cats dogs")
        self.none_words.setText("only")
        self.use_from_date.setChecked(True)
        self.from_date.setDate(QDate(2026, 1, 1))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert subjects(world.window) == ["New cats"]
    assert world.window.search_box.text() == "cats OR dogs -only" and not world.window.filters.chip.isHidden()
    world.window.filters.chip.click()  # the dates go, the words stay
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and world.window.filters.chip.isHidden())
    assert sorted(subjects(world.window)) == ["New cats", "Old cats"]
    world.window.search_box.clear()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 6)
    select(world.window, 0)


def test_changing_folder_drops_the_advanced_dates(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self.use_to_date.setChecked(True)
        self.to_date.setDate(QDate(2001, 1, 1))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0 and not world.window.filters.chip.isHidden())
    account = account_node(world.window)
    world.window.tree.setCurrentIndex(world.window.tree_model.index(5, 0, account))  # Flagged (Inbox, Unread, Outbox, Sent, Drafts, Flagged: D-232)
    world.window.tree.setCurrentIndex(world.window.tree_model.index(0, 0, account))  # Inbox again
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.window.filters.chip.isHidden())


def test_the_scope_offers_the_same_three_choices_as_the_search_field(qtbot: QtBot) -> None:
    """D-287: This folder only, This folder + subfolders, All folders, in this order, in the dialog and in the search field's menu."""
    dialog = AdvancedSearchDialog()
    qtbot.addWidget(dialog)
    names = ["This folder only", "This folder + subfolders", "All folders"]
    assert [dialog.scope.itemText(number) for number in range(dialog.scope.count())] == names
    assert [SearchScope(dialog.scope.itemData(number)) for number in range(dialog.scope.count())] == list(SearchScope)
    field = SearchField()
    qtbot.addWidget(field)
    menu = field.scope.menu()
    assert menu is not None and [action.text() for action in menu.actions()] == names
    dialog.scope.setCurrentIndex(1)
    assert dialog.search().scope is SearchScope.SUBFOLDERS


def test_the_window_searches_a_folder_and_its_subfolders(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: This folder + subfolders, from the search field and from Advanced search, shows the folder's matches and those of
    every folder inside it, and a message moved into a subfolder joins the list at once."""
    world.call("folder.create", account=world.account_id, folder="Inbox/Old")
    world.call("folder.create", account=world.account_id, folder="Projects")
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    first, second = (str(getattr(world.window.list_model.row_at(row), "message_id")) for row in (0, 1))
    world.call("message.move", account=world.account_id, folder="Inbox", message=first, target="Inbox/Old")
    world.call("message.move", account=world.account_id, folder="Inbox", message=second, target="Projects")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.search_box.setText("Message")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.scope.set_current_index(1)  # This folder + subfolders
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert sorted(str(getattr(world.window.list_model.row_at(row), "folder")) for row in range(2)) == ["Inbox", "Inbox/Old"]
    world.call("message.move", account=world.account_id, folder="Projects", message=second, target="Inbox/Old")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)  # it joins at once
    world.window.scope.set_current_index(0)  # This folder only
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self.all_words.setText("Message")
        self.scope.setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.scope.text() in ("This folder + subfolders", "")  # the field shows where the search looks (or only its ▾)
    assert world.window.scope.current_data() is SearchScope.SUBFOLDERS
    world.call("folder.delete", account=world.account_id, folder="Inbox/Old", with_messages=True)  # its mail leaves the list
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
