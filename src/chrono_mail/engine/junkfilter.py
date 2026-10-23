"""The junk filter (D-238): new mail that the server marked as spam, or that the learned words make very likely junk, goes to the
Junk folder before the rules run. It learns from what the person does (Junk, Not junk, moves into and out of Junk, replies), from
the server's verdict, and once from the mail already kept. It only ever moves mail, through the mailbox (so on IMAP the server
first, D-117); what it learned stays on this computer only (D-201)."""

from dataclasses import dataclass, field

from chrono_mail.config import Rule, load_rules
from chrono_mail.core import ChronoMailError, ConfigurationError, CryptoError, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.crypto import DataKey
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_addressbook import AddressesParameters, contact_addresses
from chrono_mail.engine.junkwords import JUNK_SCORE, JunkWords, fingerprint, load_words, save_words, tokens
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand
from chrono_mail.engine.rule_engine import Subject, applies_to, condition_holds, header_values, rule_matches
from chrono_mail.engine.schedule import SCHEDULED_FOLDER
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.mime import ParsedMessage, parse_message

# --- Version ------------------------------------------------------------
__version__ = "junkfilter.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.1.310: junk.learn-existing tells how far it is (mailbox n of m, messages read of all), every 100 messages
# v2.1.308: Initial junk filter: new mail, learning from the person and the server, the first learning of the mail kept (D-238)

EXISTING_EACH: int = 2_000  # per place where mail is kept: the newest junk and the newest read good mail learned at first
PROGRESS_STEP: int = 100  # messages between two reports of how far the first learning is
SPAM_HEADERS: tuple[str, ...] = ("X-Spam-Flag", "X-Spam-Status", "X-Spam")  # SpamAssassin, rspamd and the servers that copy them
NOT_LEARNED_AS_GOOD: frozenset[FolderRole] = frozenset({FolderRole.JUNK, FolderRole.TRASH, FolderRole.DRAFTS})


@dataclass
class JunkReport:
    moved: int = 0
    failed: list[str] = field(default_factory=list)


def server_says_junk(raw: bytes) -> bool:
    """The mail server's own spam check marked the message as spam ("X-Spam-Flag: YES", "X-Spam-Status: Yes, score=…")."""
    return any(value.strip().casefold().startswith("yes") for name in SPAM_HEADERS for value in header_values(raw, name))


# --- the words, loaded once and kept with the engine ------------------------------------------------------------------


def _key(engine: Engine) -> DataKey | None:
    config = engine.config()
    return None if config.key_id is None else engine.data_key(config)


def _tell_once(engine: Engine, text: str) -> None:
    if engine.first_time(text):
        engine.notice(text)


def _loaded(engine: Engine) -> JunkWords | None:
    """The learned words (hold engine.junk.lock). None while the key cannot be had: the filter then only follows the server."""
    holder = engine.junk
    if holder.words is not None:
        return holder.words
    path = engine.directories.junk_filter_file
    try:
        key = _key(engine)
    except (CryptoError, ConfigurationError) as error:
        _tell_once(engine, f"The junk filter cannot read what it learned: {error}")
        return None
    try:
        holder.words = load_words(path, key)
    except StorageError as error:  # damaged: kept aside for a look, the filter learns again from the start
        try:
            path.replace(path.with_name(path.name + ".bad"))
        except OSError:
            pass
        _tell_once(engine, f"The junk filter starts learning again: {error}")
        holder.words = JunkWords()
    return holder.words


def _save(engine: Engine, words: JunkWords) -> None:
    try:
        save_words(engine.directories.junk_filter_file, words, _key(engine), engine.durable)
    except (StorageError, CryptoError, ConfigurationError) as error:
        _tell_once(engine, f"The junk filter could not keep what it learned: {error}")


def _learn_all(engine: Engine, gathered: list[tuple[str, frozenset[str]]], junk: bool, place: str | None = None) -> int:
    """Learn these messages (fingerprint and words) as junk or good; returns how many were new to it."""
    with engine.junk.lock:
        words = _loaded(engine)
        if words is None:
            return 0
        changed = sum(1 for key, found in gathered if words.learn(key, found, junk))
        if place is not None:
            words.places.add(place)
        if changed or place is not None:
            _save(engine, words)
        return changed


