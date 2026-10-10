import pytest

from chrono_mail.core import MessageFlag
from chrono_mail.store.layout import SEPARATOR, build_name, join_info, new_store_id, parse_name, split_info

# --- Version ------------------------------------------------------------
__version__ = "test_store_layout.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: Initial store layout tests


def test_separator_is_not_a_colon() -> None:
    assert SEPARATOR == "!"
    assert ":" not in build_name(new_store_id(), "FS")


def test_store_ids_are_random_hex() -> None:
    ids = {new_store_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(len(identifier) == 32 and identifier == identifier.lower() for identifier in ids)
    int(next(iter(ids)), 16)


def test_names_round_trip() -> None:
    identifier = new_store_id()
    assert parse_name(build_name(identifier, None)) == (identifier, None)
    assert parse_name(build_name(identifier, "")) == (identifier, "")
    assert parse_name(build_name(identifier, "FRS")) == (identifier, "FRS")


@pytest.mark.parametrize(
    "name",
    ["", ".DS_Store", "readme.txt", "g" * 32, "A" * 32, "0" * 31, "0" * 33, "0" * 32 + ":2,S", "0" * 32 + "!1,S", "0" * 32 + "!2,S!", "0" * 32 + "!2,S/x"],
)
def test_foreign_names_are_not_messages(name: str) -> None:
    assert parse_name(name) is None


def test_known_and_extra_letters_are_separated_and_ordered() -> None:
    flags, extra = split_info("PSaFx")
    assert flags == frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED})
    assert extra == "Pax"
    assert join_info(flags, extra) == "FPSax"


def test_join_without_flags() -> None:
    assert join_info(frozenset(), "") == ""
