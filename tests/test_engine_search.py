from datetime import UTC, datetime, timedelta
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.engine import UsageError
from chrono_mail.engine.commands_mail import date_bounds
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_search.py 4.2.408"

# --- Version history ----------------------------------------------------
# v4.2.408: the Sent days, the days a snoozed message comes back and the days a waiting message is sent (D-288)
# v4.2.406: message.search, message.groups and message.order with subfolders: the folder and every folder inside it (D-287)
# v3.6.351: a search date at the edge of the calendar is a usage error, never a crash; one before 1970 in the named zone
# v0.5.166: the groups by sender are named and counted by the name the From column shows
# v0.3.109: Sorting by contact, attachment and preview through the commands; no groups for the yes/no sorts
# v0.2.94: Search results in any sort order; message.groups; date group boundaries
# v0.0.19: Initial tests of the advanced search options


def fill(setup: Setup) -> None:
    mails = [
        ("Cats", "all about cats", "Ann <ann@x.org>", datetime(2026, 3, 10, 12, 0, tzinfo=UTC), None),
        ("Dogs", "all about dogs", "Bob <bob@x.org>", datetime(2026, 4, 10, 12, 0, tzinfo=UTC), b"x" * 5000),
        ("Both", "cats and dogs", "Ann <ann@x.org>", datetime(2026, 5, 10, 12, 0, tzinfo=UTC), None),
    ]
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for number, (subject, body, sender, moment, attachment) in enumerate(mails):
            raw = raw_message(subject=subject, body=body + "\n", sender=sender, message_id=f"<s{number}@x>", date=moment, attachment=attachment)
            mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset())


def found(setup: Setup, **values: object) -> list[str]:
    rows: list[Any] = list(call(setup.engine, "message.search", account=setup.account_id, **values))
    return sorted(row.subject for row in rows)


def test_or_and_not_work_through_the_command(setup: Setup) -> None:
    fill(setup)
    assert found(setup, query="cats OR dogs") == ["Both", "Cats", "Dogs"]
    assert found(setup, query="about NOT dogs") == ["Cats"]
    assert found(setup, query="-from:ann", folder="Inbox") == ["Dogs"]


def test_date_ranges_are_inclusive_days(setup: Setup) -> None:
    fill(setup)
    assert found(setup, date_from="2026-04-10") == ["Both", "Dogs"]
    assert found(setup, date_to="2026-04-10") == ["Cats", "Dogs"]
    assert found(setup, date_from="2026-03-11", date_to="2026-05-09") == ["Dogs"]
    assert found(setup, query="cats", date_from="2026-04-01") == ["Both"]
    assert found(setup, date_from="2027-01-01") == []


def test_size_limits_and_attachments(setup: Setup) -> None:
    fill(setup)
    assert found(setup, minimum_size=3000) == ["Dogs"]
    assert found(setup, maximum_size=3000) == ["Both", "Cats"]
    assert found(setup, has_attachments=True) == ["Dogs"]


def test_bad_dates_are_reported(setup: Setup) -> None:
    fill(setup)
    for bad in ("tomorrow", "2026-13-01", "31/12/2026"):
        with pytest.raises(UsageError, match="must be a date"):
            call(setup.engine, "message.search", account=setup.account_id, date_from=bad)
    assert found(setup, date_from="  ") == ["Both", "Cats", "Dogs"]


def test_search_follows_the_sort_order(setup: Setup) -> None:
    fill(setup)

    def listed(**values: object) -> list[str]:
        rows: list[Any] = list(call(setup.engine, "message.search", account=setup.account_id, query="all OR cats", **values))
        return [row.subject for row in rows]

    assert listed() == ["Both", "Dogs", "Cats"]
    assert listed(sort="subject", oldest_first=True) == ["Both", "Cats", "Dogs"]
    assert listed(sort="sender", oldest_first=True)[-1] == "Dogs"
    assert listed(sort="size")[0] == "Dogs"


def test_groups_through_the_command(setup: Setup) -> None:
    fill(setup)
    groups: list[Any] = list(call(setup.engine, "message.groups", account=setup.account_id, folder="Inbox", sort="sender", oldest_first=True))
    assert [(group.value, group.count) for group in groups] == [("Ann", 2), ("Bob", 1)]  # the sender as the From column shows it
    sizes: list[Any] = list(call(setup.engine, "message.groups", account=setup.account_id, query="cats", sort="size"))
    assert [(group.key, group.count) for group in sizes] == [("tiny", 2)]


def test_date_group_boundaries_are_local_midnights_from_monday() -> None:
    bounds = date_bounds(datetime(2026, 2, 14, 15, 30).astimezone())  # a Saturday
    starts = [moment.replace(tzinfo=None) for moment in bounds.starts()]
    assert starts == [datetime(2026, 2, 14), datetime(2026, 2, 13), datetime(2026, 2, 9), datetime(2026, 2, 2), datetime(2026, 2, 1), datetime(2026, 1, 1)]
    assert all(moment.utcoffset() is not None for moment in bounds.starts())
    assert date_bounds(datetime(2026, 1, 5, 8, 0).astimezone()).last_month.replace(tzinfo=None) == datetime(2025, 12, 1)
    assert timedelta(0) <= bounds.today - bounds.yesterday <= timedelta(hours=25)



