import ctypes
import os
import sys

# --- Version ------------------------------------------------------------
__version__ = "running.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: Initial: whether Chrono Mail is already running for this user, by looking at the processes themselves (Linux and Windows)

PROCESS_NAME: str = "chrono-mail"  # the name of the program's process on Linux (the launcher, the packaged program, and what the program sets itself)
WINDOWS_PROCESS_NAME: str = "chrono-mail.exe"  # the installed program, and the launcher pip makes when it runs from source
_PR_SET_NAME: int = 15  # prctl option of Linux: the name of the calling thread, which for the first thread is the process name


def set_process_name() -> None:
    """On Linux, name this process "chrono-mail", so that another start recognises it also when it was started with python -m.
    Must be called from the first thread. Does nothing elsewhere (Windows cannot rename a process)."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_NAME, ctypes.c_char_p(PROCESS_NAME.encode("ascii")), 0, 0, 0)
    except (AttributeError, OSError):
        pass  # without prctl the launcher's name still identifies the program


def program_is_running() -> bool:
    """Whether another Chrono Mail program (not the command line tool, not this process) runs for the same user on this computer."""
    return bool(running_programs())


def running_programs() -> list[int]:
    """The process numbers of the other Chrono Mail programs of this user on this computer."""
    ignored = {os.getpid(), os.getppid()}  # on Windows the launcher of a source run is the parent of the Python process
    if sys.platform == "win32":
        return _running_on_windows(ignored)
    if sys.platform.startswith("linux"):
        return _running_on_linux(ignored)
    return []  # other systems (macOS) are not covered


def _running_on_linux(ignored: set[int]) -> list[int]:
    """Every process has its name in /proc/<number>/comm and belongs to the user who owns /proc/<number>."""
    user = os.getuid()
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        return []
    found: list[int] = []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) in ignored:
            continue
        try:
            if entry.stat().st_uid != user:
                continue
            with open(f"/proc/{entry.name}/comm", encoding="utf-8", errors="replace") as file:
                name = file.read().strip()
        except OSError:
            continue  # the process ended meanwhile
        if name == PROCESS_NAME:
            found.append(int(entry.name))
    return found


class _WtsProcessInfo(ctypes.Structure):
    """WTS_PROCESS_INFOW of the Windows Terminal Services API."""

    _fields_ = [("session_id", ctypes.c_uint32), ("process_id", ctypes.c_uint32), ("process_name", ctypes.c_wchar_p), ("user_sid", ctypes.c_void_p)]


def windows_processes() -> list[tuple[int, int, str, bytes]]:
    """Every process of this computer as (process number, session, name, the user's security identifier; empty when not known).
    WTSEnumerateProcessesW gives them all in one call. An empty list on other systems or when the call fails."""
    try:
        windll = getattr(ctypes, "windll")  # noqa: B009 - exists on Windows only, so the type checker cannot know it; the calls below are typed by hand
        wtsapi32 = windll.wtsapi32
        advapi32 = windll.advapi32
    except (AttributeError, OSError):
        return []
    listing = ctypes.POINTER(_WtsProcessInfo)()
    count = ctypes.c_uint32(0)
    if not wtsapi32.WTSEnumerateProcessesW(None, 0, 1, ctypes.byref(listing), ctypes.byref(count)):  # None: this computer
        return []
    try:
        processes: list[tuple[int, int, str, bytes]] = []
        for number in range(count.value):
            item = listing[number]
            sid = b""
            if item.user_sid:
                length = int(advapi32.GetLengthSid(ctypes.c_void_p(item.user_sid)))
                sid = ctypes.string_at(item.user_sid, length)
            processes.append((int(item.process_id), int(item.session_id), item.process_name or "", sid))
        return processes
    finally:
        wtsapi32.WTSFreeMemory(listing)


def _running_on_windows(ignored: set[int]) -> list[int]:
    """The processes named chrono-mail.exe of the same user (the same session when the user is not known)."""
    processes = windows_processes()
    own = next((item for item in processes if item[0] == os.getpid()), None)
    found: list[int] = []
    for number, session, name, sid in processes:
        if number in ignored or name.lower() != WINDOWS_PROCESS_NAME:
            continue
        if own is None or (sid == own[3] if sid and own[3] else session == own[1]):
            found.append(number)
    return found
