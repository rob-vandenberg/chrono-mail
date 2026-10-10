from datetime import UTC, datetime
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_search.py 0.0.19"

# --- Version history ----------------------------------------------------
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
