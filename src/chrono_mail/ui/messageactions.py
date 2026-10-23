from collections.abc import Callable, Sequence
from datetime import datetime

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import QMenu, QMessageBox, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_actions import FOLDER_EMPTY, FOLDER_MARK_READ, EmptyResult, FolderActionParameters, MarkReadResult
from chrono_mail.engine.commands_mail import MessageParameters
from chrono_mail.engine.commands_schedule import MAIL_UNSCHEDULE, MESSAGE_WAKE, WakeParameters
from chrono_mail.engine.commands_many import (
    MESSAGE_ARCHIVE_MANY, MESSAGE_COPY_MANY, MESSAGE_DELETE_MANY, MESSAGE_FLAG_MANY, MESSAGE_JUNK_MANY, MESSAGE_MOVE_MANY, MESSAGE_NOT_JUNK_MANY,
    MESSAGE_SNOOZE_MANY, MESSAGE_UNARCHIVE_MANY, ManyDeleteParameters, ManyFlagParameters, ManyParameters, ManyResult, ManySnoozeParameters,
    ManyTargetParameters, NotDone,
)
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.engine.schedule import snooze_presets
from chrono_mail.store import MessageNotFoundError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH
from chrono_mail.ui.mailstate import MailState, Placed
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.models import LOCAL_PLACES, SCHEDULED_TARGET_ROLES
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.undoing import Undoing
from chrono_mail.ui.whendialog import WhenDialog
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "messageactions.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: every action on the selection waits for the places of messages still on their way, then takes them all (W12b)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v2.4.329: every action takes every selected message, also those not loaded yet (W12); Toggle read and Toggle flag count them all
# v2.2.313: every action of the person has its number for Undo (one key press, however many commands); Undo waits for the commands under way (B-18)
# v2.0.306: Archive or Unarchive follows the selected messages, not the folder on the left: all archived is Unarchive, else the others are archived (D-101)
# v1.9.274: R-19 round 4: the rows are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: every engine call typed; a scheduled message moved to Drafts is unscheduled by a typed call (_unschedules)
# v1.9.265: A message not done because its file is gone gives the one warning of the action again (D-109; silent since 1.1.247)
# v1.9.264: R-17: the actions on messages as a part of their own (out of ui/mainwindow.py): the marks, Delete (also for good), Move (also
#           a drag) and Copy, Archive and Unarchive, Junk and Not junk, Snooze and Cancel snooze, Mark all as read, Empty folder; one
#           command per account and folder (R-08), greyed at the key press, one line for what could not be done

_WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # in the snooze menu, English like the rest of the interface


