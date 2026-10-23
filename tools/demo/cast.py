from dataclasses import dataclass
from typing import Literal

# --- Version ------------------------------------------------------------
__version__ = "cast.py 2.3.320"

# --- Version history ----------------------------------------------------
# v2.3.320: Initial: the person of the demo and her 14 accounts, with their folders (D-243)

# Everything here is made up. Only the domains example.com, example.org and example.net (and names below them) are used:
# they are set aside for examples and belong to nobody (RFC 2606), so no address of the demo can reach a real person.

PERSON: str = "Anna Clark"

Kind = Literal["pop3", "imap"]
Storage = Literal["own", "local"]
Security = Literal["tls", "starttls", "none"]


@dataclass(frozen=True, slots=True)
class DemoAccount:
    key: str  # how the builder refers to it (never shown)
    account_name: str  # how Chrono Mail names it in its lists
    address: str
    kind: Kind
    incoming_host: str
    outgoing_host: str
    storage: Storage = "own"
    folders: tuple[str, ...] = ()  # its own folders (an account in Local Folders: none; its folder there is local_folder)
    local_folder: str = ""  # the folder under Local Folders its mail is moved to by its rule
    incoming_security: Security = "tls"
    outgoing_security: Security = "starttls"

    @property
    def incoming_port(self) -> int:
        if self.kind == "imap":
            return 993 if self.incoming_security == "tls" else 143
        return 995 if self.incoming_security == "tls" else 110

    @property
    def outgoing_port(self) -> int:
        return 465 if self.outgoing_security == "tls" else 587


ACCOUNTS: tuple[DemoAccount, ...] = (
    # IMAP: the mail stays on the server and is kept in step
    DemoAccount("work", "Work", "anna.clark@harbourdesign.example.com", "imap", "mail.harbourdesign.example.com", "mail.harbourdesign.example.com",
                folders=("Clients", "Clients/Bluebird Bakery", "Clients/Northwind Travel", "Clients/Maple & Oak", "Projects", "Projects/Website relaunch",
                         "Projects/Spring catalogue", "Admin", "Admin/Expenses", "Team"), outgoing_security="tls"),
    DemoAccount("personal", "Personal", "anna.clark@example.org", "imap", "imap.example.org", "smtp.example.org",
                folders=("Family", "Friends", "Holidays", "Holidays/Portugal 2024", "Holidays/Scotland", "House", "House/Insurance", "Receipts", "Health")),
    DemoAccount("bookclub", "Riverside Book Club", "riverside.readers@example.net", "imap", "imap.example.net", "smtp.example.net",
                folders=("Meetings", "Book suggestions", "Members")),
    DemoAccount("garden", "Green Street Garden", "anna@greenstreet.example.org", "imap", "mail.greenstreet.example.org", "mail.greenstreet.example.org",
                folders=("Volunteers", "Plot waiting list", "Events")),
    # POP3 with folders of their own
    DemoAccount("freelance", "Freelance studio", "studio@annaclark.example.com", "pop3", "pop.annaclark.example.com", "smtp.annaclark.example.com",
                folders=("Quotes", "Invoices", "Clients", "Portfolio")),
    DemoAccount("alumni", "University", "a.clark@alumni.example.net", "pop3", "pop.alumni.example.net", "smtp.alumni.example.net",
                folders=("Reunion", "Newsletter")),
    # POP3 in Local Folders: one folder each, filled by its rule
    DemoAccount("shopping", "Shopping", "anna.shops@example.net", "pop3", "pop.example.net", "smtp.example.net", storage="local", local_folder="Shopping"),
    DemoAccount("newsletters", "Newsletters", "anna.reads@example.com", "pop3", "pop.example.com", "smtp.example.com", storage="local", local_folder="Newsletters"),
    DemoAccount("family", "Clark family", "clarkfamily@example.org", "pop3", "pop.example.org", "smtp.example.org", storage="local", local_folder="Family"),
    DemoAccount("travel", "Travel", "anna.travels@example.net", "pop3", "mail.example.net", "mail.example.net", storage="local", local_folder="Travel"),
    DemoAccount("oldisp", "Old address", "aclark@isp.example.com", "pop3", "pop3.isp.example.com", "smtp.isp.example.com", storage="local", local_folder="Old address",
                incoming_security="starttls"),
    DemoAccount("tennis", "Tennis club", "anna@tennis.example.org", "pop3", "mail.tennis.example.org", "mail.tennis.example.org", storage="local", local_folder="Tennis club"),
    DemoAccount("bills", "Bills", "anna.bills@example.com", "pop3", "pop.example.com", "smtp.example.com", storage="local", local_folder="Bills"),
    DemoAccount("school", "School", "clark.parents@school.example.net", "pop3", "pop.school.example.net", "smtp.school.example.net", storage="local", local_folder="School"),
)
