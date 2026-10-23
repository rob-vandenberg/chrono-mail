import random
from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "people.py 2.3.321"

# --- Version history ----------------------------------------------------
# v2.3.321: Initial: the people and companies who write to the demo's person, by circle (D-243)

# Everyone here is made up; every address is on example.com, example.org or example.net (or a name below them).


@dataclass(frozen=True, slots=True)
class Person:
    name: str
    address: str
    circle: str  # which part of the person's life: work, client, family, friend, bookclub, garden, school, tennis, alumni, freelance
    birthday: tuple[int, int, int] | None = None  # year, month, day: for the contacts and the Birthdays calendar
    phone: str = ""
    company: str = ""
    title: str = ""


@dataclass(frozen=True, slots=True)
class Sender:
    """A company or service: shops, newsletters, bills, travel. Writes, never gets an answer."""

    name: str
    address: str
    account: str  # the DemoAccount.key it writes to
    kind: str  # receipt, newsletter, bill, booking, notice, update


FIRST_NAMES: tuple[str, ...] = (
    "Oliver", "Emma", "James", "Sophie", "Daniel", "Grace", "Thomas", "Lucy", "Samuel", "Hannah", "Benjamin", "Chloe", "Matthew", "Ella", "Joseph",
    "Isabel", "David", "Megan", "Edward", "Laura", "Henry", "Rachel", "Peter", "Alice", "Michael", "Fiona", "Robert", "Claire", "William", "Helen",
    "George", "Katie", "Andrew", "Sarah", "Richard", "Natalie", "Simon", "Rebecca", "Paul", "Amy", "Martin", "Eleanor", "Stephen", "Jessica", "Mark",
    "Victoria", "Adam", "Charlotte", "Luke", "Holly", "Nathan", "Abigail", "Ryan", "Zoe", "Patrick", "Ruth", "Jonathan", "Maya", "Christopher", "Iris",
)
LAST_NAMES: tuple[str, ...] = (
    "Bennett", "Hughes", "Fletcher", "Morgan", "Turner", "Harper", "Walsh", "Doyle", "Prescott", "Whitaker", "Lambert", "Ashworth", "Kendall", "Barlow",
    "Sutton", "Hayward", "Pearce", "Rowe", "Gibbs", "Holloway", "Mercer", "Lowe", "Fairfax", "Dunn", "Harlow", "Sheldon", "Quinn", "Radley", "Tanner",
    "Webb", "Ellison", "Marsh", "Norris", "Oakley", "Pryce", "Sinclair", "Thorne", "Vaughan", "Winslow", "Yardley", "Bramley", "Carver", "Drake",
    "Ellis", "Forbes", "Garland", "Hale", "Ingram", "Jarvis", "Kirby", "Lyle", "Monroe", "Nash", "Osborne", "Parry", "Rhodes", "Stroud", "Talbot",
)
CIRCLES: tuple[tuple[str, int, str], ...] = (  # circle, how many people, the domain their addresses are on
    ("work", 22, "harbourdesign.example.com"),
    ("client", 26, "example.com"),
    ("family", 12, "example.org"),
    ("friend", 30, "example.net"),
    ("bookclub", 16, "example.net"),
    ("garden", 18, "example.org"),
    ("school", 10, "school.example.net"),
    ("tennis", 14, "example.org"),
    ("alumni", 12, "alumni.example.net"),
    ("freelance", 16, "example.com"),
)
CLIENT_COMPANIES: tuple[str, ...] = ("Bluebird Bakery", "Northwind Travel", "Maple & Oak", "Copper Kettle Café", "Riverbend Dental", "Lumen Opticians",
                                     "Fernhill Nurseries", "Harbour Lights Cinema", "Saltmarsh Brewery", "Kestrel Cycles")
WORK_TITLES: tuple[str, ...] = ("Art director", "Senior designer", "Designer", "Project manager", "Copywriter", "Studio manager", "Developer",
                                "Illustrator", "Account manager", "Junior designer")
FAMILY: tuple[tuple[str, str], ...] = (  # name, relation: the family keeps the surname Clark
    ("David Clark", "husband"), ("Lily Clark", "daughter"), ("Sam Clark", "son"), ("Margaret Clark", "mother"), ("Robert Clark", "father"),
    ("Jenny Clark", "sister"), ("Tom Fairbairn", "brother-in-law"), ("Ruth Clark", "aunt"), ("Ben Clark", "cousin"), ("Nora Fairbairn", "niece"),
    ("Arthur Clark", "uncle"), ("Elsie Clark", "grandmother"),
)

