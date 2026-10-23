import random
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import AccountEditParameters, AccountParameters, NoParameters, account_edit
from chrono_mail.engine.commands_folders import index_rebuild
from chrono_mail.engine.commands_mail import SendParameters, mail_send
from chrono_mail.engine.commands_schedule import SnoozeParameters, message_snooze
from chrono_mail.engine.commands_sidebar import GroupAddParameters, PinParameters, favorite_pin, group_add
from chrono_mail.engine.commands_tags import CreateParameters, tag_create
from chrono_mail.engine.junkfilter import junk_learn_existing
from chrono_mail.store import Store
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.sync.imap_sync import local_names
from tools.demo import texts
from tools.demo.cast import ACCOUNTS, PERSON, DemoAccount
from tools.demo.people import SENDERS, Person, Sender

# --- Version ------------------------------------------------------------
__version__ = "mail.py 2.3.321"

# --- Version history ----------------------------------------------------
# v2.3.321: Initial: the demo's 100,000 messages over five years, spam, marks, tags, archive, snooze, schedule, drafts and Outbox (D-243)

TOTAL_MESSAGES: int = 100_000
SPAM_SHARE: float = 0.055  # of all messages; at least 5,000 at the full size
YEARS: int = 5
YEAR_WEIGHTS: tuple[float, ...] = (0.13, 0.17, 0.2, 0.23, 0.27)  # the oldest year first: mail grows year by year

SHARES: dict[str, float] = {  # how much of the mail each account gets
    "work": 0.27, "personal": 0.13, "bookclub": 0.03, "garden": 0.03, "freelance": 0.08, "alumni": 0.02,
    "shopping": 0.08, "newsletters": 0.13, "family": 0.05, "travel": 0.03, "oldisp": 0.02, "tennis": 0.02, "bills": 0.03, "school": 0.03,
}
CIRCLES_OF: dict[str, tuple[str, ...]] = {  # the people who write to each account
    "work": ("work", "client"), "personal": ("friend", "family"), "bookclub": ("bookclub",), "garden": ("garden",), "freelance": ("freelance",),
    "alumni": ("alumni",), "family": ("family",), "school": ("school",), "tennis": ("tennis",), "oldisp": ("friend",),
}
TAGS: tuple[tuple[str, str], ...] = (("Important", "#e74c3c"), ("Clients", "#3498db"), ("Family", "#2ecc71"), ("Receipts", "#f39c12"),
                                      ("To read", "#9b59b6"), ("Travel", "#1abc9c"))
SIGNATURES: dict[str, str] = {
    "work": "Anna Clark\nSenior designer, Harbour Design Studio\n+44 7700 900123",
    "freelance": "Anna Clark Studio\nDesign for small businesses\nannaclark.example.com",
}
LOCAL_ONLY_FOLDERS: tuple[str, ...] = ("Outbox", "Scheduled", "Recovered", "Snoozed")

Progress = Callable[[str, int, int], None]


@dataclass(slots=True)
class Planned:
    """One message to store: where, what, and what is known about it beyond the message itself."""

    account: str  # DemoAccount.key
    folder: tuple[str, ...]
    raw: bytes
    moment: datetime
    flags: set[MessageFlag] = field(default_factory=set[MessageFlag])
    tags: tuple[str, ...] = ()
    archived_from: str | None = None


