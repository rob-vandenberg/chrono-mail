from chrono_mail.config import ServerRole
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_engine_outbox import failed_send

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_activity.py 0.5.145"

# --- Version history ----------------------------------------------------
# v0.5.145: Initial tests of the steps a mail check and Send All report per account (the Operations window)

Seen = list[tuple[str, str, str, int | None, int | None]]


def listen(engine_holder: Setup | World) -> Seen:
    seen: Seen = []
    engine_holder.engine.activity = lambda account_id, direction, step, done, total: seen.append((account_id, direction, step, done, total))
    return seen


def steps(seen: Seen) -> list[str]:
    return [step for _, _, step, _, _ in seen]


def test_a_pop3_check_reports_its_steps_in_order(setup: Setup) -> None:
    seen = listen(setup)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert {(account_id, direction) for account_id, direction, *_ in seen} == {(setup.account_id, "receive")}
    assert steps(seen) == ["Connecting", "Connected", "Listing messages", "Downloading", "Downloading", "Downloading", "Disconnected", "Applying rules"]
    assert [(done, total) for _, _, step, done, total in seen if step == "Downloading"] == [(1, 3), (2, 3), (3, 3)]


def test_a_pop3_check_with_nothing_new_has_no_download_or_rules_step(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    seen = listen(setup)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert steps(seen) == ["Connecting", "Connected", "Listing messages", "Disconnected"]


def test_a_pop3_check_that_deletes_on_the_server_says_so(setup: Setup) -> None:
    call(setup.engine, "account.policy", account=setup.account_id, policy="delete_after_download")
    seen = listen(setup)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert "Deleting messages on the server" in steps(seen)
    assert steps(seen).index("Deleting messages on the server") < steps(seen).index("Disconnected")


def test_an_imap_check_reports_the_folders_it_synchronises(world: World) -> None:
    world.imap.add("INBOX", mail(1))
    seen = listen(world)
    world.fetch()
    found = steps(seen)
    assert found[:3] == ["Connecting", "Connected", "Listing folders"]
    assert "Synchronising Inbox" in found and "Downloading Inbox" in found
    assert found.index("Disconnected") > found.index("Synchronising Inbox")
    assert {(account_id, direction) for account_id, direction, *_ in seen} == {(world.account, "receive")}


def test_send_all_reports_only_accounts_with_mail_waiting(setup: Setup) -> None:
    seen = listen(setup)
    call(setup.engine, "mail.send-outbox")
    assert seen == []  # nothing waits: no row
    failed_send(setup, "First")
    failed_send(setup, "Second")
    seen.clear()
    done = call(setup.engine, "mail.send-outbox")
    assert done.sent == 2
    assert seen == [
        (setup.account_id, "send", "Sending", 1, 2), (setup.account_id, "send", "Sending", 2, 2), (setup.account_id, "send", "Disconnected", None, None),
    ]


def test_a_failed_send_names_its_account(setup: Setup) -> None:
    failed_send(setup)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    done = call(setup.engine, "mail.send-outbox")
    assert done.sent == 0 and len(done.failed) == 1 and done.failed_accounts == (setup.account_id,)
