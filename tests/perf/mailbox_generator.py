import base64
import random
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

# --- Version ------------------------------------------------------------
__version__ = "mailbox_generator.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial synthetic mailbox generator

# Deterministic, realistic-looking mail: a Zipf-like vocabulary so full-text search sees both very
# common and very rare words, a pool of senders, ten years of dates, HTML alternatives and attachments.

VOCABULARY_SIZE: int = 6000
SENDER_COUNT: int = 3000
START: datetime = datetime(2016, 10, 1, tzinfo=UTC)
YEARS_IN_SECONDS: int = 10 * 365 * 24 * 3600
_SYLLABLES: list[str] = ["ka", "lo", "mi", "ra", "te", "su", "no", "vi", "pe", "da", "gu", "ho", "bri", "tan", "sel", "dor", "fen", "mar", "quo", "zil"]


def _word(index: int) -> str:
    parts: list[str] = []
    value = index + 1
    while value:
        parts.append(_SYLLABLES[value % len(_SYLLABLES)])
        value //= len(_SYLLABLES)
    return "".join(parts)


VOCABULARY: list[str] = [_word(index) for index in range(VOCABULARY_SIZE)]
FIRST_NAMES: list[str] = [_word(index * 7 + 3).capitalize() for index in range(120)]
LAST_NAMES: list[str] = [_word(index * 11 + 5).capitalize() for index in range(250)]


def sender_for(number: int) -> tuple[str, str]:
    index = number % SENDER_COUNT
    first, last = FIRST_NAMES[index % len(FIRST_NAMES)], LAST_NAMES[(index // len(FIRST_NAMES) + index) % len(LAST_NAMES)]
    return f"{first} {last}", f"{first.lower()}.{last.lower()}{index}@example{index % 40}.com"


def _zipf_word(generator: random.Random) -> str:
    return VOCABULARY[min(VOCABULARY_SIZE - 1, int(VOCABULARY_SIZE ** generator.random()) - 1)]


def message_date(number: int) -> datetime:
    return START + timedelta(seconds=(number * 7919 + (number % 13) * 104729) % YEARS_IN_SECONDS)


def generate_message(number: int) -> bytes:
    """Message number N, always the same bytes."""
    generator = random.Random(number)
    name, address = sender_for(number)
    subject = " ".join(_zipf_word(generator) for _ in range(generator.randint(2, 9)))
    if number % 1000 == 7:
        subject += f" rarityword{number // 1000}"
    paragraphs = [" ".join(_zipf_word(generator) for _ in range(generator.randint(8, 60))) for _ in range(generator.randint(1, 8))]
    body = "\n\n".join(paragraphs) + "\n"
    thread = number // 5
    lines = [
        f"From: {name} <{address}>",
        f"To: Bob Receiver <bob@example.com>{', carol@example.org' if number % 4 == 0 else ''}",
        f"Subject: {subject}",
        f"Date: {format_datetime(message_date(number))}",
        f"Message-ID: <m{number}@perf.example.com>",
    ]
    if number % 5:
        lines.append(f"In-Reply-To: <m{number - 1}@perf.example.com>")
        lines.append(f"References: <m{thread * 5}@perf.example.com>")
    lines.append("MIME-Version: 1.0")
    kind = number % 20
    if kind in (0, 1):  # about 10 percent carry an attachment of 4 to 40 KB
        attachment = base64.encodebytes(generator.randbytes(generator.randint(4_000, 40_000))).decode("ascii")
        lines += ["Content-Type: multipart/mixed; boundary=BND", "", "--BND", "Content-Type: text/plain; charset=utf-8", "", body,
                  "--BND", "Content-Type: application/octet-stream", f'Content-Disposition: attachment; filename="file{number}.bin"',
                  "Content-Transfer-Encoding: base64", "", attachment, "--BND--", ""]
    elif kind in (2, 3, 4):  # about 15 percent are HTML with a text alternative
        html = "<html><body>" + "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs) + "</body></html>"
        lines += ["Content-Type: multipart/alternative; boundary=ALT", "", "--ALT", "Content-Type: text/plain; charset=utf-8", "", body,
                  "--ALT", "Content-Type: text/html; charset=utf-8", "", html, "--ALT--", ""]
    else:
        lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    return "\r\n".join(lines).encode("utf-8")
