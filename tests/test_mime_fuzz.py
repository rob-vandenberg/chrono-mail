import random
from pathlib import Path

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
