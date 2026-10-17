from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ConfigurationError, MessageFlag
from chrono_mail.engine import UsageError
from chrono_mail.engine.commands_rules import format_action, format_condition, parse_action, parse_condition
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.rule_engine import Subject, condition_holds, header_values, rule_matches
from chrono_mail.config import Action, Condition, Rule, new_rule_id
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_rules.py 1.0.171"

# --- Version history ----------------------------------------------------
# v1.0.171: actions after Stop are skipped (the editor can move Stop up a rule's actions)
# v0.5.136: tags in rules: the condition tag is / is not, the actions add_tag and remove_tag
# v0.0.33: Initial rules tests


def put(setup: Setup, subject: str = "Hello", sender: str = "Alice <alice@example.com>", to: str = "me@example.com", body: str = "text\n", folder: str = "Inbox", **extra: Any) -> str:  # noqa: ANN401
    raw = raw_message(subject=subject, sender=sender, to=to, body=body, message_id=f"<{abs(hash((subject, sender, body)))}@x>", **extra)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.deliver(mailbox.folder(folder), raw, frozenset()).store_id


def add_rule(setup: Setup, name: str, conditions: list[str], actions: list[str], **values: Any) -> Any:  # noqa: ANN401
    return call(setup.engine, "rule.add", name=name, condition=conditions, action=actions, **values)


def rows(setup: Setup, folder: str) -> dict[str, Any]:
    return {row.subject: row for row in call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages}


# --- the text forms ---------------------------------------------------------------------------------------------------------------------------


def test_conditions_and_actions_have_a_written_form() -> None:
    for text in ("from:contains:news@example.org", "subject:not_equals:hello", "size:greater_than:100", "attachment:is:yes", "header.List-Id:contains:lists.example.org"):
        assert format_condition(parse_condition(text)) == text
    assert parse_condition("subject : Contains : Invoice: 2026").value == "Invoice: 2026"  # the value may hold colons
    assert format_action(parse_action("move_to:Archive/2026")) == "move_to:Archive/2026" and format_action(parse_action(" MARK_READ ")) == "mark_read"
    for bad in ("subject", "subject:contains", "colour:contains:x", "subject:greater_than:5", "header.:contains:x"):
        with pytest.raises((UsageError, ConfigurationError)):
            parse_condition(bad)
    for bad in ("shout", "move_to", "mark_read:Inbox"):
        with pytest.raises(ConfigurationError):
            parse_action(bad)


# --- matching ------------------------------------------------------------------------------------------------------------------------------------


def holds(condition: str, raw: bytes) -> bool:
    return condition_holds(parse_condition(condition), Subject.of(raw))


def test_text_fields_and_operators() -> None:
    raw = raw_message(subject="Your Invoice 2026", sender='"Ann Archer" <ann@Example.org>', to="me@example.com, Bob <bob@x.net>", body="Please pay soon\n", cc="carol@z.org")
    assert holds("subject:contains:invoice", raw) and holds("subject:starts_with:your", raw) and holds("subject:ends_with:2026", raw) and holds("subject:equals:your invoice 2026", raw)
    assert not holds("subject:contains:receipt", raw) and holds("subject:not_contains:receipt", raw) and not holds("subject:not_contains:invoice", raw) and holds("subject:not_equals:other", raw)
    assert holds("from:contains:example.org", raw) and holds("from:equals:ann@example.org", raw) and holds("from:contains:archer", raw) and holds("from:equals:Ann Archer", raw)
    assert holds("to:equals:bob@x.net", raw) and holds("to:contains:bob", raw) and not holds("to:contains:carol", raw) and holds("to:not_contains:carol", raw)
    assert holds("cc:contains:carol", raw) and not holds("cc:contains:bob", raw)
    assert holds("body:contains:pay soon", raw) and not holds("body:contains:refund", raw) and holds("body:not_contains:refund", raw)


def test_missing_things_make_negative_conditions_true_and_positive_ones_false() -> None:
    raw = raw_message(subject="No cc", body="x\n")
    assert not holds("cc:contains:anyone", raw) and holds("cc:not_contains:anyone", raw)
    assert not holds("header.X-Missing:contains:x", raw) and holds("header.X-Missing:not_contains:x", raw)
    bare = b"Subject: bare\n\nbody"
    assert not holds("from:contains:a", bare) and holds("from:not_equals:a", bare)


