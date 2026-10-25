import ctypes
import os
import sys
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "running.py 3.6.351"

# --- Version history ----------------------------------------------------
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v0.5.164: the Linux part is a block for "not Windows" (an early return did not satisfy the type check for Windows)
# v0.5.161: the Linux part says it is not for Windows, so the type check on Windows reports nothing
# v0.5.147: Windows: every process above this one (parent, grandparent …) is skipped, not only the parent: a run from source is launcher, redirector, program
# v0.5.144: Initial: whether Chrono Mail is already running for this user, by looking at the processes themselves (Linux and Windows)

PROCESS_NAME: str = "chrono-mail"  # the name of the program's process on Linux (the launcher, the packaged program, and what the program sets itself)
WINDOWS_PROCESS_NAME: str = "chrono-mail.exe"  # the installed program, and the launcher pip makes when it runs from source
MAXIMUM_ANCESTORS: int = 64  # steps up the chain of parents; a broken chain never loops for ever
_TH32CS_SNAPPROCESS: int = 0x2
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
    ignored = {os.getpid(), os.getppid()}
    if sys.platform == "win32":
        ignored |= ancestors(os.getpid(), windows_parents())  # a run from source: launcher -> the venv's redirector -> the program
        return _running_on_windows(ignored)
    if sys.platform.startswith("linux"):
        return _running_on_linux(ignored)
    return []  # other systems (macOS) are not covered


def _running_on_linux(ignored: set[int]) -> list[int]:
    """Every process has its name in /proc/<number>/comm and belongs to the user who owns /proc/<number>."""
    found: list[int] = []
    if sys.platform != "win32":  # never called on Windows; inside this block the type check for Windows knows to skip it
        user = os.getuid()
        try:
            entries = list(os.scandir("/proc"))
        except OSError:
            return []
        for entry in entries:
            if not digits(entry.name) or int(entry.name) in ignored:
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


def ancestors(process: int, parents: dict[int, int]) -> set[int]:
    """The processes above this one: its parent, the parent's parent and so on, as far as the list knows them (at most
    MAXIMUM_ANCESTORS steps; a loop or a process that is not in the list ends the walk)."""
    found: set[int] = set()
    current = process
    for _ in range(MAXIMUM_ANCESTORS):
        parent = parents.get(current)
        if parent is None or parent == 0 or parent == process or parent in found:
            break
        found.add(parent)
        current = parent
    return found


class _ProcessEntry(ctypes.Structure):
    """PROCESSENTRY32W of the Windows Tool Help API."""

    _fields_ = [
        ("size", ctypes.c_uint32), ("usage", ctypes.c_uint32), ("process_id", ctypes.c_uint32), ("default_heap_id", ctypes.c_size_t),
        ("module_id", ctypes.c_uint32), ("threads", ctypes.c_uint32), ("parent_process_id", ctypes.c_uint32), ("priority", ctypes.c_long),
        ("flags", ctypes.c_uint32), ("exe_file", ctypes.c_wchar * 260),
    ]


def windows_parents() -> dict[int, int]:
    """Every process of this computer with the number of its parent, from a Tool Help snapshot. Empty on other systems or
    when the snapshot fails."""
    try:
        windll = getattr(ctypes, "windll")  # noqa: B009 - exists on Windows only, so the type checker cannot know it; the calls below are typed by hand
        kernel32 = windll.kernel32
    except (AttributeError, OSError):
        return {}
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot is None or snapshot == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        return {}
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(_ProcessEntry)
        parents: dict[int, int] = {}
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.process_id)] = int(entry.parent_process_id)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return parents
    finally:
        kernel32.CloseHandle(snapshot)


def _running_on_windows(ignored: set[int]) -> list[int]:
    """The processes named chrono-mail.exe of the same user (the same session when the user is not known)."""
    return windows_matches(windows_processes(), os.getpid(), ignored)


def windows_matches(processes: list[tuple[int, int, str, bytes]], own_number: int, ignored: set[int]) -> list[int]:
    """From the process list of windows_processes: the other Chrono Mail programs of the user of process own_number."""
    own = next((item for item in processes if item[0] == own_number), None)
    found: list[int] = []
    for number, session, name, sid in processes:
        if number in ignored or name.lower() != WINDOWS_PROCESS_NAME:
            continue
        if own is None or (sid == own[3] if sid and own[3] else session == own[1]):
            found.append(number)
    return found
