import re
import sqlite3

from chrono_mail.core import ChronoMailError, CollectionKind
from chrono_mail.engine.commands_accounts import _address, _own_name
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.engine.session import Engine
from chrono_mail.pim.contact import parse_contacts
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "calendarnames.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: Initial: a calendar named only by an address is shown by a name: the account's for its own address, else the contact's (D-346)

_BARE_ADDRESS = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+")
CANDIDATE_LIMIT: int = 50  # the contacts the index finds for one address, read a page at a time


class CollectionNamer:
    """Names collections for one command or one fact (D-346): a collection name that is only an e-mail address is shown by the name of the
    account with that address (this collection's account first), else of a contact with that address in any address book, else the address.
    Only the contacts the index finds for an address are read, and the name found is kept by the engine until something changes. The name kept and sent to the server is never changed by this."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, account_id: str, name: str) -> str:
        address = name.strip()
        if not _BARE_ADDRESS.fullmatch(address):
            return name
        lowered = address.lower()
        own = sorted((item for item in self._engine.config().accounts if _address(item).lower() == lowered),
                     key=lambda item: item.account.account_id != account_id)
        for item in own:  # this collection's own account first
            shown = _own_name(item)
            if shown.lower() != lowered:
                return shown
        known = self._engine.contact_names.get(lowered)
        if known is None:
            generation = self._engine.contact_names.generation()
            known = self._contact_name(lowered)
            self._engine.contact_names.keep(lowered, known, generation)
        return known or name

    def _contact_name(self, address: str) -> str:
        """The full name of a contact with this address: the contact index finds the few that mention it, only those are read (a review of
        4.4.608: never every contact)."""
        for account in pim_account_ids(self._engine):
            try:
                with open_pim(self._engine, account) as store:
                    offset = 0
                    while True:  # page through what the index finds: a big address book may mention the address's words often
                        found = store.index.query(ItemQuery(CollectionKind.CONTACTS, (), address, "", limit=CANDIDATE_LIMIT, offset=offset, groups=False))
                        for indexed in found:
                            try:
                                contacts = parse_contacts(store.get(indexed.collection, indexed.item_id))
                            except ChronoMailError:
                                continue  # a contact that cannot be read or decrypted names nobody
                            for contact in contacts[:1]:
                                full = contact.full_name()
                                addresses = {email.value.strip().lower() for email in contact.emails}
                                if full and full.lower() not in addresses and address in addresses:  # a contact without a name names nobody
                                    return full
                        if len(found) < CANDIDATE_LIMIT:
                            break
                        offset += CANDIDATE_LIMIT
            except (ChronoMailError, sqlite3.Error, OSError):
                continue  # an account's address books that cannot be opened or read now (a key, a removed account, a busy index) name nobody
        return ""

def shown_collection_name(engine: Engine, account_id: str, name: str) -> str:
    """How one collection is named on screen (D-346); a command that names many uses one CollectionNamer."""
    return CollectionNamer(engine)(account_id, name)