class MessageActions(QObject):
    """What can be done with the selected messages. Every action is a command of the engine; the rows follow its facts (D-146)."""

    def __init__(self, backend: Backend, state: MailState, status: StatusReport, messages: MessageListPane, undoing: Undoing, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._undoing: Undoing = undoing
        self._state: MailState = state
        self._status: StatusReport = status
        self._messages: MessageListPane = messages
        self._parent: QWidget = parent
        self.move_menu: QMenu = QMenu(parent)
        self.move_menu.aboutToShow.connect(self.fill_move_menu)
        self.folder_popup: QMenu | None = None

    # --- marks --------------------------------------------------------------------------------------------------

    def set_flags(self, targets: list[tuple[str, str]], values: dict[str, bool], account: str | None = None, undo: int = 0) -> None:
        """Marks for many messages: one command per folder (on an IMAP server in groups, R-08); each row takes its marks from the answer.
        undo: the number of the person's action (0: not undone, as the mark of a message opened in the reading pane)."""
        account_id = account or self._state.view.account
        if account_id is None or not targets:
            return

        def answered(result: ManyResult) -> None:
            for message_id, flags in result.flags:
                self._messages.model.replace_flags(message_id, flags)  # the folder list's numbers follow the facts (D-146)

        self.act_many(
            MESSAGE_FLAG_MANY, lambda account, folder, messages: ManyFlagParameters(account, folder, messages, seen=values.get("seen"), flagged=values.get("flagged"), undo=undo),
            [(account_id, folder, message_id) for folder, message_id in targets], "", "Could not change", take_away=False, each=answered,
        )

    def mark_selected(self, seen: bool | None = None, flagged: bool | None = None) -> None:
        if self._messages.deferred(lambda: self.mark_selected(seen, flagged)):
            return
        self.mark_messages(self._messages.selected_rows(), seen, flagged)

    def mark_messages(self, rows: Sequence[Placed], seen: bool | None = None, flagged: bool | None = None) -> None:
        values: dict[str, bool] = {}
        if seen is not None:
            values["seen"] = seen
        if flagged is not None:
            values["flagged"] = flagged
        account_of = self._state.account_of
        number = self._undoing.new_action()  # one key press, however many accounts: one step of Undo
        for account in dict.fromkeys(account_of(row) for row in rows):  # the combined views mix accounts
            self.set_flags([(row.folder, row.message_id) for row in rows if account_of(row) == account], values, account, number)

    def toggle_read(self) -> None:
        """All read: unread, else read; counted over every selected message (their rows are asked first when they are not loaded)."""
        if self._messages.deferred(lambda: self.toggle_read()):
            return
        chosen = self._messages.selected_rows()
        if chosen:
            self._messages.with_rows(chosen, lambda rows: self.mark_messages(chosen, seen=any("seen" not in row.flags for row in rows)))

    def toggle_flag(self) -> None:
        if self._messages.deferred(lambda: self.toggle_flag()):
            return
        chosen = self._messages.selected_rows()
        if chosen:
            self._messages.with_rows(chosen, lambda rows: self.mark_messages(chosen, flagged=any("flagged" not in row.flags for row in rows)))

    # --- delete, archive, junk ---------------------------------------------------------------------------------------

    def delete_selected(self) -> None:
        if self._messages.deferred(lambda: self.delete_selected()):
            return
        self.delete_rows(self._messages.selected_rows())

    def delete_rows(self, rows: Sequence[Placed]) -> None:
        if not rows or self._state.view.account is None:
            return
        in_trash = self._state.view.folder_role == "trash"
        if in_trash and QMessageBox.question(self._parent, "Delete", f"Delete {counted(len(rows), 'message')} for good? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_DELETE_MANY, lambda account, folder, messages: ManyDeleteParameters(account, folder, messages, undo=number), self._state.targets(rows),
            f"Deleted {counted(len(rows), 'message')}" if in_trash else f"Moved {counted(len(rows), 'message')} to Trash", "Could not delete",
        )

    def delete_permanently(self) -> None:
        """Shift+Delete: remove the selected messages for good, without a stop in Trash, after asking."""
        if self._messages.deferred(lambda: self.delete_permanently()):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        if QMessageBox.question(self._parent, "Delete permanently", f"Delete {counted(len(rows), 'message')} for good, without moving them to Trash? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        self.act_many(
            MESSAGE_DELETE_MANY, lambda account, folder, messages: ManyDeleteParameters(account, folder, messages, permanent=True), self._state.targets(rows),
            f"Deleted {counted(len(rows), 'message')} for good", "Could not delete",
        )

    def unarchive_selected(self) -> None:
        """Back to the folder each message was archived from (the Inbox when that is not known)."""
        if self._messages.deferred(lambda: self.unarchive_selected()):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_UNARCHIVE_MANY, lambda account, folder, messages: ManyParameters(account, folder, messages, undo=number), self._state.targets(rows),
            f"Unarchived {counted(len(rows), 'message')}", "Could not unarchive",
        )

    def archive_selected(self) -> None:
        """Archive, or Unarchive when every selected message is archived: it follows the messages, never the folder on the left (a
        search of all folders shows messages of every folder, D-101)."""
        if self._messages.deferred(lambda: self.archive_selected()):
            return
        selected = self._messages.selected_rows()
        if self._state.all_archived(selected):
            self.unarchive_selected()
            return
        rows = [row for row in selected if not self._state.archived(row)]  # already there
        if not self._messages.selected_rows() or self._state.view.account is None:
            return
        if not rows:
            self._status.show("Everything selected is in the archive already")
            return
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_ARCHIVE_MANY, lambda account, folder, messages: ManyParameters(account, folder, messages, undo=number), self._state.targets(rows),
            f"Archived {counted(len(rows), 'message')}", "Could not archive",
        )

    def junk_selected(self, junk: bool) -> None:
        """Move the selected messages to the Junk folder, or (in Junk) back to the Inbox."""
        if self._messages.deferred(lambda: self.junk_selected(junk)):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        finished = f"Moved {counted(len(rows), 'message')} to Junk" if junk else f"Moved {counted(len(rows), 'message')} back to the Inbox"
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_JUNK_MANY if junk else MESSAGE_NOT_JUNK_MANY, lambda account, folder, messages: ManyParameters(account, folder, messages, undo=number),
            self._state.targets(rows), finished, "Could not move",
        )

    def junk_key(self) -> None:
        """One key does both, as the folder decides: Move to Junk, or in Junk back to the Inbox."""
        self.junk_selected(self._state.view.folder_role != "junk")

    # --- snooze ----------------------------------------------------------------------------------------------------------

    def fill_snooze_menu(self, menu: QMenu, cancel: QAction) -> None:
        """The ready-made times, counted from now, and a choice of one's own."""
        menu.clear()
        if self._state.in_snoozed():
            menu.addAction(cancel)
            menu.addSeparator()
        for name, moment in snooze_presets(datetime.now()):
            label = f"{name} ({_WEEKDAYS[moment.weekday()]} {moment:%H:%M})"
            menu.addAction(label, lambda until=moment.strftime("%Y-%m-%dT%H:%M"): self.snooze_until(until))
        menu.addSeparator()
        menu.addAction("Custom date and time…", self.snooze_selected)

    def snooze_selected(self) -> None:
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        dialog = WhenDialog("Snooze", "Bring the message back, unread, at:", "Snooze", self._parent)
        if dialog.exec() != WhenDialog.DialogCode.Accepted:
            return
        self.snooze_until(dialog.chosen())

    def snooze_until(self, until: str) -> None:
        """Put the selected messages away until this local time (2026-10-13T09:00)."""
        if self._messages.deferred(lambda: self.snooze_until(until)):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_SNOOZE_MANY, lambda account, folder, messages: ManySnoozeParameters(account, folder, until, messages, undo=number), self._state.targets(rows),
            f"Snoozed {counted(len(rows), 'message')} until {until.replace('T', ' ')}", "Could not snooze",
        )

    def cancel_snooze_selected(self) -> None:
        """In the Snoozed folder: the selected messages go back now to the folder they came from (unread, as when their time comes)."""
        if self._messages.deferred(lambda: self.cancel_snooze_selected()):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None or not self._state.in_snoozed():
            return
        account = self._state.view.account
        remaining = len(rows)
        number = self._undoing.new_action()
        self._messages.take_away([row.message_id for row in rows])

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._status.finished(f"Brought back {counted(len(rows), 'message')}")

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining
            remaining -= 1
            self._status.report("Could not cancel the snooze", error)

        for row in rows:
            message_id = row.message_id
            on_success, on_error = self._each(message_id, done, failed)
            self._undoing.started()
            self._backend.ask(MESSAGE_WAKE, WakeParameters(self._state.account_of(row) or account, message_id, undo=number), on_success, on_error, owner=self, in_order=True)

    # --- move and copy -----------------------------------------------------------------------------------------------------

    def popup_folders(self, how: str) -> None:
        """Move or copy the selected messages: the folders of their account, as a menu at the pointer."""
        rows = self._messages.selected_rows()
        if not rows:
            return
        account = self._state.account_of(rows[0])
        menu = QMenu(self._parent)
        for info in self._state.folders.get(account, []):
            path = info.path
            if path == rows[0].folder or path in LOCAL_PLACES:
                continue
            menu.addAction(path).triggered.connect(lambda checked=False, target=path: self.copy_selected(target) if how == "copy" else self.move_selected(target))
        self.folder_popup = menu  # kept: a menu shown with popup() is gone when its last reference is
        menu.popup(QCursor.pos())

    def copy_selected(self, target: str) -> None:
        if self._messages.deferred(lambda: self.copy_selected(target)):
            return
        rows = self._messages.selected_rows()
        if not rows:
            return
        number = self._undoing.new_action()
        self.act_many(
            MESSAGE_COPY_MANY, lambda account, folder, messages: ManyTargetParameters(account, folder, target, messages, undo=number), self._state.targets(rows),
            f"Copied {counted(len(rows), 'message')} to {target}", "Could not copy", take_away=False,
        )

    def fill_move_menu(self) -> None:
        self.move_menu.clear()
        view = self._state.view
        if view.account is None:
            return
        scheduled = self._state.in_scheduled_view()
        for info in self._state.folders.get(view.account, []):
            path = info.path
            if path == view.folder or path in LOCAL_PLACES or (scheduled and info.role not in SCHEDULED_TARGET_ROLES):
                continue  # a scheduled message goes back to Drafts or to Trash, nowhere else
            self.move_menu.addAction(path).triggered.connect(lambda checked=False, target=path: self.move_selected(target))

    def move_selected(self, target: str) -> None:
        if self._messages.deferred(lambda: self.move_selected(target)):
            return
        rows = self._messages.selected_rows()
        if not rows or self._state.view.account is None:
            return
        self.move_many(target, self._state.targets(rows))

    def messages_dropped(self, account: str, target: str, messages: list[tuple[str, str]]) -> None:
        """Messages were dragged to a folder of the folder list."""
        self.move_many(target, [(account, folder, message_id) for folder, message_id in messages])

    def move_many(self, target: str, targets: list[tuple[str, str, str]]) -> None:
        """A move of messages (by the menu or a drag): a scheduled message moved to Drafts goes back to being a draft (D-178), one by one;
        the others in one command per folder (R-08)."""
        unschedule = [item for item in targets if self._unschedules(item[0], item[1], target)]
        moves = [item for item in targets if item not in unschedule]
        finished = f"Moved {counted(len(targets), 'message')} to {target}"
        if unschedule:
            self._messages.take_away([message_id for _, _, message_id in unschedule])
            done, failed = self._answers(len(unschedule), finished, "Could not move")
            for account, folder, message_id in unschedule:
                on_success, on_error = self._each(message_id, done, failed)
                self._undoing.started()
                self._backend.ask(MAIL_UNSCHEDULE, MessageParameters(account, folder, message_id), on_success, on_error, owner=self, in_order=True)
        if moves:
            number = self._undoing.new_action()  # a scheduled message moved to Drafts is not undone (its schedule is gone from the message)
            self.act_many(MESSAGE_MOVE_MANY, lambda account, folder, messages: ManyTargetParameters(account, folder, target, messages, undo=number), moves, finished, "Could not move")

    def _unschedules(self, account: str, folder: str, target: str) -> bool:
        """Whether a move is the engine's unschedule: a scheduled message moved to Drafts goes back to being a draft (its schedule
        removed); everything else is a plain move (D-178)."""
        return folder == SCHEDULED_FOLDER_PATH and self._state.folder_role_of(account, target) == "drafts"

    # --- the whole folder -------------------------------------------------------------------------------------------------

    def mark_folder_read(self) -> None:
        """Mark every message of the folder being shown as read."""
        view = self._state.view
        if view.account is None or view.folder is None:
            return
        folder = view.folder

        def done(result: MarkReadResult) -> None:
            changed = result.changed
            self._status.finished(f"Marked {counted(changed, 'message')} in {folder} as read" if changed else f"Nothing to mark in {folder}")
            self._undoing.ended()

        def failed(error: ChronoMailError) -> None:
            self._status.report("Could not mark the messages", error)
            self._undoing.ended()

        self._undoing.started()
        self._backend.ask(FOLDER_MARK_READ, FolderActionParameters(view.account, folder, undo=self._undoing.new_action()), done, failed, owner=self)

    def empty_folder(self) -> None:
        """Delete everything in Trash or Junk for good, after asking."""
        view = self._state.view
        if view.account is None or view.folder is None or view.folder_role not in ("trash", "junk"):
            return
        folder = view.folder
        answer = QMessageBox.question(self._parent, "Empty folder", f"Delete all messages in {folder} for good? This cannot be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result: EmptyResult) -> None:
            self._status.finished(f"Deleted {counted(result.removed, 'message')} from {folder}")

        self._backend.ask(FOLDER_EMPTY, FolderActionParameters(view.account, folder), done, lambda error: self._status.report("Could not empty the folder", error), owner=self)

    # --- many messages at once ----------------------------------------------------------------------------------------------

    def act_many[P](
        self, command: TypedCommand[P, ManyResult], parameters: Callable[[str, str, tuple[str, ...]], P], targets: list[tuple[str, str, str]], finished: str,
        title: str, take_away: bool = True, each: Callable[[ManyResult], None] | None = None,
    ) -> None:
        """An action on many messages (R-08): greyed at the key press (take_away), one command per account and folder, each message's row
        follows its fact; when every answer is in, one line: the summary, or how many could not be done and why (one reason, not one line each)."""
        groups: dict[tuple[str, str], list[str]] = {}
        for account, folder, message_id in targets:
            groups.setdefault((account, folder), []).append(message_id)
        if not groups:
            return
        if take_away:
            self._messages.take_away([message_id for _, _, message_id in targets])
        remaining, failures, total = len(groups), list[NotDone](), len(targets)
        model = self._messages.model

        def finish() -> None:
            nonlocal remaining
            remaining -= 1
            if remaining:
                return
            if failures:
                reason = failures[0].reason if all(item.reason == failures[0].reason for item in failures) else f"{failures[0].reason} (and other reasons)"
                self._status.report(title, ChronoMailError(f"{len(failures):,} of {counted(total, 'message')}: {reason}"))
            if finished:
                self._status.finished(finished, keep_error=bool(failures))

        for (account, folder), messages in groups.items():

            def answered(result: ManyResult, messages: list[str] = messages) -> None:
                for message_id in messages:
                    model.clear_pending(message_id)  # done ones left with their fact; failed ones are back
                failures.extend(result.failed)
                if result.gone or any(item.missing for item in result.failed):
                    self._status.warn_missing()  # a file gone here: one warning for the action, with the advice to repair the index (D-109)
                if each is not None:
                    each(result)
                finish()
                self._undoing.ended()

            def refused(error: ChronoMailError, messages: list[str] = messages) -> None:
                for message_id in messages:
                    model.clear_pending(message_id)
                failures.extend(NotDone(message_id, str(error)) for message_id in messages)
                if isinstance(error, MessageNotFoundError):
                    self._status.warn_missing()
                finish()
                self._undoing.ended()

            self._undoing.started()
            self._backend.ask(command, parameters(account, folder, tuple(messages)), answered, refused, owner=self, in_order=True)

    def _answers(self, count: int, finished: str, title: str) -> tuple[Callable[[object], None], Callable[[ChronoMailError], None]]:
        """The answers of an action on several messages, one per message: when all are in, failed ones too, the summary only when
        none failed, else the error stays in the red line."""
        remaining, failures = count, 0

        def finish() -> None:
            if remaining == 0:
                self._status.finished(finished, keep_error=failures > 0)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            finish()

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining, failures
            remaining -= 1
            failures += 1
            self._status.report(title, error)
            finish()

        return done, failed

    def _each(self, message_id: str, done: Callable[[object], None], failed: Callable[[ChronoMailError], None]) -> tuple[Callable[[object], None], Callable[[ChronoMailError], None]]:
        """The answers of one message's action: its action ended, so it is no longer greyed (when it went out of the view
        its fact took the row out before this answer came)."""

        def ended(result: object) -> None:
            self._messages.model.clear_pending(message_id)
            done(result)
            self._undoing.ended()

        def refused(error: ChronoMailError) -> None:
            self._messages.model.clear_pending(message_id)
            failed(error)
            self._undoing.ended()

        return ended, refused
