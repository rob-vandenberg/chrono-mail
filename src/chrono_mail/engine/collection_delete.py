"""Deleting a calendar, task list or address book (R-28, D-197). One of this computer only is deleted here. One of a server is deleted on the server
first and here when the server confirmed it, so every program that synchronises with that server removes it too; offline it is deleted here at once
and on the server at the next synchronisation; a server that changed it since it was last synchronised gets nothing deleted."""

from chrono_mail.core import AuthenticationError, ChronoMailError, CollectionInfo, ProtocolError
from chrono_mail.engine.collection_looks import KIND_WORDS, key_of
from chrono_mail.engine.commands_dav import _client, _lock_for, _state_path, keep_looks
from chrono_mail.engine.pimaccess import account_name
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavError, canonical_href, passing_failure
from chrono_mail.sync.davdeletes import changed_on_server, remember
from chrono_mail.sync.davsync import DavState
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "collection_delete.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.1.235: Initial: a server's collection deleted on the server first; offline here and remembered; changed on the server: nothing deleted (R-28)


def together(store: PimStore, info: CollectionInfo) -> list[CollectionInfo]:
    """The collections here at the same place on the server (a calendar there can hold events and tasks: two collections here). Deleting it on
    the server takes them all."""
    href = canonical_href(info.server_value("href"))
    if not href:
        return [info]
    return [info] + [other for other in store.collections() if other.collection_id != info.collection_id and canonical_href(other.server_value("href")) == href]


def _gone_here(engine: Engine, account_id: str, store: PimStore, group: list[CollectionInfo]) -> None:
    """Take the collections away here (to the deleted items, 30 days) with what was synchronised and what the account kept of their look."""
    state = DavState(_state_path(engine, account_id), engine.durable)
    for info in group:
        store.delete_collection(info.collection_id)
        state.collections.pop(info.collection_id, None)
    state.save()
    keys = {key_of(info) for info in group}
    kept = {look.key: look for look in engine.config().find(account_id).collection_looks if look.key in keys}
    keep_looks(engine, account_id, kept, {key: None for key in kept})


def delete_collection(engine: Engine, account_id: str, store: PimStore, info: CollectionInfo) -> None:
    """Delete a collection. One of a server: on the server first (after the person's question), here when the server confirmed it."""
    href = info.server_value("href")
    settings = engine.config().find(account_id)
    if not href or not settings.dav_url:
        store.delete_collection(info.collection_id)
        return
    word = KIND_WORDS[info.kind]
    if info.read_only:
        raise UsageError(f"This {word} is read only on its server and cannot be deleted there; untick it in the account to stop keeping it here")
    with _lock_for(account_id):  # one at a time with the synchronisation of this account
        group = together(store, info)
        state = DavState(_state_path(engine, account_id), durable=False)
        etags = {address: link.etag for member in group for address, link in state.get(member.collection_id).links.items()}
        try:
            with _client(engine, settings) as client:
                try:
                    changed = changed_on_server(client, href, info.kind.value, etags)
                except DavError as error:
                    if error.status not in (404, 410):
                        raise
                    changed = -1  # the server confirms it is not there any more: nothing to delete there
                if changed > 0:
                    raise UsageError(
                        f"“{info.name}” was changed on the server since it was last synchronised ({counted(changed, 'item')}): nothing was deleted. "
                        "Refresh to bring the changes here, then delete it again if you still want to"
                    )
                if changed == 0:
                    client.delete(href)  # a 404 meanwhile counts as done
        except UsageError:
            raise
        except (ProtocolError, AuthenticationError) as error:
            if not passing_failure(error):
                raise UsageError(f"The server did not delete the {word} “{info.name}” ({error}); nothing was changed here") from None
            remember(store, href, tuple(member.collection_id for member in group), info.kind.value, info.name, etags)
            _gone_here(engine, account_id, store, group)
            engine.notice(f"{info.name}: the server of {account_name(engine, account_id)} could not be reached ({error}); it is deleted there at the next synchronisation")
            return
        except ChronoMailError as error:  # no password stored, for one: deleted there once it can be
            remember(store, href, tuple(member.collection_id for member in group), info.kind.value, info.name, etags)
            _gone_here(engine, account_id, store, group)
            engine.notice(f"{info.name}: {error}; it is deleted on the server at the next synchronisation")
            return
        _gone_here(engine, account_id, store, group)
