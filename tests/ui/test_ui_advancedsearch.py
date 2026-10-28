from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from PySide6.QtWidgets import QFormLayout, QLabel

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog, SearchScope, build_query
from chrono_mail.engine.commands_view import OrderParameters
from chrono_mail.ui.messagelist import MessageListModel, ViewSpec
from chrono_mail.ui.searchfield import SearchField
from tests.mailfactory import raw_message
from tests.ui.test_ui_window import select, subjects
from tests.ui.world import account_node, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_advancedsearch.py 4.2.408"

# --- Version history ----------------------------------------------------
# v4.2.408: the lower fields line up with the upper ones; closing More gives the room back; the filters on are kept;
#           Where to search and the main fields first, the rest below More... (opens and closes wherever it is clicked); Sent, Tag,
#           Snoozed until and Send at, greyed where such mail cannot be; the new days and the tag reach the list (D-288)
# v4.2.407: All folders at start; the dialog opens on the search field's choice; Advanced search… in the field's menu (D-287)
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
    dialog.received.use_from.setChecked(True)
    dialog.received.day_from.setDate(QDate(2026, 2, 1))
    dialog.received.use_to.setChecked(True)
    dialog.received.day_to.setDate(QDate(2026, 3, 31))
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
        self.received.use_from.setChecked(True)
        self.received.day_from.setDate(QDate(2026, 1, 1))
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
        self.received.use_to.setChecked(True)
        self.received.day_to.setDate(QDate(2001, 1, 1))
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


def test_both_start_on_all_folders_and_the_dialog_opens_on_the_choice_given(qtbot: QtBot) -> None:
    """D-287: All folders at every start, in the search field and in Advanced search; the dialog opens where the field looks;
    the field's menu ends with Advanced search… after a line, which is no choice."""
    assert AdvancedSearch().scope is SearchScope.ALL
    field = SearchField()
    qtbot.addWidget(field)
    assert field.scope.current_data() is SearchScope.ALL and field.scope.text() == "All folders" and field.scope.toolTip() == "All folders"
    for scope in SearchScope:
        dialog = AdvancedSearchDialog(scope=scope)
        qtbot.addWidget(dialog)
        assert dialog.search().scope is scope
    command = QAction("Advanced search…", field)
    field.scope.add_command(command)
    menu = field.scope.menu()
    assert menu is not None
    actions = menu.actions()
    assert [action.text() for action in actions[:3]] == ["This folder only", "This folder + subfolders", "All folders"]
    assert actions[3].isSeparator() and actions[4] is command and not command.isCheckable()


def test_the_search_fields_menu_opens_advanced_search(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-287: Advanced search… in the search field's ▾ menu opens the dialog; the button keeps showing where the search looks."""
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    opened: list[bool] = []

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        opened.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    menu = world.window.scope.menu()
    assert menu is not None
    (item,) = [action for action in menu.actions() if action.text().startswith("Advanced search")]
    item.trigger()
    assert opened == [True] and world.window.scope.current_data() is SearchScope.ALL


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
    assert world.window.scope.current_data() is SearchScope.ALL  # at every start
    world.window.scope.set_current_index(0)  # This folder only
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

    opened_on: list[SearchScope] = []

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        opened_on.append(SearchScope(self.scope.currentData()))
        self.all_words.setText("Message")
        self.scope.setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.scope.text() in ("This folder + subfolders", "")  # the field shows where the search looks (or only its ▾)
    assert world.window.scope.current_data() is SearchScope.SUBFOLDERS and opened_on == [SearchScope.FOLDER]  # it opened on the field's choice
    world.window.act_advanced.trigger()  # again: now on This folder + subfolders, which it keeps
    assert opened_on == [SearchScope.FOLDER, SearchScope.SUBFOLDERS]
    world.call("folder.delete", account=world.account_id, folder="Inbox/Old", with_messages=True)  # its mail leaves the list
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)


def _labels(dialog: AdvancedSearchDialog, name: str) -> list[str]:
    layout = dialog.findChild(QFormLayout, name)
    assert layout is not None
    found: list[str] = []
    for row in range(layout.rowCount()):
        item = layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
        widget = item.widget() if item is not None else None
        assert isinstance(widget, QLabel)
        found.append(widget.text())
    return found


