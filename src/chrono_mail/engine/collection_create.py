"""Making a calendar, task list or address book in the account the person chose (R-29, D-198). In the local ones: here, the truth itself. In an
account with a calendar server: on the server first and here when the server made it; offline here at once and on the server at the next
synchronisation; a lasting refusal makes nothing."""

from chrono_mail.core import LOCAL_ACCOUNT_ID, LOOK_KEPT, AuthenticationError, ChronoMailError, CollectionInfo, CollectionKind, CollectionLook, ProtocolError
from chrono_mail.engine.collection_looks import KIND_WORDS
from chrono_mail.engine.commands_dav import _client, _lock_for, keep_looks
from chrono_mail.engine.pimaccess import account_name
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import canonical_colour, folder_key, passing_failure
from chrono_mail.sync.davcreates import make_on_server, remember, tie_for

# --- Version ------------------------------------------------------------
__version__ = "collection_create.py 1.1.236"

# --- Version history ----------------------------------------------------
# v1.1.236: Initial: a collection made in a server's account on the server first; offline here and made there later (R-29)


def create_collection(engine: Engine, account_id: str, store: PimStore, kind: CollectionKind, name: str, colour: str) -> CollectionInfo:
    """Make a collection in this account: here for the local ones; on the server first for an account with a calendar server."""
    if account_id == LOCAL_ACCOUNT_ID:
        return store.create_collection(kind, name, colour)
    settings = engine.config().find(account_id)
    if not settings.dav_url:
        raise UsageError(f"{account_name(engine, account_id)} has no calendar and contacts server: a {KIND_WORDS[kind]} can only be made there with one")
    name = " ".join(name.split())
    if not name or len(name) > 200:
        raise UsageError("A name of up to 200 characters is needed")
    word = KIND_WORDS[kind]
    with _lock_for(account_id):  # one at a time with the synchronisation of this account
        try:
            with _client(engine, settings) as client:
                remote = make_on_server(client, kind.value, name, colour)
        except (ProtocolError, AuthenticationError) as error:
            if not passing_failure(error):
                raise UsageError(f"The server did not make the {word} “{name}” ({error}); nothing was made") from None
            return _later(engine, account_id, store, kind, name, colour, str(error))
        except ChronoMailError as error:  # no password stored, for one: made there once it can be
            return _later(engine, account_id, store, kind, name, colour, str(error))
        info = store.create_collection(kind, name, colour, server=tie_for(remote))
        if colour and kind is not CollectionKind.CONTACTS and canonical_colour(remote.colour) != colour.lower():
            # the server keeps no colour (or another one): the person's colour is kept with the account (D-192)
            key = folder_key(remote)
            keep_looks(engine, account_id, {key: None}, {key: CollectionLook(key, colour=colour.lower(), colour_state=LOOK_KEPT, base_colour=remote.colour)})
        return info


def _later(engine: Engine, account_id: str, store: PimStore, kind: CollectionKind, name: str, colour: str, reason: str) -> CollectionInfo:
    """The server cannot be reached: made here now (its items are written here) and made on the server at the next synchronisation."""
    info = store.create_collection(kind, name, colour)
    remember(store, info.collection_id)
    engine.notice(f"{name}: the server of {account_name(engine, account_id)} could not be reached ({reason}); it is made there at the next synchronisation")
    return info
