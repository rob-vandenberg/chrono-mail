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
__version__ = "test_engine_search.py 3.6.351"

# --- Version history ----------------------------------------------------
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
