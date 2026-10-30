from chrono_mail.engine.changes import MessageRow
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_contact_sort.py 4.2.432"

# --- Version history ----------------------------------------------------
# v4.2.432: Initial: the Contact sort judges every row by the person the list shows, one rule for the whole list (D-295)


def _sent_to_a_contact_and_flagged(setup: Setup) -> None:
    """Zed and Alice (who wrote the three Inbox messages) are contacts; a message is sent to Zed; all four are flagged."""
    book = next(made.collection for made in call(setup.engine, "pim.enable") if made.kind == "contacts")
    call(setup.engine, "contact.add", collection=book, name="Zed", email=("home|zed@example.org",))
    call(setup.engine, "contact.add", collection=book, name="Alice", email=("home|alice@example.com",))
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert call(setup.engine, "mail.send", account=setup.account_id, to="zed@example.org", subject="To Zed", body="hi").sent
    for folder in ("Inbox", "Sent"):
        rows: list[MessageRow] = list(call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages)
        for row in rows:
            call(setup.engine, "message.flag", account=setup.account_id, folder=folder, message=row.message_id, flagged=True)


def test_in_favorites_flagged_a_sent_message_is_judged_by_its_sender_as_its_row_shows(setup: Setup) -> None:
    _sent_to_a_contact_and_flagged(setup)
    contacts_first: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="flagged", sort="contact")
    contacts_last: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="flagged", sort="contact", oldest_first=True)
    assert contacts_first[-1].subject == contacts_last[0].subject == "To Zed"  # its row shows the sender (this account, no contact)


def test_a_search_of_every_folder_judges_a_sent_message_by_its_sender(setup: Setup) -> None:
    _sent_to_a_contact_and_flagged(setup)
    contacts_first: list[MessageRow] = list(call(setup.engine, "message.search", account=setup.account_id, sort="contact"))
    contacts_last: list[MessageRow] = list(call(setup.engine, "message.search", account=setup.account_id, sort="contact", oldest_first=True))
    assert contacts_first[-1].subject == contacts_last[0].subject == "To Zed"


def test_sent_alone_still_judges_each_message_by_its_first_recipient(setup: Setup) -> None:
    _sent_to_a_contact_and_flagged(setup)
    assert call(setup.engine, "mail.send", account=setup.account_id, to="nobody@example.net", subject="To nobody", body="hi").sent
    folder: list[MessageRow] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Sent", sort="contact").messages)
    favorites: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="sent", sort="contact")
    assert [row.subject for row in folder] == [row.subject for row in favorites] == ["To Zed", "To nobody"]  # the row shows who it went to
