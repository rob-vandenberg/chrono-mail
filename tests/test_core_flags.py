import pytest

from chrono_mail.core import MessageFlag, flags_from_maildir_suffix, flags_to_maildir_suffix

# --- Version ------------------------------------------------------------
__version__ = "test_core_flags.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial core tests

ALL_FLAGS: frozenset[MessageFlag] = frozenset(MessageFlag)


def test_letters_and_imap_names() -> None:
    assert {flag.maildir_letter for flag in MessageFlag} == {"D", "F", "R", "S", "T"}
    assert MessageFlag.SEEN.imap_name == "\\Seen"
    assert MessageFlag.REPLIED.imap_name == "\\Answered"
    assert MessageFlag.TRASHED.imap_name == "\\Deleted"


def test_suffix_is_sorted_in_ascii_order() -> None:
    assert flags_to_maildir_suffix(ALL_FLAGS) == "DFRST"
    assert flags_to_maildir_suffix(frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED})) == "FS"


def test_empty_flags() -> None:
    assert flags_to_maildir_suffix(frozenset()) == ""
    assert flags_from_maildir_suffix("") == frozenset()


def test_round_trip_for_every_combination() -> None:
    members = list(MessageFlag)
    for mask in range(1 << len(members)):
        flags = frozenset(flag for index, flag in enumerate(members) if mask >> index & 1)
        assert flags_from_maildir_suffix(flags_to_maildir_suffix(flags)) == flags


@pytest.mark.parametrize("suffix", ["P", "PS", "xyz", "Sé", "2,S"])
def test_unknown_letters_are_ignored(suffix: str) -> None:
    flags = flags_from_maildir_suffix(suffix)
    assert flags <= ALL_FLAGS
    assert flags == ({MessageFlag.SEEN} if "S" in suffix else set())


def test_duplicate_letters_collapse() -> None:
    assert flags_from_maildir_suffix("SSS") == frozenset({MessageFlag.SEEN})
