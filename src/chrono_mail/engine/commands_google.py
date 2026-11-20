import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field, replace

from chrono_mail.config import AccountSettings, AppConfig, ServerRole, new_account_id
from chrono_mail.core import AccountKind, ChronoMailError
from chrono_mail.core.account import Account, ConnectionSecurity, ServerSettings
from chrono_mail.config.google import google_client_codes
from chrono_mail.engine import google_access
from chrono_mail.engine.commands_accounts import NoParameters, _refuse_same_account, shown_name
from chrono_mail.engine.google_access import forget_access, google_client, google_kinds
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.newkey import recovery_code_not_stored
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.engine.walletlabels import label_account
from chrono_mail.protocols.dav import DavClient
from chrono_mail.protocols.google import CALDAV_START, CARDDAV_START, GoogleClient, SignInWaiter

# --- Version ------------------------------------------------------------
__version__ = "commands_google.py 4.6.646"

# --- Version history ----------------------------------------------------
# v4.6.646: a new sign-in uses the client ID and client secret the person typed, kept by each account it adds; signing in again uses the
#           account's own (B-34, D-364); google.copy-codes gives the Google accounts the codes of the former private file, once
# v4.4.608: an added account gets no Shown as name, but the person's name on Google, kept apart (google_name); signing in again renews it (D-346)
# v4.4.597: each part checks only its own kind of folder (D-335)
# v4.4.593: Initial: Google sign-in in the person's browser; it adds Google Calendar and Google Contacts as two accounts of calendars and
#           contacts, or signs an existing pair in again (D-335)

SIGN_IN_SECONDS: float = 300.0  # how long the sign-in page in the browser is waited for
PARTS: tuple[tuple[str, str], ...] = (("Google Calendar", CALDAV_START), ("Google Contacts", CARDDAV_START))
_WAITING_LOCK = threading.Lock()
_waiting: dict[str, SignInWaiter] = {}  # ticket -> the sign-in under way, from its start until it has kept what it got
CODES_COPIED_FILE: str = "google-codes-copied"  # in the settings folder: the one copy of the former file's codes is done (D-364)
_stopped: set[str] = set()  # tickets stopped before their sign-in started: it refuses at once


@dataclass(frozen=True, slots=True)
class SignInParameters:
    address: str = field(default="", metadata=option("The Google address to suggest on the sign-in page (empty: Google asks)"))
    account: str = field(default="", metadata=option("A Google account to sign in again (empty: add Google Calendar and Google Contacts)"))
    ticket: str = field(default="", metadata=option("A name for this sign-in, so google.cancel-sign-in can stop it (empty: only a stop of all)"))
    client_id: str = field(default="", metadata=option("Adding: the client ID of your Google project (signing in again uses the account's own)"))
    client_secret: str = field(default="", metadata=option("Adding: the client secret of your Google project", secret=True))


@dataclass(frozen=True, slots=True)
class CancelParameters:
    ticket: str = field(default="", metadata=option("The sign-in to stop, as google.sign-in was given it (empty: every sign-in waiting now)"))


@dataclass(frozen=True, slots=True)
class SignInResult:
    address: str  # the Google account that signed in
    account_ids: tuple[str, ...]  # the accounts added, or signed in again
    key_created: bool = False
    show_recovery_code: bool = False


def google_sign_in(engine: Engine, parameters: SignInParameters) -> SignInResult:
    """Open Google's sign-in page in the person's browser and wait for it (D-335). Without an account: Google Calendar and Google Contacts are
    added as two accounts of calendars and contacts, each kept in step with its server like every other one, its refresh token kept as its
    password; a part that offers nothing is left out. With an account: that Google address's accounts take the new sign-in."""
    again = engine.resolve_account(parameters.account) if parameters.account.strip() else None
    if again is not None and not again.google:
        raise UsageError("This account does not sign in with Google")
    if again is not None:
        client = google_client(engine, again)  # the account's own project (D-364)
    else:
        client_id, secret = parameters.client_id.strip(), parameters.client_secret.strip()
        if not client_id or not secret:
            raise UsageError("Fill in the client ID and client secret of your Google project first")
        client = GoogleClient(client_id, secret)
    hint = again.account.incoming.username if again is not None else parameters.address.strip()
    waiter = SignInWaiter(client, hint)
    ticket = parameters.ticket or f"sign-in-{id(waiter)}"
    with _WAITING_LOCK:  # registered before anything else, kept until the end: a stop at any moment is heard
        _waiting[ticket] = waiter
        if ticket in _stopped:
            _stopped.discard(ticket)
            waiter.cancel()
    try:
        waiter.check()
        try:
            opened = webbrowser.open(waiter.url)
        except webbrowser.Error:
            opened = False
        if not opened:
            raise UsageError("Chrono Mail could not open your web browser for Google's sign-in: set a default web browser and try again")
        signed = waiter.wait(SIGN_IN_SECONDS, engine.stop_event)
        address = signed.address or hint
        if not address:
            raise UsageError("Google did not say which account signed in")
        if again is not None:
            return _sign_in_again(engine, again, address, signed.refresh_token, signed.name, waiter)
        return _add(engine, address, signed.refresh_token, signed.access_token, signed.name, waiter, client)
    finally:
        waiter.close()
        with _WAITING_LOCK:
            _waiting.pop(ticket, None)


