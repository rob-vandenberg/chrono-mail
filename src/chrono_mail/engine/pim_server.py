"""Every change of an appointment, task or contact goes through here (D-193, D-194): in a collection of a server it is done on the server first and
here when the server took it; offline it is done here and sent at the next synchronisation; a lasting "no" of the server changes nothing and is
told. A collection of this computer only is the truth itself: written here at once."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager

from chrono_mail.core import CollectionInfo
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_dav import _client, _lock_for, _state_path
from chrono_mail.engine.pimaccess import account_name
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import PimStore
from chrono_mail.sync.davitems import CONFLICT, WAITING, ItemGateway, Outcome, ServerRefused, on_server
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "pim_server.py 2.4.333"

# --- Version history ----------------------------------------------------
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

    def put(self, info: CollectionInfo, text: str, item: str | None = None) -> str:
        """Save an item (new when item is None); returns its id here."""
        gateway = self._server(info)
        if gateway is None:
            return self.store.put(info.collection_id, text, item)
        try:
            return self._count(gateway.save(info, text, item))
        except ServerRefused as error:
            raise UsageError(f"The server did not take the change ({error}); nothing was changed") from None

    def delete(self, info: CollectionInfo, item: str) -> None:
        gateway = self._server(info)
        if gateway is None:
            self.store.delete(info.collection_id, item)
            return
        try:
            self._count(gateway.delete(info, item))
        except ServerRefused as error:
            raise UsageError(f"The server did not take the delete ({error}); nothing was changed") from None

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
            self.engine.notice(f"The server of {account_name(self.engine, self.account)} could not be reached: {self.waiting} change(s) go there at the next synchronisation")
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
