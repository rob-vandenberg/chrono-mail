from typing import Any  # command results are command-specific dataclasses

from chrono_mail.config import ServerRole
from chrono_mail.mime import parse_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_schedule import at, iso, rows

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_outbox.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: Initial Send All and forward as attachment tests


def failed_send(setup: Setup, subject: str = "Waiting") -> Any:  # noqa: ANN401
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", cc="copy@example.com", bcc="hidden@example.com", subject=subject, body="x")
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    return result


def test_send_all_sends_what_waits_in_the_outbox_with_its_bcc(setup: Setup) -> None:
    result = failed_send(setup)
    assert not result.sent and setup.smtp.received == []
    (waiting,) = rows(setup, "Outbox")
    assert waiting.subject == "Waiting"
    done = call(setup.engine, "mail.send-outbox")
    assert (done.sent, done.failed) == (1, ())
    (received,) = setup.smtp.received
    assert set(received.recipients) == {"you@example.com", "copy@example.com", "hidden@example.com"} and b"hidden@example.com" not in received.data
    assert rows(setup, "Outbox") == [] and [row.subject for row in rows(setup, "Sent")] == ["Waiting"]
    sent_copy = call(setup.engine, "message.show", account=setup.account_id, folder="Sent", message=rows(setup, "Sent")[0].message_id)
    assert sent_copy is not None
    assert call(setup.engine, "mail.send-outbox").sent == 0 and len(setup.smtp.received) == 1  # never twice


def test_send_all_leaves_scheduled_messages_and_keeps_a_failure_in_the_outbox(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Later", body="x", send_at=iso(3600))
    failed_send(setup)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    done = call(setup.engine, "mail.send-outbox")
    assert done.sent == 0 and len(done.failed) == 1 and "Waiting" in done.failed[0] and "stays in the Outbox" in done.failed[0]
    assert sorted(row.subject for row in rows(setup, "Outbox")) == ["Later", "Waiting"] and setup.smtp.received == []
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-outbox").sent == 1 and [row.subject for row in rows(setup, "Outbox")] == ["Later"]


def test_forward_as_attachment_sends_the_original_as_an_eml_file(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    original = rows(setup, "Inbox")[0]
    draft = call(setup.engine, "message.compose", account=setup.account_id, folder="Inbox", message=original.message_id, kind="forward_attachment")
    assert draft.kind == "forward_attachment" and draft.subject == f"Fwd: {original.subject}" and draft.body.strip() == "" and draft.attachment_names == (f"{original.subject}.eml",)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject=draft.subject, body="see below", source_folder="Inbox", source_message=original.message_id, attach_source_message=True)
    (received,) = setup.smtp.received
    assert b"Content-Type: message/rfc822" in received.data and b"Content-Transfer-Encoding: base64\r\nContent-Disposition: attachment; filename" not in received.data
    parsed = parse_message(received.data)
    (attached,) = parsed.attachments
    assert attached.filename == f"{original.subject}.eml" and attached.content_type == "message/rfc822" and original.subject.encode() in attached.data
