import random
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.crypto import DataKey
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_pim import ColourParameters, tag_colour
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.pimstore import PimStore
from chrono_mail.sync.davsync import DavState, ItemLink, digest_of
from tools.demo import texts
from tools.demo.mail import Progress
from tools.demo.people import Person

# --- Version ------------------------------------------------------------
__version__ = "pim.py 2.3.321"

# --- Version history ----------------------------------------------------
# v2.3.321: Initial: the demo's calendars (500 entries, five years back to one ahead), tasks, contacts with groups, tags (D-243)

TOTAL_EVENTS: int = 500
TOTAL_TASKS: int = 100
TOTAL_CONTACTS: int = 100
PIM_TAGS: tuple[tuple[str, str], ...] = (("Important", "#e74c3c"), ("Family", "#2ecc71"), ("Clients", "#3498db"), ("Travel", "#1abc9c"),
                                          ("Health", "#e67e22"), ("Errands", "#95a5a6"))
DAV_SERVERS: dict[str, str] = {  # the calendar server of the IMAP accounts that have one (none of them exists)
    "work": "https://dav.harbourdesign.example.com/",
    "personal": "https://dav.example.org/",
    "bookclub": "https://dav.example.net/",
}
HOLIDAYS: tuple[tuple[int, int, str], ...] = ((1, 1, "New Year's Day"), (2, 14, "Valentine's Day"), (3, 17, "St Patrick's Day"), (5, 1, "May Day"),
                                              (6, 21, "Midsummer"), (10, 31, "Halloween"), (11, 5, "Bonfire Night"), (12, 24, "Christmas Eve"),
                                              (12, 25, "Christmas Day"), (12, 26, "Boxing Day"), (12, 31, "New Year's Eve"))
ONE_OFF: dict[str, tuple[tuple[str, str, int], ...]] = {  # calendar -> (summary, location, minutes)
    "Home": (("Dentist", "Riverbend Dental", 45), ("Haircut", "Snip & Style", 60), ("Dinner with the Hughes", "Rosa's", 150),
             ("Parents' evening", "Westbrook Primary School", 60), ("Car service", "Main Street Garage", 90), ("Yoga", "Community hall", 60),
             ("Cinema", "Harbour Lights Cinema", 150), ("Lily's swimming lesson", "Leisure centre", 45), ("Sam's football", "Park pitches", 90),
             ("Coffee with Jenny", "The café on Mill Lane", 60), ("Plumber", "", 120), ("Doctor", "Health centre", 30)),
    "Work": (("Client call: {client}", "Video call", 45), ("Design review", "Studio, meeting room", 60), ("Presentation for {client}", "{client}", 90),
             ("Print check", "Printworks", 120), ("Workshop: {project}", "Studio", 180), ("Lunch with {client}", "Rosa's", 90),
             ("Photo shoot", "Studio", 240), ("Planning", "Studio, meeting room", 60)),
    "Family": (("Sunday lunch at Mum's", "", 180), ("School play", "Westbrook Primary School", 90), ("Birthday party", "", 180),
               ("Zoo trip", "City Zoo", 300), ("Beach day", "Saltmarsh beach", 360)),
    "Book club": (("Book club: {book}", "{place}", 120),),
}
TASKS: tuple[tuple[str, str], ...] = (  # summary, list
    ("Renew car insurance", "Personal"), ("Book dentist for the kids", "Personal"), ("Order birthday present for Jenny", "Personal"),
    ("Pay council tax", "Personal"), ("Fix the garden gate", "Personal"), ("Return library books", "Personal"), ("Plan summer holiday", "Personal"),
    ("Clear out the garage", "Personal"), ("Send thank-you cards", "Personal"), ("Buy seeds for the plot", "Personal"), ("Back up the photos", "Personal"),
    ("Book flights to {destination}", "Personal"), ("Call the bank", "Personal"), ("Sort the school uniform", "Personal"), ("Water the plants", "Personal"),
    ("Send invoice to {client}", "Studio tasks"), ("Finish the sketches for {project}", "Studio tasks"), ("Order the font licences", "Studio tasks"),
    ("Prepare the {client} presentation", "Studio tasks"), ("Check {thing}", "Studio tasks"), ("Update the portfolio", "Studio tasks"),
    ("Write the brief for {project}", "Studio tasks"), ("Archive last year's projects", "Studio tasks"), ("Ask for print quotes", "Studio tasks"),
)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _calendar(component: str) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Chrono Mail//Chrono Mail//EN\r\n{component}END:VCALENDAR\r\n"


