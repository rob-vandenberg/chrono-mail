import random
from pathlib import Path

from chrono_mail.core import MessageFormatError

from chrono_mail.mime import (
    Address,
    Attachment,
    OutgoingMessage,
    ParsedMessage,
    build_message,
    parse_message,
)

# --- Version ------------------------------------------------------------
__version__ = "test_mime_fuzz.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial fuzz-style tests

CORPUS: Path = Path(__file__).resolve().parent / "data" / "mime"
SEED: int = 20261008
MUTATIONS_PER_SEED_MESSAGE: int = 150


def seed_messages() -> list[bytes]:
    built = build_message(
        OutgoingMessage(
            from_address=Address("Jörg Müller", "joerg@example.com"),
            to_addresses=(Address("Alice", "alice@example.com"),),
            subject="Fuzz ☕",
            text_body="text body\n",
            html_body="<p>html body</p>",
            attachments=(Attachment("a.bin", "application/octet-stream", None, False, bytes(range(200))),),
            references=("<a@b>", "<c@d>"),
        )
    )
    return [built, *(path.read_bytes() for path in sorted(CORPUS.glob("*.eml")))]


def mutate(raw: bytes, generator: random.Random) -> bytes:
    data = bytearray(raw)
    for _ in range(generator.randint(1, 5)):
        choice = generator.randrange(7)
        if not data:
            data = bytearray(generator.randbytes(generator.randint(1, 64)))
        position = generator.randrange(len(data) + 1)
        if choice == 0:  # flip a byte
            data[min(position, len(data) - 1)] = generator.randrange(256)
        elif choice == 1:  # truncate
            del data[position:]
        elif choice == 2:  # delete a slice
            del data[position : position + generator.randint(1, 50)]
        elif choice == 3:  # duplicate a slice
            data[position:position] = data[max(0, position - 40) : position]
        elif choice == 4:  # insert random bytes
            data[position:position] = generator.randbytes(generator.randint(1, 30))
        elif choice == 5:  # swap line endings
            data = bytearray(bytes(data).replace(b"\r\n", generator.choice([b"\n", b"\r", b"\r\n\r\n"])))
        else:  # shuffle lines
            lines = bytes(data).split(b"\n")
            generator.shuffle(lines)
            data = bytearray(b"\n".join(lines))
    return bytes(data)


def test_mutated_messages_never_raise() -> None:
    generator = random.Random(SEED)
    count = 0
    for original in seed_messages():
        for _ in range(MUTATIONS_PER_SEED_MESSAGE):
            candidate = mutate(original, generator)
            message = parse_message(candidate)
            assert isinstance(message, ParsedMessage)
            assert isinstance(message.subject, str)
            assert all(isinstance(attachment.data, bytes) for attachment in message.attachments)
            count += 1
    assert count >= 2000


def test_random_bytes_never_raise() -> None:
    generator = random.Random(SEED + 1)
    for _ in range(500):
        assert isinstance(parse_message(generator.randbytes(generator.randint(0, 2000))), ParsedMessage)


# --- the builder with hostile text (AGENTS.md section 4.2: input of any kind, never a crash) -----------------------

ALPHABET: str = (
    "abcXYZ019 .,;:<>()[]@\"'\\/-_=+*&%$#!?\t\r\n\x00\x07\x1b\x7f"
    "éüß中文م\U0001f600​‮﻿𐏿"
)


TAME: str = "abcdefghij ABCDEFGHIJ0123456789.-_\u00e9\u00fc\u4e2d\u0645"


def random_text(generator: random.Random, limit: int = 40) -> str:
    """Mostly hostile text, sometimes plain text, so that the builder both succeeds and refuses."""
    alphabet = TAME if generator.random() < 0.75 else ALPHABET
    return "".join(generator.choice(alphabet) for _ in range(generator.randint(0, limit)))


def test_builder_never_fails_in_any_way_but_message_format_error() -> None:
    generator = random.Random(SEED + 2)
    built = rejected = 0
    for _ in range(3000):
        outgoing = OutgoingMessage(
            from_address=Address(random_text(generator), generator.choice(["a@example.com", "a@example.com", "b.c@example.org", random_text(generator, 12) + "@example.com", random_text(generator, 12)])),
            to_addresses=tuple(Address(random_text(generator, 20), generator.choice(["b@example.com", "c@example.com", random_text(generator, 12)])) for _ in range(generator.randint(0, 3))),
            subject=random_text(generator),
            text_body=random_text(generator, 300),
            html_body=generator.choice([None, random_text(generator, 300)]),
            attachments=tuple(
                Attachment(generator.choice([None, random_text(generator, 15)]), generator.choice(["text/plain", "image/png", random_text(generator, 10), ""]), generator.choice([None, random_text(generator, 10)]), generator.random() < 0.5, generator.randbytes(generator.randint(0, 50)))
                for _ in range(generator.randint(0, 2))
            ),
            in_reply_to=generator.choice([None, random_text(generator, 20)]),
            references=tuple(random_text(generator, 10) for _ in range(generator.randint(0, 2))),
        )
        try:
            raw = build_message(outgoing)
        except MessageFormatError:
            rejected += 1
            continue
        built += 1
        header_section = raw.split(b"\r\n\r\n", 1)[0]
        assert header_section.isascii(), "header text must be encoded"
        assert b"\r\nBcc:" not in header_section and not header_section.lower().startswith(b"bcc:")
        assert isinstance(parse_message(raw), ParsedMessage)
    assert built > 300 and rejected > 300  # both outcomes are really exercised