def test_the_new_sorts_through_the_commands(setup: Setup) -> None:
    fill(setup)
    book = next(made.collection for made in call(setup.engine, "pim.enable") if made.kind == "contacts")
    call(setup.engine, "contact.add", collection=book, name="Bob", email=("home|bob@x.org",))

    def listed(command: str, **values: object) -> list[str]:
        if command == "message.list":
            return [row.subject for row in call(setup.engine, command, account=setup.account_id, folder="Inbox", **values).messages]
        rows: list[Any] = list(call(setup.engine, command, account=setup.account_id, **values))
        return [row.subject for row in rows]

    for command in ("message.list", "message.search"):
        assert listed(command, sort="contact") == ["Dogs", "Both", "Cats"]  # from a contact first, then newest first
        assert listed(command, sort="contact", oldest_first=True) == ["Both", "Cats", "Dogs"]
        assert listed(command, sort="attachment") == ["Dogs", "Both", "Cats"]
        assert listed(command, sort="preview", oldest_first=True) == ["Cats", "Dogs", "Both"]  # all about cats, all about dogs, cats and dogs
    for sort in ("unread", "flagged", "attachment", "replied", "contact"):
        assert call(setup.engine, "message.groups", account=setup.account_id, folder="Inbox", sort=sort) == ()
    groups: list[Any] = list(call(setup.engine, "message.groups", account=setup.account_id, folder="Inbox", sort="conversation"))
    assert [(group.value, group.count) for group in groups] == [("1", 3)]


def test_a_search_date_at_the_edge_of_the_calendar_is_a_usage_error_never_a_crash(setup: Setup) -> None:
    """The three reviewers of 2026-10-26 (RV-34, RV-36 elsewhere): 0001-01-01 or 9999-12-31 raised OverflowError ("Unexpected error"),
    and on Windows a day before 1970 an OSError, from the platform's conversion. The day is taken in the named zone; the edges are told."""
    for edge in ({"date_from": "0001-01-01"}, {"date_to": "9999-12-31"}):
        try:
            found(setup, **edge)
        except UsageError as error:
            assert "must be a date between" in str(error)
    assert found(setup, date_from="1960-01-01") == found(setup)


def test_a_search_with_subfolders_through_the_commands(setup: Setup) -> None:
    """D-287: subfolders makes a folder's search look in every folder inside it; without it only the folder itself."""
    fill(setup)
    for name in ("Inbox/Old", "Inbox/Old/2025", "Other"):
        call(setup.engine, "folder.create", account=setup.account_id, folder=name)
    rows: list[Any] = list(call(setup.engine, "message.search", account=setup.account_id, folder="Inbox"))
    by_subject = {row.subject: row.message_id for row in rows}
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=by_subject["Dogs"], target="Inbox/Old/2025")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=by_subject["Both"], target="Other")
    assert found(setup, query="about", folder="Inbox") == ["Cats"]
    assert found(setup, query="about", folder="Inbox", subfolders=True) == ["Cats", "Dogs"]
    assert found(setup, query="dogs", folder="Inbox", subfolders=True) == ["Dogs"]  # Other is not inside Inbox
    groups: list[Any] = list(call(setup.engine, "message.groups", account=setup.account_id, query="about", folder="Inbox", subfolders=True))
    assert sum(group.count for group in groups) == 2
    order: Any = call(setup.engine, "message.order", account=setup.account_id, query="about", folder="Inbox", subfolders=True)
    assert order.total == 2 and sorted(order.folders) == ["Inbox", "Inbox/Old/2025"]


NOW = 1_800_000_000.0  # 2027-01-15


def day(seconds_from_now: float) -> str:
    return datetime.fromtimestamp(NOW + seconds_from_now).strftime("%Y-%m-%d")


def test_the_sent_days_the_snoozed_days_and_the_send_days_through_the_commands(setup: Setup) -> None:
    """D-288: Sent searches the message's own date; Snoozed until the day a snoozed message comes back; Send at the day a waiting
    message goes; both of the last two: only what matches both; in a Favorites view too."""
    fill(setup)
    setup.engine.clock = lambda: NOW
    assert found(setup, sent_from="2026-04-01") == ["Both", "Dogs"]
    assert found(setup, sent_to="2026-04-30") == ["Cats", "Dogs"]
    rows: list[Any] = list(call(setup.engine, "message.search", account=setup.account_id, folder="Inbox"))
    by_subject = {row.subject: row.message_id for row in rows}
    soon = datetime.fromtimestamp(NOW + 86400).strftime("%Y-%m-%dT%H:%M")
    later = datetime.fromtimestamp(NOW + 10 * 86400).strftime("%Y-%m-%dT%H:%M")
    call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=by_subject["Cats"], until=soon)
    call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=by_subject["Dogs"], until=later)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Later", body="see you", send_at=soon)
    assert found(setup, snoozed_from=day(5 * 86400)) == ["Dogs"]
    assert found(setup, snoozed_to=day(2 * 86400)) == ["Cats"]
    assert found(setup, send_at_from=day(0), send_at_to=day(2 * 86400)) == ["Later"]
    assert found(setup, send_at_from=day(5 * 86400)) == []
    assert found(setup, snoozed_to=day(2 * 86400), send_at_to=day(2 * 86400)) == []  # nothing is both
    combined: list[Any] = list(call(setup.engine, "message.combined", view="snoozed", snoozed_from=day(5 * 86400)))
    assert [row.subject for row in combined] == ["Dogs"]
