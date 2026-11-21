from pathlib import Path

from chrono_mail.pim.conflictwords import Words, rows
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_engine_conflicts import collection_of, linked

# --- Version ------------------------------------------------------------
__version__ = "test_engine_conflict_members.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial: a group's conflict names its members' contacts, so the window shows each member by name or address, not "A member"


def group_card(name: str, *members: str) -> str:
    lines = "".join(f"X-ADDRESSBOOKSERVER-MEMBER:{member}\r\n" for member in members)
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:g1\r\nX-ADDRESSBOOKSERVER-KIND:group\r\nFN:{name}\r\n{lines}END:VCARD\r\n"


def test_a_member_reads_as_its_contact_s_name_or_its_address() -> None:
    names = {"anna-1": "Anna de Vries"}
    words = Words(lambda moment: str(moment), lambda colour: colour, lambda reference: names.get(reference.removeprefix("urn:uuid:"), ""))
    base = group_card("Family", "urn:uuid:anna-1")
    local = group_card("Gezin", "urn:uuid:anna-1", "mailto:bob@example.com")
    server = group_card("Familie", "urn:uuid:anna-1", "urn:uuid:gone-9")
    texts = {text for row in rows(base, local, server, words) for text in (row.local, row.server)}
    assert {"Anna de Vries", "bob@example.com", "A member"} <= texts  # a contact not in this address book has no name here


def test_a_group_s_conflict_names_the_contacts_of_its_members(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        book = collection_of(engine, account, "contacts")
        anna = call(engine, "contact.add", collection=book, name="Anna de Vries", email=("anna@example.com",))
        bob = call(engine, "contact.add", collection=book, name="Bob Jansen", email=("bob@example.com",))
        group = call(engine, "contact-group.create", collection=book, name="Family", contacts=(anna.item,)).item
        href, text = next((href, text) for href, (_, text) in server.collections["contacts"].items.items() if "FN:Family" in text)
        anna_line = next(line for line in text.replace("\r\n ", "").split("\r\n") if str(anna.uid).removeprefix("urn:uuid:") in line and "MEMBER" in line)
        changed = text.replace("FN:Family", "FN:Familie").replace(anna_line, anna_line + "\r\n" + anna_line.replace(str(anna.uid).removeprefix("urn:uuid:"), str(bob.uid).removeprefix("urn:uuid:")))
        server.add("contacts", href, changed)  # on the phone: renamed, and Bob added
        call(engine, "contact-group.rename", item=group, name="Gezin")  # here: renamed too
        (conflict,) = call(engine, "conflict.list").conflicts
        assert set(dict(conflict.member_names).values()) == {"Anna de Vries", "Bob Jansen"}