def _sign_in_again(engine: Engine, again: AccountSettings, address: str, refresh_token: str, name: str, waiter: SignInWaiter) -> SignInResult:
    known = again.account.incoming.username.lower()
    if address.lower() != known:
        raise UsageError(f"You signed in as {address}, but this account belongs to {known}: sign in with {known}")
    renewed = tuple(item.account.account_id for item in engine.config().accounts if item.google and item.account.incoming.username.lower() == known)
    waiter.check()  # stopped meanwhile: nothing is kept
    for account_id in renewed:
        engine.passwords.set(account_id, ServerRole.INCOMING, refresh_token)
        forget_access(account_id)
    if name:  # the person's name on Google, which a sign-in from before the profile permission did not give (D-346)
        def named(fresh: AppConfig) -> AppConfig:
            for item in fresh.accounts:
                if item.account.account_id in renewed:  # an account removed meanwhile is left out
                    fresh = fresh.with_account(replace(item, google_name=name))
            return fresh

        engine.change_config(named)
    return SignInResult(address, renewed)


def _add(engine: Engine, address: str, refresh_token: str, access_token: str, name: str, waiter: SignInWaiter, client: GoogleClient) -> SignInResult:
    here = {item.account.incoming.host.lower() for item in engine.config().accounts if item.google and item.account.incoming.username.lower() == address.lower()}
    missing = [(part, start.format(address=urllib.parse.quote(address))) for part, start in PARTS]
    missing = [(part, url) for part, url in missing if (urllib.parse.urlsplit(url).hostname or "").lower() not in here]  # a part removed comes back
    if not missing:
        raise UsageError(f"The Google calendars and contacts of {address} are here already. To sign in again: Settings > Accounts, the account, "
                         "Sign in with Google")
    wanted: list[tuple[str, str]] = []
    for part, url in missing:
        with DavClient(url, "", "", bearer=access_token, kinds=google_kinds(url)) as server:
            if server.all_collections():
                wanted.append((part, url))
    if not wanted:
        raise UsageError(f"Google was reached, but it offers no calendars or contacts for {address}")
    accounts = engine.config().accounts
    for _, url in wanted:
        _refuse_same_account(accounts, AccountKind.DAV, address, urllib.parse.urlsplit(url).hostname or "", None)
    waiter.check()  # stopped while Google was asked: nothing is kept
    _, key_created = ensure_local_folders(engine, True)  # the data key the calendars are kept with exists from here on
    added: list[AccountSettings] = []
    for _, url in wanted:
        host = urllib.parse.urlsplit(url).hostname or ""
        server = ServerSettings(host, 443, ConnectionSecurity.TLS, address)
        added.append(AccountSettings(Account(new_account_id(), name or address, "", AccountKind.DAV, server, server), dav_url=url, google=True, google_name=name, google_client_id=client.client_id))  # no Shown as name: the person chooses one (D-346)
    try:
        for settings in added:
            engine.passwords.set(settings.account.account_id, ServerRole.INCOMING, refresh_token)
            engine.passwords.set(settings.account.account_id, ServerRole.GOOGLE_SECRET, client.client_secret)  # D-364

        def add(fresh: AppConfig) -> AppConfig:
            waiter.check()  # the last moment a stop is heard: before the commit point
            for settings in added:
                _refuse_same_account(fresh.accounts, AccountKind.DAV, address, settings.account.incoming.host, None)  # added meanwhile?
                fresh = fresh.with_account(settings)
            return fresh

        engine.change_config(add)  # the commit point
    except ChronoMailError:
        for settings in added:
            engine.passwords.delete(settings.account.account_id, ServerRole.INCOMING)
            engine.passwords.delete(settings.account.account_id, ServerRole.GOOGLE_SECRET)
        raise
    for settings in added:
        label_account(engine, settings.account.account_id)  # B-23
    return SignInResult(address, tuple(settings.account.account_id for settings in added), key_created, recovery_code_not_stored(engine))


