from collections.abc import Iterable, Sequence
from typing import Protocol

from chrono_mail.config import LOCAL_FOLDER_KINDS
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.engine.commands_folders import FolderInfo
from chrono_mail.engine.commands_pim import TagInfo
from chrono_mail.engine.commands_sidebar import EMPTY_SIDEBAR, SidebarInfo
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH, SEND_AT_FOLDERS, SNOOZED_FOLDER_PATH
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.models import FAVORITES, LOCAL_PLACES, USED_ONLY, favorite_in_use, local_folder_kind

# --- Version ------------------------------------------------------------
__version__ = "mailstate.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: all_archived stops at the first message that is not archived
# v2.4.329: account_of, targets and archived take any message by its place (a row, or a selected message not loaded yet, W12)
# v2.2.314: in_timed: the Outbox too (a delayed send shows when it goes, B-21); outbox_count: the messages waiting in the Outbox, each place where mail is kept counted once (the warning on Quit, B-20)
# v2.0.306: all_archived: whether every selected message is in an archive folder (Archive or Unarchive follows the messages, D-101)
# v1.9.274: R-19 round 4: the accounts, tags and rows are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.264: R-17: what the parts of the mail window share: the accounts, their folders with the engine's numbers, the sidebar, the
#           tags and the current view, with the questions every part asks of them (they were asked again in many places)



class Placed(Protocol):
    """A message by its place: a row of the list, or a selected message whose row has not come into view (W12)."""

    @property
    def account(self) -> str: ...

    @property
    def folder(self) -> str: ...

    @property
    def message_id(self) -> str: ...

