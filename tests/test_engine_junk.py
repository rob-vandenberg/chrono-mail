from collections.abc import Generator
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import MessageFlag
from chrono_mail.engine import Engine
from chrono_mail.engine.junkfilter import server_says_junk
from chrono_mail.engine.junkwords import JUNK_SCORE, MINIMUM_LEARNED, JunkWords, fingerprint, load_words, save_words, tokens
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.mime import parse_message
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, add_account, call
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_junk.py 2.1.310"

# --- Version history ----------------------------------------------------
# v2.1.310: the first learning tells how far it is
# v2.1.308: Initial junk filter tests (D-238)

JUNK_WORDS: tuple[str, ...] = ("casino", "jackpot", "viagra", "lottery", "winner", "prize", "bitcoin", "unclaimed", "inheritance", "pharmacy")
GOOD_WORDS: tuple[str, ...] = ("agenda", "meeting", "project", "invoice", "minutes", "schedule", "budget", "colleague", "review", "quarterly")


def junk_mail(number: int, sender: str = "Promo <promo@spam.example>") -> bytes:
    words = " ".join(JUNK_WORDS[(number + step) % len(JUNK_WORDS)] for step in range(6))
    return raw_message(subject=f"Claim your {JUNK_WORDS[number % len(JUNK_WORDS)]} now", sender=sender, body=f"{words} click here\n", message_id=f"<junk{number}@spam.example>")


def good_mail(number: int, sender: str = "Colleague <colleague@work.example>") -> bytes:
    words = " ".join(GOOD_WORDS[(number + step) % len(GOOD_WORDS)] for step in range(6))
    return raw_message(subject=f"The {GOOD_WORDS[number % len(GOOD_WORDS)]} of tomorrow", sender=sender, body=f"{words} see you\n", message_id=f"<good{number}@work.example>")


def server_marked(raw: bytes, value: str = "YES") -> bytes:
    return f"X-Spam-Flag: {value}\r\n".encode() + raw


@pytest.fixture
def setup(tmp_path: Path) -> Generator[Setup]:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        yield Setup(engine, pop, smtp, add_account(engine, pop, smtp))


def deliver(setup: Setup, raw: bytes, folder: str = "Inbox", seen: bool = False) -> str:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.deliver(mailbox.folder(folder), raw, frozenset({MessageFlag.SEEN}) if seen else frozenset()).store_id


def subjects(setup: Setup, folder: str) -> set[str]:
    return {row.subject for row in call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages}


def ids(setup: Setup, folder: str) -> tuple[str, ...]:
    return tuple(row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages)


def learned(engine: Engine) -> JunkWords:
    with engine.junk.lock:
        words = engine.junk.words
    assert words is not None
    return words


def arrive(setup: Setup, *messages: bytes) -> Any:  # noqa: ANN401 - a FetchResult
    for number, raw in enumerate(messages):
        setup.pop.messages[f"new{len(setup.pop.messages)}-{number}"] = raw
    return call(setup.engine, "mail.fetch", account=setup.account_id)


def train(setup: Setup) -> None:
    """The person marks enough junk and good mail for the filter to judge by words."""
    for number in range(MINIMUM_LEARNED):
        deliver(setup, junk_mail(number))
        deliver(setup, good_mail(number), folder="Junk")
    call(setup.engine, "message.junk-many", account=setup.account_id, folder="Inbox", messages=list(ids(setup, "Inbox")))
    call(setup.engine, "message.not-junk-many", account=setup.account_id, folder="Junk", messages=[row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, folder="Junk").messages if "tomorrow" in row.subject])


# --- the words --------------------------------------------------------------------------------------------------------------------------------


def test_the_words_judge_only_after_enough_of_both_and_then_tell_junk_from_good() -> None:
    words = JunkWords()
    for number in range(MINIMUM_LEARNED - 1):
        words.learn(f"j{number}", tokens(parse_message(junk_mail(number))), junk=True)
        words.learn(f"g{number}", tokens(parse_message(good_mail(number))), junk=False)
    assert words.score(tokens(parse_message(junk_mail(99)))) is None  # not enough learned: no judgement at all
    words.learn("j-last", tokens(parse_message(junk_mail(98))), junk=True)
    words.learn("g-last", tokens(parse_message(good_mail(98))), junk=False)
    junk_score, good_score = words.score(tokens(parse_message(junk_mail(99)))), words.score(tokens(parse_message(good_mail(99))))
    assert junk_score is not None and junk_score >= JUNK_SCORE
    assert good_score is not None and good_score < 0.5