def google_cancel_sign_in(engine: Engine, parameters: CancelParameters) -> SignInResult:
    """Stop a sign-in (the person pressed Cancel or closed its window), at any moment up to the commit point: it keeps nothing. A ticket not
    started yet is refused when it starts."""
    with _WAITING_LOCK:
        if parameters.ticket:
            waiter = _waiting.get(parameters.ticket)
            if waiter is None:
                _stopped.add(parameters.ticket)
            waiting = [waiter] if waiter is not None else list[SignInWaiter]()
        else:
            waiting = list(_waiting.values())
    for each in waiting:
        each.cancel()
    return SignInResult("", ())


@dataclass(frozen=True, slots=True)
class CopiedCodes:
    names: tuple[str, ...]  # the Google accounts that got the codes of the former private file, as Settings > Accounts names them


def google_copy_codes(engine: Engine, parameters: NoParameters) -> CopiedCodes:
    """Once, at the first start of 4.6.646 (D-364): the Google accounts without a client ID get the client ID and client secret of the
    private file they signed in with before, when this computer has it. Then CODES_COPIED_FILE says it is done and the file is never read
    again. A credential store that refuses leaves it for the next start."""
    done = engine.directories.config_directory / CODES_COPIED_FILE
    if done.exists():
        return CopiedCodes(())
    waiting = [item for item in engine.config().accounts if item.google and not item.google_client_id]
    codes = google_client_codes(google_access.FORMER_CODES_FILE) if waiting else None
    names: list[str] = []
    if codes is not None:
        client_id, secret = codes
        for item in waiting:  # the secrets first: an account never has a client ID without its secret
            engine.passwords.set(item.account.account_id, ServerRole.GOOGLE_SECRET, secret)
        given = {item.account.account_id for item in waiting}

        def give(fresh: AppConfig) -> AppConfig:
            for item in fresh.accounts:
                if item.account.account_id in given and not item.google_client_id:
                    fresh = fresh.with_account(replace(item, google_client_id=client_id))
            return fresh

        config = engine.change_config(give)
        for item in config.accounts:
            if item.account.account_id in given:
                forget_access(item.account.account_id)
                label_account(engine, item.account.account_id)
                names.append(shown_name(item, config.accounts))
    try:
        engine.directories.config_directory.mkdir(parents=True, exist_ok=True)
        done.touch()
    except OSError:
        pass  # asked again at the next start, which finds every account with its codes and copies nothing
    return CopiedCodes(tuple(names))


GOOGLE_SIGN_IN: TypedCommand[SignInParameters, SignInResult] = TypedCommand("google.sign-in", SignInParameters, SignInResult)
GOOGLE_CANCEL_SIGN_IN: TypedCommand[CancelParameters, SignInResult] = TypedCommand("google.cancel-sign-in", CancelParameters, SignInResult)
GOOGLE_COPY_CODES: TypedCommand[NoParameters, CopiedCodes] = TypedCommand("google.copy-codes", NoParameters, CopiedCodes)


def register(registry: CommandRegistry) -> None:
    registry.register(GOOGLE_SIGN_IN.id, "Sign in with Google: add Google Calendar and Google Contacts, or sign a Google account in again", SignInParameters,
                      google_sign_in, doing="Waiting for Google's sign-in in your browser")
    registry.register(GOOGLE_COPY_CODES.id, "Once: give the Google accounts the client ID and client secret of the former private file",
                      NoParameters, google_copy_codes, doing="Giving the Google accounts their client ID and client secret")
    registry.register(GOOGLE_CANCEL_SIGN_IN.id, "Stop a Google sign-in under way: it keeps nothing", CancelParameters, google_cancel_sign_in, doing="Stopping the sign-in")

