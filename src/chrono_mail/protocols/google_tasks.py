"""Google Tasks through Google's Tasks API (B-14, D-370). Google keeps no tasks in its CalDAV, so the client of a Google calendar account
also shows Google's task lists to the task synchronisation as if they were CalDAV task lists: each list at "/google-tasks/<list id>/", each
task as a VTODO text at an address of its own. The synchronisation, the server-first writes and Undo then work as for any task list (D-193).

What Google keeps of a task (checked in Google's documentation, 2026-11-21): its title (1,024 characters), notes (8,192), done or not with
the moment it was done, a due date without a time, and the task it is a subtask of. Everything else a task has here (a due time, priority,
start, tags, reminders, a repeat) stays on this computer (OR-21, D-201): a task read from Google is laid over the copy here, never in its
place. Google gives no version check on a write, so a task is read just before it is written and a newer version on Google is a conflict.
Google hides every task ticked off in its own app as it hides a list's cleared ones; every done task shows here as done, only a task
Google deleted leaves the list (the owner, Q138)."""

import http.client
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import override

from chrono_mail.core import AuthenticationError, ProtocolError
from chrono_mail.core.jsontext import load_json
from chrono_mail.pim.ical import Moment, Task, parse_tasks, tasks_to_text
from chrono_mail.protocols.dav import DavClient, DavConflict, DavError, RemoteChanges, RemoteCollection, RemoteItem, canonical_href
from chrono_mail.protocols.google import GOOGLE_NOT_FOUND, SIGN_IN_AGAIN

# --- Version ------------------------------------------------------------
__version__ = "google_tasks.py 4.7.658"

# --- Version history ----------------------------------------------------
# v4.7.658: Initial: Google's task lists and tasks for the task synchronisation of a Google calendar account (B-14, D-370)

TASKS_API: str = "https://tasks.googleapis.com/tasks/v1"
TASKS_SCOPE: str = "https://www.googleapis.com/auth/tasks"  # "Create, edit, organize, and delete all your tasks"
TASKS_PLACE: str = "/google-tasks/"  # the address the synchronisation knows Google's task lists by; never sent anywhere
MAXIMUM_TITLE: int = 1024  # Google's limits
MAXIMUM_NOTES: int = 8192
PAGE_SIZE: int = 100  # the most Google gives on one page
MAXIMUM_PAGES: int = 1100  # 100,000 tasks at most at Google, with room
MAXIMUM_ANSWER: int = 4 * 1024 * 1024  # one page of 100 tasks with long notes
CHANGES_MARGIN: timedelta = timedelta(minutes=10)  # the next check asks for what changed since this long before this one began (clocks differ)
NEEDS_SIGN_IN: str = ("Sign in with Google once more to see your Google Tasks (Settings > Accounts > the Google account > Edit > "
                      "Sign in with Google)")
API_OFF: str = ("Switch on “Google Tasks API” in your own Google Cloud project to see your Google Tasks (console.cloud.google.com > "
                "APIs & Services > Library)")
TOO_LONG_TITLE: str = f"Google keeps at most {MAXIMUM_TITLE:,} characters in a task's title: make it shorter"
TOO_LONG_NOTES: str = f"Google keeps at most {MAXIMUM_NOTES:,} characters in a task's notes: make them shorter"
SCOPE_REASONS: frozenset[str] = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
OFF_REASONS: frozenset[str] = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
BUSY_REASONS: frozenset[str] = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RATE_LIMIT_EXCEEDED", "dailyLimitExceeded"})


def is_tasks_place(href: str) -> bool:
    """Whether an address is one of Google's task lists or tasks."""
    return canonical_href(href).startswith(TASKS_PLACE)


def list_place(list_id: str) -> str:
    return f"{TASKS_PLACE}{list_id}/"


def _parts(href: str) -> tuple[str, str]:
    """(list id, the item's name without .ics; "" for the list itself)."""
    pieces = [piece for piece in canonical_href(href)[len(TASKS_PLACE) :].split("/") if piece]
    if not pieces:
        raise DavError("Not one of Google's task lists", 404)
    name = pieces[1] if len(pieces) > 1 else ""
    return pieces[0], name[:-4] if name.endswith(".ics") else name


# --- Google's answers ------------------------------------------------------------------------------------------------------------------------


