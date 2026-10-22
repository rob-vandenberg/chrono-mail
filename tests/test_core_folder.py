import dataclasses

import pytest

from chrono_mail.core import Folder, FolderRole

# --- Version ------------------------------------------------------------
__version__ = "test_core_folder.py 0.0.4"

# --- Version history ----------------------------------------------------
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
