import hashlib
import json
import os
import zipfile
from pathlib import Path

import pyzipper
import pytest

from chrono_mail.config import ServerRole, parse_config
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, MessageFlag
from chrono_mail.engine import Engine, UsageError, build_registry
from chrono_mail.engine.backup import MANIFEST_ENTRY, BackupCancelled, PASSWORD_ALPHABET, PASSWORD_LENGTH, cancel_backup
from chrono_mail.engine.commands_backup import BACKUP_CHOICES, BACKUP_MAKE, BACKUP_NEW_PASSWORD, BackupParameters, BackupResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_backup.py 2.4.322"

# --- Version history ----------------------------------------------------
# v2.4.322: Initial: Back up (D-244): every part readable without this computer's key, passwords only in a protected backup,
#           nothing changed by a backup, a cancel leaves nothing, a message moved meanwhile backed up exactly once

REGISTRY = build_registry()
EVENT: str = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\nUID:{uid}@example.com\r\nDTSTAMP:20261020T100000Z\r\nDTSTART:20261024T100000Z\r\nDTEND:20261024T110000Z\r\nSUMMARY:Dentist\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def _engine(tmp_path: Path, messages: int = 3) -> tuple[Engine, dict[str, bytes]]:
    """An engine with Local Folders holding some read messages, and this computer's calendar holding one appointment."""
    engine = make_engine(tmp_path)
    ensure_local_pim(engine)
    kept: dict[str, bytes] = {}
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        for number in range(messages):
            raw = raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<b{number}@example.com>")
            kept[mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset({MessageFlag.SEEN})).store_id] = raw
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        calendar = store.collections(CollectionKind.EVENTS)[0].collection_id
        store.put(calendar, EVENT.format(uid="dentist"))
    return engine, kept


def _make(engine: Engine, target: Path, **choices: object) -> BackupResult:
    values: dict[str, object] = {"path": str(target), "accounts": ("local-folders", "this-computer")}
    values.update(choices)
    return BACKUP_MAKE.answer(REGISTRY.run(engine, BACKUP_MAKE.id, values))


def _message_entries(names: list[str]) -> list[str]:
    return [name for name in names if "/store/folders/" in name and ("/cur/" in name or "/new/" in name)]


def _tree(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_backup_holds_every_message_and_item_readable_without_this_computers_key(tmp_path: Path) -> None:
    engine, kept = _engine(tmp_path)
    result = _make(engine, tmp_path / "Chrono Mail backup")
    target = tmp_path / "Chrono Mail backup.cmz"  # the extension is added
    assert Path(result.path) == target and result.size == target.stat().st_size and result.messages == 3 and result.items == 1
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(MANIFEST_ENTRY))
        found = {Path(name).name[:32]: archive.read(name) for name in _message_entries(names)}
        assert found == kept  # exactly as received, decrypted
        items = [name for name in names if "/pim/collections/" in name and "/items/" in name]
        assert len(items) == 1 and b"SUMMARY:Dentist" in archive.read(items[0])
        assert set(manifest["sealed"]) >= set(_message_entries(names)) | set(items)  # a restore encrypts them again
        accounts = parse_config(archive.read("config/config.json").decode())
    assert manifest["format"] == 1 and manifest["protected"] is False and manifest["passwords"] is False and manifest["messages"] == 3
    assert [account.account.account_id for account in accounts.accounts] == [LOCAL_ACCOUNT_ID] and accounts.key_id is None
    assert not any(name.endswith(".sqlite") or "/.deleted/" in name for name in names)  # the indexes are rebuilt, removed folders stay out