def _reasons(raw: bytes) -> tuple[set[str], str]:
    """Google's reason codes and its message for a refusal."""
    try:
        answer = json.loads(raw)
    except ValueError:
        return set(), ""
    error = answer.get("error") if isinstance(answer, dict) else None
    if not isinstance(error, dict):
        return set(), ""
    reasons: set[str] = set()
    for key in ("errors", "details"):
        listed = error.get(key)
        if isinstance(listed, list):
            reasons.update(str(each.get("reason", "")) for each in listed if isinstance(each, dict))
    return reasons, str(error.get("message", ""))


def call(access: str, method: str, path: str, query: dict[str, str] | None = None, body: dict[str, object] | None = None) -> dict[str, object]:
    """One request to Google's Tasks API. A refusal is a DavError with Google's status, so the synchronisation tells a lasting "no" (a
    conflict) from a passing one (tried again); the tasks permission or the API missing says in plain words what to do."""
    url = TASKS_API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAXIMUM_ANSWER + 1)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise AuthenticationError(SIGN_IN_AGAIN) from None
        try:
            answer = error.read(64 * 1024)
        except (OSError, http.client.HTTPException):
            answer = b""
        reasons, message = _reasons(answer)
        if error.code == 403 and (reasons & SCOPE_REASONS or "insufficient authentication scopes" in message.lower()):
            raise DavError(NEEDS_SIGN_IN, 403) from None
        if error.code == 403 and (reasons & OFF_REASONS or "has not been used in project" in message or "is disabled" in message):
            raise DavError(API_OFF, 403) from None
        if error.code == 429 or (error.code == 403 and reasons & BUSY_REASONS):
            raise DavError(f"Google is busy: {message or error.code}", 429) from None  # asking later works
        raise DavError(f"Google refused: {message or error.code}", error.code) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", error)
        if isinstance(error, socket.gaierror) or isinstance(reason, socket.gaierror):
            raise ProtocolError(GOOGLE_NOT_FOUND) from None
        raise ProtocolError(f"Google could not be reached: {reason}") from None
    if len(raw) > MAXIMUM_ANSWER:
        raise ProtocolError("Google's answer is far too large")
    if not raw:
        return {}
    try:
        answer = json.loads(raw)
    except ValueError:
        raise ProtocolError("Google's answer could not be read") from None
    if not isinstance(answer, dict):
        raise ProtocolError("Google's answer could not be read")
    return {str(key): value for key, value in answer.items()}


def _text_field(task: dict[str, object], name: str) -> str:
    value = task.get(name)
    return value if isinstance(value, str) else ""


