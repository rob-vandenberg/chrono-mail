import pytest

from chrono_mail.core import AuthenticationError, ProtocolError
from chrono_mail.protocols.dav import DavClient, DavConflict, DavError, canonical_colour, parse_multistatus, passing_failure
from chrono_mail.protocols.tls import fingerprint
from tests.fakedav import FakeDav

# --- Version ------------------------------------------------------------
__version__ = "test_protocol_dav.py 1.10.278"

# --- Version history ----------------------------------------------------
# v1.10.278: read: one item by the multiget first (Kerio's GET of a contact is 404); not there only on 404; a failure raises
# v1.1.235: an empty answer means not there; a collection made is returned as listed; a yes without a collection is an error (Kerio)
# v1.1.234: a home that cannot be listed raises; exists confirms only on 404; make_collection makes calendars, task lists and address books (R-25)
# v1.1.224: the server's name is looked up once per client; a failed connection looks it up again
# v1.1.223: one connection kept open; a read is sent again on a dropped connection, a write never twice; Connection: close followed (R-36)
# v1.1.221: a colour in one form; a collection's name and colour set on the server (PROPPATCH); a passing failure told from a refusal
# v1.1.220: 5 s to refuse a closed port (Windows needs 2 s)
# v0.0.65: Initial tests of the CalDAV and CardDAV client

EVENT = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e1\r\nDTSTART:20261012T090000Z\r\nSUMMARY:Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
CARD = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Ann Lee\r\nEND:VCARD\r\n"


def test_discovery_finds_the_homes_and_the_collections_and_skips_what_is_not_a_calendar() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url, "ann", "secret")
        assert "calendar-access" in dav.options()
        calendar_home, address_home = dav.homes()
        assert (calendar_home, address_home) == ("/dav/calendars/ann/", "/dav/addressbooks/ann/")
        found = {(c.name, c.kind, c.component): c for c in dav.collections(calendar_home, address_home)}
        assert set(found) == {("Calendar", "events", "VEVENT"), ("Tasks", "tasks", "VTODO"), ("Contacts", "contacts", "VCARD")}  # the INBOX folder is not a calendar
        events = found[("Calendar", "events", "VEVENT")]
        assert events.href == "/dav/calendars/ann/events/" and events.colour == "#336699" and not events.read_only and events.ctag == "ctag-1" and events.sync_token == "token-1"


def test_discovery_follows_the_well_known_address_when_the_given_one_is_the_root() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url.replace("/dav/", "/"), "ann", "secret")
        calendar_home, address_home = dav.homes()  # / is nothing here: /.well-known/caldav leads to /dav/
        assert calendar_home == "/dav/calendars/ann/" and address_home == "/dav/addressbooks/ann/"


def test_a_collection_without_write_rights_is_read_only() -> None:
    with FakeDav(read_only=("tasks",)) as server:
        dav = DavClient(server.url, "ann", "secret")
        found = {c.kind: c for c in dav.collections(*dav.homes())}
        assert found["tasks"].read_only and not found["events"].read_only and not found["contacts"].read_only


def test_the_password_is_checked_and_a_wrong_one_is_an_authentication_error() -> None:
    with FakeDav() as server:
        with pytest.raises(AuthenticationError):
            DavClient(server.url, "ann", "wrong").options()


def test_items_changes_and_fetching() -> None:
    with FakeDav() as server:
        server.add("events", "a.ics", EVENT)
        server.add("events", "b.ics", EVENT.replace("e1", "e2"))
        dav = DavClient(server.url, "ann", "secret")
        collection = next(c for c in dav.collections(*dav.homes()) if c.kind == "events")
        assert sorted((i.href.rsplit("/", 1)[1], i.etag) for i in dav.items(collection)) == [("a.ics", "e1"), ("b.ics", "e2")]
        first = dav.changes(collection, "")
        assert sorted(i.href.rsplit("/", 1)[1] for i in first.changed) == ["a.ics", "b.ics"] and first.removed == () and first.token.startswith("token-")
        server.add("events", "c.ics", EVENT.replace("e1", "e3"))
        server.remove("events", "a.ics")
        later = dav.changes(collection, first.token)
        assert [i.href.rsplit("/", 1)[1] for i in later.changed] == ["c.ics"] and [h.rsplit("/", 1)[1] for h in later.removed] == ["a.ics"] and later.token != first.token
        none = dav.changes(collection, later.token)
        assert none.changed == () and none.removed == ()
        got = dav.fetch(collection, [i.href for i in dav.items(collection)])
        assert sorted((href.rsplit("/", 1)[1], etag) for href, (etag, _) in got.items()) == [("b.ics", "e2"), ("c.ics", "e4")] or len(got) == 2
        assert all("BEGIN:VEVENT" in text for _, text in got.values())
        with pytest.raises(DavConflict):
            dav.changes(collection, "token-from-another-server")  # a token the server does not know: the caller starts again from nothing