def _event(uid: str, summary: str, start: datetime | date, end: datetime | date, created: datetime, location: str = "", rule: str = "",
           categories: tuple[str, ...] = (), alarm: int = 0, description: str = "") -> str:
    lines = [f"UID:{uid}", f"DTSTAMP:{_stamp(created)}", f"CREATED:{_stamp(created)}", f"SUMMARY:{summary}"]
    if isinstance(start, datetime) and isinstance(end, datetime):
        lines += [f"DTSTART:{_stamp(start)}", f"DTEND:{_stamp(end)}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}", "TRANSP:TRANSPARENT"]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append("DESCRIPTION:" + description.replace("\n", "\\n"))
    if rule:
        lines.append(f"RRULE:{rule}")
    if categories:
        lines.append("CATEGORIES:" + ",".join(categories))
    body = "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\n"
    if alarm:
        body += f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:{summary}\r\nTRIGGER:-PT{alarm}M\r\nEND:VALARM\r\n"
    return _calendar(body + "END:VEVENT\r\n")


def _task(uid: str, summary: str, due: date, created: datetime, done: datetime | None, priority: int, categories: tuple[str, ...], percent: int) -> str:
    lines = [f"UID:{uid}", f"DTSTAMP:{_stamp(created)}", f"CREATED:{_stamp(created)}", f"SUMMARY:{summary}", f"DUE;VALUE=DATE:{due:%Y%m%d}"]
    if done is not None:
        lines += ["STATUS:COMPLETED", f"COMPLETED:{_stamp(done)}", "PERCENT-COMPLETE:100"]
    else:
        lines += ["STATUS:NEEDS-ACTION" if percent == 0 else "STATUS:IN-PROCESS", f"PERCENT-COMPLETE:{percent}"]
    if priority:
        lines.append(f"PRIORITY:{priority}")
    if categories:
        lines.append("CATEGORIES:" + ",".join(categories))
    return _calendar("BEGIN:VTODO\r\n" + "\r\n".join(lines) + "\r\nEND:VTODO\r\n")


def _card(uid: str, person: Person, categories: tuple[str, ...], street: str) -> str:
    first, _, last = person.name.partition(" ")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}", f"FN:{person.name}", f"N:{last};{first};;;", f"EMAIL;TYPE=INTERNET:{person.address}"]
    if person.phone:
        lines.append(f"TEL;TYPE=CELL:{person.phone}")
    if person.company:
        lines.append(f"ORG:{person.company}")
    if person.title:
        lines.append(f"TITLE:{person.title.capitalize()}")
    if person.birthday is not None:
        year, month, day = person.birthday
        lines.append(f"BDAY:{year:04d}-{month:02d}-{day:02d}")
    if street:
        lines.append(f"ADR;TYPE=HOME:;;{street};Harbourton;;HB1 2AB;United Kingdom")
    if categories:
        lines.append("CATEGORIES:" + ",".join(categories))
    return "\r\n".join(lines) + "\r\nEND:VCARD\r\n"


def _group(uid: str, name: str, members: list[str]) -> str:
    lines = ["BEGIN:VCARD", "VERSION:4.0", f"UID:{uid}", "KIND:group", f"FN:{name}", f"N:{name};;;;"]
    lines += [f"MEMBER:urn:uuid:{member}" for member in members]
    return "\r\n".join(lines) + "\r\nEND:VCARD\r\n"