def _gather(mailbox: Mailbox, references: list[MessageRef]) -> list[tuple[str, frozenset[str]]]:
    gathered: list[tuple[str, frozenset[str]]] = []
    for reference in references:
        try:
            raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        except ChronoMailError:
            continue  # a message that cannot be read teaches nothing
        gathered.append((fingerprint(raw), tokens(parse_message(raw))))
    return gathered


def learn(mailbox: Mailbox, references: list[MessageRef], junk: bool) -> None:
    """The person said these messages are junk (or good). Never raises: learning never stands in the way of what the person did."""
    if references:
        _learn_all(mailbox.engine, _gather(mailbox, references), junk)


def learn_from_move(mailbox: Mailbox, source: tuple[str, ...], references: list[MessageRef]) -> None:
    """A move by the person: into Junk teaches junk; out of Junk (not to Trash) teaches good."""
    if not references:
        return
    roles = {folder.path: folder.role for folder in mailbox.store.list_folders()}
    before, after = roles.get(source), roles.get(references[0].folder_path)
    if after is FolderRole.JUNK and before is not FolderRole.JUNK:
        learn(mailbox, references, junk=True)
    elif before is FolderRole.JUNK and after not in (FolderRole.JUNK, FolderRole.TRASH):
        learn(mailbox, references, junk=False)


# --- new mail --------------------------------------------------------------------------------------------------------


class _Trust:
    """The senders the person trusts: their contacts, the people they wrote to, the senders their rules name. Asked only for a
    message that would go to Junk, so a check without junk costs nothing."""

    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox: Mailbox = mailbox
        self.own: frozenset[str] = frozenset(item.account.email_address.casefold() for item in mailbox.engine.config().accounts if item.account.email_address)
        self._contacts: frozenset[str] | None = None
        self._rules: tuple[Rule, ...] | None = None

    def contacts(self) -> frozenset[str]:
        if self._contacts is None:
            try:
                self._contacts = frozenset(item.address for item in contact_addresses(self.mailbox.engine, AddressesParameters()))
            except ChronoMailError:
                self._contacts = frozenset()
        return self._contacts

    def rules(self) -> tuple[Rule, ...]:
        if self._rules is None:
            try:
                rules = load_rules(self.mailbox.engine.directories.rules_file).rules
            except (ConfigurationError, OSError):
                rules = ()
            self._rules = tuple(rule for rule in rules if applies_to(rule, self.mailbox.account_id) and any(c.field == "from" and not c.operator.startswith("not_") for c in rule.conditions))
        return self._rules

    def wrote_to(self, sender: str) -> bool:
        try:
            return self.mailbox.index.wrote_to(self.mailbox.role_folder(FolderRole.SENT), sender)
        except StorageError:
            return False

    def trusted(self, raw: bytes, parsed: ParsedMessage) -> bool:
        sender = parsed.from_addresses[0].address.casefold() if parsed.from_addresses else ""
        if sender and sender not in self.own:  # one's own address as the sender is a common forgery: never a pass
            if sender in self.contacts() or self.wrote_to(sender):
                return True
        subject = Subject(raw, parsed)
        return any(
            rule_matches(rule, subject) and any(c.field == "from" and not c.operator.startswith("not_") and condition_holds(c, subject) for c in rule.conditions)
            for rule in self.rules()
        )


def filter_new(mailbox: Mailbox, references: list[MessageRef]) -> tuple[list[MessageRef], JunkReport]:
    """New mail in the Inbox: junk goes to the Junk folder; the rest is returned for the rules. Never raises: a failure keeps the message
    where it is (the rules then see it as usual)."""
    report = JunkReport()
    engine = mailbox.engine
    if not references or not mailbox.settings.junk_filter:
        return list(references), report
    try:
        junk_folder = mailbox.role_folder(FolderRole.JUNK)
    except StorageError:
        return list(references), report
    trust = _Trust(mailbox)
    kept: list[MessageRef] = []
    taught: list[tuple[str, frozenset[str]]] = []
    for reference in references:
        try:
            raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        except ChronoMailError:
            kept.append(reference)  # the rules report what cannot be read
            continue
        parsed = parse_message(raw)
        found = tokens(parsed)
        by_server = server_says_junk(raw)
        if not by_server:
            with engine.junk.lock:
                words = _loaded(engine)
                score = None if words is None else words.score(found)
            if score is None or score < JUNK_SCORE:
                kept.append(reference)
                continue
        if trust.trusted(raw, parsed):
            kept.append(reference)
            continue
        try:
            mailbox.move(reference, junk_folder)
        except ChronoMailError as error:
            report.failed.append(f"A message could not be moved to the Junk folder: {error}")
            kept.append(reference)
            continue
        report.moved += 1
        if by_server:
            taught.append((fingerprint(raw), found))  # the server's verdict teaches; the filter's own guess never does
    if taught:
        _learn_all(engine, taught, junk=True)
    return kept, report