SENDERS: tuple[Sender, ...] = (
    Sender("Pinewood Home Store", "orders@pinewood.example.com", "shopping", "receipt"),
    Sender("Brightside Books", "hello@brightsidebooks.example.net", "shopping", "receipt"),
    Sender("Greengrocer Direct", "orders@greengrocer.example.org", "shopping", "receipt"),
    Sender("Parcel Express", "tracking@parcelexpress.example.com", "shopping", "update"),
    Sender("Northfield Outdoor", "shop@northfield.example.net", "shopping", "receipt"),
    Sender("The Weekly Kitchen", "letters@weeklykitchen.example.com", "newsletters", "newsletter"),
    Sender("Design Notes", "editor@designnotes.example.org", "newsletters", "newsletter"),
    Sender("City Museum", "members@citymuseum.example.org", "newsletters", "newsletter"),
    Sender("Garden Monthly", "news@gardenmonthly.example.net", "newsletters", "newsletter"),
    Sender("Typeface Digest", "digest@typeface.example.com", "newsletters", "newsletter"),
    Sender("Local Food Co-op", "news@foodcoop.example.org", "newsletters", "newsletter"),
    Sender("Coastline Energy", "billing@coastline-energy.example.com", "bills", "bill"),
    Sender("Clearwater Utilities", "accounts@clearwater.example.org", "bills", "bill"),
    Sender("Fibrelink Broadband", "billing@fibrelink.example.net", "bills", "bill"),
    Sender("Harbour Council", "council.tax@harbourcouncil.example.org", "bills", "bill"),
    Sender("Steady Mobile", "bills@steadymobile.example.com", "bills", "bill"),
    Sender("Skyway Airlines", "bookings@skyway.example.com", "travel", "booking"),
    Sender("Railroute", "tickets@railroute.example.net", "travel", "booking"),
    Sender("Seaview Hotels", "reservations@seaview.example.org", "travel", "booking"),
    Sender("Island Ferries", "booking@islandferries.example.com", "travel", "booking"),
    Sender("Westbrook Primary School", "office@school.example.net", "school", "notice"),
    Sender("Westbrook PTA", "pta@school.example.net", "school", "notice"),
    Sender("Riverside Tennis Club", "secretary@tennis.example.org", "tennis", "notice"),
    Sender("University Alumni Office", "alumni@alumni.example.net", "alumni", "newsletter"),
    Sender("Old Mill Internet", "support@isp.example.com", "oldisp", "notice"),
)


def _address(name: str, domain: str, taken: set[str]) -> str:
    first, _, last = name.partition(" ")
    for form in (f"{first}.{last}", f"{first[0]}.{last}", f"{first}{last[0]}", f"{first}.{last}2"):
        address = f"{form.lower()}@{domain}"
        if address not in taken:
            taken.add(address)
            return address
    raise ValueError(f"No address left for {name}")


def make_people(generator: random.Random) -> list[Person]:
    """Everyone who writes to the person, the same every time for the same generator."""
    taken: set[str] = set()
    people: list[Person] = []
    used_names: set[str] = {name for name, _ in FAMILY}
    for name, relation in FAMILY:
        year = {"husband": 1981, "daughter": 2012, "son": 2015, "mother": 1953, "father": 1950, "grandmother": 1931}.get(relation, generator.randint(1960, 1995))
        people.append(Person(name, _address(name, "example.org", taken), "family", (year, generator.randint(1, 12), generator.randint(1, 28)),
                             f"+44 7700 900{generator.randint(100, 999)}", "", relation))
    for circle, count, domain in CIRCLES:
        if circle == "family":
            continue
        for number in range(count):
            while True:
                name = f"{generator.choice(FIRST_NAMES)} {generator.choice(LAST_NAMES)}"
                if name not in used_names:
                    used_names.add(name)
                    break
            company, title = "", ""
            where = domain
            if circle == "work":
                company, title = "Harbour Design Studio", WORK_TITLES[number % len(WORK_TITLES)]
            elif circle == "client":
                company = CLIENT_COMPANIES[number % len(CLIENT_COMPANIES)]
                where = company.lower().replace(" & ", "and").replace(" ", "").replace("é", "e") + ".example.com"
                title = generator.choice(("Owner", "Marketing manager", "Director", "Office manager", "Founder"))
            birthday = (generator.randint(1958, 1996), generator.randint(1, 12), generator.randint(1, 28)) if circle in ("friend", "work", "bookclub") and number % 3 == 0 else None
            people.append(Person(name, _address(name, where, taken), circle, birthday, f"+44 7700 900{generator.randint(100, 999)}", company, title))
    return people
