import json
from pathlib import Path

from chrono_mail.store.atomic import replace_file_atomically
from chrono_mail.ui.searchform import MAXIMUM_TEXT, SearchForm, form_data, form_from

# --- Version ------------------------------------------------------------
__version__ = "savedsearches.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: Initial: the searches Advanced search saved under a name, and the form as it was last left, in one file on this computer
#           only (saved-searches.json beside the window's settings), replaced in one step; a damaged file is kept aside, never
#           written over, one that cannot be opened now is left alone (D-289)

SAVED_SEARCHES_FILE: str = "saved-searches.json"
FILE_FORMAT: int = 1


class SavedSearches:
    """The saved searches by name, and the form as it was last left. path None: kept in memory only (a test, a window without a
    folder of its own)."""

    def __init__(self, path: Path | None) -> None:
        self.path: Path | None = path
        self._saved: dict[str, SearchForm] = {}
        self.last: SearchForm | None = None
        self._damaged: bool = False  # the file could not be read: it is put aside before the first write, never written over
        self._unreadable: bool = False  # the file is there but could not be opened now: it is left alone, nothing is written over it
        if path is not None:
            self._read(path)

    def _read(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (UnicodeDecodeError, ValueError):
            self._damaged = True
            return
        except OSError:
            self._unreadable = True  # a passing fault (no access, a disk error): the file may be fine, so it is kept as it is
            return
        if not isinstance(data, dict):
            self._damaged = True
            return
        saved = data.get("saved")
        for entry in saved if isinstance(saved, list) else []:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name.strip() and self.named(name) is None:  # a name twice (in other letters): the first counts
                self._saved[name.strip()[:MAXIMUM_TEXT]] = form_from(entry.get("form"))
        if data.get("last") is not None:  # none left yet: the window opens with the standard form
            self.last = form_from(data.get("last"))

    def names(self) -> list[str]:
        """The names, in alphabetical order (without regard to case)."""
        return sorted(self._saved, key=str.casefold)

    def form(self, name: str) -> SearchForm | None:
        return self._saved.get(name)

    def named(self, name: str) -> str | None:
        """The saved name that is this name without regard to case, if there is one."""
        wanted = name.strip()[:MAXIMUM_TEXT].casefold()  # as it is kept: a name is cut to its longest
        return next((known for known in self._saved if known.casefold() == wanted), None)

    def ready(self) -> None:
        """Before a change (and before asking about a name): a file that could not be opened is read again; still not: the change is
        refused (OSError) and the window says so, rather than seeming to keep what is lost at the next start."""
        if not self._unreadable or self.path is None:
            return
        self._unreadable = False
        self._read(self.path)
        if self._unreadable:
            raise OSError(f"{self.path.name} cannot be opened")

    def put(self, name: str, form: SearchForm) -> None:
        """Save a form under a name; a name that differs only in case replaces that one."""
        self.ready()
        saved = dict(self._saved)
        known = self.named(name)
        if known is not None:
            del saved[known]
        saved[name.strip()[:MAXIMUM_TEXT]] = form
        self._write(saved, self.last)

    def rename(self, old: str, new: str) -> None:
        """Another name for a saved search; a search that already has the new name (without regard to case) is replaced."""
        self.ready()
        saved = dict(self._saved)
        form = saved.pop(old, None)
        if form is None:
            return
        known = next((name for name in saved if name.casefold() == new.strip()[:MAXIMUM_TEXT].casefold()), None)
        if known is not None:
            del saved[known]
        saved[new.strip()[:MAXIMUM_TEXT]] = form
        self._write(saved, self.last)

    def delete(self, name: str) -> None:
        self.ready()
        if name in self._saved:
            self._write({known: form for known, form in self._saved.items() if known != name}, self.last)

    def keep_last(self, form: SearchForm) -> None:
        """The form as it was left: the window opens with it again, also after a restart."""
        self.ready()
        self._write(dict(self._saved), form)

    def _write(self, saved: dict[str, SearchForm], last: SearchForm | None) -> None:
        """The searches and the form last left as they are to be: in the file first, here only when it was written (a failed
        write changes nothing, so what the window shows is what the file holds)."""
        if self.path is None:
            self._saved, self.last = saved, last
            return
        data = {
            "format": FILE_FORMAT,
            "last": form_data(last) if last is not None else None,
            "saved": [{"name": name, "form": form_data(saved[name])} for name in sorted(saved, key=str.casefold)],
        }
        if self._damaged and self.path.exists():
            aside = self.path.with_name(f"{self.path.name}.damaged")
            number = 1
            while aside.exists():  # an older one stays too
                number += 1
                aside = self.path.with_name(f"{self.path.name}.damaged{number}")
            self.path.replace(aside)  # kept for whoever wants to look at it
        self._damaged = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        replace_file_atomically(self.path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), durable=True)
        self._saved, self.last = saved, last