# --- the first learning of the mail already kept ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JunkLearnResult:
    places: int  # places where mail is kept learned now
    junk: int  # messages learned as junk
    good: int  # messages learned as good


def _existing(engine: Engine, place: str, what: str) -> tuple[list[tuple[str, frozenset[str]]], list[tuple[str, frozenset[str]]]]:
    """The newest junk and the newest read good mail of one place where mail is kept, as fingerprints and words; how far it is, is told
    as what (the status line shows it)."""
    with open_mailbox(engine, place) as mailbox:
        folders = mailbox.store.list_folders()
        junk_rows = [row for folder in folders if folder.role is FolderRole.JUNK for row in mailbox.index.page(folder.path, limit=EXISTING_EACH)][:EXISTING_EACH]
        good_rows = [
            row for folder in folders if folder.role not in NOT_LEARNED_AS_GOOD and folder.path not in (OUTBOX, (SCHEDULED_FOLDER,))
            for row in mailbox.index.page(folder.path, limit=EXISTING_EACH) if MessageFlag.SEEN in row.flags
        ]
        good_rows.sort(key=lambda row: row.date.timestamp() if row.date is not None else 0.0, reverse=True)
        store = mailbox.store
    learned: tuple[list[tuple[str, frozenset[str]]], list[tuple[str, frozenset[str]]]] = ([], [])
    chosen = ((0, junk_rows), (1, good_rows[:EXISTING_EACH]))
    total = sum(len(rows) for _, rows in chosen)
    done = 0
    engine.progress(what, done, total)
    for side, rows in chosen:
        for row in rows:
            if engine.stop_event.is_set():
                return learned
            done += 1
            if done % PROGRESS_STEP == 0:
                engine.progress(what, done, total)
            try:
                raw = store.read(MessageRef(store.account_id, row.folder, row.store_id), hint=row.path)
            except ChronoMailError:
                continue
            learned[side].append((fingerprint(raw), tokens(parse_message(raw))))
    return learned


def junk_learn_existing(engine: Engine, parameters: NoParameters) -> JunkLearnResult:
    """Learn once from the mail already kept, per place where mail is kept: the Junk folders as junk, read mail elsewhere as good."""
    with engine.junk.lock:
        words = _loaded(engine)
        done = None if words is None else set(words.places)
    if done is None:
        return JunkLearnResult(0, 0, 0)
    places = junk = good = 0
    waiting = [place for place in engine.storage_groups() if place not in done]
    for position, place in enumerate(waiting, 1):
        try:
            junk_items, good_items = _existing(engine, place, f"Junk filter: learning from your mail, mailbox {position} of {len(waiting)}")
        except ChronoMailError as error:
            _tell_once(engine, f"The junk filter could not learn from the mail kept: {error}")
            continue
        if engine.stop_event.is_set():
            break  # not marked as learned: the next start goes on
        junk += _learn_all(engine, junk_items, junk=True)
        good += _learn_all(engine, good_items, junk=False, place=place)
        places += 1
    return JunkLearnResult(places, junk, good)


JUNK_LEARN_EXISTING: TypedCommand[NoParameters, JunkLearnResult] = TypedCommand("junk.learn-existing", NoParameters, JunkLearnResult)


def register(registry: CommandRegistry) -> None:
    registry.register(JUNK_LEARN_EXISTING.id, "Let the junk filter learn once from the mail already kept", NoParameters, junk_learn_existing, doing="Junk filter: learning from your mail")
