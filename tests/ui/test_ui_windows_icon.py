import ctypes
import struct
import sys

import pytest

from chrono_mail.ui import app as application
from chrono_mail.ui import icons

# --- Version ------------------------------------------------------------
__version__ = "test_ui_windows_icon.py 0.0.55"

# --- Version history ----------------------------------------------------
# v0.0.55: Initial tests of the application id and the Windows icon file


def test_the_ico_file_holds_the_envelope_in_every_size_windows_asks_for() -> None:
    data = (icons.RESOURCES / "chrono-mail.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = []
    for position in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * position)
        assert (planes, bits) == (1, 32) and data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(data)
        sizes.append(width or 256)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]


def test_the_application_id_is_set_on_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Shell32:
        @staticmethod
        def SetCurrentProcessExplicitAppUserModelID(identifier: str) -> int:  # noqa: N802 - the name Windows gives it
            calls.append(identifier)
            return 0

    class Windll:
        shell32 = Shell32()

    monkeypatch.setattr(sys, "platform", "linux")
    assert application.set_application_id() is False and calls == []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", Windll(), raising=False)
    assert application.set_application_id() is True and calls == [application.APPLICATION_ID]
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert application.set_application_id() is False  # no such call: no harm done


def test_windows_uses_the_ico_file_and_other_systems_the_svg(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    windows = icons.application_icon()
    monkeypatch.setattr(sys, "platform", "linux")
    other = icons.application_icon()
    assert not windows.isNull() and not other.isNull()
    assert 256 in [size.width() for size in windows.availableSizes()] and 16 in [size.width() for size in windows.availableSizes()]
