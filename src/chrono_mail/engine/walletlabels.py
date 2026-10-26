import threading

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, ConfigurationError
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "walletlabels.py 3.6.371"

# --- Version history ----------------------------------------------------
# v3.6.371: Initial: the labels a person reads in the wallet for Chrono Mail's passwords and key; given at every save, rename and start (B-23, D-275)

PROGRAM_NAME: str = "Chrono Mail"
LABEL_LOCK: threading.Lock = threading.Lock()  # labels are written from one reading of the settings at a time: a rename during the start pass is never labelled with the old name
SERVER_WORDS: dict[ServerRole, str] = {
    ServerRole.INCOMING: "incoming mail server",
    ServerRole.OUTGOING: "outgoing mail server",
    ServerRole.DAV: "calendar and contacts server",
}


def _program(engine: Engine) -> str:
    """"Chrono Mail", or "Chrono Mail (<folder>)" for a profile, so its entries are never taken for the person's own (D-274)."""
    profile = engine.directories.profile_name
    return f"{PROGRAM_NAME} ({profile})" if profile else PROGRAM_NAME


def password_label(engine: Engine, settings: AccountSettings, role: ServerRole) -> str:
    """"Chrono Mail: Work, anna@example.com (incoming mail server)": the account name when there is one, then the address (an account
    of calendars and contacts only has none: its user name), then the server the password is for."""
    account = settings.account
    names = dict.fromkeys(name for name in (settings.account_name, account.email_address or account.incoming.username) if name)
    server = SERVER_WORDS[ServerRole.DAV] if account.kind is AccountKind.DAV else SERVER_WORDS[role]  # its one password is its server's
    return f"{_program(engine)}: {', '.join(names)} ({server})"


def key_label(engine: Engine) -> str:
    return f"{_program(engine)}: the key that encrypts your mail"


def label_account(engine: Engine, account_id: str) -> None:
    """Every password of the account gets its label, as the account is named now. An entry that is not there stays absent; a label that
    cannot be written changes nothing (the stores never raise for a label)."""
    with LABEL_LOCK:
        try:
            settings = engine.config().find(account_id)
        except ConfigurationError:
            return  # removed meanwhile: its passwords went with it
        for role in ServerRole:
            if not engine.passwords.label(account_id, role, password_label(engine, settings, role)):
                return  # the wallet could not be asked (locked, the unlock refused): never ask it again and again


def label_key(engine: Engine, key_id: bytes) -> bool:
    return engine.keys.label(key_id, key_label(engine))


def label_wallet(engine: Engine, other_keys: tuple[bytes, ...] = ()) -> int:
    """At start: every entry of Chrono Mail in the wallet gets its label (entries of older versions have none; a label that could not be
    written is tried again here), also the older key Local Folders' kept mail needs (other_keys). Only labels change, never a password or the name an entry is found by. How many accounts were looked at (0 when the wallet
    could not be asked: the first failure ends it, so a locked wallet is never asked once per entry)."""
    with LABEL_LOCK:
        config = engine.config()
        accounts = [settings for settings in config.accounts if settings.account.kind is not AccountKind.LOCAL]  # Local Folders has no password
        keys = (*((bytes.fromhex(config.key_id),) if config.key_id is not None else ()), *other_keys)
        for key_id in keys:
            if not label_key(engine, key_id):
                return 0  # the wallet could not be asked (locked, the unlock refused): never ask it again for every entry
        for settings in accounts:
            for role in ServerRole:
                if not engine.passwords.label(settings.account.account_id, role, password_label(engine, settings, role)):
                    return 0
        return len(accounts)