def _png(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    """A small picture of one colour, as a real PNG file (made here: no file is read)."""
    row = b"\x00" + bytes(colour) * width
    raw = zlib.compress(row * height)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _pdf(title: str, lines: tuple[str, ...]) -> bytes:
    """A one-page PDF with a few lines of text (made here)."""
    text = "BT /F1 16 Tf 60 760 Td (" + title.replace("(", "").replace(")", "") + ") Tj /F1 11 Tf"
    for line in lines:
        text += " 0 -22 Td (" + line.replace("(", "").replace(")", "") + ") Tj"
    text += " ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text.encode("latin-1", "replace") + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n".encode()
    return out


class MailMaker:
    """Makes the demo's messages, the same ones for the same seed and day."""

    def __init__(self, people: list[Person], today: datetime, total: int, seed: int = 2026) -> None:
        self.random: random.Random = random.Random(seed)
        self.today: datetime = today
        self.total: int = total
        self.people: list[Person] = people
        self.by_circle: dict[str, list[Person]] = {}
        for person in people:
            self.by_circle.setdefault(person.circle, []).append(person)
        self.accounts: dict[str, DemoAccount] = {account.key: account for account in ACCOUNTS}
        self.pictures: tuple[bytes, ...] = tuple(_png(64, 48, colour) for colour in ((84, 140, 196), (214, 140, 72), (96, 168, 112), (188, 96, 120)))

    # --- time ---------------------------------------------------------------------------------------------------------------------

    def moment(self, oldest_years: float = YEARS, newest_years: float = 0.0) -> datetime:
        """A moment in the past, more often in recent years (YEAR_WEIGHTS), at a time of day people write."""
        while True:
            year = self.random.choices(range(YEARS), weights=YEAR_WEIGHTS)[0]  # 0 is the oldest
            back = (YEARS - 1 - year) * 365 + self.random.random() * 365
            if newest_years * 365 <= back <= oldest_years * 365:
                break
        day = self.today - timedelta(days=back)
        hour = self.random.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 4, 8, 12, 14, 14, 12, 10, 12, 13, 12, 11, 9, 8, 8, 7, 5, 3, 2])[0]
        moment = day.replace(hour=hour, minute=self.random.randint(0, 59), second=self.random.randint(0, 59), microsecond=0)
        return min(moment, self.today - timedelta(minutes=self.random.randint(5, 600)))

    # --- one message --------------------------------------------------------------------------------------------------------------

    def message(
        self, sender: tuple[str, str], to: tuple[str, str], subject: str, text: str, moment: datetime, account: DemoAccount,
        html: str | None = None, attachments: tuple[tuple[str, str, bytes], ...] = (), replies_to: str | None = None,
        references: tuple[str, ...] = (), spam: bool = False, server_marked: bool = False, message_id: str | None = None,
    ) -> tuple[bytes, str]:
        """A raw message as it arrived on the account's server, and its Message-ID."""
        mail = EmailMessage(policy=SMTP)
        mail["Received"] = (f"from mx.{sender[1].rpartition('@')[2]} by {account.incoming_host} with ESMTPS; "
                            f"{format_datetime(moment + timedelta(seconds=self.random.randint(2, 40)))}")
        mail["From"] = f"{sender[0]} <{sender[1]}>" if sender[0] else sender[1]
        mail["To"] = f"{to[0]} <{to[1]}>"
        mail["Subject"] = subject
        mail["Date"] = format_datetime(moment)
        identifier = message_id or make_msgid(idstring=f"{self.random.getrandbits(48):012x}", domain=sender[1].rpartition("@")[2])
        mail["Message-ID"] = identifier
        if replies_to:
            mail["In-Reply-To"] = replies_to
            mail["References"] = " ".join((*references, replies_to))
        if spam:
            if server_marked:
                mail["X-Spam-Flag"] = "YES"
                mail["X-Spam-Status"] = f"Yes, score={self.random.randint(60, 190) / 10:.1f} required=5.0"
            else:
                mail["X-Spam-Status"] = f"No, score={self.random.randint(5, 45) / 10:.1f} required=5.0"
        mail.set_content(text)
        if html is not None:
            mail.add_alternative(html, subtype="html")
        for name, kind, data in attachments:
            maintype, _, subtype = kind.partition("/")
            mail.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        return mail.as_bytes(), identifier

    def _attachments(self, circle: str) -> tuple[tuple[str, str, bytes], ...]:
        roll = self.random.random()
        if circle in ("family", "friend") and roll < 0.08:
            return tuple((f"IMG_{self.random.randint(1000, 9999)}.png", "image/png", self.random.choice(self.pictures)) for _ in range(self.random.randint(1, 3)))
        if circle in ("work", "client", "freelance") and roll < 0.07:
            name = self.random.choice(("brief", "proof", "notes", "timeline", "quote"))
            return ((f"{name}.pdf", "application/pdf", _pdf(name.capitalize(), (texts.fill("For {project}", self.random), "Harbour Design Studio"))),)
        if circle in ("work", "client") and roll < 0.09:
            return (("budget.csv", "text/csv", b"Item,Hours,Cost\nDesign,12,960\nPrint,0,340\nTotal,12,1300\n"),)
        if circle == "school" and roll < 0.3:
            return (("letter.pdf", "application/pdf", _pdf("Letter to parents", ("Westbrook Primary School", "Please read and return the slip."))),)
        return ()

    # --- the mail of a person ----------------------------------------------------------------------------------------------------

    def conversation(self, account: DemoAccount, person: Person, start: datetime) -> list[Planned]:
        """One conversation: a person writes, the person of the demo may answer, and so on. Answers go to Sent."""
        anna = (PERSON, account.address)
        subject = texts.subject(person.circle, self.random)
        signature = SIGNATURES.get(account.key, "")
        planned: list[Planned] = []
        text = texts.body(person.circle, person.name, self.random)
        raw, identifier = self.message((person.name, person.address), anna, subject, text, start, account, attachments=self._attachments(person.circle))
        first = Planned(account.key, ("Inbox",), raw, start, tags=self._tags(person.circle))
        planned.append(first)
        references: tuple[str, ...] = ()
        moment, last_text, last_id, last_incoming = start, text, identifier, first
        for turn in range(self.random.choices((0, 1, 2, 3, 4), weights=(45, 30, 13, 8, 4))[0]):
            moment = moment + timedelta(minutes=self.random.randint(20, 60 * 30))
            if moment >= self.today:
                break
            if turn % 2 == 0:
                answer = texts.reply_body(self.random, person.name, last_text, signature)
                raw, new_id = self.message(anna, (person.name, person.address), f"Re: {subject}", answer, moment, account,
                                           replies_to=last_id, references=references)
                planned.append(Planned(account.key, ("Sent",), raw, moment, {MessageFlag.SEEN}))
                last_incoming.flags.add(MessageFlag.REPLIED)
            else:
                answer = texts.body(person.circle, person.name, self.random)
                raw, new_id = self.message((person.name, person.address), anna, f"Re: {subject}", answer, moment, account,
                                           replies_to=last_id, references=references)
                last_incoming = Planned(account.key, ("Inbox",), raw, moment, tags=first.tags)
                planned.append(last_incoming)
            references = (*references, last_id)
            last_text, last_id = answer, new_id
        return planned

    def _tags(self, circle: str) -> tuple[str, ...]:
        roll = self.random.random()
        if circle == "client" and roll < 0.18:
            return ("Clients",)
        if circle == "family" and roll < 0.12:
            return ("Family",)
        if roll < 0.012:
            return ("Important",)
        return ()

    def from_sender(self, account: DemoAccount, sender: Sender, moment: datetime) -> Planned:
        anna = (PERSON, account.address)
        number = self.random.randint(100000, 999999)
        attachments: tuple[tuple[str, str, bytes], ...] = ()
        html: str | None = None
        tags: tuple[str, ...] = ()
        if sender.kind == "receipt":
            item, amount = self.random.choice(texts.FILL["item"]), self.random.choice(texts.FILL["amount"])
            subject = f"Your order {number} is confirmed"
            text = f"Hello Anna,\n\nThank you for your order.\n\nOrder {number}\n{item.capitalize()}: {amount}\n\nWe'll let you know when it is on its way.\n\n{sender.name}\n"
            tags = ("Receipts",) if self.random.random() < 0.3 else ()
        elif sender.kind == "update":
            subject = f"Your parcel {number} is on its way"
            text = f"Hello Anna,\n\nGood news: your parcel {number} will arrive {self.random.choice(texts.FILL['day'])} between 9:00 and 13:00.\n\n{sender.name}\n"
        elif sender.kind == "bill":
            amount, month = self.random.choice(texts.FILL["amount"]), moment.strftime("%B %Y")
            subject = f"Your {sender.name} bill for {month}"
            text = f"Dear Anna Clark,\n\nYour bill for {month} is ready: {amount}.\nIt will be collected by direct debit on the 1st.\n\nAccount number {number}\n\n{sender.name}\n"
            attachments = ((f"bill-{moment:%Y-%m}.pdf", "application/pdf", _pdf(f"{sender.name}", (f"Bill for {month}", f"Amount due: {amount}", f"Account {number}"))),)
            tags = ("Receipts",) if self.random.random() < 0.4 else ()
        elif sender.kind == "booking":
            place = self.random.choice(texts.FILL["destination"])
            subject = f"Booking confirmation: {place}"
            text = f"Dear Anna Clark,\n\nYour booking {number} to {place} is confirmed.\nYour tickets are attached.\n\nHave a good trip!\n{sender.name}\n"
            attachments = ((f"booking-{number}.pdf", "application/pdf", _pdf(f"Booking {number}", (f"Destination: {place}", "Passenger: Anna Clark"))),)
            tags = ("Travel",) if self.random.random() < 0.6 else ()
        elif sender.kind == "newsletter":
            topic = self.random.choice(("This week's picks", "News and events", "What's new", "Our favourites this month", "Ideas for the weekend"))
            subject = f"{sender.name}: {topic}"
            text = f"{sender.name}\n{topic}\n\nRead this newsletter in a program that shows HTML.\n\nUnsubscribe at any time.\n"
            html = (f"<html><body style='font-family:sans-serif;background:#f4f1ea;margin:0;padding:24px'>"
                    f"<div style='max-width:560px;margin:auto;background:#ffffff;padding:24px;border-radius:8px'>"
                    f"<h1 style='color:#2c5f6f;margin-top:0'>{sender.name}</h1><h2 style='color:#555'>{topic}</h2>"
                    + "".join(f"<p>{texts.fill(line, self.random)}</p>" for line in self.random.sample(texts.SENTENCES["friend"] + texts.SENTENCES["garden"], 3))
                    + f"<p style='font-size:12px;color:#999'>You receive this because you subscribed with {account.address}.</p></div></body></html>")
            tags = ("To read",) if self.random.random() < 0.08 else ()
        else:
            subject = self.random.choice(texts.SUBJECTS.get(account.key, texts.SUBJECTS["school"]))
            subject = texts.fill(subject, self.random)
            text = texts.body(account.key if account.key in texts.SENTENCES else "school", sender.name, self.random)
        raw, _ = self.message((sender.name, sender.address), anna, subject, text, moment, account, html=html, attachments=attachments)
        return Planned(account.key, ("Inbox",), raw, moment, tags=tags)

    def spam(self, account: DemoAccount, moment: datetime) -> Planned:
        server_marked = self.random.random() < 0.6
        subject = self.random.choice(texts.SPAM_SUBJECTS)
        if server_marked and self.random.random() < 0.4:
            subject = "***SPAM*** " + subject
        domain = self.random.choice(("deals", "prize", "secure-login", "offers", "winner", "bestprice")) + f"{self.random.randint(1, 99)}.example.net"
        text = "\n\n".join(self.random.sample(texts.SPAM_LINES, 3)) + "\n"
        html = f"<html><body><h2 style='color:#d00'>{subject}</h2>" + "".join(f"<p>{line}</p>" for line in text.split("\n\n")) + "</body></html>"
        raw, _ = self.message(("", f"no-reply@{domain}"), (PERSON, account.address), subject, text, moment, account, html=html, spam=True, server_marked=server_marked)
        return Planned(account.key, ("Junk",), raw, moment, {MessageFlag.SEEN} if self.random.random() < 0.8 else set[MessageFlag]())

    # --- all of it ----------------------------------------------------------------------------------------------------------------

    def plan(self) -> list[Planned]:
        """Every message of the demo, before it is put in its folder."""
        planned: list[Planned] = []
        good = round(self.total * (1 - SPAM_SHARE))
        for key, share in SHARES.items():
            account = self.accounts[key]
            wanted = round(good * share / sum(SHARES.values()))  # the shares are weights: together they make all the good mail
            mine: list[Planned] = []
            senders = [sender for sender in SENDERS if sender.account == key]
            people = [person for circle in CIRCLES_OF.get(key, ()) for person in self.by_circle.get(circle, [])]
            oldest = YEARS
            newest = 3.0 if key == "oldisp" else 0.0  # the old address was given up three years ago
            while len(mine) < wanted:
                start = self.moment(oldest, newest)
                if people and (not senders or self.random.random() < 0.75):
                    mine.extend(self.conversation(account, self.random.choice(people), start))
                else:
                    mine.append(self.from_sender(account, self.random.choice(senders), start))
            planned.extend(mine[:wanted])
        spam_total = self.total - len(planned)
        junk_accounts = [account for account in ACCOUNTS if account.key != "oldisp"]
        for number in range(spam_total):
            planned.append(self.spam(junk_accounts[number % len(junk_accounts)], self.moment()))
        return planned

    def place(self, planned: list[Planned]) -> None:
        """Put every message where the person would have it now: recent mail in the Inbox, older mail in folders and the Archive, a few in
        Trash, the mail of the Local Folders accounts in their folder there (as their rule put it); and set what was read and flagged."""
        newest_inbox: dict[str, int] = {}
        for item in sorted(planned, key=lambda entry: entry.moment, reverse=True):
            account = self.accounts[item.account]
            age = (self.today - item.moment).days
            if item.folder == ("Inbox",):
                if account.storage == "local":
                    seen = newest_inbox.get(account.key, 0)
                    newest_inbox[account.key] = seen + 1
                    if seen >= 3:
                        item.folder = (account.local_folder,)
                elif age > 45:
                    roll = self.random.random()
                    if roll < 0.35 and account.folders:
                        item.folder = tuple(self._topic_folder(account, item).split("/"))
                    elif roll < 0.6:
                        item.folder, item.archived_from = ("Archive",), "Inbox"
                elif age < 20 and self.random.random() < 0.02:
                    item.folder = ("Trash",)
                if age > 10 or self.random.random() < 0.55:
                    item.flags.add(MessageFlag.SEEN)
                if self.random.random() < 0.02:
                    item.flags.add(MessageFlag.FLAGGED)
            elif item.folder == ("Junk",) and account.storage == "local":
                pass  # Local Folders has one Junk for all its accounts
        for item in planned:
            if item.folder != ("Sent",) and MessageFlag.SEEN not in item.flags and (self.today - item.moment).days > 30 and self.random.random() < 0.9:
                item.flags.add(MessageFlag.SEEN)  # nearly everything old has been read

    def unsent(self) -> list[Planned]:
        """The drafts the person is still writing, and a reply that waits in the Outbox (its send failed)."""
        planned: list[Planned] = []
        drafts = (("work", "Notes for the Bluebird call", "Points to raise:\n\n1. Colours of the new boxes\n2. Print run\n3. Dates\n"),
                  ("personal", "Packing list", "Passports, chargers, sun cream, the good camera...\n"),
                  ("freelance", "Quote for Kestrel Cycles", "Hello,\n\nThank you for your message. For the new logo and business cards I'd suggest\n"))
        for key, subject, text in drafts:
            account = self.accounts[key]
            moment = self.today - timedelta(hours=self.random.randint(2, 80))
            mail = EmailMessage(policy=SMTP)
            mail["From"] = f"{PERSON} <{account.address}>"
            mail["Subject"] = subject
            mail["Date"] = format_datetime(moment)
            mail["Message-ID"] = make_msgid(domain=account.address.rpartition("@")[2])
            mail.set_content(text)
            planned.append(Planned(key, ("Drafts",), mail.as_bytes(), moment, {MessageFlag.DRAFT, MessageFlag.SEEN}))
        moment = self.today - timedelta(hours=3)
        outbox = EmailMessage(policy=SMTP)
        outbox["From"] = f"{PERSON} <studio@annaclark.example.com>"
        outbox["To"] = "Oliver Bennett <oliver.bennett@example.com>"
        outbox["Subject"] = "Re: Business cards"
        outbox["Date"] = format_datetime(moment)
        outbox["Message-ID"] = make_msgid(domain="annaclark.example.com")
        outbox.set_content("Hi Oliver,\n\nThe proofs look good, I'll send them to the printer tomorrow.\n\nAnna\n")
        planned.append(Planned("freelance", ("Outbox",), outbox.as_bytes(), moment, {MessageFlag.SEEN}))
        return planned

    def _topic_folder(self, account: DemoAccount, item: Planned) -> str:
        folders = list(account.folders)
        leaves = [folder for folder in folders if not any(other.startswith(folder + "/") for other in folders)]
        return self.random.choice(leaves)