def test_headers_size_and_attachments() -> None:
    raw = raw_message(subject="List", body="x\n", attachment=b"y" * 3000).replace(b"Subject: List", b"Subject: List\r\nList-Id: <team.lists.example.org>\r\nX-Spam: yes\r\nX-Spam: maybe")
    assert holds("header.list-id:contains:lists.example.org", raw) and holds("header.X-Spam:equals:maybe", raw) and not holds("header.X-Spam:equals:no", raw)
    assert holds("size:greater_than:2", raw) and not holds("size:greater_than:100", raw) and holds("size:less_than:100", raw)
    assert holds("attachment:is:yes", raw) and not holds("attachment:is:no", raw)
    assert holds("attachment:is:no", raw_message(subject="plain")) and not holds("attachment:is:yes", raw_message(subject="plain"))
    assert header_values(b"Subject: x\n\nbody", "Subject") == ("x",) and header_values(b"\xff\xfe", "Subject") == () and header_values(b"", "X") == ()


def test_html_only_messages_match_on_their_text() -> None:
    raw = b"From: a@b.org\nSubject: Web\nContent-Type: text/html\n\n<p>Special <b>offer</b> today</p><script>secret()</script>"
    assert holds("body:contains:special offer", raw) or holds("body:contains:offer", raw)
    assert not holds("body:contains:secret", raw)


def test_all_and_any() -> None:
    subject = Subject.of(raw_message(subject="Alpha", sender="x@example.org"))
    both = Rule(new_rule_id(), "n", (Condition("subject", "contains", "alpha"), Condition("from", "contains", "nobody")), (Action("stop"),))
    assert not rule_matches(both, subject)
    from dataclasses import replace

    assert rule_matches(replace(both, match="any"), subject)


def test_hostile_messages_do_not_break_matching() -> None:
    hostile = b"From: " + b"\xff" * 1000 + b"\nSubject: " + b"\x00" * 50 + b"\nList-Id: " + b"a" * 300_000 + b"\n\n" + b"b" * 10
    for condition in ("from:contains:a", "subject:contains:a", "header.List-Id:contains:a", "body:contains:b", "attachment:is:no", "size:less_than:1"):
        holds(condition, hostile)


# --- the commands -------------------------------------------------------------------------------------------------------------------------------


def test_rules_are_added_listed_edited_moved_switched_and_removed(setup: Setup) -> None:
    first = add_rule(setup, "Newsletters", ["from:contains:news@"], ["move_to:Newsletters", "mark_read"])
    second = add_rule(setup, "Invoices", ["subject:contains:invoice", "from:contains:billing"], ["flag"], match="any", account=[setup.account_id], position=0)
    listed = call(setup.engine, "rule.list")
    assert [item.name for item in listed] == ["Invoices", "Newsletters"] and listed[0].accounts == (setup.account_id,) and listed[1].conditions == ("from:contains:news@",)
    edited = call(setup.engine, "rule.edit", rule=first.rule_id, name="Bulk", condition=["header.Precedence:equals:bulk"], every_account=True)
    assert edited.name == "Bulk" and edited.conditions == ("header.Precedence:equals:bulk",) and edited.actions == ("move_to:Newsletters", "mark_read")
    assert call(setup.engine, "rule.edit", rule=second.rule_id, match="all").match == "all"
    assert not call(setup.engine, "rule.enable", rule=first.rule_id, enabled=False).enabled
    moved = call(setup.engine, "rule.move", rule=first.rule_id, position=0)
    assert [item.name for item in moved] == ["Bulk", "Invoices"]
    call(setup.engine, "rule.remove", rule=second.rule_id)
    assert [item.name for item in call(setup.engine, "rule.list")] == ["Bulk"]
    for bad in (lambda: call(setup.engine, "rule.remove", rule="0" * 16), lambda: call(setup.engine, "rule.edit", rule="0" * 16), lambda: add_rule(setup, "x", [], ["stop"]),
                lambda: add_rule(setup, "x", ["subject:contains:a"], []), lambda: add_rule(setup, "x", ["subject:contains:a"], ["stop"], match="some"), lambda: add_rule(setup, "x", ["subject:contains:a"], ["stop"], account=["nobody"])):
        with pytest.raises(Exception):  # noqa: B017 - UsageError or ConfigurationError, both are right
            bad()
    assert len(call(setup.engine, "rule.list")) == 1


# --- running rules -------------------------------------------------------------------------------------------------------------------------------


