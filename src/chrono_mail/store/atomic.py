import os
import sys
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "atomic.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: Initial atomic file helpers


def fsync_directory(path: Path) -> None:
    """Make a rename or creation in this directory durable. Windows cannot open directories."""
    if sys.platform == "win32":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_file(path: Path, data: bytes, durable: bool) -> None:
    """Create a file that must not exist yet, write everything, flush it to disk."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        if durable:
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_file_atomically(path: Path, data: bytes, durable: bool) -> None:
    """Replace (or create) a small file in one step: readers see the old or the new content, never a mix."""
    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    try:
        write_new_file(temporary, data, durable)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if durable:
        fsync_directory(path.parent)