def _place_key(account: DemoAccount, account_ids: dict[str, str]) -> str:
    """The mailbox a demo account's mail is kept in: its own, or Local Folders."""
    return LOCAL_ACCOUNT_ID if account.storage == "local" else account_ids[account.key]


def store_mail(engine: Engine, account_ids: dict[str, str], planned: list[Planned], progress: Progress) -> dict[str, list[tuple[Planned, str]]]:
    """Write every message to its mailbox, encrypted, in large batches (made safe on disk once per batch), with its tags and archive
    origin (when it came is in its Received line, as on a real server). Returns per mailbox the messages and their store ids."""
    stored: dict[str, list[tuple[Planned, str]]] = {}
    by_place: dict[str, list[Planned]] = {}
    accounts = {account.key: account for account in ACCOUNTS}
    for item in planned:
        by_place.setdefault(_place_key(accounts[item.account], account_ids), []).append(item)
    done = 0
    for place, items in by_place.items():
        settings = engine.storage_settings(engine.resolve_account(place))
        store: Store = engine.open_store(settings)
        known = {folder.path for folder in store.list_folders()}
        for item in items:
            if item.folder not in known:
                store.ensure_folder(item.folder)
                known.add(item.folder)
        mine = stored.setdefault(place, [])
        for start in range(0, len(items), 2000):
            with store.written_together():
                for item in items[start:start + 2000]:
                    reference = store.deliver(item.folder, item.raw, frozenset(item.flags), verify=False)
                    if item.tags or item.archived_from:
                        store.properties.change(reference.store_id, lambda now, entry=item: replace(now, tags=entry.tags, archived_from=entry.archived_from))
                    mine.append((item, reference.store_id))
                    done += 1
                    if done % 1000 == 0:
                        progress("Messages", done, len(planned))
    if done % 1000:
        progress("Messages", done, len(planned))
    return stored