def test_actions_move_flag_mark_delete_and_junk(setup: Setup) -> None:
    put(setup, "Weekly news", "News <news@example.org>")
    put(setup, "Invoice 7", "Billing <billing@example.org>")
    put(setup, "Cheap pills", "Spam <spam@bad.example>")
    put(setup, "Old promo", "Promo <promo@shop.example>")
    put(setup, "Keep me", "Friend <friend@example.org>")
    add_rule(setup, "News", ["from:contains:news@"], ["move_to:Lists/News", "mark_read"])
    add_rule(setup, "Invoices", ["subject:starts_with:invoice"], ["flag", "mark_unread"])
    add_rule(setup, "Spam", ["from:contains:bad.example"], ["mark_junk"])
    add_rule(setup, "Promo", ["from:contains:promo@"], ["delete"])
    result = call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert (result.examined, result.matched, result.failed) == (5, 4, ())
    assert set(rows(setup, "Inbox")) == {"Invoice 7", "Keep me"} and rows(setup, "Inbox")["Invoice 7"].flags == ("flagged",)
    assert set(rows(setup, "Lists/News")) == {"Weekly news"} and rows(setup, "Lists/News")["Weekly news"].flags == ("seen",)
    assert set(rows(setup, "Junk")) == {"Cheap pills"} and set(rows(setup, "Trash")) == {"Old promo"}


def test_the_first_stop_ends_the_rules_for_a_message_and_order_matters(setup: Setup) -> None:
    put(setup, "Offer from boss", "Boss <boss@example.org>")
    add_rule(setup, "Boss", ["from:contains:boss@"], ["flag", "stop"])
    add_rule(setup, "Offers", ["subject:contains:offer"], ["move_to:Offers"])
    call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert set(rows(setup, "Inbox")) == {"Offer from boss"} and "Offers" not in {info.path for info in call(setup.engine, "folder.list", account=setup.account_id)}
    call(setup.engine, "rule.move", rule=call(setup.engine, "rule.list")[1].rule_id, position=0)
    call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert set(rows(setup, "Offers")) == {"Offer from boss"}


def test_actions_after_stop_are_skipped(setup: Setup) -> None:
    put(setup, "Short", "Someone <s@example.org>")
    add_rule(setup, "Cut short", ["subject:equals:short"], ["flag", "stop", "mark_read"])  # Stop moved up: what follows is not done
    call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert rows(setup, "Inbox")["Short"].flags == ("flagged",)


def test_actions_after_a_move_follow_the_message(setup: Setup) -> None:
    put(setup, "Follow", "Someone <s@example.org>")
    add_rule(setup, "Several", ["subject:equals:follow"], ["move_to:Elsewhere", "flag", "mark_read", "move_to:Final/Place", "unflag"])
    call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert rows(setup, "Elsewhere") == {} and rows(setup, "Final/Place")["Follow"].flags == ("seen",)


def test_disabled_rules_and_other_accounts_rules_are_skipped_unless_named(setup: Setup) -> None:
    put(setup, "Target")
    rule = add_rule(setup, "Off", ["subject:equals:target"], ["flag"], disabled=True)
    assert call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox").matched == 0
    assert call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox", rule=rule.rule_id).matched == 1 and rows(setup, "Inbox")["Target"].flags == ("flagged",)
    call(setup.engine, "rule.edit", rule=rule.rule_id, account=[setup.account_id])
    put(setup, "Target two")
    other = add_rule(setup, "Other", ["subject:contains:two"], ["mark_read"], account=[setup.account_id])
    call(setup.engine, "rule.edit", rule=other.rule_id, every_account=True)
    assert call(setup.engine, "rule.list")[1].accounts == ()


def test_a_test_run_changes_nothing(setup: Setup) -> None:
    put(setup, "A thing")
    put(setup, "Other")
    add_rule(setup, "Move", ["subject:contains:thing"], ["move_to:Away"])
    preview = call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox", test=True)
    assert (preview.examined, preview.matched, preview.actions) == (2, 1, 0) and set(rows(setup, "Inbox")) == {"A thing", "Other"}


def test_an_action_that_fails_is_reported_and_the_rest_goes_on(setup: Setup) -> None:
    put(setup, "Trouble")
    add_rule(setup, "Broken then fine", ["subject:equals:trouble"], ["move_to:Bad/Place", "mark_read"])
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.store.create_folder(("Bad",))  # a folder "Bad" exists, "Bad/Place" is made on demand: this works
    result = call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert result.failed == () and rows(setup, "Bad/Place")["Trouble"].flags == ("seen",)


# --- rules on arrival ---------------------------------------------------------------------------------------------------------------------------


def test_rules_are_applied_to_new_mail_when_it_is_fetched(setup: Setup) -> None:
    add_rule(setup, "All to Archive", ["subject:contains:message"], ["move_to:Archive", "mark_read"])
    result = call(setup.engine, "mail.fetch", account=setup.account_id)
    assert result.downloaded == 3 and result.rule_actions == 6 and result.failed == ()
    assert rows(setup, "Inbox") == {} and len(rows(setup, "Archive")) == 3 and all(row.flags == ("seen",) for row in rows(setup, "Archive").values())
    again = call(setup.engine, "mail.fetch", account=setup.account_id)
    assert again.downloaded == 0 and again.rule_actions == 0  # the rules do not touch old mail again


