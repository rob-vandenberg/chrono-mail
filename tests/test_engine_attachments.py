import os
import stat
import sys
from pathlib import Path

from chrono_mail.engine.commands_attachments import is_program
from chrono_mail.engine.mailbox import open_mailbox
from tests.test_engine_commands import Setup, call, first_id, rich, setup  # noqa: F401 - rich and setup are fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_engine_attachments.py 2.0.302"

# --- Version history ----------------------------------------------------
# v2.0.302: Initial tests: Open attachment writes a private copy, tells a program apart, and the copies go at the next start (D-236)

PROGRAM_MAIL: bytes = (
    b"From: a@example.com\r\nSubject: A program\r\nContent-Type: multipart/mixed; boundary=M\r\n\r\n"
    b"--M\r\nContent-Type: text/plain\r\n\r\nRun me.\r\n"
    b"--M\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=\"Setup.EXE\"\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nTVo=\r\n--M--\r\n"
)


def test_open_writes_a_private_copy_each_in_a_folder_of_its_own(rich: Setup) -> None:
    message = first_id(rich)
    first = call(rich.engine, "message.attachment.open", account=rich.account_id, folder="Inbox", message=message, index=1)
    second = call(rich.engine, "message.attachment.open", account=rich.account_id, folder="Inbox", message=message, index=1)
    path, again = Path(first.path), Path(second.path)
    assert path.read_bytes() == b"%PDF-0" and again.read_bytes() == b"%PDF-0" and path.parent != again.parent  # names never clash
    assert path.name.endswith(".pdf") and "/" not in path.name and ".." not in path.name and not first.program
    assert path.parent.parent == rich.engine.directories.opened_directory
    if sys.platform != "win32":  # readable by the person only
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(rich.engine.directories.opened_directory).st_mode) == 0o700


def test_a_program_is_told_apart_so_that_the_person_is_asked_first(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), PROGRAM_MAIL, frozenset())
    row = next(row for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages if row.subject == "A program")
    result = call(setup.engine, "message.attachment.open", account=setup.account_id, folder="Inbox", message=row.message_id, index=0)
    assert result.program and Path(result.path).name == "Setup.EXE"
    assert is_program("tool.sh") and is_program("x.desktop") and is_program("a.tar.JAR") and not is_program("photo.png") and not is_program("notes")


def test_the_copies_of_an_earlier_run_go_at_the_next_start(rich: Setup) -> None:
    opened = rich.engine.directories.opened_directory
    assert call(rich.engine, "message.attachment.clean-opened").removed == 0  # nothing yet, and no folder needed
    call(rich.engine, "message.attachment.open", account=rich.account_id, folder="Inbox", message=first_id(rich), index=2)
    assert len(list(opened.iterdir())) == 1
    assert call(rich.engine, "message.attachment.clean-opened").removed == 1 and list(opened.iterdir()) == []
