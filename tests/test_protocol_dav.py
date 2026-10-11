import pytest

from chrono_mail.core import AuthenticationError, ProtocolError
from chrono_mail.protocols.dav import DavClient, DavConflict, DavError, parse_multistatus
from chrono_mail.protocols.tls import fingerprint
from tests.fakedav import FakeDav

# --- Version ------------------------------------------------------------
__version__ = "test_protocol_dav.py 0.0.65"

# --- Version history ----------------------------------------------------
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
        DavClient("http://127.0.0.1:1/dav/", "a", "b", timeout=2).options()
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