def test_where_to_search_and_the_main_fields_come_first_and_more_opens_wherever_it_is_clicked(qtbot: QtBot) -> None:
    """D-288: Where to search, Words, From, To, Subject, Body and Received; the rest below More..., a bar that opens and closes
    wherever it is clicked, its arrow pointing the way it goes."""
    dialog = AdvancedSearchDialog(tags=["Work", "Home"])
    qtbot.addWidget(dialog)
    dialog.show()
    assert _labels(dialog, "main") == ["Where to search", "Words", "From", "To", "Subject contains", "Body contains", "Received"]
    assert _labels(dialog, "extra") == [
        "This exact phrase", "Any of these words", "None of these words", "Sent", "Size", "Only", "Tag", "Snoozed until", "Send at",
    ]
    assert dialog.more.text_label.text() == "More..." and dialog.more.arrow.text() == "▾" and not dialog.more_fields.isVisible()
    for place in (QPoint(dialog.more.width() - 3, dialog.more.height() // 2), QPoint(dialog.more.width() // 2, dialog.more.height() // 2), QPoint(3, 3)):
        before = dialog.more_fields.isVisible()
        qtbot.mouseClick(dialog.more, Qt.MouseButton.LeftButton, pos=place)
        assert dialog.more_fields.isVisible() != before
        assert dialog.more.arrow.text() == ("▴" if dialog.more_fields.isVisible() else "▾")
    assert [dialog.tag.itemText(number) for number in range(dialog.tag.count())] == ["No tag chosen", "Work", "Home"]
    dialog.tag.setCurrentIndex(1)
    dialog.sent.use_from.setChecked(True)
    dialog.sent.day_from.setDate(QDate(2026, 4, 1))
    found = dialog.search()
    assert found.tag == "Work" and found.sent_from == "2026-04-01" and found.sent_to is None and found.extra_filters


def test_snoozed_until_and_send_at_are_greyed_where_such_mail_cannot_be(qtbot: QtBot) -> None:
    """D-288: usable in all folders, in Snoozed (Snoozed until) and in Scheduled or the Outbox (Send at), and in their Favorites
    entries; greyed elsewhere, and a greyed row asks for nothing."""
    cases: list[tuple[str, str | None, SearchScope, bool, bool]] = [
        ("inbox", None, SearchScope.FOLDER, False, False),
        ("inbox", None, SearchScope.SUBFOLDERS, False, False),
        ("inbox", None, SearchScope.ALL, True, True),
        ("snoozed", None, SearchScope.FOLDER, True, False),
        ("scheduled", None, SearchScope.FOLDER, False, True),
        ("outbox", None, SearchScope.FOLDER, False, True),
        ("other", "inboxes", SearchScope.ALL, False, False),
        ("other", "snoozed", SearchScope.ALL, True, False),
        ("other", "scheduled", SearchScope.ALL, False, True),
    ]
    for role, combined, scope, snoozed, send_at in cases:
        dialog = AdvancedSearchDialog(scope=scope, folder_role=role, combined=combined)
        qtbot.addWidget(dialog)
        assert (dialog.snoozed.use_from.isEnabled(), dialog.send_at.use_from.isEnabled()) == (snoozed, send_at), (role, combined, scope)
    dialog = AdvancedSearchDialog(scope=SearchScope.ALL, folder_role="inbox")
    qtbot.addWidget(dialog)
    dialog.snoozed.use_to.setChecked(True)
    assert dialog.search().snoozed_to is not None
    dialog.scope.setCurrentIndex(0)  # This folder only: greyed, the tick kept, nothing asked
    assert not dialog.snoozed.use_to.isEnabled() and dialog.snoozed.use_to.isChecked() and dialog.search().snoozed_to is None


def test_the_window_passes_the_sent_days_and_the_tag_to_the_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-288: the Sent days narrow the list (the chip shows them) and the Tag is Filter > Tagged with."""
    put(world, "Old cats", "cats", datetime(2025, 1, 5, 9, 0, tzinfo=UTC))
    put(world, "New cats", "cats", datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 5)

    def fake_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self.all_words.setText("cats")
        self.sent.use_from.setChecked(True)
        self.sent.day_from.setDate(QDate(2026, 1, 1))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", fake_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: subjects(world.window) == ["New cats"] and not world.window.filters.chip.isHidden())
    spec = world.window.messages.view_spec()
    assert spec is not None and spec.sent_from == "2026-01-01" and spec.date_from is None
    assert {"from", "to", "folder"} <= set(world.window.columns.columns)  # a search of all folders: sent and received mail, and where each is

    def tagged_exec(self: AdvancedSearchDialog) -> QDialog.DialogCode:
        self.tag.addItem("Work", "Work")  # a tag of the window's own would come from the tags kept
        self.tag.setCurrentIndex(self.tag.count() - 1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AdvancedSearchDialog, "exec", tagged_exec)
    world.window.act_advanced.trigger()
    wait(qtbot, lambda: world.window.mail_state.view.tag == "Work" and world.window.filters.button.isChecked())


def test_the_lower_fields_line_up_with_the_upper_ones_and_closing_more_gives_the_room_back(qtbot: QtBot) -> None:
    """D-288: the labels and fields below More... start where those above it do; the window grows when More opens and shrinks back
    when it closes; it opens with the filters that are on."""
    dialog = AdvancedSearchDialog(tags=["Work"], marks=(True, False, True, "Work"))
    qtbot.addWidget(dialog)
    dialog.show()
    closed = dialog.height()
    dialog.more.click()
    qtbot.wait(20)
    assert dialog.height() > closed
    for upper, lower in ((dialog.scope, dialog.phrase), (dialog.all_words, dialog.any_words)):
        assert upper.mapTo(dialog, QPoint(0, 0)).x() == lower.mapTo(dialog, QPoint(0, 0)).x()
    upper_label, lower_label = dialog.findChild(QLabel, "scopeLabel"), dialog.findChild(QLabel, "phraseLabel")
    assert upper_label is not None and lower_label is not None and upper_label.mapTo(dialog, QPoint(0, 0)).x() == lower_label.mapTo(dialog, QPoint(0, 0)).x()
    dialog.resize(dialog.width() + 80, dialog.height())  # the person makes it wider: that width stays
    wide = dialog.width()
    dialog.more.click()
    qtbot.wait(20)
    assert dialog.height() == closed and dialog.width() == wide
    found = dialog.search()
    assert (found.unread, found.flagged, found.attachments, found.tag) == (True, False, True, "Work")
    other = AdvancedSearchDialog(tags=["Work"], marks=(False, False, False, "work"))  # the same tag in other letters: not listed twice
    qtbot.addWidget(other)
    assert other.tag.count() == 2 and other.search().tag == "Work"
