from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import MessageFormatError, StorageError
from chrono_mail.engine import UsageError
from chrono_mail.store import Store
from tests.test_engine_commands import Setup, call, first_id, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_drafts.py 3.6.358"

# --- Version history ----------------------------------------------------
# v3.6.358: draft.save tells where each attachment it was given is in the stored draft (CR-03)
# v3.1.341: a failed send replaces its draft too: the message waits in the Outbox, no second copy beside it (RV-155)
# v2.5.336: the failed save breaks Store.deliver_placed, which a save uses now (D-248)
# v0.0.86: A forward as attachment can be saved as a draft and taken up again
# v0.0.24: Rich text composing
# v0.0.16: Initial draft command tests


def drafts(setup: Setup) -> list[Any]:
    return list(call(setup.engine, "message.list", account=setup.account_id, folder="Drafts").messages)


def test_a_draft_is_stored_in_drafts_with_every_field_and_sent_nowhere(setup: Setup) -> None:
    saved = call(setup.engine, "draft.save", account=setup.account_id, to="Alice <alice@example.com>", cc="carol@example.com", bcc="hidden@example.com", subject="Work in progress", body="first lines\nsecond line")
    assert saved.folder == "Drafts"
    (row,) = drafts(setup)
    assert row.message_id == saved.message_id and sorted(row.flags) == ["draft", "seen"] and row.subject == "Work in progress"
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert opened.to == ("Alice <alice@example.com>",) and opened.cc == ("carol@example.com",) and opened.bcc == ("hidden@example.com",)
    assert opened.subject == "Work in progress" and opened.body.startswith("first lines") and opened.kind == "new" and opened.attachments == ()
    assert setup.smtp.received == [] and call(setup.engine, "message.list", account=setup.account_id, folder="Outbox").total == 0
    assert call(setup.engine, "message.search", account=setup.account_id, query="progress")[0].message_id == saved.message_id  # indexed


def test_saving_again_replaces_the_earlier_version(setup: Setup) -> None:
    first = call(setup.engine, "draft.save", account=setup.account_id, subject="v1", body="one")
    second = call(setup.engine, "draft.save", account=setup.account_id, subject="v2", body="two", replace_message=first.message_id)
    third = call(setup.engine, "draft.save", account=setup.account_id, subject="v3", body="three", replace_folder="Drafts", replace_message=second.message_id)
    assert len({first.message_id, second.message_id, third.message_id}) == 3
    (row,) = drafts(setup)
    assert row.message_id == third.message_id and row.subject == "v3"
    assert call(setup.engine, "message.list", account=setup.account_id, folder="Trash").total == 0  # replaced, not "deleted"


def test_a_failed_save_never_loses_the_earlier_version(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    first = call(setup.engine, "draft.save", account=setup.account_id, subject="precious", body="keep me")
    with pytest.raises(MessageFormatError):  # cannot be built: header injection
        call(setup.engine, "draft.save", account=setup.account_id, subject="x\r\nBcc: evil@example.com", replace_message=first.message_id)
    original = Store.deliver_placed  # where a message is written (deliver gives only its reference)

    def failing(self: Store, *args: object, **kwargs: object) -> object:
        raise StorageError("disk full")

    monkeypatch.setattr(Store, "deliver_placed", failing)
    with pytest.raises(StorageError):
        call(setup.engine, "draft.save", account=setup.account_id, subject="newer", replace_message=first.message_id)
    monkeypatch.setattr(Store, "deliver_placed", original)
    (row,) = drafts(setup)
    assert row.message_id == first.message_id and row.subject == "precious"


def test_attachments_can_be_added_kept_and_dropped_across_saves(setup: Setup, tmp_path: Path) -> None:
    one, two = tmp_path / "one.txt", tmp_path / "two.txt"
    one.write_text("first file")
    two.write_text("second file")
    saved = call(setup.engine, "draft.save", account=setup.account_id, subject="files", attach=str(one))
    assert [(a.position, a.name) for a in call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id).attachments] == [(0, "one.txt")]
    saved = call(setup.engine, "draft.save", account=setup.account_id, subject="files", keep_attachments="0", attach=str(two), replace_message=saved.message_id)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert [a.name for a in opened.attachments] == ["one.txt", "two.txt"] and [a.size for a in opened.attachments] == [10, 11]
    saved = call(setup.engine, "draft.save", account=setup.account_id, subject="files", keep_attachments="1", replace_message=saved.message_id)
    assert [a.name for a in call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id).attachments] == ["two.txt"]
    for bad in ("5", "-1", "x", ""):
        with pytest.raises(UsageError):
            call(setup.engine, "draft.save", account=setup.account_id, keep_attachments=bad or " ", replace_message=saved.message_id)
    with pytest.raises(UsageError, match="stored message"):
        call(setup.engine, "draft.save", account=setup.account_id, keep_attachments="0")
    with pytest.raises(UsageError):
        call(setup.engine, "draft.save", account=setup.account_id, attach=str(tmp_path / "missing.txt"))
    assert len(drafts(setup)) == 1