def test_a_message_is_counted_once_and_a_correction_unlearns_the_first_verdict() -> None:
    words = JunkWords()
    found = tokens(parse_message(junk_mail(1)))
    assert words.learn("one", found, junk=True) and not words.learn("one", found, junk=True)  # the same message twice: once
    assert (words.junk_messages, words.good_messages) == (1, 0)
    assert words.learn("one", found, junk=False)  # the person corrected it
    assert (words.junk_messages, words.good_messages) == (0, 1) and all(counts[0] == 0 for counts in words.words.values())


def test_the_words_file_is_encrypted_and_comes_back_the_same(setup: Setup) -> None:
    words = JunkWords()
    words.learn("one", tokens(parse_message(junk_mail(1))), junk=True)
    words.places.add("place")
    path = setup.engine.directories.junk_filter_file
    key = setup.engine.data_key(setup.engine.config())
    save_words(path, words, key, durable=False)
    assert b"casino" not in path.read_bytes() and b"jackpot" not in path.read_bytes()
    again = load_words(path, key)
    assert again.words == words.words and again.verdicts == words.verdicts and again.places == {"place"} and again.junk_messages == 1


def test_a_damaged_words_file_is_set_aside_and_the_mail_still_arrives(setup: Setup) -> None:
    path = setup.engine.directories.junk_filter_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a junk filter file")
    result = arrive(setup, good_mail(1))
    assert result.downloaded == 1 and subjects(setup, "Inbox") == {"The meeting of tomorrow"}
    assert path.with_name(path.name + ".bad").exists()


def test_hostile_text_is_cut_and_judged_quickly() -> None:
    raw = raw_message(subject="a" * 100_000, body=("x" * 50 + " ") * 40_000)
    found = tokens(parse_message(raw))
    assert len(found) <= 2_000 and all(len(token) <= 26 for token in found)


# --- the server's verdict ---------------------------------------------------------------------------------------------------------------------


def test_the_servers_spam_lines_are_read() -> None:
    plain = raw_message(subject="x")
    assert server_says_junk(server_marked(plain)) and server_says_junk(b"X-Spam-Status: Yes, score=9.1\r\n" + plain) and server_says_junk(b"X-Spam: yes\r\n" + plain)
    assert not server_says_junk(server_marked(plain, "NO")) and not server_says_junk(b"X-Spam-Status: No, score=0.1\r\n" + plain) and not server_says_junk(plain)


def test_mail_the_server_marked_goes_to_junk_before_the_rules_and_teaches_the_filter(setup: Setup) -> None:
    call(setup.engine, "rule.add", name="Everything to its own folder", condition=[], action=["move_to:Mine"], account=[setup.account_id])
    result = arrive(setup, server_marked(junk_mail(1)), good_mail(1))
    assert result.downloaded == 2 and result.junked == 1 and result.failed == ()
    assert subjects(setup, "Junk") == {"Claim your jackpot now"} and subjects(setup, "Mine") == {"The meeting of tomorrow"} and subjects(setup, "Inbox") == set()
    assert learned(setup.engine).junk_messages == 1  # the server's verdict teaches


def test_the_filter_switched_off_moves_nothing(setup: Setup) -> None:
    call(setup.engine, "account.edit", account=setup.account_id, junk_filter=False)
    result = arrive(setup, server_marked(junk_mail(1)))
    assert result.junked == 0 and subjects(setup, "Inbox") == {"Claim your jackpot now"}


# --- trusted senders --------------------------------------------------------------------------------------------------------------------------


def test_people_written_to_and_senders_a_rule_names_are_never_junk(setup: Setup) -> None:
    deliver(setup, raw_message(subject="My letter", sender="Me <me@example.com>", to="Friend <friend@example.org>"), folder="Sent")
    call(setup.engine, "rule.add", name="Shop", condition=["from:contains:shop.example"], action=["move_to:Shop"])
    result = arrive(setup, server_marked(junk_mail(1, sender="Friend <friend@example.org>")), server_marked(junk_mail(2, sender="Shop <news@shop.example>")))
    assert result.junked == 0 and subjects(setup, "Junk") == set()
    assert subjects(setup, "Inbox") == {"Claim your jackpot now"} and subjects(setup, "Shop") == {"Claim your viagra now"}


def test_ones_own_address_as_the_sender_is_no_pass(setup: Setup) -> None:
    deliver(setup, raw_message(subject="To myself", sender="Me <me@example.com>", to="Me <me@example.com>"), folder="Sent")
    result = arrive(setup, server_marked(junk_mail(1, sender="Me <me@example.com>")))  # a forged sender: spam's favourite
    assert result.junked == 1 and subjects(setup, "Junk") == {"Claim your jackpot now"}


