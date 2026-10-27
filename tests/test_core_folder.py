import dataclasses

import pytest

from chrono_mail.core import Folder, FolderRole
from chrono_mail.core.folder import roles_by_name

# --- Version ------------------------------------------------------------
__version__ = "test_core_folder.py 4.2.387"

# --- Version history ----------------------------------------------------
# v4.2.387: standard folder names: the language with the most matches wins, the other languages' names stay ordinary folders
# v0.0.4: Initial core tests


def test_folder_with_nested_path() -> None:
    folder = Folder("a1", ("Archive", "2026"), FolderRole.ARCHIVE)
    assert folder.path == ("Archive", "2026")
    assert folder.role is FolderRole.ARCHIVE


def test_empty_path_is_rejected() -> None:
    with pytest.raises(ValueError):
        Folder("a1", (), FolderRole.OTHER)


@pytest.mark.parametrize("path", [("",), ("Archive", ""), ("  ",), ("", "2026")])
def test_empty_segment_is_rejected(path: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        Folder("a1", path, FolderRole.OTHER)


def test_folder_is_immutable_and_hashable() -> None:
    folder = Folder("a1", ("Inbox",), FolderRole.INBOX)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(folder, "role", FolderRole.SENT)
    assert {folder, Folder("a1", ("Inbox",), FolderRole.INBOX)} == {folder}


def test_standard_names_follow_the_language_of_the_source() -> None:
    names = ["Posteingang", " gesendet ", "Entwürfe", "Papierkorb", "Postvak UIT", "Spam"]
    assert roles_by_name(names) == {0: FolderRole.INBOX, 1: FolderRole.SENT, 2: FolderRole.DRAFTS, 3: FolderRole.TRASH, 5: FolderRole.JUNK}