def test_a_save_tells_where_each_attachment_it_was_given_is_in_the_stored_draft(setup: Setup, tmp_path: Path) -> None:
    """CR-03: the window learns from the save itself where its attachments are now, in the order it gave them."""
    one, two, three = tmp_path / "one.txt", tmp_path / "two.txt", tmp_path / "three.txt"
    for path in (one, two, three):
        path.write_text(path.stem)
    first = call(setup.engine, "draft.save", account=setup.account_id, subject="files", attach=[str(one), str(two)])
    assert first.attachment_positions == (0, 1)
    second = call(setup.engine, "draft.save", account=setup.account_id, subject="files", keep_attachments=["1"], attach=[str(three)], replace_message=first.message_id)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=second.message_id)
    assert second.attachment_positions == (0, 1)
    assert [(item.position, item.name) for item in opened.attachments] == [(0, "two.txt"), (1, "three.txt")]
    assert call(setup.engine, "draft.save", account=setup.account_id, subject="none").attachment_positions == ()


def test_the_places_count_a_part_the_new_text_shows_and_an_attached_message() -> None:
    """A kept file whose cid the new HTML names, and an attached mail (written again), keep their places in turn."""
    from chrono_mail.engine.commands_drafts import _positions  # noqa: PLC2701 - the numbering itself
    from chrono_mail.mime import Address, Attachment, OutgoingMessage, build_message

    given = (
        Attachment("pic.png", "image/png", "<img1@example.com>", False, b"\x89PNG"),
        Attachment("note.eml", "message/rfc822", None, False, b"Subject: x\r\n\r\nbody\r\n"),
        Attachment("a.txt", "text/plain", None, False, b"hello"),
    )
    outgoing = OutgoingMessage(
        from_address=Address("", "me@example.com"), to_addresses=(), cc_addresses=(), bcc_addresses=(), subject="s", text_body="t",
        html_body='<p><img src="cid:img1@example.com"></p>', attachments=given,
    )
    assert _positions(build_message(outgoing, draft=True), (given[0], None, *given[1:])) == (0, -1, 1, 2)


