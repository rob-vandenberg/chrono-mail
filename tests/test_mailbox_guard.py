import re
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_mailbox_guard.py 3.5.348"

# --- Version history ----------------------------------------------------
# v3.5.348: the restore may hand the store to _put_properties (the tags and notes of a message from the backup; 3.3.343 moved it into a helper)
# v3.1.341: file_sent_again may take the store and index: only the move to Sent of a message the server took (RV-50)
# v1.1.206: tags are changed through the messages' properties files (D-176)
# v0.5.134: changes of tags count as changes too
# v0.5.123: Initial guard: no command changes mail or folders past the mailbox, which does IMAP on the server first (D-118)

# In an IMAP account every change is made on the server first (D-117, D-118). engine/mailbox.py is the one place that does
# that; a command that changes the store or the index of a mailbox directly would change it here only (the POP way).
# This test fails when such a call appears, so that a new action cannot forget the server unnoticed.

SOURCE = Path(__file__).resolve().parent.parent / "src" / "chrono_mail"
CHANGES = (
    "create_folder|ensure_folder|rename_folder|delete_folder|remove_folder|deliver|move|copy|delete|set_flags|add|remove|update_flags|update_tags"
    r"|properties\.set_tags|properties\.copy|properties\.forget|properties\.rename_tag|properties\.take_from"
)  # properties.change is left out: it writes what exists only here (when a snoozed message comes back, where an archived one came from)
DIRECT_CHANGE = re.compile(rf"\bmailbox\.(store|index)\.({CHANGES})\(")
HANDED_OVER = re.compile(r"\bmailbox\.(store|index)\s*[,)]")  # the store or index given whole to other code

# Places that may hand them over, each with its reason.
ALLOWED_HAND_OVER: dict[tuple[str, str], str] = {
    ("commands_folders.py", "reconcile_index(mailbox.store, mailbox.index)"): "repairs the index from the store; changes nothing on any server",
    ("commands_mail.py", "send_and_file(mailbox.store, mailbox.index,"): "Outbox is local only; the Sent copy goes to the server at once (put_on_server)",
    ("commands_mail.py", "client, mailbox.store, mailbox.index, state, inbox, settings.pop3,"): "the POP3 download",
    ("commands_mail.py", "client, mailbox.store, mailbox.index, state, ImapSyncOptions("): "the IMAP sync itself, under the account's lock",
    ("commands_outbox.py", "mailbox.store, mailbox.index, sender.account.outgoing, password, message.ref,"): "sending the Outbox; the Sent copy goes to the server at once",
    ("commands_outbox.py", "file_sent_again(mailbox.store, mailbox.index,"): "a message the server took before, only moved to Sent; the Sent copy goes to the server at once",
    ("commands_schedule.py", "mailbox.store, mailbox.index, sender.account.outgoing, password, _outgoing(sender, parsed), OUTBOX"): "scheduled and repeating mail; the Sent copy goes to the server at once",
    ("restore.py", "_put_properties(mailbox.store, "): "a restore writes a message's tags and notes from the backup, as it did inline before 3.3.343; IMAP tags reach the server at the next check (D-050)",
}


def _sources() -> list[Path]:
    return sorted(path for folder in ("engine", "cli") for path in (SOURCE / folder).glob("*.py") if path.name != "mailbox.py")


def test_no_command_changes_the_store_or_index_of_a_mailbox_directly() -> None:
    found = [
        f"{path.name}:{number}: {line.strip()}"
        for path in _sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if DIRECT_CHANGE.search(line)
    ]
    assert found == [], "use the Mailbox methods (IMAP on the server first):\n" + "\n".join(found)


def test_the_store_or_index_is_handed_over_only_where_allowed() -> None:
    found = [
        f"{path.name}:{number}: {line.strip()}"
        for path in _sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if HANDED_OVER.search(line) and not any(path.name == name and text in line for name, text in ALLOWED_HAND_OVER)
    ]
    assert found == [], "a new place hands over the store or index of a mailbox; check that IMAP stays server-first:\n" + "\n".join(found)


def test_the_guard_finds_a_direct_change() -> None:
    assert DIRECT_CHANGE.search("        mailbox.store.rename_folder(old, new)")
    assert DIRECT_CHANGE.search("x = mailbox.index.remove(store_id)")
    assert DIRECT_CHANGE.search("mailbox.store.properties.set_tags(store_id, tags)") and DIRECT_CHANGE.search("mailbox.index.update_tags(store_id, tags)")
    assert not DIRECT_CHANGE.search("raw = mailbox.store.read(reference)")
    assert not DIRECT_CHANGE.search("mailbox.store.properties.change(store_id, update)")  # snooze and archive origin: only here
    assert HANDED_OVER.search("send_and_file(mailbox.store, mailbox.index, x)")