def test_a_server_without_multiget_is_read_item_by_item_and_one_without_sync_is_listed() -> None:
    with FakeDav(multiget=False, sync_tokens=False) as server:
        server.add("contacts", "a.vcf", CARD)
        server.add("contacts", "b.vcf", CARD.replace("c1", "c2"))
        dav = DavClient(server.url, "ann", "secret")
        book = next(c for c in dav.collections(*dav.homes()) if c.kind == "contacts")
        assert book.sync_token == "" and "sync-collection" not in dav.options()
        with pytest.raises(DavError):
            dav.changes(book, "")  # no change report: the caller falls back to listing
        assert len(dav.items(book)) == 2
        got = dav.fetch(book, [i.href for i in dav.items(book)])
        assert len(got) == 2 and all("FN:Ann Lee" in text for _, text in got.values()) and server.count("GET") == 2  # one by one


def test_writing_creating_replacing_and_deleting_with_the_checks_of_the_standard() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url, "ann", "secret")
        collection = next(c for c in dav.collections(*dav.homes()) if c.kind == "events")
        href = collection.href + "new.ics"
        etag = dav.put(href, EVENT, "events", create=True)
        assert etag and server.collections["events"].items["new.ics"][1] == EVENT
        with pytest.raises(DavConflict):
            dav.put(href, EVENT, "events", create=True)  # exists already
        newer = dav.put(href, EVENT.replace("Meeting", "Changed"), "events", etag=etag)
        assert newer and newer != etag and "Changed" in server.collections["events"].items["new.ics"][1]
        with pytest.raises(DavConflict):
            dav.put(href, EVENT, "events", etag=etag)  # the old version tag: somebody else changed it
        with pytest.raises(DavConflict):
            dav.delete(href, etag=etag)
        dav.delete(href, etag=newer)
        assert "new.ics" not in server.collections["events"].items
        dav.delete(href)  # already gone: fine
        with pytest.raises(DavError):
            dav.put(collection.href + "x.ics", EVENT, "events", etag='"nothing"')


def test_a_server_that_does_not_send_the_etag_after_a_write_is_asked() -> None:
    with FakeDav(etag_on_put=False) as server:
        dav = DavClient(server.url, "ann", "secret")
        collection = next(c for c in dav.collections(*dav.homes()) if c.kind == "contacts")
        href = collection.href + "ann.vcf"
        assert dav.put(href, CARD, "contacts", create=True) == ""
        assert dav.etag_of(href) == server.collections["contacts"].items["ann.vcf"][0]


def test_a_read_only_collection_refuses_writes() -> None:
    with FakeDav(read_only=("events",)) as server:
        dav = DavClient(server.url, "ann", "secret")
        collection = next(c for c in dav.collections(*dav.homes()) if c.kind == "events")
        with pytest.raises(DavError) as raised:
            dav.put(collection.href + "x.ics", EVENT, "events", create=True)
        assert raised.value.status == 403


def test_a_server_that_cannot_be_reached_or_is_not_a_dav_server_gives_clear_errors() -> None:
    with pytest.raises(ProtocolError, match="could not be reached"):
        DavClient("http://127.0.0.1:1/dav/", "a", "b", timeout=5).options()  # Windows takes 2 s to refuse a closed port
    for bad in ("ftp://host/", "http://example.com/", "https://", "no scheme"):
        with pytest.raises(DavError):
            DavClient(bad, "a", "b")