def test_a_part_in_the_middle_that_is_not_read_back_leaves_the_others_their_places(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine.commands_drafts import _positions  # noqa: PLC2701 - the numbering itself
    from chrono_mail.mime import Address, Attachment, OutgoingMessage, build_message

    monkeypatch.setattr("chrono_mail.mime.parser.MAXIMUM_PART_BYTES", 1000)  # a part too large to read back
    given = (
        Attachment("a.txt", "text/plain", None, False, b"first"),
        Attachment("big.bin", "application/octet-stream", None, False, b"\x00" * 5000),
        Attachment("c.txt", "text/plain", None, False, b"third"),
    )
    outgoing = OutgoingMessage(from_address=Address("", "me@example.com"), to_addresses=(), cc_addresses=(), bcc_addresses=(), subject="s", text_body="t", attachments=given)
    assert _positions(build_message(outgoing, draft=True), given) == (0, -1, 1)


def test_a_reply_draft_remembers_what_it_answers(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    original = first_id(setup)
    draft = call(setup.engine, "message.compose", account=setup.account_id, folder="Inbox", message=original, kind="reply")
    saved = call(setup.engine, "draft.save", account=setup.account_id, to=draft.to, subject=draft.subject, body=draft.body, kind="reply", in_reply_to=draft.in_reply_to, references=draft.references, source_folder="Inbox", source_message=original)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert opened.kind == "reply" and opened.source_folder == "Inbox" and opened.source_message == original
    assert opened.in_reply_to == draft.in_reply_to and opened.references == draft.references and "wrote:" in opened.body


def test_a_folder_with_a_slash_in_a_draft_source(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="Projects/Alpha")
    saved = call(setup.engine, "draft.save", account=setup.account_id, kind="forward", source_folder="Projects/Alpha", source_message="a" * 32)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert (opened.kind, opened.source_folder, opened.source_message) == ("forward", "Projects/Alpha", "a" * 32)
    with pytest.raises(UsageError):
        call(setup.engine, "draft.save", account=setup.account_id, source_folder="Inbox", source_message="not-an-id")


def test_empty_unicode_and_large_drafts(setup: Setup) -> None:
    empty = call(setup.engine, "draft.save", account=setup.account_id)
    assert call(setup.engine, "draft.open", account=setup.account_id, message=empty.message_id).subject == ""
    text = "Héllo wörld ☕ 日本語 " * 20000
    saved = call(setup.engine, "draft.save", account=setup.account_id, to="Jörg Müller <joerg@example.com>", subject="Ünïcode ☕", body=text, replace_message=empty.message_id)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert opened.subject == "Ünïcode ☕" and opened.body.replace("\r\n", "\n").strip() == text.strip() and opened.to == ("Jörg Müller <joerg@example.com>",)


def test_invalid_addresses_and_unknown_drafts(setup: Setup) -> None:
    with pytest.raises(UsageError):
        call(setup.engine, "draft.save", account=setup.account_id, to="not an address")
    with pytest.raises(StorageError):
        call(setup.engine, "draft.open", account=setup.account_id, message="0" * 32)
    with pytest.raises(UsageError):
        call(setup.engine, "draft.open", account=setup.account_id, message="short")
    with pytest.raises(UsageError):
        call(setup.engine, "draft.open", account=setup.account_id, message="0" * 32, folder="Nowhere")


def test_sending_removes_the_draft_it_came_from_also_when_it_waits_in_the_outbox(setup: Setup) -> None:
    """A failed send keeps the message in the Outbox, sent from there; a draft left beside it could be sent as a second copy (RV-155)."""
    saved = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Final", body="done")
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    failed = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Final", body="done", replaces_folder="Drafts", replaces_message=saved.message_id)
    assert failed.sent is False and drafts(setup) == [] and call(setup.engine, "message.list", account=setup.account_id, folder="Outbox").total == 1
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-outbox").sent == 1 and call(setup.engine, "message.list", account=setup.account_id, folder="Sent").total == 1
    second = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Next", body="done")
    sent = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Next", body="done", replaces_folder="Drafts", replaces_message=second.message_id)
    assert sent.sent is True and drafts(setup) == []
    assert call(setup.engine, "message.list", account=setup.account_id, folder="Sent").total == 2
    ok_again = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Again", replaces_folder="Drafts", replaces_message="0" * 32)
    assert ok_again.sent is True  # a draft that is already gone does not make a sent message an error


def test_a_message_in_the_outbox_can_be_opened_and_sent_again(setup: Setup) -> None:
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    failed = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", cc="cc@example.com", subject="Stuck", body="please send")
    assert failed.sent is False and failed.folder == "Outbox"
    opened = call(setup.engine, "draft.open", account=setup.account_id, folder="Outbox", message=failed.message_id)
    assert opened.to == ("you@example.com",) and opened.cc == ("cc@example.com",) and opened.subject == "Stuck" and "please send" in opened.body
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    sent = call(setup.engine, "mail.send", account=setup.account_id, to=opened.to, cc=opened.cc, subject=opened.subject, body=opened.body, replaces_folder="Outbox", replaces_message=opened.message_id)
    assert sent.sent is True and call(setup.engine, "message.list", account=setup.account_id, folder="Outbox").total == 0
    assert len(setup.smtp.received) == 1


def test_draft_commands_exist_for_the_command_line() -> None:
    from chrono_mail.engine import build_registry

    assert {"draft.save", "draft.open"} <= set(build_registry().ids())


def test_a_message_can_be_sent_with_the_attachments_of_a_stored_draft(setup: Setup, tmp_path: Path) -> None:
    one, two = tmp_path / "one.txt", tmp_path / "two.txt"
    one.write_text("first file")
    two.write_text("second file")
    saved = call(setup.engine, "draft.save", account=setup.account_id, to="you@example.com", subject="Files", attach=(str(one), str(two)))
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Files", body="see", keep_folder="Drafts", keep_message=saved.message_id, keep_attachments="1",
                  replaces_folder="Drafts", replaces_message=saved.message_id)
    assert result.sent is True
    data = setup.smtp.received[0].data
    assert b"two.txt" in data and b"one.txt" not in data and drafts(setup) == []
    with pytest.raises(UsageError, match="stored message"):
        call(setup.engine, "mail.send", account=setup.account_id, to="a@example.com", keep_attachments="0")
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", account=setup.account_id, to="a@example.com", keep_folder="Inbox", keep_message="0" * 32, keep_attachments="x")


# --- rich text drafts (0.0.24) ---------------------------------------------------------------------------------------------------


HTML = "<html><body><p>Hello <b>bold</b> and <i>slanted</i></p></body></html>"


def test_a_rich_text_draft_keeps_both_versions_and_opens_with_the_html(setup: Setup) -> None:
    saved = call(setup.engine, "draft.save", account=setup.account_id, to="a@example.com", subject="Rich", body="Hello bold and slanted", html=HTML)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=saved.message_id)
    assert opened.body.strip() == "Hello bold and slanted" and opened.html is not None and "<b>bold</b>" in opened.html
    plain = call(setup.engine, "draft.save", account=setup.account_id, subject="Plain", body="only text", replace_message=saved.message_id)
    assert call(setup.engine, "draft.open", account=setup.account_id, message=plain.message_id).html is None


