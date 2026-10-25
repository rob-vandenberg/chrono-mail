"""The words of the junk filter (D-238): which words appear in junk mail and which in good mail, how sure a new message is junk,
and the one file that keeps them. A learning filter as Paul Graham described and Gary Robinson refined (each word's spam
probability, combined with Fisher's method). No I/O beyond its own file; the behaviour around it is in junkfilter.py."""

import hashlib
import json
import math
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.core import CryptoError, StorageError
from chrono_mail.crypto import DataKey, decrypt_message, encrypt_message
from chrono_mail.index import html_to_text
from chrono_mail.mime import ParsedMessage
from chrono_mail.store.atomic import replace_file_atomically
from chrono_mail.core.jsontext import load_json
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "junkwords.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v2.2.313: set_verdict: a message's verdict set to junk, good or none at all (Undo puts back what was learned before, B-18)
# v2.1.308: Initial junk filter words: tokens, learning, scoring, the encrypted file (D-238)

FORMAT: int = 1
MAXIMUM_TEXT_CHARACTERS: int = 100_000  # of a body: a huge message is judged by its start
MAXIMUM_TOKENS_PER_MESSAGE: int = 2_000
MAXIMUM_WORDS: int = 150_000  # the words kept; the least seen go first
MAXIMUM_REMEMBERED: int = 100_000  # messages whose verdict is remembered (so a message is never counted twice); the oldest go first
MINIMUM_LEARNED: int = 20  # junk and good messages each, before the filter judges by words at all
JUNK_SCORE: float = 0.95  # only mail the filter is very sure about is junk
INTERESTING_WORDS: int = 150  # the words furthest from "no idea" decide
UNKNOWN_STRENGTH: float = 1.0  # Robinson's s: how much a rarely seen word leans to 0.5
WORD: re.Pattern[str] = re.compile(r"[^\W_]{3,24}")  # letters and digits only: linear on any input
JUNK, GOOD = "j", "g"


def fingerprint(raw: bytes) -> str:
    """One message, wherever it is: a move keeps the bytes, so it keeps the fingerprint."""
    return hashlib.sha256(raw).hexdigest()[:32]


def _words(text: str, prefix: str, into: set[str]) -> None:
    for match in WORD.finditer(text[:MAXIMUM_TEXT_CHARACTERS]):
        word = match.group().casefold()
        if not digits(word):
            into.add(prefix + word)
        if len(into) >= MAXIMUM_TOKENS_PER_MESSAGE:
            return


def tokens(parsed: ParsedMessage) -> frozenset[str]:
    """The words of a message the filter weighs: the subject's, the sender's and the body's, each kind apart."""
    found: set[str] = set()
    _words(parsed.subject, "s:", found)
    for address in parsed.from_addresses[:1]:
        _words(address.name, "n:", found)
        domain = address.address.rpartition("@")[2].casefold()
        if domain:
            found.add("d:" + domain[:100])
    body = parsed.text_body or (html_to_text(parsed.html_body) if parsed.html_body else "")
    _words(body, "", found)
    if parsed.html_body and not parsed.text_body:
        found.add("x:html-only")
    if any(not attachment.inline for attachment in parsed.attachments):
        found.add("x:attachment")
    return frozenset(found)


