from dataclasses import dataclass, field

from chrono_mail.config import ServerRole
from chrono_mail.core import AccountKind, ConfigurationError, ProtocolError
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, option
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.imap import ImapClient, ImapRefused

# --- Version ------------------------------------------------------------
__version__ = "commands_watch.py 4.2.429"

# --- Version history ----------------------------------------------------
# v4.2.429: mail.wait runs behind the person's work (it waits for minutes: never counted as the person's)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.270: R-19: typed mail.wait
# v0.0.43: Certificate exceptions
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
    with ImapClient(settings.account.incoming, password, ssl_context=engine.tls_context(settings)) as client:
        if "IDLE" not in client.capabilities:
            return WaitResult(False, False)
        try:
            client.select("INBOX", read_only=True)
            return WaitResult(True, client.idle(seconds, engine.stop_event.is_set))
        except ImapRefused as error:
            raise ProtocolError(f"The server would not wait for mail: {error}") from None


MAIL_WAIT: TypedCommand[WaitParameters, WaitResult] = TypedCommand("mail.wait", WaitParameters, WaitResult)


def register(registry: CommandRegistry) -> None:
    registry.register(MAIL_WAIT.id, "Wait until the server reports new mail in the Inbox (IMAP), for at most the given time", WaitParameters, mail_wait, doing="Waiting for new mail", behind=True)
