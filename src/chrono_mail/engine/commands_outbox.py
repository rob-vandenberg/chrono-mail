from dataclasses import dataclass, field

from chrono_mail.config import ServerRole
from chrono_mail.core import ChronoMailError, FolderRole
from chrono_mail.engine.commands_schedule import KIND, _due_time, _mark_replied, _notes, _places, _sender
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, option
from chrono_mail.engine.session import Engine
from chrono_mail.mime import parse_message
from chrono_mail.sync import send_stored

# --- Version ------------------------------------------------------------
__version__ = "commands_outbox.py 0.4.120"

# --- Version history ----------------------------------------------------
# v0.4.120: The Sent copy goes to the server at once (IMAP)
# v0.0.86: Initial Send All: send what waits in the Outbox


@dataclass(frozen=True, slots=True)
class SendOutboxParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))


@dataclass(frozen=True, slots=True)
class SendOutboxResult:
    sent: int
    failed: tuple[str, ...]


def _recipients(parsed_to: tuple[str, ...]) -> tuple[str, ...]:
    """Each address once, in the order given."""
    seen: set[str] = set()
    unique: list[str] = []
    for address in parsed_to:
        if address.lower() not in seen:
            seen.add(address.lower())
            unique.append(address)
    return tuple(unique)


def mail_send_outbox(engine: Engine, parameters: SendOutboxParameters) -> SendOutboxResult:
    """Send the messages that wait in the Outbox because an earlier send failed: as they are stored, to their To, Cc and
    Bcc, without the Bcc header. Scheduled and repeating messages wait for their time and are left alone. A message is only
    moved to Sent after the server accepted it; one that fails again stays in the Outbox."""
    sent = 0
    failed: list[str] = []
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            for message in mailbox.store.list_messages(mailbox.folder("Outbox")):
                hint = mailbox.hint(message.ref.store_id)
                try:
                    raw = mailbox.store.read(message.ref, hint=hint)
                    parsed = parse_message(raw)
                except ChronoMailError as error:
                    failed.append(f"a message in the Outbox cannot be read: {error}")
                    continue
                if _due_time(parsed) is not None or _notes(parsed).get(KIND) == "recurring":
                    continue
                recipients = _recipients(tuple(address.address for address in (*parsed.to_addresses, *parsed.cc_addresses, *parsed.bcc_addresses)))
                if not recipients:
                    failed.append(f"{parsed.subject}: there is no recipient")
                    continue
                sender = _sender(members, parsed)
                password = engine.passwords.get(sender.account.account_id, ServerRole.OUTGOING) if sender is not None else None
                if sender is None or password is None:
                    failed.append(f"{parsed.subject}: no password is stored for the account it is sent from")
                    continue
                try:
                    outcome = send_stored(
                        mailbox.store, mailbox.index, sender.account.outgoing, password, message.ref, hint, raw, sender.account.email_address,
                        recipients, mailbox.role_folder(FolderRole.SENT), ssl_context=engine.tls_context(sender, outgoing=True),
                    )
                except ChronoMailError as error:
                    failed.append(f"{parsed.subject}: {error}")
                    continue
                if outcome.sent:
                    sent += 1
                    mailbox.put_on_server(outcome.reference)  # IMAP: the Sent copy is in Sent on the server at once
                    _mark_replied(mailbox, parsed)
                    if outcome.warning:
                        failed.append(f"{parsed.subject}: {outcome.warning}")
                else:
                    failed.append(f"{parsed.subject}: {outcome.error} (it stays in the Outbox)")
    return SendOutboxResult(sent, tuple(failed))


def register(registry: CommandRegistry) -> None:
    registry.register("mail.send-outbox", "Send the messages that wait in the Outbox (not the scheduled ones)", SendOutboxParameters, mail_send_outbox)