def _chi2q(chi: float, freedom: int) -> float:
    """The chance that a chi-squared value this high comes by chance (an even number of degrees of freedom)."""
    half = chi / 2.0
    term = total = math.exp(-half)
    for step in range(1, freedom // 2):
        term *= half / step
        total += term
    return min(total, 1.0)


@dataclass
class JunkWords:
    """What the filter has learned. Not thread-safe: JunkHolder guards it."""

    junk_messages: int = 0
    good_messages: int = 0
    words: dict[str, list[int]] = field(default_factory=dict)  # word -> [in junk messages, in good messages]
    verdicts: dict[str, str] = field(default_factory=dict)  # fingerprint -> JUNK or GOOD: what it was learned as
    places: set[str] = field(default_factory=set)  # the places where mail is kept whose existing mail was learned once

    def learn(self, key: str, found: frozenset[str], junk: bool) -> bool:
        """Learn one message as junk or good; one learned the other way first is unlearned. False: it was learned so already."""
        return self.set_verdict(key, found, JUNK if junk else GOOD)

    def set_verdict(self, key: str, found: frozenset[str], wanted: str | None) -> bool:
        """One message's verdict: JUNK, GOOD, or None (not learned at all); its words are counted again to match. False: it was so already."""
        before = self.verdicts.pop(key, None)
        if before == wanted:
            if wanted is not None:
                self.verdicts[key] = wanted
            return False
        if before is not None:
            self._count(found, before, -1)
        if wanted is None:
            return True
        self._count(found, wanted, 1)
        self.verdicts[key] = wanted
        while len(self.verdicts) > MAXIMUM_REMEMBERED:
            del self.verdicts[next(iter(self.verdicts))]
        if len(self.words) > MAXIMUM_WORDS:
            self._prune()
        return True

    def _count(self, found: Iterable[str], side: str, step: int) -> None:
        column = 0 if side == JUNK else 1
        if side == JUNK:
            self.junk_messages = max(0, self.junk_messages + step)
        else:
            self.good_messages = max(0, self.good_messages + step)
        for word in found:
            counts = self.words.setdefault(word, [0, 0])
            counts[column] = max(0, counts[column] + step)
            if counts == [0, 0]:
                del self.words[word]

    def _prune(self) -> None:
        kept = sorted(self.words.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)[: MAXIMUM_WORDS * 9 // 10]
        self.words = dict(kept)

    def ready(self) -> bool:
        return self.junk_messages >= MINIMUM_LEARNED and self.good_messages >= MINIMUM_LEARNED

    def score(self, found: frozenset[str]) -> float | None:
        """How sure the filter is that the message is junk, 0 to 1; None while it has not learned enough to judge."""
        if not self.ready():
            return None
        leanings: list[float] = []
        for word in found:
            counts = self.words.get(word)
            if counts is None:
                continue
            junk_rate = min(1.0, counts[0] / self.junk_messages)
            good_rate = min(1.0, counts[1] / self.good_messages)
            if junk_rate + good_rate == 0:
                continue
            seen = counts[0] + counts[1]
            leaning = (UNKNOWN_STRENGTH * 0.5 + seen * junk_rate / (junk_rate + good_rate)) / (UNKNOWN_STRENGTH + seen)
            if abs(leaning - 0.5) > 0.1:
                leanings.append(min(0.99, max(0.01, leaning)))
        if not leanings:
            return 0.5
        leanings.sort(key=lambda value: abs(value - 0.5), reverse=True)
        chosen = leanings[:INTERESTING_WORDS]
        junk_side = 1.0 - _chi2q(-2.0 * sum(math.log(1.0 - value) for value in chosen), 2 * len(chosen))
        good_side = 1.0 - _chi2q(-2.0 * sum(math.log(value) for value in chosen), 2 * len(chosen))
        return (junk_side - good_side + 1.0) / 2.0


def _to_bytes(words: JunkWords) -> bytes:
    return json.dumps(
        {"format": FORMAT, "junk": words.junk_messages, "good": words.good_messages, "words": words.words, "verdicts": words.verdicts, "places": sorted(words.places)},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _from_bytes(data: bytes) -> JunkWords:
    try:
        loaded: object = load_json(data.decode("utf-8"))
    except ValueError:
        raise StorageError("The junk filter's file is damaged") from None
    if not isinstance(loaded, dict) or loaded.get("format") != FORMAT:
        raise StorageError("The junk filter's file has an unknown format")
    junk, good, words, verdicts, places = (loaded.get(name) for name in ("junk", "good", "words", "verdicts", "places"))
    if not isinstance(junk, int) or not isinstance(good, int) or not isinstance(words, dict) or not isinstance(verdicts, dict) or not isinstance(places, list):
        raise StorageError("The junk filter's file is damaged")
    result = JunkWords(max(0, junk), max(0, good))
    for word, counts in words.items():
        if isinstance(word, str) and isinstance(counts, list) and len(counts) == 2 and all(isinstance(count, int) and count >= 0 for count in counts):
            result.words[word] = [counts[0], counts[1]]
    result.verdicts = {key: value for key, value in verdicts.items() if isinstance(key, str) and value in (JUNK, GOOD)}
    result.places = {place for place in places if isinstance(place, str)}
    return result


def load_words(path: Path, key: DataKey | None) -> JunkWords:
    """The words kept in the file (encrypted with the data key when there is one); nothing learned yet when there is no file."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return JunkWords()
    except OSError as error:
        raise StorageError(f"The junk filter's file could not be read: {error.strerror}") from None
    if key is not None:
        try:
            data = decrypt_message(data, key)
        except CryptoError as error:
            raise StorageError(f"The junk filter's file could not be read: {error}") from None
    return _from_bytes(data)


def save_words(path: Path, words: JunkWords, key: DataKey | None, durable: bool) -> None:
    """Write the words in one step: the old file or the new one, never a mix. Encrypted like the mail: they are words of the mail."""
    data = _to_bytes(words)
    if key is not None:
        data = encrypt_message(data, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file_atomically(path, data, durable)
    except OSError as error:
        raise StorageError(f"The junk filter's file could not be written: {error.strerror}") from None


@dataclass
class JunkHolder:
    """The engine's one copy of the words, loaded once, behind a lock (a mail check and a click can learn at the same time)."""

    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    words: JunkWords | None = None