class MailState:
    """What the parts of the mail window know in common. Each part changes only what it is the owner of; the others read it."""

    def __init__(self) -> None:
        self.accounts: list[AccountSummary] = []  # the accounts with mail (and Local Folders): everything about mail works on these
        self.all_accounts: list[AccountSummary] = []  # also the accounts of calendars, contacts and tasks only (D-185)
        self.folders: dict[str, list[FolderInfo]] = {}  # every mailbox's folders with the engine's numbers (R-10): the folder list owns it
        self.sidebar: SidebarInfo = EMPTY_SIDEBAR  # pinned folders and groups (sidebar.show): the folder list owns it
        self.tags: tuple[TagInfo, ...] = ()  # the answer of tag.list, in the person's order: the tags part owns it
        self.view: CurrentView = CurrentView()  # what the message list shows (R-14): the message list owns it

    # --- accounts -----------------------------------------------------------------------------------------

    def set_accounts(self, every: list[AccountSummary]) -> None:
        self.all_accounts = every
        self.accounts = [account for account in every if account.kind != "dav"]

    def mail_accounts(self) -> list[AccountSummary]:
        """The accounts that have servers: Local Folders is a place to keep mail, not an account to check or send from."""
        return [account for account in self.accounts if account.kind != "local"]

    def mail_account_ids(self) -> list[str]:
        return [account.account_id for account in self.mail_accounts()]

    def accounts_with_mail(self) -> list[str]:
        """The places where mail is kept (accounts that share Local Folders count once: Local Folders holds their mail and answers
        for them), for their folders and numbers and for searching the mail of a contact."""
        return [account.account_id for account in self.accounts if account.storage != "local"]

    def account(self, account_id: str) -> AccountSummary | None:
        return next((account for account in self.accounts if account.account_id == account_id), None)

    def account_name(self, account_id: str) -> str:
        """How the account is named in the status line and the Operations window: its name, else its address, else its id."""
        account = self.account(account_id)
        return account.shown_name or account.email_address if account is not None else account_id

    def local_id(self) -> str:
        return next((account.account_id for account in self.accounts if account.kind == "local"), "")

    def is_local(self, account_id: str) -> bool:
        return any(item.account_id == account_id and item.kind == "local" for item in self.accounts)

    def tree_accounts(self) -> list[tuple[str, str]]:
        """The accounts with a folder tree of their own (IMAP, and POP3 kept in its own folders): (account id, name)."""
        return [
            (account.account_id, account.label)
            for account in self.accounts
            if account.kind != "local" and account.storage != "local"
        ]

    def calendar_accounts(self, only: set[str] | None = None) -> list[str]:
        """The accounts that have a calendar and contacts server (all, or only these)."""
        return [
            account.account_id for account in self.all_accounts
            if account.dav_url and (only is None or account.account_id in only)
        ]

    # --- folders --------------------------------------------------------------------------------------------

    def folder_role_of(self, account: str, path: str) -> str:
        return next((info.role for info in self.folders.get(account, []) if info.path == path), "other")

    def rule_folders(self) -> list[str]:
        return sorted({info.path for infos in self.folders.values() for info in infos})

    def favorites_in_use(self) -> tuple[tuple[str, str], ...]:
        """The standard favorites for Settings > Sidebar: Snoozed and Archive only once a mailbox has that folder."""
        return tuple((view, label) for view, label, _ in FAVORITES if view not in USED_ONLY or favorite_in_use(view, self.accounts_with_mail(), self.folders))

    def local_standard_folders(self) -> list[tuple[str, str]]:
        """The standard folders Local Folders has, for Settings > Sidebar: (kind, folder), in the order of the folder list."""
        infos = self.folders.get(self.local_id(), [])
        found = [(local_folder_kind(info.path, info.role), info.path) for info in infos]
        return sorted(((kind, path) for kind, path in found if kind), key=lambda pair: LOCAL_FOLDER_KINDS.index(pair[0]))

    def total_unread(self) -> int:
        """The unread messages of every Inbox, each place where mail is kept counted once (the tray icon's number)."""
        return sum(info.unread for place in self.accounts_with_mail() for info in self.folders.get(place, []) if info.role == "inbox")

    def outbox_count(self) -> int:
        """The messages that wait in the Outbox (failed, delayed or counting down), each place where mail is kept counted once."""
        return sum(info.messages for place in self.accounts_with_mail() for info in self.folders.get(place, []) if info.path == "Outbox")

    def group_accounts(self, group_id: str) -> tuple[str, ...]:
        return next((group.accounts for group in self.sidebar.groups if group.group_id == group_id), ())

    # --- the view ------------------------------------------------------------------------------------------

    def account_of(self, row: Placed) -> str:
        """The account a row of the list belongs to: rows of the combined views say so themselves."""
        return row.account or self.view.account or ""

    def targets(self, rows: Sequence[Placed]) -> list[tuple[str, str, str]]:
        """(account, folder, message) of each row (a Favorites view mixes accounts)."""
        account = self.view.account or ""
        return [(self.account_of(row) or account, row.folder, row.message_id) for row in rows]

    def in_snoozed(self) -> bool:
        """The Snoozed folder of an account is shown (its messages know when they come back)."""
        view = self.view
        return view.combined is None and view.kind == EntryKind.FOLDER and view.folder == SNOOZED_FOLDER_PATH

    def in_timed(self) -> bool:
        """The Snoozed or the Scheduled folder or the Outbox of an account is shown: its messages leave at a time (it comes back, it is sent)."""
        view = self.view
        return view.combined is None and view.kind == EntryKind.FOLDER and view.folder in (SNOOZED_FOLDER_PATH, *SEND_AT_FOLDERS)

    def in_drafts(self) -> bool:
        view = self.view
        return view.folder_role == "drafts" or view.folder in LOCAL_PLACES or view.combined == "scheduled"

    def in_scheduled_view(self) -> bool:
        """Local Folders > Scheduled (or an account's), or Favorites > Scheduled."""
        view = self.view
        return view.combined == "scheduled" or (view.combined is None and view.kind == EntryKind.FOLDER and view.folder == SCHEDULED_FOLDER_PATH)

    def archived(self, row: Placed) -> bool:
        """The message is in an archive folder of its account (by the folder's role, not its name)."""
        return self.folder_role_of(self.account_of(row), row.folder) == "archive"

    def all_archived(self, rows: Iterable[Placed]) -> bool:
        """Every one of these messages is in an archive folder: Archive is Unarchive for them, wherever they are shown (D-101). It stops at
        the first that is not (16,000 selected were all read again after every change of the list)."""
        found = False
        for row in rows:
            if not self.archived(row):
                return False
            found = True
        return found

    def can_make_folder(self) -> bool:
        """New folder needs one account: an account row, one of its folders (also its Unread and Flagged, a pinned folder)."""
        return bool(self.view.account) and self.view.kind in (EntryKind.ACCOUNT, EntryKind.FOLDER, EntryKind.UNREAD, EntryKind.FLAGGED)
