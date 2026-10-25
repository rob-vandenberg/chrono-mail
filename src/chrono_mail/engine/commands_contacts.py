from dataclasses import dataclass, field
from email.utils import formataddr

from chrono_mail.core import CollectionKind
from chrono_mail.engine.groups import group_addresses
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, option
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_contacts.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.10.277: A contact group whose name matches is suggested too: choosing it puts every member's address in the field (R-39)
# v1.9.273: R-19: typed contact.suggest
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.23: Initial address suggestions

MAXIMUM_SUGGESTIONS: int = 50


@dataclass(frozen=True, slots=True)
class SuggestParameters:
    text: str = field(metadata=option("Part of a name or an address"))
    account: str | None = field(default=None, metadata=option("Only the addresses seen in this account (default: all accounts)"))
    limit: int = field(default=8, metadata=option("How many suggestions at most"))


@dataclass(frozen=True, slots=True)
class Suggestion:
    name: str
    address: str
    display: str  # as it goes into an address field: Name <address> (for a group: every member's, separated by commas)
    times_written_to: int
    times_received_from: int
    label: str = ""  # what the list of suggestions shows when it is not the display: a group's name and its number of addresses


def _group_suggestions(engine: Engine, text: str, limit: int) -> list[Suggestion]:
    """The contact groups whose name matches what is being typed, each with the addresses of its members (a group without any is left out)."""
    found: list[Suggestion] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            books = {info.collection_id: info.name for info in store.collections(CollectionKind.CONTACTS)}
            for group in store.index.query(ItemQuery(CollectionKind.CONTACTS, tuple(books), text, groups=True, limit=limit)):
                addresses, _ = group_addresses(store, group)
                if addresses:
                    count = len(addresses)
                    found.append(Suggestion(group.title, "", ", ".join(addresses), 0, 0, f"{group.title} (group, {count} address{'es' if count != 1 else ''}) – {books.get(group.collection, '')}"))
    return sorted(found, key=lambda suggestion: suggestion.label.casefold())[:limit]


def contact_suggest(engine: Engine, parameters: SuggestParameters) -> tuple[Suggestion, ...]:
    """Addresses seen in the mail that match what is being typed, the ones written to most often first, after the contact groups whose name matches.
    Own addresses are left out."""
    config = engine.config()
    own = {item.account.email_address.lower() for item in config.accounts}
    limit = max(1, min(parameters.limit, MAXIMUM_SUGGESTIONS))
    groups = _group_suggestions(engine, parameters.text, limit) if parameters.text.strip() else []
    merged: dict[str, Suggestion] = {}
    wanted_place = engine.storage_settings(engine.resolve_account(parameters.account, config), config).account.account_id if parameters.account else None
    for place, members in engine.storage_groups(config).items():
        if wanted_place is not None and place != wanted_place:
            continue
        with open_mailbox(engine, members[0].account.account_id) as mailbox:  # accounts that share their mail are looked at once
            for known in mailbox.index.suggest(parameters.text, limit + len(own)):
                address = known.address.lower()
                if address in own:
                    continue
                before = merged.get(address)
                display = formataddr((known.name, known.address)) if known.name else known.address
                merged[address] = Suggestion(
                    known.name or (before.name if before else ""), known.address, display,
                    known.as_recipient + (before.times_written_to if before else 0), known.as_sender + (before.times_received_from if before else 0),
                )
    ranked = sorted(merged.values(), key=lambda suggestion: (-(suggestion.times_written_to * 3 + suggestion.times_received_from), suggestion.address.lower()))
    return tuple((groups + ranked)[:limit])


CONTACT_SUGGEST: TypedCommand[SuggestParameters, tuple[Suggestion, ...]] = TypedCommand("contact.suggest", SuggestParameters, tuple)  # every element is a Suggestion


def register(registry: CommandRegistry) -> None:
    registry.register(CONTACT_SUGGEST.id, "Suggest addresses from the mail for what is being typed", SuggestParameters, contact_suggest, doing="Looking up addresses")
