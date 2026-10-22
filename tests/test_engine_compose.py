from datetime import UTC, datetime

import pytest

from chrono_mail.engine.compose import ComposeKind, build_draft, subject_for
from chrono_mail.mime import Address, Attachment, ParsedMessage

# --- Version ------------------------------------------------------------
__version__ = "test_engine_compose.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Settings window with pages for everything
# v0.0.13: Initial reply and forward draft tests

ME = "me@example.com"
ALICE = Address("Alice", "alice@example.com")
BOB = Address("", "bob@example.com")
WHEN = datetime(2026, 10, 8, 10, 30, tzinfo=UTC)


def message(
    subject: str = "Plan",
    from_addresses: tuple[Address, ...] = (ALICE,),
    to_addresses: tuple[Address, ...] = (Address("Me", ME), BOB),
    reply_to_addresses: tuple[Address, ...] = (),
    text_body: str | None = "line one\n\nline three\n",
    html_body: str | None = None,
    attachments: tuple[Attachment, ...] = (),
) -> ParsedMessage:
    return ParsedMessage(
        subject=subject, from_addresses=from_addresses, to_addresses=to_addresses, cc_addresses=(Address("", "carol@example.com"),),
        reply_to_addresses=reply_to_addresses, date=WHEN, message_id="<m1@x>", references=("<root@x>",), text_body=text_body,
        html_body=html_body, attachments=attachments,
    )


@pytest.mark.parametrize(
    ("subject", "kind", "expected"),
    [("Plan", "reply", "Re: Plan"), ("Re: Plan", "reply", "Re: Plan"), ("RE: Plan", "reply", "RE: Plan"), ("AW: Plan", "reply", "AW: Plan"), ("re:Plan", "reply_all", "re:Plan"),
     ("Plan", "forward", "Fwd: Plan"), ("Fwd: Plan", "forward", "Fwd: Plan"), ("FW: Plan", "forward", "FW: Plan"), ("WG: Plan", "forward", "WG: Plan"),
     ("Re: Plan", "forward", "Fwd: Re: Plan"), ("Fwd: Plan", "reply", "Re: Fwd: Plan"), ("", "reply", "Re: "), ("Reply needed", "reply", "Re: Reply needed")],
)
def test_subject_prefix_is_added_once(subject: str, kind: ComposeKind, expected: str) -> None:
    assert subject_for(subject, kind) == expected


def test_reply_goes_to_the_sender_and_quotes_the_text() -> None:
    draft = build_draft(message(), "reply", ME)
    assert draft.to == ("Alice <alice@example.com>",) and draft.cc == ()
    assert draft.subject == "Re: Plan" and draft.in_reply_to == "<m1@x>" and draft.references == ("<root@x>", "<m1@x>")
    assert draft.body == "\n\nOn Thu, 08 Oct 2026 10:30, Alice <alice@example.com> wrote:\n> line one\n>\n> line three\n"
    assert draft.attachment_names == ()


def test_reply_to_header_wins() -> None:
    draft = build_draft(message(reply_to_addresses=(Address("List", "list@example.org"),)), "reply", ME)
    assert draft.to == ("List <list@example.org>",)


def test_reply_all_adds_the_other_recipients_but_not_me_or_duplicates() -> None:
    draft = build_draft(message(to_addresses=(Address("Me", "ME@Example.com"), BOB, ALICE)), "reply_all", ME)
    assert draft.to == ("Alice <alice@example.com>",)
    assert draft.cc == ("bob@example.com", "carol@example.com")


def test_replying_to_my_own_message_goes_to_the_people_i_wrote_to() -> None:
    mine = message(from_addresses=(Address("Me", ME),), to_addresses=(ALICE, BOB))
    assert build_draft(mine, "reply", ME).to == ("Alice <alice@example.com>", "bob@example.com")
    assert build_draft(message(from_addresses=(Address("Me", "ME@example.com"),), to_addresses=()), "reply", ME).to == ()


def test_html_only_message_is_quoted_as_text() -> None:
    draft = build_draft(message(text_body=None, html_body="<p>Hello <b>there</b></p><script>x()</script>"), "reply", ME)
    assert draft.body.endswith("wrote:\n> Hello there\n") and "x()" not in draft.body
    paragraphs = build_draft(message(text_body=None, html_body="<div>First <i>para</i></div><p>Second</p><ul><li>one</li><li>two</li></ul><br><br><br><br>Last"), "reply", ME)
    assert paragraphs.body.endswith("wrote:\n> First para\n>\n> Second\n>\n> one\n> two\n>\n> Last\n")


def test_message_without_sender_date_or_body() -> None:
    draft = build_draft(ParsedMessage(), "reply", ME)
    assert draft.to == () and draft.subject == "Re: " and draft.in_reply_to is None and draft.references == ()
    assert "an unknown sender wrote:" in draft.body and "an unknown date" in draft.body
    assert build_draft(ParsedMessage(), "forward", ME).body.startswith("\n\n---------- Forwarded message")


def test_forward() -> None:
    attachment = Attachment("a.pdf", "application/pdf", None, False, b"x")
    inline = Attachment("logo.png", "image/png", "<l>", True, b"y")
    draft = build_draft(message(attachments=(inline, attachment)), "forward", ME)
    assert draft.to == () and draft.cc == () and draft.subject == "Fwd: Plan" and draft.in_reply_to is None
    assert draft.attachment_names == ("a.pdf",)  # inline pictures belong to the body
    assert draft.body.startswith("\n\n---------- Forwarded message ----------\nFrom: Alice <alice@example.com>\nDate: Thu, 08 Oct 2026 10:30\nSubject: Plan\nTo: Me <me@example.com>, bob@example.com\nCc: carol@example.com\n\nline one")


def test_hostile_text_does_not_break_the_draft() -> None:
    nasty = "\x00\r\n> ".join(["a" * 5000] * 3)
    draft = build_draft(message(text_body=nasty, subject="x\x00y"), "reply", ME)
    assert isinstance(draft.body, str) and draft.subject == "Re: x\x00y"


def test_the_quote_style_of_a_reply() -> None:
    original = message(text_body="line one\nline two\n")
    above = build_draft(original, "reply", "me@example.com")
    below = build_draft(original, "reply", "me@example.com", "below")
    none = build_draft(original, "reply", "me@example.com", "none")
    assert above.body.startswith("\n\n") and "> line one" in above.body and above.body == build_draft(original, "reply", "me@example.com", "above").body
    assert not below.body.startswith("\n") and below.body.endswith("\n\n\n") and "> line two" in below.body
    assert none.body == "\n\n" and "> " not in none.body
    forward = build_draft(original, "forward", "me@example.com", "none")
    assert "Forwarded message" in forward.body  # the style is for replies only