def record_imap(engine: Engine, account_ids: dict[str, str], stored: dict[str, list[tuple[Planned, str]]]) -> None:
    """For the IMAP accounts: every message known on the server, with a UID and the marks there, as after a synchronisation."""
    for account in ACCOUNTS:
        if account.kind != "imap":
            continue
        account_id = account_ids[account.key]
        state = ImapState(engine.directories.imap_state_file(account_id), durable=False)
        next_uid: dict[tuple[str, ...], int] = {}
        for item, store_id in sorted(stored.get(account_id, []), key=lambda pair: pair[0].moment):
            if item.folder[0] in LOCAL_ONLY_FOLDERS:
                continue
            uid = next_uid.get(item.folder, 0) + 1
            next_uid[item.folder] = uid
            state.map(item.folder, uid, store_id, local_names(frozenset(item.flags), (), False))  # as the program compares them with the server


def finishing_touches(engine: Engine, account_ids: dict[str, str], stored: dict[str, list[tuple[Planned, str]]], today: datetime) -> None:
    """What a person does over the years beyond receiving: tags with colours, signatures, a group of accounts, pinned folders, snoozed mail,
    scheduled and repeating messages."""
    for name, colour in TAGS:
        tag_create(engine, CreateParameters(name, colour))
    for key, signature in SIGNATURES.items():
        account_edit(engine, AccountEditParameters(account_ids[key], signature=signature))
    group_add(engine, GroupAddParameters("Clubs", (account_ids["bookclub"], account_ids["garden"])))
    favorite_pin(engine, PinParameters(account_ids["work"], "Clients/Bluebird Bakery"))
    favorite_pin(engine, PinParameters(account_ids["personal"], "Holidays"))
    local = [(item, store_id) for item, store_id in stored.get(LOCAL_ACCOUNT_ID, []) if item.folder != ("Junk",) and item.folder[0] not in ("Sent", "Trash")]
    local.sort(key=lambda pair: pair[0].moment, reverse=True)
    for days, (item, store_id) in zip((1, 2, 4, 7, 14), local[3:8], strict=False):
        until = (today + timedelta(days=days)).replace(hour=9, minute=0)
        message_snooze(engine, SnoozeParameters(LOCAL_ACCOUNT_ID, "/".join(item.folder), store_id, until.strftime("%Y-%m-%dT%H:%M")))
    freelance = account_ids["freelance"]
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1, hour=9, minute=0)
    mail_send(engine, SendParameters(freelance, ("accounts@bluebirdbakery.example.com",), "Monthly invoice", "Hello,\n\nPlease find this month's invoice attached.\n\nKind regards,\nAnna\n",
                                     send_at=next_month.strftime("%Y-%m-%dT%H:%M"), repeat_months=1))
    birthday = (today + timedelta(days=9)).replace(hour=8, minute=0)
    mail_send(engine, SendParameters(account_ids["personal"], ("jenny.clark@example.org",), "Happy birthday!", "Happy birthday, Jenny! Have a wonderful day.\n\nLove,\nAnna\n",
                                     send_at=birthday.strftime("%Y-%m-%dT%H:%M")))
    mail_send(engine, SendParameters(account_ids["garden"], ("volunteers@greenstreet.example.org",), "Work party this Saturday",
                                     "Hi all,\n\nA reminder: work party this Saturday from 10:00. Bring gloves!\n\nAnna\n",
                                     send_at=(today + timedelta(days=2)).replace(hour=18, minute=0).strftime("%Y-%m-%dT%H:%M"), repeat_weeks=2))


def build_mail(engine: Engine, account_ids: dict[str, str], people: list[Person], today: datetime, total: int, progress: Progress) -> int:
    """All of the demo's mail. Returns how many messages came in and were sent over the years (the drafts, the Outbox and the scheduled
    messages come on top)."""
    maker = MailMaker(people, today, total)
    planned = maker.plan()
    maker.place(planned)
    received = len(planned)
    planned.extend(maker.unsent())
    stored = store_mail(engine, account_ids, planned, progress)
    record_imap(engine, account_ids, stored)
    places = list(stored)
    for number, place in enumerate(places, start=1):
        progress("Search index", number, len(places))
        index_rebuild(engine, AccountParameters(place))
    finishing_touches(engine, account_ids, stored, today)
    junk_learn_existing(engine, NoParameters())
    return received