def test_hostile_answers_are_refused() -> None:
    with pytest.raises(DavError, match="DOCTYPE"):
        parse_multistatus(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><d:multistatus xmlns:d="DAV:"/>')
    with pytest.raises(DavError, match="not valid XML"):
        parse_multistatus(b"<d:multistatus xmlns:d='DAV:'><d:response>")
    with pytest.raises(DavError, match="not a multistatus"):
        parse_multistatus(b"<html/>")
    assert parse_multistatus(b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/x</d:href></d:response><d:response/></d:multistatus>')[0].href == "/x"
    with pytest.raises(DavError, match="far too large"):
        parse_multistatus(b"<" + b"a" * (33 * 1024 * 1024))


def test_a_self_signed_certificate_is_only_accepted_when_it_is_the_one_that_was_approved() -> None:
    with FakeDav(tls=True) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            DavClient(server.url, "ann", "secret").options()  # not approved: refused
        wrong = DavClient(server.url, "ann", "secret", trusted=frozenset({"0" * 64}))
        with pytest.raises(ProtocolError, match="certificate"):
            wrong.options()  # another certificate was approved
        right = DavClient(server.url, "ann", "secret", trusted=frozenset({fingerprint(server.certificate)}))
        assert "calendar-access" in right.options() and right.homes()[0] == "/dav/calendars/ann/"


def test_a_colour_from_a_server_is_read_in_one_form() -> None:
    assert canonical_colour("#336699FF") == canonical_colour("#336699") == canonical_colour(" #336699 ") == "#336699"
    assert canonical_colour("#ABC") == "#aabbcc"
    assert canonical_colour("") == canonical_colour("red") == canonical_colour("#12345") == canonical_colour("#zzzzzz") == ""


def test_a_collection_gets_another_name_and_colour_and_each_refusal_is_told_per_part() -> None:
    with FakeDav() as server:
        client = DavClient(server.url, "ann", "secret")
        href = server.collection_path("events")
        assert client.set_collection(href, "Home", "#aabbcc") == {"name": 200, "colour": 200}
        assert (server.collections["events"].name, server.collections["events"].colour) == ("Home", "#AABBCCFF")
        server.keeps_colour = False
        assert client.set_collection(href, "Again", "#000000") == {"name": 424, "colour": 403}  # all or nothing: the name waits on the colour
        assert server.collections["events"].name == "Home"
        server.failing = True
        with pytest.raises(DavError) as failed:
            client.set_collection(href, "Later")
        assert passing_failure(failed.value)
        server.failing = False
        server.read_only = ("events",)
        with pytest.raises(DavError) as refused:
            client.set_collection(href, "Never")
        assert not passing_failure(refused.value)
        assert passing_failure(ProtocolError("The server could not be reached")) and passing_failure(AuthenticationError("no"))


def test_one_connection_carries_many_requests() -> None:
    with FakeDav() as server, DavClient(server.url, "ann", "secret") as client:
        homes = client.homes()
        for _ in range(5):
            client.collections(*homes)
        assert server.count("PROPFIND") >= 6 and server.connections == 1


def test_a_read_goes_again_on_a_connection_the_server_dropped_and_a_write_never_twice() -> None:
    with FakeDav() as server, DavClient(server.url, "ann", "secret") as client:
        href = server.collection_path("events")
        client.propfind(href, [("DAV:", "resourcetype")])
        server.drop_silently = True  # it closes each connection after its answer, without saying so
        client.propfind(href, [("DAV:", "resourcetype")])  # the reused connection is gone: sent once more on a new one
        assert server.count("PROPFIND") == 2
        with pytest.raises(ProtocolError) as broken:
            client.put(href + "e1.ics", EVENT, "events", create=True)  # a write on the dropped connection: never sent again
        assert passing_failure(broken.value) and server.count("PUT") <= 1 and len(server.collections["events"].items) == 0


def test_a_write_after_a_pause_gets_a_new_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.protocols import dav

    monkeypatch.setattr(dav, "IDLE_BEFORE_WRITE", 0.0)
    with FakeDav() as server, DavClient(server.url, "ann", "secret") as client:
        href = server.collection_path("events")
        client.propfind(href, [("DAV:", "resourcetype")])
        server.drop_silently = True
        client.put(href + "e1.ics", EVENT, "events", create=True)
        assert len(server.collections["events"].items) == 1 and server.count("PUT") == 1 and server.connections == 2


def test_a_server_that_says_it_closes_gets_a_new_connection_each_time() -> None:
    with FakeDav() as server, DavClient(server.url, "ann", "secret") as client:
        server.say_close = True
        href = server.collection_path("events")
        for _ in range(3):
            client.propfind(href, [("DAV:", "resourcetype")])
        client.put(href + "e1.ics", EVENT, "events", create=True)
        assert server.connections == 4 and len(server.collections["events"].items) == 1


def test_the_server_s_name_is_looked_up_once_for_many_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from chrono_mail.protocols import dav

    lookups: list[str] = []
    real = socket.getaddrinfo

    def counting(host: str, port: int, *rest: object, **named: object) -> object:
        lookups.append(host)
        return real(host, port, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(dav.socket, "getaddrinfo", counting)
    with FakeDav() as server, DavClient(server.url.replace("127.0.0.1", "localhost"), "ann", "secret") as client:
        server.say_close = True  # like Kerio: a new connection for every request
        href = server.collection_path("events")
        for _ in range(5):
            client.propfind(href, [("DAV:", "resourcetype")])
        assert server.connections == 5 and [host for host in lookups if host == "localhost"] == ["localhost"]  # a numeric address asks nobody


def test_a_failed_connection_looks_the_name_up_again(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from chrono_mail.protocols import dav

    lookups: list[str] = []
    real = socket.getaddrinfo

    def counting(host: str, port: int, *rest: object, **named: object) -> object:
        lookups.append(host)
        return real(host, port, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(dav.socket, "getaddrinfo", counting)
    with FakeDav() as server, DavClient(server.url.replace("127.0.0.1", "localhost"), "ann", "secret") as client:
        server.say_close = True
        href = server.collection_path("events")
        client.propfind(href, [("DAV:", "resourcetype")])
        client._addresses = [("127.0.0.1", 1)]  # noqa: SLF001 - as if the server moved away from the address found first
        with pytest.raises(ProtocolError):
            client.propfind(href, [("DAV:", "resourcetype")])
        client.propfind(href, [("DAV:", "resourcetype")])  # looked up again: reached
        assert [host for host in lookups if host == "localhost"] == ["localhost", "localhost"]


def test_a_home_that_cannot_be_listed_is_an_error_never_no_collections() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url, "ann", "secret")
        homes = dav.homes()
        server.homes_failing = True
        with pytest.raises(DavError):
            dav.collections(*homes)


def test_only_a_not_found_answer_confirms_that_a_collection_is_gone() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url, "ann", "secret")
        assert dav.exists("/dav/calendars/ann/events/") is True
        assert dav.exists("/dav/calendars/ann/never/") is False
        server.collection_check_failing = True
        with pytest.raises(ProtocolError):
            dav.exists("/dav/calendars/ann/never/")  # no confirmation either way


def test_a_calendar_a_task_list_and_an_address_book_are_made_on_the_server() -> None:
    with FakeDav() as server:
        dav = DavClient(server.url, "ann", "secret")
        made = dav.make_collection("/dav/calendars/ann/work/", "events", "Work", "#aabbcc")
        assert (made.href, made.name, made.kind) == ("/dav/calendars/ann/work/", "Work", "events")
        dav.make_collection("/dav/calendars/ann/todo/", "tasks", "To do")
        dav.make_collection("/dav/addressbooks/ann/club/", "contacts", "Club")
        made = {(c.name, c.kind, c.colour) for c in dav.all_collections()}
        assert {("Work", "events", "#aabbcc"), ("To do", "tasks", ""), ("Club", "contacts", "")} <= made
        with pytest.raises(DavConflict):
            dav.make_collection("/dav/calendars/ann/work/", "events", "Again")


def test_an_empty_answer_about_an_address_means_nothing_is_there() -> None:
    with FakeDav() as server:
        server.empty_for_unknown = True
        dav = DavClient(server.url, "ann", "secret")
        assert dav.exists("/dav/calendars/ann/never/") is False and dav.exists("/dav/calendars/ann/events/") is True


def test_a_yes_without_a_collection_the_server_lists_is_an_error() -> None:
    with FakeDav() as server:
        server.makes_nothing = True
        dav = DavClient(server.url, "ann", "secret")
        with pytest.raises(DavError, match="made no address book"):
            dav.make_collection("/dav/addressbooks/ann/club/", "contacts", "Club")


def test_one_item_is_read_by_the_multiget_first_and_not_there_only_when_the_server_says_so() -> None:
    with FakeDav() as server:
        server.add("contacts", "a.vcf", CARD)
        server.no_contact_by_get = True  # Kerio
        dav = DavClient(server.url, "ann", "secret")
        book = next(c for c in dav.collections(*dav.homes()) if c.kind == "contacts")
        href = book.href + "a.vcf"
        assert dav.get(href) is None  # what the sync read before
        found = dav.read(book, href)
        assert found is not None and "FN:Ann Lee" in found[1] and found[0]
        assert dav.read(book, book.href + "gone.vcf") is None
    with FakeDav(multiget=False) as server:
        server.add("contacts", "a.vcf", CARD)
        dav = DavClient(server.url, "ann", "secret")
        book = next(c for c in dav.collections(*dav.homes()) if c.kind == "contacts")
        found = dav.read(book, book.href + "a.vcf")  # no multiget: one by one
        assert found is not None and "FN:Ann Lee" in found[1]
        server.gets_failing = True
        with pytest.raises(ProtocolError):
            dav.read(book, book.href + "a.vcf")  # a failure is never "not there" (D-196)