# --- learning from the person -----------------------------------------------------------------------------------------------------------------


def test_after_learning_from_the_person_the_words_catch_junk_without_teaching_themselves(setup: Setup) -> None:
    train(setup)
    words = learned(setup.engine)
    assert (words.junk_messages, words.good_messages) == (MINIMUM_LEARNED, MINIMUM_LEARNED)
    result = arrive(setup, junk_mail(77, sender="Other <other@spam2.example>"), good_mail(77))
    assert result.junked == 1 and "Claim your viagra now" in subjects(setup, "Junk") and "The project of tomorrow" in subjects(setup, "Inbox")
    assert learned(setup.engine).junk_messages == MINIMUM_LEARNED  # its own guess teaches nothing


def test_not_junk_takes_the_message_where_the_rules_would_have_put_it(setup: Setup) -> None:
    call(setup.engine, "rule.add", name="Work", condition=["from:contains:work.example"], action=["move_to:Work"])
    message = deliver(setup, good_mail(1), folder="Junk")
    result = call(setup.engine, "message.not-junk", account=setup.account_id, folder="Junk", message=message)
    assert result.folder == "Work" and subjects(setup, "Work") == {"The meeting of tomorrow"} and subjects(setup, "Inbox") == set()
    assert learned(setup.engine).good_messages == 1


def test_moves_into_junk_teach_junk_and_out_of_it_good_but_not_to_trash(setup: Setup) -> None:
    junk = deliver(setup, junk_mail(1))
    call(setup.engine, "message.move-many", account=setup.account_id, folder="Inbox", target="Junk", messages=[junk])
    assert learned(setup.engine).junk_messages == 1
    (moved,) = ids(setup, "Junk")
    call(setup.engine, "message.move", account=setup.account_id, folder="Junk", target="Trash", message=moved)
    assert learned(setup.engine).good_messages == 0  # thrown away: no verdict on it
    good = deliver(setup, good_mail(1), folder="Junk")
    call(setup.engine, "message.move", account=setup.account_id, folder="Junk", target="Inbox", message=good)
    assert learned(setup.engine).good_messages == 1


def test_a_sent_reply_teaches_that_its_source_is_good(setup: Setup) -> None:
    source = deliver(setup, good_mail(1))
    result = call(
        setup.engine, "mail.send", account=setup.account_id, to=["colleague@work.example"], subject="Re: the meeting", body="Yes\n",
        mark_replied=True, source_folder="Inbox", source_message=source,
    )
    assert result.sent and learned(setup.engine).good_messages == 1


# --- the first learning of the mail kept ------------------------------------------------------------------------------------------------------


def test_the_mail_kept_is_learned_once_junk_folders_as_junk_read_mail_as_good(setup: Setup) -> None:
    deliver(setup, junk_mail(1), folder="Junk")
    deliver(setup, junk_mail(2), folder="Junk")
    deliver(setup, good_mail(1), seen=True)
    deliver(setup, good_mail(2))  # unread: not taken as good
    deliver(setup, good_mail(3), folder="Trash", seen=True)  # thrown away: not taken as good
    first = call(setup.engine, "junk.learn-existing")
    assert (first.places, first.junk, first.good) == (1, 2, 1)
    assert fingerprint(good_mail(1)) in learned(setup.engine).verdicts
    second = call(setup.engine, "junk.learn-existing")
    assert (second.places, second.junk, second.good) == (0, 0, 0)  # once per place where mail is kept


def test_the_first_learning_tells_how_far_it_is_every_100_messages(setup: Setup) -> None:
    for number in range(150):
        deliver(setup, junk_mail(number), folder="Junk")
    told: list[tuple[str, int, object]] = []
    setup.engine.progress = lambda what, done, total: told.append((what, done, total))
    call(setup.engine, "junk.learn-existing")
    assert told == [("Junk filter: learning from your mail, mailbox 1 of 1", 0, 150), ("Junk filter: learning from your mail, mailbox 1 of 1", 100, 150)]


# --- IMAP: the move to Junk goes to the server first ------------------------------------------------------------------------------------------


def test_imap_junk_goes_to_the_servers_junk_folder(world: World) -> None:
    world.imap.add("INBOX", server_marked(junk_mail(1)))
    world.imap.add("INBOX", good_mail(1))
    result = world.fetch()
    assert result.junked == 1
    assert [row.subject for row in world.rows("Junk")] == ["Claim your jackpot now"] and [row.subject for row in world.rows("Inbox")] == ["The meeting of tomorrow"]
    assert len(world.imap.uids("Junk")) == 1 and len(world.imap.uids("INBOX")) == 1
