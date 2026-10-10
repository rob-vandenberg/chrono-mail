from dataclasses import dataclass, field

from chrono_mail.config import ServerRole
from chrono_mail.core import AccountKind, ConfigurationError, ProtocolError
from chrono_mail.engine.registry import CommandRegistry, option
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.imap import ImapClient, ImapRefused

# --- Version ------------------------------------------------------------
__version__ = "commands_watch.py 0.0.32"

# --- Version history ----------------------------------------------------
# v0.0.32: Initial mail.wait (IMAP IDLE)

MAXIMUM_WAIT_SECONDS: int = 29 * 60  # servers may drop an IDLE after 30 minutes


@dataclass(frozen=True, slots=True)
class WaitParameters:
    account: str = field(metadata=option("The account"))
    seconds: int = field(default=300, metadata=option("How long to wait at most (up to 1,740 seconds)"))


@dataclass(frozen=True, slots=True)
class WaitResult:
    supported: bool  # False: this account (or its server) cannot tell about new mail; check from time to time instead
    changed: bool  # True: the server reported a change in the Inbox


def mail_wait(engine: Engine, parameters: WaitParameters) -> WaitResult:
    """Wait for the server to say that something happened in the Inbox (IMAP IDLE), then return so that mail can be checked.
    Nothing is downloaded or changed here. Returns early when the program is ending."""
    settings = engine.resolve_account(parameters.account)
    if settings.account.kind is not AccountKind.IMAP:
        return WaitResult(False, False)
    password = engine.passwords.get(settings.account.account_id, ServerRole.INCOMING)
    if password is None:
        raise ConfigurationError("No password is stored for this account")
    seconds = max(1, min(parameters.seconds, MAXIMUM_WAIT_SECONDS))
    with ImapClient(settings.account.incoming, password) as client:
        if "IDLE" not in client.capabilities:
            return WaitResult(False, False)
        try:
            client.select("INBOX", read_only=True)
            return WaitResult(True, client.idle(seconds, engine.stop_event.is_set))
        except ImapRefused as error:
            raise ProtocolError(f"The server would not wait for mail: {error}") from None


def register(registry: CommandRegistry) -> None:
    registry.register("mail.wait", "Wait until the server reports new mail in the Inbox (IMAP), for at most the given time", WaitParameters, mail_wait)
