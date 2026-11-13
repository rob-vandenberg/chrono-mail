"""Every change of an appointment, task or contact goes through here (D-193, D-194): in a collection of a server it is done on the server first and
here when the server took it; offline it is done here and sent at the next synchronisation; a lasting "no" of the server changes nothing and is
told. A collection of this computer only is the truth itself: written here at once."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager

from chrono_mail.core import CollectionInfo
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_dav import _client, _lock_for, _state_path
from chrono_mail.engine.google_colours import sync_colours
from chrono_mail.engine.pimaccess import account_name
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pim.itip import with_organizer
from chrono_mail.pimstore import PimStore
from chrono_mail.sync.davitems import CONFLICT, DONE, WAITING, ItemGateway, Outcome, ServerRefused, on_server
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "pim_server.py 4.4.598"

# --- Version history ----------------------------------------------------
# v4.4.598: An event the server of a Google calendar took gets its colour tag sent to Google right after it (D-337)
# v4.4.555: put_organizer_last: a copy saved without its organizer first, so a server that mails whatever it is told stays silent; put: mailed (D-310)
# v4.4.515: a delete can say what the server's copy needs first; restore: the way back after a refused delete, refused as well: a sync conflict, told calmly (D-310)
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.1.225: Initial: items of a server's collections saved, deleted and moved on the server first (R-23, D-194)


class ItemWriter:
    """The writes of one command to the items of one account. For a server's collections the connection and the account's synchronisation lock are
    taken at the first write and held until the command ends, so a bulk job (an import, a tag renamed) asks for them once."""

    def __init__(self, engine: Engine, account: str, store: PimStore, stack: ExitStack) -> None:
        self.engine: Engine = engine
        self.account: str = account
        self.store: PimStore = store
        self._stack: ExitStack = stack
        self._gateway: ItemGateway | None = None
        self.waiting: int = 0
        self.conflicts: int = 0

    def _server(self, info: CollectionInfo) -> ItemGateway | None:
        if not on_server(info):
            return None
        settings = self.engine.config().find(self.account)
        if not settings.dav_url:
            return None  # the server was taken away: what is here is all there is
        if self._gateway is None:
            client = self._stack.enter_context(_client(self.engine, settings))
            self._stack.enter_context(_lock_for(self.account))
            self._gateway = ItemGateway(client, self.store, _state_path(self.engine, self.account), self.engine.durable)
        return self._gateway

    def _count(self, outcome: Outcome) -> str:
        if outcome.status == WAITING:
            self.waiting += 1
        elif outcome.status == CONFLICT:
            self.conflicts += 1
        return outcome.item

    def put(self, info: CollectionInfo, text: str, item: str | None = None, mailed: bool = False) -> str:
        """Save an item (new when item is None); returns its id here. A copy that waits to go silently goes on doing so, unless mailed: the server is to mail this write (D-310)."""
        gateway = self._server(info)
        if gateway is None:
            return self.store.put(info.collection_id, text, item)
        if item is not None and not mailed and gateway.organizer_waits(info, item):
            return self.put_organizer_last(info, text, item)
        try:
            outcome = gateway.save(info, text, item)
        except ServerRefused as error:
            raise UsageError(f"The server did not take the change ({error}); nothing was changed") from None
        if mailed:
            gateway.organizer_now(info, outcome.item)
        saved = self._count(outcome)
        self._colour(info, outcome)
        return saved

    def _colour(self, info: CollectionInfo, outcome: Outcome) -> None:
        """An event Google's server took: its colour tag goes to Google right after it, as the synchronisation would send it (D-337). What
        fails waits for the next synchronisation; the save itself is done."""
        if outcome.status != DONE or not outcome.item:
            return
        settings = self.engine.config().find(self.account)
        if settings.google:
            sync_colours(self.engine, settings, self.store, (outcome.item,), info.collection_id)

    def put_organizer_last(self, info: CollectionInfo, text: str, item: str | None = None) -> str:
        """Save an item a server may mail nothing about, whatever it is told: first without its organizer, then with it. Measured on Kerio: a first save with an
        organizer mails, these two do not (D-310). Offline the synchronisation sends both, in that order."""
        gateway = self._server(info)
        bare = with_organizer(text, "")
        if gateway is None or bare is None:
            return self.put(info, text, item, mailed=True)
        try:
            outcome = gateway.save(info, bare, item)
        except ServerRefused as error:
            raise UsageError(f"The server did not take the change ({error}); nothing was changed") from None
        saved = self._count(outcome)
        if outcome.status == WAITING:
            gateway.organizer_later(info, saved)
            return self.store.put(info.collection_id, text, saved)
        self.restore(info, text, saved)
        gateway.organizer_now(info, saved)
        return saved

    def delete(self, info: CollectionInfo, item: str, before: str = "") -> None:
        gateway = self._server(info)
        if gateway is None:
            self.store.delete(info.collection_id, item)
            return
        try:
            self._count(gateway.delete(info, item, before))
        except ServerRefused as error:
            raise UsageError(f"The server did not take the delete ({error}); nothing was changed") from None

    def restore(self, info: CollectionInfo, text: str, item: str) -> None:
        """An item back to this text after the server refused its delete. The server refuses that too: the text stays here and a sync conflict
        waits until the person fixed the server and settled it (D-310)."""
        gateway = self._server(info)
        if gateway is None:
            self.store.put(info.collection_id, text, item)
            return
        try:
            outcome = gateway.save(info, text, item)
            self._count(outcome)
            self._colour(info, outcome)
        except ServerRefused as error:
            gateway.hold(info, item, text, str(error))
            self.engine.report(Change("conflicts_changed", self.account))
            raise UsageError(f"The server of {account_name(self.engine, self.account)} did not take this change. Your meeting is kept here as it was; "
                             "when the server works again, you can settle it in Settings > Sync conflicts") from None

    def move(self, source: CollectionInfo, item: str, target: CollectionInfo) -> str:
        gateway = self._server(source)
        if gateway is None or not on_server(target):
            return self.store.move(source.collection_id, item, target.collection_id)
        try:
            return self._count(gateway.move(source, item, target))
        except ServerRefused as error:
            raise UsageError(f"The server did not take the move ({error}); nothing was changed") from None

    def tell(self) -> None:
        """What the person should know after the command: changes waiting for the server, conflicts (told once each, as facts too)."""
        if self.waiting:
            self.engine.notice(f"The server of {account_name(self.engine, self.account)} could not be reached: {counted(self.waiting, 'change')} waiting, sent at the next synchronisation")
            self.engine.report(Change("waiting_changed", self.account))
        if self.conflicts:
            self.engine.notice(f"{counted(self.conflicts, 'item')} of {account_name(self.engine, self.account)} were changed on the server meanwhile: see Settings > Sync conflicts")
            self.engine.report(Change("conflicts_changed", self.account))


@contextmanager
def item_writer(engine: Engine, account: str, store: PimStore) -> Generator[ItemWriter]:
    """An ItemWriter for one command; what came of its writes is told when it ends without an error."""
    with ExitStack() as stack:
        writer = ItemWriter(engine, account, store, stack)
        yield writer
    writer.tell()
