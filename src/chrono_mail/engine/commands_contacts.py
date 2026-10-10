from dataclasses import dataclass, field
from email.utils import formataddr

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_contacts.py 0.0.45"

# --- Version history ----------------------------------------------------
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
    display: str  # as it goes into an address field: Name <address>
    times_written_to: int
    times_received_from: int


def contact_suggest(engine: Engine, parameters: SuggestParameters) -> tuple[Suggestion, ...]:
    """Addresses seen in the mail that match what is being typed, the ones written to most often first. Own addresses are left out."""
    config = engine.config()
    own = {item.account.email_address.lower() for item in config.accounts}
    limit = max(1, min(parameters.limit, MAXIMUM_SUGGESTIONS))
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
    return tuple(ranked[:limit])


def register(registry: CommandRegistry) -> None:
    registry.register("contact.suggest", "Suggest addresses from the mail for what is being typed", SuggestParameters, contact_suggest)