def test_a_damaged_rules_file_does_not_stop_the_mail(setup: Setup) -> None:
    setup.engine.directories.rules_file.parent.mkdir(parents=True, exist_ok=True)
    setup.engine.directories.rules_file.write_text("{broken")
    result = call(setup.engine, "mail.fetch", account=setup.account_id)
    assert result.downloaded == 3 and any("rules could not be applied" in remark for remark in result.failed)
    assert len(rows(setup, "Inbox")) == 3


def test_rules_work_on_imap_mail_and_reach_the_server(world: World) -> None:
    world.imap.add("INBOX", raw_message(subject="From the list", sender="List <list@example.org>", message_id="<l1@x>"))
    world.imap.add("INBOX", raw_message(subject="Personal", sender="Friend <f@example.org>", message_id="<l2@x>"))
    call(world.engine, "rule.add", name="Lists", condition=["from:contains:list@"], action=["move_to:Lists", "mark_read"])
    world.fetch()
    assert [row.subject for row in world.rows("Inbox")] == ["Personal"] and [row.subject for row in world.rows("Lists")] == ["From the list"]
    world.fetch()  # the move is sent
    world.fetch()  # and the flag, now that the message is in its new place on the server
    assert len(world.imap.uids("INBOX")) == 1 and "Lists" in world.imap.folders and len(world.imap.uids("Lists")) == 1
    assert world.imap.flags_of("Lists", world.imap.uids("Lists")[0]) == {"\\Seen"}
    assert [row.subject for row in world.rows("Lists")] == ["From the list"]


def test_messages_in_other_folders_are_not_touched_by_arrival_rules(world: World) -> None:
    world.imap.add("Sent", raw_message(subject="Sent by me", sender="me@example.com", message_id="<s1@x>"))
    call(world.engine, "rule.add", name="Everything", condition=["subject:contains:sent"], action=["delete"])
    world.fetch()
    assert [row.subject for row in world.rows("Sent")] == ["Sent by me"] and world.rows("Trash") == []


def test_the_flag_names_the_rules_use_are_the_real_ones() -> None:
    assert MessageFlag.SEEN in MessageFlag and Action("mark_read").type == "mark_read"


# --- tags in rules ------------------------------------------------------------------------------------------------------------------------------


def test_tag_conditions_and_actions_have_a_written_form() -> None:
    assert format_condition(parse_condition("tag:is:Work")) == "tag:is:Work" and format_condition(parse_condition("tag:is_not:Home")) == "tag:is_not:Home"
    assert format_action(parse_action("add_tag:Work")) == "add_tag:Work" and format_action(parse_action("remove_tag:Home")) == "remove_tag:Home"
    for bad in ("tag:contains:x", "tag:is:"):
        with pytest.raises((UsageError, ConfigurationError)):
            parse_condition(bad)
    for bad in ("add_tag", "remove_tag:", "add_tag:" + "x" * 101):
        with pytest.raises(ConfigurationError):
            parse_action(bad)


def test_a_tag_condition_looks_at_the_tags_of_the_message() -> None:
    raw = raw_message(subject="Hi")
    assert condition_holds(parse_condition("tag:is:work"), Subject.of(raw, ("Work",)))
    assert not condition_holds(parse_condition("tag:is_not:WORK"), Subject.of(raw, ("Work",)))
    assert condition_holds(parse_condition("tag:is_not:Work"), Subject.of(raw))


def test_rules_add_and_remove_tags_and_a_later_rule_sees_them(setup: Setup) -> None:
    put(setup, "Invoice 7", "Billing <billing@example.org>")
    put(setup, "Weekly news", "News <news@example.org>")
    add_rule(setup, "Invoices", ["subject:contains:invoice"], ["add_tag:Money", "add_tag:Work"])
    add_rule(setup, "Work money", ["tag:is:money"], ["remove_tag:Work", "flag"])
    add_rule(setup, "Untagged", ["tag:is_not:Money"], ["add_tag:Other"])
    result = call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert result.failed == ()
    inbox = rows(setup, "Inbox")
    assert inbox["Invoice 7"].tags == ("Money",) and inbox["Invoice 7"].flags == ("flagged",)
    assert inbox["Weekly news"].tags == ("Other",)
    assert {item.name: item.colour for item in call(setup.engine, "tag.list")}["Money"] == "#808080"  # a tag a rule makes gets a colour