def test_a_backup_changes_nothing_here(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    before = _tree(tmp_path / "data"), _tree(tmp_path / "config")
    _make(engine, tmp_path / "out.cmz")
    assert (_tree(tmp_path / "data"), _tree(tmp_path / "config")) == before


def test_a_protected_backup_opens_only_with_its_password_and_holds_the_passwords_only_when_asked(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    engine.passwords.set(LOCAL_ACCOUNT_ID, ServerRole.INCOMING, "letmein")
    password = BACKUP_NEW_PASSWORD.answer(REGISTRY.run(engine, BACKUP_NEW_PASSWORD.id, {})).password
    _make(engine, tmp_path / "with.cmz", password=password, passwords=True)
    _make(engine, tmp_path / "without.cmz", password=password)
    with pyzipper.AESZipFile(tmp_path / "with.cmz") as archive:
        with pytest.raises(RuntimeError):
            archive.read(MANIFEST_ENTRY)  # no password: nothing can be read
        archive.setpassword(password.encode())
        assert json.loads(archive.read(MANIFEST_ENTRY))["protected"] is True
        assert json.loads(archive.read("config/passwords.json")) == {LOCAL_ACCOUNT_ID: {"incoming": "letmein"}}
    with pyzipper.AESZipFile(tmp_path / "without.cmz") as archive:
        assert "config/passwords.json" not in archive.namelist()


def test_passwords_are_never_put_in_a_backup_without_a_backup_password(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    with pytest.raises(UsageError, match="must be protected"):
        _make(engine, tmp_path / "x.cmz", passwords=True)
    with pytest.raises(UsageError, match="made by Chrono Mail"):
        _make(engine, tmp_path / "x.cmz", password="1234")  # only the program's own passwords: never one that can be guessed
    assert list(tmp_path.glob("x.cmz*")) == []


def test_a_new_backup_password_is_24_letters_and_digits_and_new_every_time(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    first, second = (BACKUP_NEW_PASSWORD.answer(REGISTRY.run(engine, BACKUP_NEW_PASSWORD.id, {})).password for _ in range(2))
    assert len(first) == PASSWORD_LENGTH and set(first) <= set(PASSWORD_ALPHABET) and first != second


def test_each_part_holds_only_what_was_chosen(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    (tmp_path / "config" / "rules.json").write_text("{}", "utf-8")
    _make(engine, tmp_path / "rules.cmz", messages=False, calendars=False, contacts=False, tasks=False, settings=False, window_settings='{"theme": "dark"}')
    with zipfile.ZipFile(tmp_path / "rules.cmz") as archive:
        names = archive.namelist()
    assert "config/rules.json" in names and "config/config.json" in names and not [name for name in names if name.startswith("data/")]
    assert "config/window.json" not in names  # the window's settings belong to Settings
    _make(engine, tmp_path / "settings.cmz", messages=False, rules=False, calendars=False, contacts=False, tasks=False, window_settings='{"theme": "dark"}')
    with zipfile.ZipFile(tmp_path / "settings.cmz") as archive:
        assert json.loads(archive.read("config/window.json")) == {"theme": "dark"} and "config/rules.json" not in archive.namelist()


def test_the_choices_name_local_folders_and_this_computer(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    rows = BACKUP_CHOICES.answer(REGISTRY.run(engine, BACKUP_CHOICES.id, {})).rows
    assert [(row.entry_id, row.name, row.parts) for row in rows] == [
        ("local-folders", "Local Folders", ("messages",)), ("this-computer", "On this computer", ("calendars", "contacts", "tasks"))
    ]


def test_a_cancelled_backup_leaves_no_file(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path, messages=150)

    def cancel(what: str, done: int, total: int | None) -> None:
        cancel_backup()  # at the 100th message

    engine.progress = cancel
    with pytest.raises(BackupCancelled, match="cancelled"):
        _make(engine, tmp_path / "x.cmz")
    assert list(tmp_path.glob("x.cmz*")) == []


def test_an_existing_file_is_replaced_only_when_asked(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    target = tmp_path / "x.cmz"
    target.write_bytes(b"precious")
    with pytest.raises(UsageError, match="exists"):
        _make(engine, target)
    assert target.read_bytes() == b"precious"
    _make(engine, target, overwrite=True)
    assert zipfile.is_zipfile(target)


def test_messages_moved_while_the_backup_runs_are_backed_up_exactly_once(tmp_path: Path) -> None:
    engine, kept = _engine(tmp_path, messages=150)
    moved: list[bool] = []

    def move_everything(what: str, done: int, total: int | None) -> None:
        if moved:
            return
        moved.append(True)
        with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:  # halfway: every message to another folder, some read already, some not
            inbox, archive = mailbox.folder("Inbox"), mailbox.folder("Sent")
            for store_id in kept:
                mailbox.move(mailbox.reference(inbox, store_id), archive)

    engine.progress = move_everything
    result = _make(engine, tmp_path / "x.cmz")
    with zipfile.ZipFile(tmp_path / "x.cmz") as archive:
        found = [Path(name).name[:32] for name in _message_entries(archive.namelist())]
    assert moved and sorted(found) == sorted(kept) and result.messages == 150


def test_the_backup_file_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows has no such mode bits: the file is private through the rights of the user's folder")
    engine, _ = _engine(tmp_path)
    _make(engine, tmp_path / "x.cmz")
    assert oct((tmp_path / "x.cmz").stat().st_mode & 0o777) == "0o600"
