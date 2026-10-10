from dataclasses import dataclass, field, replace
from typing import Literal

from chrono_mail.core import FolderRole
from chrono_mail.engine.commands_mail import MessageRow, _rows
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import SearchFilter

# --- Version ------------------------------------------------------------
__version__ = "commands_combined.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Initial combined views over the mailboxes of all accounts (Favorites)

VIEWS: tuple[str, ...] = ("inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash")
_ROLE_OF_VIEW: dict[str, FolderRole] = {"inboxes": FolderRole.INBOX, "sent": FolderRole.SENT, "drafts": FolderRole.DRAFTS, "junk": FolderRole.JUNK, "trash": FolderRole.TRASH}


@dataclass(frozen=True, slots=True)
class CombinedParameters:
    view: Literal["inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash"] = field(metadata=option("inboxes, unread, flagged, outbox, sent, drafts, junk or trash"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("Only the mailboxes of these accounts (default: all)"))
    query: str = field(default="", metadata=option("Words to find in these messages (as in message.search)"))
    offset: int = field(default=0, metadata=option("How many results to skip"))
    limit: int = field(default=50, metadata=option("How many results to show"))


def _places(engine: Engine, accounts: tuple[str, ...]) -> list[str]:
    """One account id for every place where mail is kept: accounts that share Local Folders count once."""
    config = engine.config()
    wanted = {engine.resolve_account(name, config).account.account_id for name in accounts}
    places: list[str] = []
    for place, members in engine.storage_groups(config).items():
        ids = {item.account.account_id for item in members} | {place}
        if wanted and not ids & wanted:
            continue
        places.append(place if place in ids else members[0].account.account_id)
    return places


def message_combined(engine: Engine, parameters: CombinedParameters) -> tuple[MessageRow, ...]:
    """The messages of one kind of folder in every mailbox, newest first: all inboxes, all unread, all flagged and so on."""
    view = parameters.view
    want = parameters.offset + parameters.limit
    filters = SearchFilter(unread=True if view == "unread" else None, flagged=True if view == "flagged" else None)
    found: list[MessageRow] = []
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox:
            for folder in mailbox.folders():
                if view in _ROLE_OF_VIEW:
                    chosen = folder.role is _ROLE_OF_VIEW[view]
                elif view == "outbox":
                    chosen = folder.path == OUTBOX
                else:
                    chosen = folder.role not in (FolderRole.TRASH, FolderRole.JUNK) and folder.path != OUTBOX
                if chosen:
                    found.extend(replace(row, account=account) for row in _rows(mailbox, mailbox.index.search(parameters.query, folder.path, filters, 0, want)))
    found.sort(key=lambda row: row.date.timestamp() if row.date else 0.0, reverse=True)
    return tuple(found[parameters.offset:want])


def register(registry: CommandRegistry) -> None:
    registry.register("message.combined", "List the messages of the same kind of folder in all mailboxes", CombinedParameters, message_combined)