def _moment(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _due_text(due: Moment) -> str:
    """Google's due date: only the day counts (its time is thrown away), given as midnight UTC."""
    day = due.date() if isinstance(due, datetime) else due
    return f"{day.isoformat()}T00:00:00.000Z"


def _due_day(text: str) -> date | None:
    moment = _moment(text)
    return moment.date() if moment is not None else None  # Google writes the day as midnight UTC: its date part is the day


# --- what Google keeps of a task ------------------------------------------------------------------------------------------------------------


def body_of(task: Task) -> dict[str, object]:
    """The fields of a task that Google keeps; the rest stays here. Too long for Google: refused before anything is sent."""
    if len(task.summary) > MAXIMUM_TITLE:
        raise DavError(TOO_LONG_TITLE, 400)
    if len(task.description) > MAXIMUM_NOTES:
        raise DavError(TOO_LONG_NOTES, 400)
    body: dict[str, object] = {"title": task.summary, "notes": task.description, "status": "completed" if task.done else "needsAction"}
    body["due"] = _due_text(task.due) if task.due is not None else None
    if task.done:
        if task.completed is not None:
            body["completed"] = _stamp(task.completed)
    else:
        body["completed"] = None
    return body


def laid_over(task: dict[str, object], uid: str, parent: str, local: str | None) -> str:
    """A task as Google has it, laid over the copy here (local: its text, or None): Google's fields from Google, the rest as it is here.
    A due date that Google did not change keeps its time here; a status Google cannot hold (in process, cancelled) stays while Google
    calls it not done."""
    olds: list[Task] = parse_tasks(local) if local else []
    old = olds[0] if olds else None
    done = _text_field(task, "status") == "completed"
    due: Moment | None = _due_day(_text_field(task, "due"))
    if due is not None and old is not None and old.due is not None:
        old_day = old.due.date() if isinstance(old.due, datetime) else old.due
        if old_day == due:
            due = old.due
    completed = _moment(_text_field(task, "completed")) if done else None
    if done and old is not None and old.done and old.completed is not None and completed is not None and old.completed.date() == completed.date():
        completed = old.completed
    if done:
        status, percent = "COMPLETED", 100
    elif old is not None and not old.done:
        status, percent = old.status, old.percent
    else:
        status, percent = "NEEDS-ACTION", 0
    base = old if old is not None else Task(uid=uid)
    fresh = replace(
        base, uid=uid, summary=_text_field(task, "title"), description=_text_field(task, "notes"), due=due, completed=completed, status=status,
        percent=percent, parent=parent,
    )
    return tasks_to_text(fresh, local)


# --- the client --------------------------------------------------------------------------------------------------------------------------------


class GoogleDavClient(DavClient):
    """The client of a Google calendar account: its calendars over Google's CalDAV, its task lists over Google's Tasks API (D-370). It
    remembers which Google task each address is (an address chosen here for a task made here, Google's own id for one made on Google)."""

    def __init__(self, base_url: str, trusted: frozenset[str], bearer: str, kinds: frozenset[str]) -> None:
        super().__init__(base_url, "", "", trusted, bearer=bearer, kinds=kinds)
        self._places: dict[str, tuple[str, str, str]] = {}  # address -> (list id, Google's task id, UID here)
        self._lookup: tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], str]] | None = None  # see _maps
        self._moved: set[str] = set()  # addresses whose task Google moved to another list: their delete is already done
        self._file: Path | None = None
        self._local: Callable[[str, str], str | None] = lambda href, uid: None
        self._listed: dict[str, dict[str, object]] = {}  # address -> the task as the last listing gave it (fetched once, not again)
        self._unlisted: frozenset[str] = frozenset()
        self._tasks_lock: threading.Lock = threading.Lock()  # one write to Google Tasks at a time (what is remembered of them changes)
        self.tasks_trouble: str = ""  # why Google's task lists could not be listed (the permission or the API missing): said in the refresh

    @property
    @override
    def unlisted(self) -> frozenset[str]:
        return self._unlisted

    @override
    def attach_local(self, local: Callable[[str, str], str | None], place: Path | None) -> None:
        self._local = local
        if place is not None and place != self._file:
            self._file = place
            self._load()

    # --- what is remembered -----------------------------------------------------------------------------------------------------------

    def _load(self) -> None:
        if self._file is None:
            return
        try:
            data = load_json(self._file.read_text("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            return  # damaged: Google's own ids still lead to every task made there; one made here is matched by the next listing
        places = data.get("places") if isinstance(data, dict) else None
        moved = data.get("moved") if isinstance(data, dict) else None
        if isinstance(places, dict):
            for href, value in places.items():
                if isinstance(value, list) and len(value) == 3 and all(isinstance(part, str) for part in value):
                    self._remember(str(href), str(value[0]), str(value[1]), str(value[2]))
        if isinstance(moved, list):
            self._moved.update(canonical_href(str(href)) for href in moved if isinstance(href, str))

    def _save(self) -> None:
        if self._file is None:
            return
        text = json.dumps({"version": 1, "places": {href: list(value) for href, value in sorted(self._places.items())}, "moved": sorted(self._moved)})
        temporary = self._file.with_name(self._file.name + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._file)
        except OSError:
            pass  # a full disk: Google has the change and it is kept in memory, saved with the next write (a failed write here never undoes Google's)

    def _remember(self, href: str, list_id: str, task_id: str, uid: str) -> None:
        place = canonical_href(href)
        if place in self._places:
            self._lookup = None  # an address that changes its task: made again at the next look
        elif self._lookup is not None:  # a new address (a listing remembers every task): added to the tables, never all made again
            self._lookup[0][(list_id, task_id)] = (place, uid)
            self._lookup[1][(list_id, uid)] = task_id
        self._places[place] = (list_id, task_id, uid)

    def _forget(self, href: str) -> tuple[str, str, str] | None:
        self._lookup = None
        return self._places.pop(canonical_href(href), None)

    def _maps(self) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], str]]:
        """(list, task id) -> (address, UID), and (list, UID) -> task id: made once after each change, so a list of thousands of tasks is
        looked up at once, never searched per task."""
        if self._lookup is None:
            by_task = {(list_id, task_id): (href, uid) for href, (list_id, task_id, uid) in self._places.items()}
            by_uid = {(list_id, uid): task_id for list_id, task_id, uid in self._places.values()}
            self._lookup = (by_task, by_uid)
        return self._lookup

    def _task_of(self, href: str) -> tuple[str, str]:
        """(list id, Google's task id) of an address: remembered, or the name itself (a task made on Google is known by its own id)."""
        found = self._places.get(canonical_href(href))
        if found is not None:
            return found[0], found[1]
        list_id, name = _parts(href)
        return list_id, name

    def _uid_of(self, list_id: str, task_id: str) -> str:
        found = self._maps()[0].get((list_id, task_id))
        return found[1] if found is not None else task_id

    def _href_of(self, list_id: str, task_id: str) -> str:
        found = self._maps()[0].get((list_id, task_id))
        return found[0] if found is not None else f"{list_place(list_id)}{task_id}.ics"

    def _task_of_uid(self, list_id: str, uid: str) -> str:
        """Google's id of the task with this UID in this list ("" when none is known)."""
        return self._maps()[1].get((list_id, uid), "")

    def _access(self) -> str:
        return self.access

    # --- reading ----------------------------------------------------------------------------------------------------------------------

    def _lists(self) -> list[RemoteCollection]:
        found: list[RemoteCollection] = []
        token = ""
        for _ in range(MAXIMUM_PAGES):
            answer = call(self._access(), "GET", "/users/@me/lists", {"maxResults": str(PAGE_SIZE), **({"pageToken": token} if token else {})})
            items = answer.get("items")
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    found.append(RemoteCollection(list_place(str(item["id"])), _text_field(item, "title") or "Tasks", "tasks", "VTODO", "", False,
                                                  _text_field(item, "updated"), "google", keeps_colour=False))
            token = _text_field(answer, "nextPageToken")
            if not token:
                return found
        raise ProtocolError("Google's list of task lists never ended")

    def _tasks(self, list_id: str, since: str = "") -> list[dict[str, object]]:
        """Every task of a list as Google shows them, done ones too (Google hides those ticked off in its own app); since: only those changed
        since then, deleted ones among them."""
        query = {"maxResults": str(PAGE_SIZE), "showCompleted": "true", "showHidden": "true", "showAssigned": "true", "showDeleted": "true" if since else "false"}
        if since:
            query["updatedMin"] = since
        found: list[dict[str, object]] = []
        token = ""
        for _ in range(MAXIMUM_PAGES):
            answer = call(self._access(), "GET", f"/lists/{urllib.parse.quote(list_id, safe='')}/tasks", {**query, **({"pageToken": token} if token else {})})
            items = answer.get("items")
            found.extend({str(key): value for key, value in item.items()} for item in (items if isinstance(items, list) else []) if isinstance(item, dict) and isinstance(item.get("id"), str))
            token = _text_field(answer, "nextPageToken")
            if not token:
                return found
        raise ProtocolError("Google's list of tasks never ended")

    def _get(self, list_id: str, task_id: str) -> dict[str, object] | None:
        try:
            task = call(self._access(), "GET", f"/lists/{urllib.parse.quote(list_id, safe='')}/tasks/{urllib.parse.quote(task_id, safe='')}")
        except DavError as error:
            if error.status in (400, 404, 410):  # not there (an id Google never gave answers 400)
                return None
            raise
        return None if task.get("deleted") is True else task

    def _text(self, list_id: str, task: dict[str, object]) -> str:
        task_id = str(task["id"])
        uid = self._uid_of(list_id, task_id)
        parent_id = _text_field(task, "parent")
        parent = self._uid_of(list_id, parent_id) if parent_id else ""
        return laid_over(task, uid, parent, self._local(list_place(list_id), uid))

    def _item(self, list_id: str, task: dict[str, object]) -> RemoteItem:
        href = self._href_of(list_id, str(task["id"]))
        if canonical_href(href) not in self._places:
            self._remember(href, list_id, str(task["id"]), str(task["id"]))  # known from now on, so a move to another list finds it
        self._listed[canonical_href(href)] = task
        return RemoteItem(href, _text_field(task, "etag"))

    @override
    def all_collections(self) -> list[RemoteCollection]:
        found = super().all_collections()
        if "tasks" not in self.kinds:
            return found
        try:
            lists = self._lists()
        except AuthenticationError:
            raise
        except DavError as error:
            if error.status != 403:
                raise
            self._unlisted, self.tasks_trouble = frozenset({"tasks"}), str(error)  # the calendars go on; the task lists here wait
            return found
        self._unlisted, self.tasks_trouble = frozenset(), ""
        return [collection for collection in found if collection.kind != "tasks"] + lists  # Google's CalDAV holds no tasks

    @override
    def items(self, collection: RemoteCollection) -> list[RemoteItem]:
        if not is_tasks_place(collection.href):
            return super().items(collection)
        list_id, _ = _parts(collection.href)
        items = [self._item(list_id, task) for task in self._tasks(list_id) if task.get("deleted") is not True]
        self._save()
        return items

    @override
    def changes(self, collection: RemoteCollection, token: str) -> RemoteChanges:
        if not is_tasks_place(collection.href):
            return super().changes(collection, token)
        list_id, _ = _parts(collection.href)
        if token and _moment(token) is None:
            raise DavConflict("The moment of the last check could not be read", 409)  # the caller starts again from nothing
        began = datetime.now(UTC)
        tasks = self._tasks(list_id, token)
        changed = [self._item(list_id, task) for task in tasks if task.get("deleted") is not True]
        removed = tuple(self._href_of(list_id, str(task["id"])) for task in tasks if task.get("deleted") is True)
        self._save()
        return RemoteChanges(tuple(changed), removed, _stamp(began - CHANGES_MARGIN))

    @override
    def read(self, collection: RemoteCollection, href: str) -> tuple[str, str] | None:
        if not is_tasks_place(href):
            return super().read(collection, href)
        return self.get(href)

    @override
    def get(self, href: str) -> tuple[str, str] | None:
        if not is_tasks_place(href):
            return super().get(href)
        if canonical_href(href) in self._moved:
            return None
        list_id, task_id = self._task_of(href)
        task = self._get(list_id, task_id)
        if task is None:
            return None
        return _text_field(task, "etag"), self._text(list_id, task)

    @override
    def fetch(self, collection: RemoteCollection, hrefs: list[str], refused: set[str] | None = None) -> dict[str, tuple[str, str]]:
        if not is_tasks_place(collection.href):
            return super().fetch(collection, hrefs, refused)
        list_id, _ = _parts(collection.href)
        found: dict[str, tuple[str, str]] = {}
        for href in hrefs:
            listed = self._listed.pop(canonical_href(href), None)
            if listed is not None:  # the listing gave it whole a moment ago: not asked again
                found[href] = (_text_field(listed, "etag"), self._text(list_id, listed))
                continue
            try:
                single = self.get(href)
            except AuthenticationError:
                raise
            except DavError as error:
                if refused is not None and error.status and error.status < 500 and error.status not in (408, 429):
                    refused.add(href)
                continue
            except ProtocolError:
                continue
            if single is not None:
                found[href] = single
            elif refused is not None:
                refused.add(href)
        return found

    @override
    def etag_of(self, href: str) -> str:
        if not is_tasks_place(href):
            return super().etag_of(href)
        found = self.get(href)
        return found[0] if found is not None else ""

    @override
    def exists(self, href: str) -> bool:
        if not is_tasks_place(href):
            return super().exists(href)
        list_id, name = _parts(href)
        if name:
            return self.get(href) is not None
        try:
            call(self._access(), "GET", f"/users/@me/lists/{urllib.parse.quote(list_id, safe='')}")
        except DavError as error:
            if error.status in (400, 404, 410):
                return False
            raise
        return True

    # --- writing: Google first ------------------------------------------------------------------------------------------------------------

    @override
    def put(self, href: str, text: str, kind: str, etag: str = "", create: bool = False) -> str:
        if not is_tasks_place(href):
            return super().put(href, text, kind, etag, create)
        tasks = parse_tasks(text)
        if not tasks:
            raise DavError("This task could not be read", 400)
        task = tasks[0]
        body = body_of(task)
        list_id, _ = _parts(href)
        quoted_list = urllib.parse.quote(list_id, safe="")
        with self._tasks_lock:
            if create:
                place = canonical_href(href)
                known = self._places.get(place) if place not in self._moved else None
                if known is not None and known[2] != task.uid:
                    raise DavConflict("That name is taken", 412)
                elsewhere = next(((other, value) for other, value in self._places.items() if value[2] == task.uid and value[0] != list_id and other not in self._moved), None)
                if known is not None:  # this task made here before, its answer lost (the connection broke): written again, never made twice
                    task_id = known[1]
                    result = call(self._access(), "PATCH", f"/lists/{quoted_list}/tasks/{urllib.parse.quote(task_id, safe='')}", body=body)
                elif elsewhere is not None:  # the same task, moved here from another list: Google moves it, keeping what it holds (a repeat)
                    other, (old_list, task_id, _) = elsewhere
                    call(self._access(), "POST", f"/lists/{urllib.parse.quote(old_list, safe='')}/tasks/{urllib.parse.quote(task_id, safe='')}/move", {"destinationTasklist": list_id})
                    self._moved.add(other)
                    self._forget(other)
                    self._remember(href, list_id, task_id, task.uid)  # known at once: a write that fails now is tried again on this task
                    self._save()
                    result = call(self._access(), "PATCH", f"/lists/{quoted_list}/tasks/{urllib.parse.quote(task_id, safe='')}", body=body)
                else:
                    result = call(self._access(), "POST", f"/lists/{quoted_list}/tasks", body={key: value for key, value in body.items() if value is not None})
                    task_id = _text_field(result, "id")
                    if not task_id:
                        raise DavError("Google did not say which task it made", 500)
                self._remember(href, list_id, task_id, task.uid)
            else:
                _, task_id = self._task_of(href)
                current = self._get(list_id, task_id)
                if current is None or (etag and _text_field(current, "etag") != etag):
                    raise DavConflict("Changed or deleted on Google since the last check", 412)
                result = call(self._access(), "PATCH", f"/lists/{quoted_list}/tasks/{urllib.parse.quote(task_id, safe='')}", body=body)
            wanted = self._task_of_uid(list_id, task.parent) if task.parent else ""
            if wanted != _text_field(result, "parent") and wanted != task_id:
                result = call(self._access(), "POST", f"/lists/{quoted_list}/tasks/{urllib.parse.quote(task_id, safe='')}/move", {"parent": wanted} if wanted else None)
            self._listed.pop(canonical_href(href), None)
            self._save()
        return _text_field(result, "etag")

    @override
    def delete(self, href: str, etag: str = "") -> None:
        if not is_tasks_place(href):
            super().delete(href, etag)
            return
        place = canonical_href(href)
        with self._tasks_lock:
            if place in self._moved:  # Google moved it to another list: nothing left here to delete
                self._moved.discard(place)
                self._save()
                return
            list_id, task_id = self._task_of(href)
            current = self._get(list_id, task_id)
            if current is not None:
                if etag and _text_field(current, "etag") != etag:
                    raise DavConflict("Changed on Google since the last check", 412)
                try:
                    call(self._access(), "DELETE", f"/lists/{urllib.parse.quote(list_id, safe='')}/tasks/{urllib.parse.quote(task_id, safe='')}")
                except DavError as error:
                    if error.status not in (404, 410):
                        raise
            self._forget(place)
            self._listed.pop(place, None)
            self._save()

    @override
    def move(self, href: str, destination: str) -> None:
        if not is_tasks_place(href):
            super().move(href, destination)
            return
        with self._tasks_lock:  # only a new address for the same task: Google's task stays as it is
            found = self._forget(href)
            list_id, task_id = found[:2] if found is not None else self._task_of(href)
            uid = found[2] if found is not None else task_id
            self._remember(destination, list_id, task_id, uid)
            self._save()

    @override
    def set_collection(self, href: str, name: str | None = None, colour: str | None = None) -> dict[str, int]:
        if not is_tasks_place(href):
            return super().set_collection(href, name, colour)
        statuses: dict[str, int] = {}
        if name is not None:  # a list is one's own at Google: its name changes there, as one's own name for a calendar (D-343)
            list_id, _ = _parts(href)
            call(self._access(), "PATCH", f"/users/@me/lists/{urllib.parse.quote(list_id, safe='')}", body={"title": name})
            statuses["name"] = 200
        if colour is not None:
            statuses["colour"] = 405  # Google keeps no colour for a task list: it stays here
        return statuses
