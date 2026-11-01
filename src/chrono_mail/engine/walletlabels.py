import threading

from chrono_mail.config import AccountSettings, KeyringPasswordStore, ServerRole
from chrono_mail.core import AccountKind, ConfigurationError
from chrono_mail.core.credentials import CredentialEntries, CredentialFailure, move_entry
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "walletlabels.py 4.2.459"

# --- Version history ----------------------------------------------------
# v4.2.459: move_profile_entries: a profile's entries move from its wallet name of before (with "/") to its own name now (D-274)
# v3.6.371: Initial: the labels a person reads in the wallet for Chrono Mail's passwords and key; given at every save, rename and start (B-23, D-275)

PROGRAM_NAME: str = "Chrono Mail"
MOVED_FILE: str = "wallet-moved"  # in a profile's settings folder: its entries are under its wallet name without "/" (D-274)
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


def move_profile_entries(engine: Engine, other_keys: tuple[bytes, ...] = ()) -> tuple[int, tuple[str, ...]]:
    """At start of a profile made before 4.2.459: its key (and the older one Local Folders' kept mail needs, other_keys) and its accounts'
    passwords move from its former wallet name to its own name now, each written, read back and only then removed (D-274). Returns how
    many moved and what was left where it was (garbled by the wallet): the key, or an account's name. Nothing for the person's own entries,
    stores that are not the wallet (tests), or a profile whose move is done (MOVED_FILE: the wallet is not asked again at every start)."""
    former = engine.directories.former_credential_service
    passwords, keys = engine.passwords, engine.keys
    done = engine.directories.config_directory / MOVED_FILE
    if not former or former == engine.directories.credential_service or done.exists():
        return 0, ()
    with LABEL_LOCK:
        config = engine.config()
        names: list[tuple[CredentialEntries, str, str]] = []  # the store's entries, the entry's name, what it is for the report
        if isinstance(keys, KeyringKeyStore):
            for key_id in (*((bytes.fromhex(config.key_id),) if config.key_id is not None else ()), *other_keys):
                names.append((keys.entries, KeyringKeyStore.entry_name(key_id), "the key that encrypts your mail"))
        if isinstance(passwords, KeyringPasswordStore):
            for settings in config.accounts:
                if settings.account.kind is AccountKind.LOCAL:
                    continue
                for role in ServerRole:
                    what = password_label(engine, settings, role).partition(": ")[2]
                    names.append((passwords.entries, KeyringPasswordStore.entry_name(settings.account.account_id, role), what))
        moved = 0
        left: list[str] = []
        try:
            for entries, name, what in names:
                outcome = move_entry(entries.under(former), entries.under(entries.service), name)  # each under its one name: no reading of the other
                if outcome == "moved":
                    moved += 1
                elif outcome == "garbled":
                    left.append(what)
        except CredentialFailure as failure:
            raise ConfigurationError(f"The wallet could not move this profile's entries: {failure.reason}") from None
        if not left and done.parent.is_dir():
            try:
                done.write_text("moved\n", "utf-8")  # all moved: later starts never ask the wallet for it again
            except OSError:
                pass  # the next start finds everything moved and writes it then
        return moved, tuple(dict.fromkeys(left))