class PimMaker:
    def __init__(self, engine: Engine, account_ids: dict[str, str], people: list[Person], today: datetime, seed: int = 2027) -> None:
        self.engine: Engine = engine
        self.account_ids: dict[str, str] = account_ids
        self.people: list[Person] = people
        self.today: datetime = today
        self.random: random.Random = random.Random(seed)
        self.collections: dict[str, tuple[str, str]] = {}  # name -> (account id, collection id)
        self.links: dict[str, dict[str, list[tuple[str, str]]]] = {}  # account id -> collection id -> (item id, text) of server collections
        self.events: int = 0

    def _uid(self) -> str:
        return str(uuid.UUID(int=self.random.getrandbits(128), version=4))

    def _collection(self, store: PimStore, account_id: str, kind: CollectionKind, name: str, colour: str, href: str = "", read_only: bool = False) -> str:
        component = {CollectionKind.EVENTS: "VEVENT", CollectionKind.TASKS: "VTODO", CollectionKind.CONTACTS: ""}[kind]
        server = {"href": href, "component": component, "followed": "1", "public": ""} if href else None
        info = store.create_collection(kind, name, colour, read_only, server)
        if href:
            self.links.setdefault(account_id, {})[info.collection_id] = []
        self.collections[f"{account_id}/{name}"] = (account_id, info.collection_id)
        return info.collection_id

    def _put(self, store: PimStore, account_id: str, collection_id: str, text: str) -> str:
        item_id = store.put(collection_id, text, verify=False)
        if collection_id in self.links.get(account_id, {}):
            self.links[account_id][collection_id].append((item_id, text))
        return item_id

    def collections_of_every_account(self) -> None:
        """The calendar servers of three IMAP accounts (that do not exist), and every calendar, task list and address book."""
        for key, url in DAV_SERVERS.items():
            account_id = self.account_ids[key]
            self.engine.change_config(lambda config, account_id=account_id, url=url: config.with_account(replace(config.find(account_id), dav_url=url)))
        ensure_local_folders(self.engine)

    def build(self, progress: Progress) -> tuple[int, int, int]:
        self.collections_of_every_account()
        work, personal, bookclub = self.account_ids["work"], self.account_ids["personal"], self.account_ids["bookclub"]
        stores: dict[str, PimStore] = {}
        for account_id in (LOCAL_ACCOUNT_ID, work, personal, bookclub):
            with open_pim(self.engine, account_id, create=True):
                pass
        calendars: dict[str, tuple[str, str]] = {}
        try:
            for account_id in (LOCAL_ACCOUNT_ID, work, personal, bookclub):
                stores[account_id] = PimStore.open(self.engine.directories.pim_directory(account_id), self._key(), durable=False)
            local, work_store, personal_store, bookclub_store = stores[LOCAL_ACCOUNT_ID], stores[work], stores[personal], stores[bookclub]
            calendars["Home"] = (LOCAL_ACCOUNT_ID, self._collection(local, LOCAL_ACCOUNT_ID, CollectionKind.EVENTS, "Home", "#3498db"))
            calendars["Birthdays"] = (LOCAL_ACCOUNT_ID, self._collection(local, LOCAL_ACCOUNT_ID, CollectionKind.EVENTS, "Birthdays", "#e91e63"))
            calendars["Holidays"] = (LOCAL_ACCOUNT_ID, self._collection(local, LOCAL_ACCOUNT_ID, CollectionKind.EVENTS, "Holidays", "#ff9800"))
            calendars["Work"] = (work, self._collection(work_store, work, CollectionKind.EVENTS, "Work", "#2c7be5", "/calendars/anna.clark/work/"))
            calendars["Family"] = (personal, self._collection(personal_store, personal, CollectionKind.EVENTS, "Family", "#27ae60", "/calendars/anna.clark/family/"))
            calendars["Book club"] = (bookclub, self._collection(bookclub_store, bookclub, CollectionKind.EVENTS, "Book club", "#8e44ad", "/calendars/riverside/meetings/"))
            shared = [(work, self._collection(work_store, work, CollectionKind.EVENTS, "Team holidays (Mark Ellis)", "#7f8c8d", "/calendars/mark.ellis/holidays/", read_only=True)),
                      (personal, self._collection(personal_store, personal, CollectionKind.EVENTS, "Team holidays (Mark Ellis)", "#7f8c8d", "/calendars/mark.ellis/holidays/", read_only=True))]
            events = self._events(stores, calendars, shared, progress)
            tasks = self._tasks(local, work_store, work)
            contacts = self._contacts(local, work_store, work)
        finally:
            for store in stores.values():
                store.close()
        self._agreed_with_servers()
        for name, colour in PIM_TAGS:
            tag_colour(self.engine, ColourParameters(name, colour))
        return events, tasks, contacts

    def _key(self) -> DataKey | None:
        config = self.engine.config()
        return self.engine.data_key(config) if config.key_id is not None else None

    def _events(self, stores: dict[str, PimStore], calendars: dict[str, tuple[str, str]], shared: list[tuple[str, str]], progress: Progress) -> int:
        count = 0
        start_day = (self.today - timedelta(days=5 * 365)).replace(hour=0, minute=0, second=0, microsecond=0)
        created = start_day - timedelta(days=30)

        def put(calendar: str, text: str) -> None:
            nonlocal count
            account_id, collection_id = calendars[calendar]
            self._put(stores[account_id], account_id, collection_id, text)
            count += 1

        for person in self.people:  # birthdays: every year
            if person.birthday is not None and count < 40:
                year, month, day = person.birthday
                first = date(start_day.year, month, day)
                put("Birthdays", _event(self._uid(), f"{person.name}'s birthday", first, first + timedelta(days=1), created, rule="FREQ=YEARLY",
                                        categories=("Family",) if person.circle == "family" else (), alarm=60 * 24))
        for month, day, name in HOLIDAYS:
            first = date(start_day.year, month, day)
            put("Holidays", _event(self._uid(), name, first, first + timedelta(days=1), created, rule="FREQ=YEARLY"))
        monday = start_day + timedelta(days=(7 - start_day.weekday()) % 7)
        put("Work", _event(self._uid(), "Studio meeting", monday.replace(hour=9, minute=30), monday.replace(hour=10, minute=15), created, "Studio, meeting room",
                           "FREQ=WEEKLY;BYDAY=MO", alarm=10))
        thursday = start_day + timedelta(days=(3 - start_day.weekday()) % 7)
        put("Home", _event(self._uid(), "Tennis doubles", thursday.replace(hour=19), thursday.replace(hour=20, minute=30), created, "Riverside Tennis Club",
                           "FREQ=WEEKLY;BYDAY=TH"))
        put("Book club", _event(self._uid(), "Book club", start_day.replace(day=1, hour=19, minute=30), start_day.replace(day=1, hour=21, minute=30), created,
                                "The library", "FREQ=MONTHLY;BYDAY=2WE", alarm=60))
        put("Home", _event(self._uid(), "Bins out", start_day.replace(hour=20), start_day.replace(hour=20, minute=15), created, rule="FREQ=WEEKLY;BYDAY=TU",
                           categories=("Errands",)))
        for account_id, collection_id in shared:  # the colleague's holidays, the same calendar under two accounts
            generator = random.Random(77)
            for number in range(8):
                begin = (self.today - timedelta(days=330) + timedelta(days=number * 55 + generator.randint(0, 20))).date()
                text = _event(f"mark-ellis-holiday-{number}", "Mark away", begin, begin + timedelta(days=generator.randint(2, 10)), created)
                self._put(stores[account_id], account_id, collection_id, text)
        count += 8
        span = 6 * 365
        while count < TOTAL_EVENTS:
            calendar = self.random.choices(("Home", "Work", "Family", "Book club"), weights=(40, 40, 15, 5))[0]
            summary, location, minutes = self.random.choice(ONE_OFF[calendar])
            day = start_day + timedelta(days=self.random.randint(0, span))
            if calendar == "Work" and day.weekday() >= 5:
                continue
            begin = day.replace(hour=self.random.choice((8, 9, 10, 11, 13, 14, 15, 16, 18, 19)), minute=self.random.choice((0, 15, 30, 45)))
            categories = self.random.choice(((), (), (), ("Important",), ("Clients",) if calendar == "Work" else ("Family",), ("Health",) if summary in ("Dentist", "Doctor") else ()))
            put(calendar, _event(self._uid(), texts.fill(summary, self.random), begin, begin + timedelta(minutes=minutes), created,
                                 texts.fill(location, self.random), categories=categories, alarm=self.random.choice((0, 0, 15, 30, 60))))
            if count % 50 == 0:
                progress("Calendar entries", count, TOTAL_EVENTS)
        for _ in range(6):  # trips of several days
            begin = (self.today - timedelta(days=self.random.randint(-300, 1700))).date()
            put("Family", _event(self._uid(), f"Holiday in {self.random.choice(texts.FILL['destination'])}", begin, begin + timedelta(days=self.random.randint(4, 12)),
                                 created, categories=("Travel",)))
        progress("Calendar entries", count, count)
        return count

    def _tasks(self, local: PimStore, work_store: PimStore, work: str) -> int:
        personal_list = self._collection(local, LOCAL_ACCOUNT_ID, CollectionKind.TASKS, "Personal", "#16a085")
        studio_list = self._collection(work_store, work, CollectionKind.TASKS, "Studio tasks", "#2c7be5", "/calendars/anna.clark/tasks/")
        for number in range(TOTAL_TASKS):
            summary, where = self.random.choice(TASKS)
            created = self.today - timedelta(days=self.random.randint(1, 700))
            due = (created + timedelta(days=self.random.randint(1, 90))).date()
            done = None
            if due < self.today.date() and self.random.random() < 0.8:
                done = datetime.combine(due, datetime.min.time(), UTC) - timedelta(days=self.random.randint(0, 5))
            percent = 0 if done else self.random.choice((0, 0, 0, 25, 50, 75))
            categories = self.random.choice(((), (), ("Important",), ("Errands",), ("Clients",) if where == "Studio tasks" else ("Family",)))
            text = _task(self._uid(), texts.fill(summary, self.random), due, created, done, self.random.choice((0, 0, 1, 5, 9)), categories, percent)
            if where == "Personal":
                self._put(local, LOCAL_ACCOUNT_ID, personal_list, text)
            else:
                self._put(work_store, work, studio_list, text)
            del number
        return TOTAL_TASKS

    def _contacts(self, local: PimStore, work_store: PimStore, work: str) -> int:
        personal_book = self._collection(local, LOCAL_ACCOUNT_ID, CollectionKind.CONTACTS, "Personal contacts", "")
        studio_book = self._collection(work_store, work, CollectionKind.CONTACTS, "Studio contacts", "", "/addressbooks/anna.clark/studio/")
        chosen = [person for person in self.people if person.circle in ("family", "work", "client")]
        chosen += [person for person in self.people if person.circle not in ("family", "work", "client")]
        chosen = chosen[:TOTAL_CONTACTS]
        groups: dict[str, list[str]] = {}
        for person in chosen:
            uid = self._uid()
            categories = ("Family",) if person.circle == "family" else ("Clients",) if person.circle == "client" else ()
            street = f"{self.random.randint(1, 120)} {self.random.choice(('Mill Lane', 'Harbour Road', 'Oak Avenue', 'Station Street', 'Church Walk'))}" if person.circle in ("family", "friend") else ""
            text = _card(uid, person, categories, street)
            if person.circle in ("work", "client"):
                self._put(work_store, work, studio_book, text)
                groups.setdefault("Design team" if person.circle == "work" else "Clients", []).append(uid)
            else:
                self._put(local, LOCAL_ACCOUNT_ID, personal_book, text)
                group = {"family": "Family", "bookclub": "Book club", "garden": "Garden volunteers", "tennis": "Tennis"}.get(person.circle)
                if group:
                    groups.setdefault(group, []).append(uid)
        for name, members in groups.items():
            book, store, account_id = (studio_book, work_store, work) if name in ("Design team", "Clients") else (personal_book, local, LOCAL_ACCOUNT_ID)
            self._put(store, account_id, book, _group(self._uid(), name, members))
        return len(chosen)

    def _agreed_with_servers(self) -> None:
        """Every item of a server's calendar, task list and address book as agreed with that server at the last synchronisation, so nothing
        is marked as waiting to be sent."""
        for account_id, collections in self.links.items():
            state = DavState(self.engine.directories.pim_directory(account_id) / "dav-state.json", durable=False)
            for collection_id, items in collections.items():
                href = ""
                for (owner, identifier) in self.collections.values():
                    if owner == account_id and identifier == collection_id:
                        href = self._href(account_id, collection_id)
                collection = state.get(collection_id)
                collection.token = f"demo-{collection_id[:8]}"
                for item_id, text in items:
                    collection.links[f"{href}{item_id}.ics"] = ItemLink(item_id, f'"{self.random.getrandbits(40):010x}"', digest_of(text))
            state.save()

    def _href(self, account_id: str, collection_id: str) -> str:
        store = PimStore.open(self.engine.directories.pim_directory(account_id), self._key(), durable=False)
        try:
            return store.get_collection(collection_id).server_value("href")
        finally:
            store.close()