def test_a_rich_text_draft_with_an_attachment_survives_a_second_save(setup: Setup, tmp_path: Path) -> None:
    attachment = tmp_path / "a.txt"
    attachment.write_text("data")
    first = call(setup.engine, "draft.save", account=setup.account_id, subject="Rich", body="x", html=HTML, attach=[str(attachment)])
    second = call(setup.engine, "draft.save", account=setup.account_id, subject="Rich", body="x", html=HTML, keep_attachments=["0"], replace_message=first.message_id)
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=second.message_id)
    assert [item.name for item in opened.attachments] == ["a.txt"] and "<b>bold</b>" in (opened.html or "")


def test_a_rich_text_message_is_sent_as_text_and_html(setup: Setup) -> None:
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Rich", body="Hello bold and slanted", html=HTML)
    assert result.sent
    data = setup.smtp.received[0].data
    assert b"multipart/alternative" in data and b"text/plain" in data and b"text/html" in data and b"<b>bold</b>" in data


def test_a_forward_as_attachment_is_kept_as_such_in_drafts(setup: Setup) -> None:
    saved = call(setup.engine, "draft.save", account=setup.account_id, subject="Fwd: x", body="", kind="forward_attachment")
    opened = call(setup.engine, "draft.open", account=setup.account_id, folder=saved.folder, message=saved.message_id)
    assert opened.kind == "forward_attachment"
