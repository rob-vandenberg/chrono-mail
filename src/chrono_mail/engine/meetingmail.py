from dataclasses import dataclass, replace
from datetime import UTC, datetime

from chrono_mail.config import AccountSettings
from chrono_mail.core import ChronoMailError, CollectionInfo
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.pim_server import ItemWriter
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import Attendee, Moment
from chrono_mail.pim.itip import BEFORE_BARE, BEFORE_LOUD, BEFORE_SILENT, attendee_addresses, ready_to_delete, request_of, told_silent, with_silence
from chrono_mail.sync.davsync import DavState

# --- Version ------------------------------------------------------------
__version__ = "meetingmail.py 4.4.557"

# --- Version history ----------------------------------------------------
# v4.4.557: news_of_status: who tells the attendees of Cancel and Restore (D-310)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.518: Who mails about a meeting, moved here from commands_invitations.py, and the mail about your own meeting (D-310)


def _server_answers(engine: Engine, account: str, info: CollectionInfo, address: str) -> bool:
    """Whether the calendar's server mails the answer: its account says so (D-310), the calendar is on the server, and the answering address is
    one the server knows as this user (a server answers only for its own user); a server that does not say: the account's own."""
    try:
        settings = engine.config().find(account)
    except ChronoMailError:
        return False
    own = settings.account.email_address.strip().lower()
    known = address in settings.dav_addresses if settings.dav_addresses else not own or own == address  # a calendar-only account has no address
    return settings.server_answers and info.server_value("href") != "" and known


@dataclass(frozen=True, slots=True)
class Mailing:
    """Who mails about one change of a calendar copy (D-310): the organizer gets one mail, or none only when the person chose that."""

    server: bool = False  # the calendar's server mails it
    chrono: bool = False  # Chrono Mail makes the mail
    told: bool = False  # the calendar's server listens: the copy tells it whether to mail (SCHEDULE-AGENT)
    bare: bool = False  # the server mails whatever it is told, and here for the wrong person: a copy goes only after its organizer was taken off


def settings_of(engine: Engine, account: str) -> AccountSettings | None:
    try:
        return engine.config().find(account) if account else None
    except ChronoMailError:
        return None


def _reached_server(engine: Engine, account: str, info: CollectionInfo, item: str) -> bool:
    """Whether the server has this item: one made while offline may be waiting here still, and then the server knows nothing to answer for."""
    state = DavState(engine.directories.pim_directory(account) / "dav-state.json", engine.durable)
    return any(link.item_id == item for link in state.get(info.collection_id).links.values())


def who_mails(engine: Engine, calendar: tuple[str, CollectionInfo] | None, account: str, address: str, gone: str = "", answers: bool = True) -> Mailing:
    """`calendar` holds the copy, `account` is the one you answer from: the box of the account that got the invite says whether an answer goes at
    all, the calendar's server how it goes. answers False: news of your own meeting, which that box does not cover. gone: the item that is about
    to be deleted: only a server that has it can mail about it."""
    settings = (settings_of(engine, calendar[0]) if calendar is not None else None) or settings_of(engine, account)
    if settings is None:
        return Mailing(chrono=True)
    on_server = calendar is not None and bool(settings.dav_url) and calendar[1].server_value("href") != ""
    reached = calendar is not None and on_server and (not gone or _reached_server(engine, calendar[0], calendar[1], gone))
    server = calendar is not None and reached and _server_answers(engine, calendar[0], calendar[1], address)
    listens = reached and settings.dav_auto_schedule and not settings.dav_always_answers
    box = settings_of(engine, account) or settings
    silenced = answers and box.answers_by == "none" and not settings.dav_always_answers
    server = server and not silenced
    return Mailing(server, not server and not silenced, listens, bool(gone) and reached and settings.dav_always_answers and not server)


def copy_for(text: str, mails: Mailing) -> str:
    """The copy as the server should get it: told to stay silent when it should, and never so when it mails."""
    return (with_silence(text, not mails.server) or text) if mails.told else text


def delete_copy(writer: ItemWriter, info: CollectionInfo, item: str, text: str, mails: Mailing) -> None:
    """Delete a copy. A server mails about that from what it holds, so what it holds says first whether it should."""
    before = ""
    if mails.told and told_silent(text) == mails.server:
        before = BEFORE_LOUD if mails.server else BEFORE_SILENT
    elif mails.bare:
        before = BEFORE_BARE
    if before:
        writer.put(info, ready_to_delete(text, before) or text, item)
    try:
        writer.delete(info, item, before)
    except UsageError:
        if before:
            writer.restore(info, text, item)  # refused: the meeting stays as it was, as the message says
        raise


def quiet(mails: Mailing) -> Mailing:
    """Nobody is told of this change: the server neither, as far as it can be silenced."""
    return replace(mails, server=False, chrono=False, bare=mails.bare or (mails.server and not mails.told))


def news_of_status(mails: Mailing, tell: bool) -> Mailing:
    """Who tells the attendees of your meeting that it is Cancelled or Restored. A server that mails whatever it is told says nothing of a status (measured on
    Kerio, D-310), so Chrono Mail mails; a server that listens mails as it is told."""
    if not tell:
        return quiet(mails)
    return replace(mails, server=False, chrono=True) if mails.server and not mails.told else mails


def your_addresses_lower(engine: Engine) -> frozenset[str]:
    return frozenset(settings.account.email_address.strip().lower() for settings in engine.config().accounts if settings.account.email_address)


def organizing_account(engine: Engine, account: str) -> AccountSettings | None:
    """The account a meeting in a calendar of `account` is organized from: that account, or for a calendar with no address of its own (Local) the
    first account that has one."""
    own = settings_of(engine, account)
    if own is not None and own.account.email_address:
        return own
    return next((settings for settings in engine.config().accounts if settings.account.email_address), None)


def organizer_from(settings: AccountSettings) -> Attendee:
    return Attendee(settings.account.email_address.strip(), settings.account.display_name)


@dataclass(frozen=True, slots=True)
class MeetingDraft:
    """A mail about your meeting, ready to send."""

    account: str = ""
    folder: str = ""
    message: str = ""


def meeting_mail(engine: Engine, settings: AccountSettings, text: str, title: str, method: str, word: str, body: str, dates: tuple[Moment, ...] = (), only: frozenset[str] | None = None) -> MeetingDraft:
    """The mail to the attendees of your meeting, or nothing when nobody can be written to. Sent only when the person presses Send, as every calendar
    mail of Chrono Mail (RFC 6047). only: of the attendees, just these addresses (in lower case)."""
    calendar = request_of(text, datetime.now(UTC), method, dates)
    yours = your_addresses_lower(engine)
    to = tuple(address for address in attendee_addresses(calendar or "") if address.lower() not in yours and (only is None or address.lower() in only))
    if calendar is None or not to:
        return MeetingDraft()
    saved = draft_save(engine, DraftSaveParameters(account=settings.account.account_id, to=to, subject=f"{word}: {title or '(no title)'}", body=body, kind="new", calendar=calendar))
    return MeetingDraft(settings.account.account_id, saved.folder, saved.message_id)
