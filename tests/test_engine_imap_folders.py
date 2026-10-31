import json
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import config_to_text, parse_config
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import UsageError
from tests.mailfactory import raw_message
from tests.test_config import make_config
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_imap_folders.py 4.2.451"

# --- Version history ----------------------------------------------------
# v4.2.451: the tick "Include this account in Send and Receive" instead of the per-account check interval (D-059)
# v1.1.241: A folder taken off the server's list of folders to show goes from the folder list; a standard folder stays (R-34)
# v0.5.123: A folder left out is no longer shown here (D-120)
# v0.0.44: Initial tests of folder choices, roles and the check interval per account


def folders(world: World) -> dict[str, Any]:
    return {choice.server_name: choice for choice in call(world.engine, "account.folders", account=world.account)}


def test_the_folders_of_the_server_are_listed_with_what_the_program_makes_of_them(world: World) -> None:
    world.imap.add("Projects/Alpha", raw_message(subject="a", message_id="<a@x>"))
    choices = folders(world)
    assert set(choices) >= {"INBOX", "Sent", "Drafts", "Trash", "Junk", "Projects/Alpha"}
    assert choices["Sent"].automatic_role == "sent" and choices["Sent"].local_path == "Sent" and choices["Sent"].shown and choices["INBOX"].automatic_role == "inbox"
    assert choices["Projects/Alpha"].automatic_role == "other" and choices["Projects/Alpha"].local_path == "Projects/Alpha" and choices["Projects/Alpha"].chosen_role == ""


def test_a_folder_can_be_left_out_and_put_back(world: World) -> None:
    world.imap.add("Projects", raw_message(subject="p", message_id="<p@x>"))
    world.fetch()
    assert [row.subject for row in world.rows("Projects")] == ["p"]
    result = call(world.engine, "account.set-folders", account=world.account, hide=["Projects"])
    assert (result.hidden, result.shown) == (1, 0) and not folders(world)["Projects"].shown and folders(world)["Projects"].local_path == ""
    world.imap.add("Projects", raw_message(subject="q", message_id="<q@x>"))
    world.fetch()
    assert "Projects" not in {item.path for item in call(world.engine, "folder.list", account=world.account)}  # ignored completely (D-120)
    assert len(world.imap.uids("Projects")) == 2  # its mail stays on the server
    call(world.engine, "account.set-folders", account=world.account, show=["Projects"])
    world.fetch()
    assert sorted(row.subject for row in world.rows("Projects")) == ["p", "q"] and folders(world)["Projects"].shown


def test_an_unsubscribed_folder_can_be_shown_on_request(world: World) -> None:
    world.imap.add("Archive Old", raw_message(subject="old", message_id="<o@x>"))
    world.imap.folders["Archive Old"].subscribed = False
    assert not folders(world)["Archive Old"].shown and not folders(world)["Archive Old"].subscribed
    call(world.engine, "account.set-folders", account=world.account, show=["Archive Old"])
    assert folders(world)["Archive Old"].shown
    world.fetch()
    assert [row.subject for row in world.rows("Archive Old")] == ["old"]


def test_a_folder_taken_off_the_servers_list_goes_and_a_standard_folder_stays(world: World) -> None:
    world.imap.add("Projects", raw_message(subject="p", message_id="<p@x>"))
    world.fetch()
    world.imap.folders["Projects"].subscribed = False  # another program unsubscribed both
    world.imap.folders["Sent"].subscribed = False
    world.fetch()
    shown = {item.path for item in call(world.engine, "folder.list", account=world.account)}
    assert "Projects" not in shown and "Sent" in shown
    assert not folders(world)["Projects"].shown and folders(world)["Sent"].shown and folders(world)["Sent"].local_path == "Sent"
    assert len(world.imap.uids("Projects")) == 1  # its mail stays on the server


def test_a_folder_can_be_given_a_role(world: World) -> None:
    world.imap.add("Verzonden", raw_message(subject="sent one", message_id="<v@x>"))
    world.fetch()
    assert [row.subject for row in world.rows("Verzonden")] == ["sent one"] and world.rows("Sent") == []
    call(world.engine, "account.set-folders", account=world.account, role=["Verzonden=sent", "Sent="])
    assert folders(world)["Verzonden"].chosen_role == "sent" and folders(world)["Verzonden"].local_path == "Sent"
    call(world.engine, "account.set-folders", account=world.account, role=["Verzonden="])
    assert folders(world)["Verzonden"].chosen_role == "" and folders(world)["Verzonden"].local_path == "Verzonden"


def test_bad_folder_choices_are_refused(world: World) -> None:
    for values in ({"hide": ["INBOX"]}, {"role": ["Sent"]}, {"role": ["Sent=spaceship"]}, {"role": ["=sent"]}, {"show": ["A"], "hide": ["A"]}):
        with pytest.raises(UsageError):
            call(world.engine, "account.set-folders", account=world.account, **values)


def test_a_pop3_account_has_no_server_folders(setup: Setup) -> None:
    with pytest.raises(UsageError, match="IMAP"):
        call(setup.engine, "account.folders", account=setup.account_id)
    with pytest.raises(UsageError, match="IMAP"):
        call(setup.engine, "account.set-folders", account=setup.account_id, hide=["x"])


def test_an_account_can_be_left_out_of_send_and_receive(setup: Setup) -> None:
    assert call(setup.engine, "account.list")[0].in_send_receive is True  # included by default
    assert call(setup.engine, "account.edit", account=setup.account_id, in_send_receive=False).in_send_receive is False
    assert call(setup.engine, "account.list")[0].in_send_receive is False
    assert call(setup.engine, "account.edit", account=setup.account_id, display_name="Other").in_send_receive is False  # not given: unchanged
    assert call(setup.engine, "account.edit", account=setup.account_id, in_send_receive=True).in_send_receive is True


def test_the_tick_is_kept_in_the_settings_file_and_an_old_interval_is_ignored() -> None:
    from dataclasses import replace

    base = make_config()
    changed = base.with_account(replace(base.accounts[0], in_send_receive=False))
    assert parse_config(config_to_text(changed)).accounts[0].in_send_receive is False
    data = json.loads(config_to_text(changed))
    del data["accounts"][0]["in_send_receive"]
    data["accounts"][0]["check_minutes"] = 15  # a file from before 4.2.451
    assert parse_config(json.dumps(data)).accounts[0].in_send_receive is True  # included, following the general refresh
    data["accounts"][0]["in_send_receive"] = "no"
    with pytest.raises(ConfigurationError, match="in_send_receive"):
        parse_config(json.dumps(data))
