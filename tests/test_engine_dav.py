from pathlib import Path

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import Engine, UsageError, commands_dav
from chrono_mail.protocols.tls import fingerprint
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_sync_dav import card, event, task

# --- Version ------------------------------------------------------------
__version__ = "test_engine_dav.py 2.0.286"

# --- Version history ----------------------------------------------------
# v2.0.286: a name clash is a sync conflict, no error line (F3)
# v1.1.221: the name and colour of a server's calendar: on the server first, offline later, refused kept, followed from the server, kept with the
#           account through untick and re-tick, compared in one form, two computers (D-191, D-192)
# v1.1.212: accounts of calendars, contacts and tasks only: dav.find-new, account.add-dav, no mail
# v1.1.211: the calendars of an account carry its account name
# v1.1.210: dav.find and dav.link: looking for the server, the certificate of the mail, the folders left out
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: Initial tests of the commands for the calendar and contacts server


def add_account(tmp_path: Path) -> tuple[Engine, str]:
    engine = make_engine(tmp_path)
    created = call(
        engine, "account.add", display_name="Me", email_address="ann@example.com", username="ann", password="secret", incoming_host="127.0.0.1", incoming_port=1,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )
    return engine, str(created.account_id)


def test_the_commands_set_discover_and_synchronise(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        server.add("tasks", "t1.ics", task("t1", "Write"))
        server.add("contacts", "c1.vcf", card("c1", "Ann Lee"))
        with pytest.raises(UsageError):
            call(engine, "dav.sync", account=account)  # no server set yet
        summary = call(engine, "account.set-dav", account=account, url=server.url)
        assert summary.dav_url == server.url and summary.dav_username == ""
        assert {(item.name, item.kind) for item in call(engine, "dav.discover", account=account)} == {("Calendar", "events"), ("Tasks", "tasks"), ("Contacts", "contacts")}
        (result,) = call(engine, "dav.sync")
        assert (result.created, result.pulled, result.errors) == (3, 3, ())
        shown = [item for item in call(engine, "collection.list") if item.account == account]
        assert sorted(item.name for item in shown) == ["Calendar", "Contacts", "Tasks"]
        assert [item.name for item in call(engine, "contact.list").contacts] == ["Ann Lee"]
        call(engine, "contact.add", collection=next(item.collection for item in shown if item.kind == "contacts"), name="Bob")
        assert len(server.collections["contacts"].items) == 2  # on the server first (D-194)
        assert call(engine, "dav.sync", account=account)[0].pushed == 0  # nothing left for the sync


def test_a_separate_user_name_and_password_and_taking_the_server_away(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav(user="zed", password="other") as server:
        call(engine, "account.set-dav", account=account, url=server.url, username="zed", password="other")
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        call(engine, "account.set-dav", account=account)
        assert call(engine, "account.list")[0].dav_url == ""
        with pytest.raises(UsageError):
            call(engine, "dav.sync", account=account)


def test_an_address_must_be_a_web_address(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with pytest.raises(ConfigurationError):
        call(engine, "account.set-dav", account=account, url="ftp://example.com")


def test_a_certificate_that_was_accepted_for_the_account_is_used(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav(tls=True) as server:
        call(engine, "account.set-dav", account=account, url=server.url)
        with pytest.raises(Exception, match="certificate"):
            call(engine, "dav.sync", account=account)
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=int(server.url.rsplit(":", 1)[1].split("/")[0]), fingerprint=fingerprint(server.certificate))
        assert call(engine, "dav.sync", account=account)[0].errors == ()


def add_searching_account(tmp_path: Path, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> tuple[Engine, str]:
    """An account whose mail server is this computer, with the search pointed at the fake server's port (the domain of the address is this computer too)."""
    monkeypatch.setattr(commands_dav, "WEB_PORT", server.port)
    engine = make_engine(tmp_path)
    created = call(
        engine, "account.add", display_name="Me", email_address="ann@localhost", username="ann", password="secret", incoming_host="127.0.0.1", incoming_port=1,
        incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )
    return engine, str(created.account_id)


def test_the_server_is_found_with_the_certificate_accepted_for_the_mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav(tls=True, public=True) as server:
        engine, account = add_searching_account(tmp_path, server, monkeypatch)
        nothing = call(engine, "dav.find", account=account)
        assert nothing.url == "" and nothing.folders == () and "certificate" in nothing.problem
        assert server.logins == []  # a certificate that was never accepted: not even asked
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=1, fingerprint=fingerprint(server.certificate))
        found = call(engine, "dav.find", account=account)
        assert found.url == f"https://127.0.0.1:{server.port}/" and found.pin == f"127.0.0.1:{server.port}={fingerprint(server.certificate)}"
        assert sorted((item.kind, item.name, item.public, item.read_only, item.ticked) for item in found.folders) == [
            ("contacts", "Contacts", False, False, True), ("contacts", "Public Contacts", True, False, True),
            ("events", "Calendar", False, False, True), ("events", "Calendar", True, False, True), ("tasks", "Tasks", False, False, True),
        ]
        assert server.logins[0] is False  # asked without the password first


def test_a_web_server_that_is_no_calendar_server_never_gets_the_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav(tls=True, plain_web=True) as server:
        engine, account = add_searching_account(tmp_path, server, monkeypatch)
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=1, fingerprint=fingerprint(server.certificate))
        found = call(engine, "dav.find", account=account)
        assert (found.url, found.folders, found.problem) == ("", (), "")
        assert server.logins and not any(server.logins)


def test_an_address_given_is_checked_as_it_is(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        found = call(engine, "dav.find", account=account, url=server.url)
        assert found.url == server.url and found.pin == "" and len(found.folders) == 3 and all(item.ticked for item in found.folders)
    with FakeDav(password="other") as server, pytest.raises(Exception, match="refused"):
        call(engine, "dav.find", account=account, url=server.url)


def test_linking_keeps_the_ticked_folders_in_step_and_unticking_takes_one_away(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav(tls=True) as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        server.add("tasks", "t1.ics", task("t1", "Write"))
        engine, account = add_searching_account(tmp_path, server, monkeypatch)
        call(engine, "account.trust-certificate", account=account, host="127.0.0.1", port=1, fingerprint=fingerprint(server.certificate))
        found = call(engine, "dav.find", account=account)
        tasks = next(item.key for item in found.folders if item.kind == "tasks")
        with pytest.raises(UsageError):  # only a certificate already accepted for this account
            call(engine, "dav.link", account=account, url=found.url, pin=f"127.0.0.1:{server.port}={'0' * 64}")
        summary = call(engine, "dav.link", account=account, url=found.url, left_out=(tasks,), pin=found.pin)
        assert summary.dav_url == found.url and found.pin in summary.trusted_certificates
        (result,) = call(engine, "dav.sync", account=account)
        assert (result.created, result.errors) == (2, ())
        assert {item.kind for item in call(engine, "collection.list") if item.account == account} == {"events", "contacts"}
        again = call(engine, "dav.find", account=account)
        assert {item.key: item.ticked for item in again.folders}[tasks] is False
        events = next(item.key for item in again.folders if item.kind == "events")
        calendar = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
        server.writes_failing = True  # offline for a moment: the new event waits here
        call(engine, "event.add", calendar=calendar, summary="Made here", start="2026-10-21T09:00", end="2026-10-21T10:00")
        server.writes_failing = False
        with pytest.raises(UsageError, match="not on the server yet"):
            call(engine, "dav.link", account=account, url=found.url, left_out=(tasks, events), pin=found.pin)
        call(engine, "dav.sync", account=account)
        call(engine, "dav.link", account=account, url=found.url, left_out=(tasks, events), pin=found.pin)
        assert {item.kind for item in call(engine, "collection.list") if item.account == account} == {"contacts"}
        assert len(server.collections["events"].items) == 2  # the server is not touched


def test_another_server_forgets_what_was_left_out(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        found = call(engine, "dav.find", account=account, url=server.url)
        call(engine, "dav.link", account=account, url=server.url, left_out=(found.folders[0].key,))
        assert engine.config().find(account).dav_left_out == (found.folders[0].key,)
        call(engine, "account.set-dav", account=account, url="https://elsewhere.example/dav/")
        assert engine.config().find(account).dav_left_out == ()


def test_the_calendars_of_an_account_carry_its_account_name(tmp_path: Path) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        call(engine, "account.set-dav", account=account, url=server.url)
        call(engine, "dav.sync", account=account)
        assert {item.account_name for item in call(engine, "collection.list") if item.account == account} == {"ann@example.com"}  # not Your name ("Me")
        call(engine, "account.edit", account=account, account_name="Kerio test")
        assert {item.account_name for item in call(engine, "collection.list") if item.account == account} == {"Kerio test"}


def test_an_account_of_calendars_only_is_added_only_when_its_server_answers(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakeDav(public=True) as server:
        found = call(engine, "dav.find-new", url=server.url, username="ann", password="secret")
        assert found.url == server.url and len(found.folders) == 5 and all(item.ticked for item in found.folders)
        with pytest.raises(Exception, match="refused"):
            call(engine, "account.add-dav", url=server.url, username="ann", password="wrong")
        assert call(engine, "account.list") == ()  # nothing half made
        public = next(item.key for item in found.folders if item.public and item.kind == "events")
        added = call(engine, "account.add-dav", url=server.url, username="ann", password="secret", left_out=(public,))
        assert added.key_created  # the first account: the data key is made (with Local Folders)
        account = next(item for item in call(engine, "account.list") if item.account_id == added.account_id)
        assert (account.kind, account.email_address, account.shown_name, account.dav_url) == ("dav", "", "ann", server.url)
        assert account.server_label == "127.0.0.1 (Calendars and contacts)" and account.label == "ann - 127.0.0.1 (Calendars and contacts)"
        (result,) = call(engine, "dav.sync", account=added.account_id)
        assert (result.created, result.errors) == (4, ())
        with pytest.raises(ConfigurationError, match="no mail"):
            call(engine, "message.list", account=added.account_id)
        assert added.account_id not in engine.storage_groups()
        with pytest.raises(ConfigurationError, match="exists already"):
            call(engine, "account.add-dav", url=server.url, username="ann", password="secret")
        call(engine, "account.edit", account=added.account_id, account_name="Family calendar")
        assert next(item for item in call(engine, "collection.list") if item.account == added.account_id).account_name == "Family calendar"
        with pytest.raises(UsageError):
            call(engine, "account.set-dav", account=added.account_id, url="")
        call(engine, "account.remove", account=added.account_id)
        assert engine.passwords.get(added.account_id, ServerRole.INCOMING) is None


def test_a_host_name_alone_is_an_address() -> None:
    assert commands_dav.web_address("kerio.example") == "https://kerio.example/"
    assert commands_dav.web_address(" https://dav.example.com/remote.php/dav ") == "https://dav.example.com/remote.php/dav"
    assert commands_dav.web_address("") == ""


# --- the name and colour of a server's calendar (D-191, D-192) ------------------------------------------------------------------------------------


def linked(tmp_path: Path, server: FakeDav) -> tuple[Engine, str, list[str]]:
    """An account kept in step with the server, synchronised once; what the engine tells the person is collected."""
    engine, account = add_account(tmp_path)
    told: list[str] = []
    engine.notice = told.append
    call(engine, "account.set-dav", account=account, url=server.url)
    assert call(engine, "dav.sync", account=account)[0].errors == ()
    return engine, account, told


def shown(engine: Engine, account: str, kind: str = "events") -> object:
    return next(item for item in call(engine, "collection.list") if item.account == account and item.kind == kind)


def test_a_rename_and_a_recolour_go_to_the_server_first(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        calendar = shown(engine, account).collection
        assert call(engine, "collection.edit", collection=calendar, name="Home").name == "Home"
        assert server.collections["events"].name == "Home"
        call(engine, "collection.edit", collection=calendar, colour="#aabbcc")
        assert server.collections["events"].colour == "#AABBCCFF" and shown(engine, account).colour == "#aabbcc"
        assert engine.config().find(account).collection_looks == () and told == []  # nothing kept here: the server has it
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert (shown(engine, account).name, shown(engine, account, "tasks").name) == ("Home", "Tasks")


def test_offline_the_change_is_made_here_kept_and_sent_at_the_next_sync(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        calendar = shown(engine, account).collection
        server.failing = True
        call(engine, "collection.edit", collection=calendar, name="Home")
        assert shown(engine, account).name == "Home" and server.collections["events"].name == "Calendar"
        assert len(told) == 1 and "next synchronisation" in told[0]
        (look,) = engine.config().find(account).collection_looks  # kept with the account: a restart keeps it
        assert (look.name, look.name_state, look.base_name) == ("Home", "waiting", "Calendar")
        server.failing = False
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert server.collections["events"].name == "Home" and engine.config().find(account).collection_looks == ()


def test_a_refused_rename_is_kept_here_and_told_and_never_sent_again(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        server.refuses_names = True
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Home")
        assert shown(engine, account).name == "Home" and server.collections["events"].name == "Calendar"
        assert len(told) == 1 and "did not take the new name" in told[0]
        sent = server.count("PROPPATCH")
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert server.count("PROPPATCH") == sent and shown(engine, account).name == "Home"


def test_a_name_and_colour_changed_on_the_server_show_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.collections["events"].name, server.collections["events"].colour = "Home", "#00FF00FF"
        call(engine, "dav.sync", account=account)
        assert (shown(engine, account).name, shown(engine, account).colour) == ("Home", "#00ff00")
        assert shown(engine, account, "tasks").name == "Tasks" and server.count("PROPPATCH") == 0


def test_a_clash_sends_nothing_and_keeps_chrono_mail_s(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.failing = True
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Mine")
        server.failing = False
        server.collections["events"].name = "Theirs"  # renamed on the phone meanwhile
        sent = server.count("PROPPATCH")
        (result,) = call(engine, "dav.sync", account=account)
        assert server.count("PROPPATCH") == sent and server.collections["events"].name == "Theirs"
        assert shown(engine, account).name == "Mine" and result.errors == ()  # a sync conflict now, no line (F3)
        assert [c.reason for c in call(engine, "conflict.list").conflicts] == ["collection_look_clash"]


def test_both_sides_making_the_same_change_is_no_clash(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.failing = True
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Home")
        server.failing = False
        server.collections["events"].name = "Home"
        assert call(engine, "dav.sync", account=account)[0].errors == () and engine.config().find(account).collection_looks == ()


def test_a_colour_in_the_server_s_other_form_is_the_same_colour(tmp_path: Path) -> None:
    with FakeDav() as server:  # the fake server writes its calendar's colour as #RRGGBBAA
        engine, account, _ = linked(tmp_path, server)
        assert shown(engine, account).colour == "#336699"
        for _ in range(2):
            assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert server.count("PROPPATCH") == 0 and engine.config().find(account).collection_looks == ()


def test_untick_and_tick_again_keep_name_colour_and_palette(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.keeps_colour = False  # a server without calendar colours: the colour is kept with the account
        engine, account, told = linked(tmp_path, server)
        calendar = shown(engine, account).collection
        call(engine, "collection.edit", collection=calendar, name="Home")
        call(engine, "collection.edit", collection=calendar, colour="#123456")
        call(engine, "collection.set-palette", collection=calendar, colours=("g11\tRood\t#ff0000", "\tMine\t#abcdef"))
        assert len(told) == 1 and "colour" in told[0]  # the server did not take it: said once
        found = call(engine, "dav.find", account=account, url=server.url)
        events = next(item.key for item in found.folders if item.kind == "events")
        call(engine, "dav.link", account=account, url=server.url, left_out=(events,))
        assert all(item.kind != "events" for item in call(engine, "collection.list") if item.account == account)
        call(engine, "dav.link", account=account, url=server.url)
        call(engine, "dav.sync", account=account)
        again = shown(engine, account)
        assert (again.name, again.colour) == ("Home", "#123456")
        assert [(entry.name, entry.colour) for entry in again.palette[1:]] == [("Rood", "#ff0000"), ("Mine", "#abcdef")]


def test_a_read_only_calendar_cannot_be_renamed_or_recoloured(tmp_path: Path) -> None:
    with FakeDav(read_only=("events",)) as server:
        engine, account, _ = linked(tmp_path, server)
        with pytest.raises(UsageError, match="read only"):
            call(engine, "collection.edit", collection=shown(engine, account).collection, name="Home")
        assert server.count("PROPPATCH") == 0


def test_a_calendar_made_before_this_version_sends_its_colour_and_keeps_its_name(tmp_path: Path) -> None:
    from chrono_mail.engine.pimaccess import open_pim

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        calendar = shown(engine, account).collection
        with open_pim(engine, account) as store:  # as 1.1.220 left it: renamed and recoloured only here, never followed
            info = store.get_collection(calendar)
            store.update_collection(calendar, name="Old name", colour="#010203", server={"href": info.server_value("href"), "component": info.server_value("component")})
        (result,) = call(engine, "dav.sync", account=account)
        assert server.collections["events"].colour == "#010203FF" and server.collections["events"].name == "Calendar"
        assert shown(engine, account).name == "Old name" and any("Chrono Mail keeps “Old name”" in line for line in result.errors)
        assert call(engine, "dav.sync", account=account)[0].errors == ()  # said once


def test_two_computers_end_up_showing_the_same(tmp_path: Path) -> None:
    with FakeDav() as server:
        first, first_account, _ = linked(tmp_path / "first", server)
        second, second_account, _ = linked(tmp_path / "second", server)
        call(first, "collection.edit", collection=shown(first, first_account).collection, name="Shared")
        call(first, "collection.edit", collection=shown(first, first_account).collection, colour="#445566")
        call(second, "dav.sync", account=second_account)
        assert (shown(second, second_account).name, shown(second, second_account).colour) == ("Shared", "#445566")
