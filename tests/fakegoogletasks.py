import json
import threading
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override

# --- Version ------------------------------------------------------------
__version__ = "fakegoogletasks.py 4.7.658"

# --- Version history ----------------------------------------------------
# v4.7.658: Initial: Google's Tasks API on this computer, as its documentation describes it (B-14, D-370)


class FakeTasksApi:
    """Google's Tasks API on this computer: task lists and tasks with etags, changes since a moment (deleted ones too), subtasks, moves
    between lists, and the refusals Google gives (the tasks permission missing, the API switched off, a lasting no)."""

    def __init__(self) -> None:
        self.lists: dict[str, str] = {}  # list id -> title
        self.tasks: dict[str, dict[str, dict[str, object]]] = {}  # list id -> task id -> the task as Google gives it
        self.counter: int = 0
        self.no_scope: bool = False  # every request: 403, the access code lacks the tasks permission
        self.api_off: bool = False  # every request: 403, the Tasks API is not switched on in the person's project
        self.refusing: bool = False  # a write: a lasting 400
        self.moves: list[tuple[str, str, str]] = []  # (task id, destination list or "", parent or "")
        self.writes: int = 0
        self._server: ThreadingHTTPServer | None = None

    # --- what Google holds --------------------------------------------------------------------------------------------------------------

    def _touch(self, task: dict[str, object]) -> None:
        self.counter += 1
        task["etag"] = f'"e{self.counter}"'
        task["updated"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def add_list(self, list_id: str, title: str) -> None:
        self.lists[list_id] = title
        self.tasks.setdefault(list_id, {})

    def add(self, list_id: str, task_id: str, title: str, **fields: object) -> dict[str, object]:
        task: dict[str, object] = {"kind": "tasks#task", "id": task_id, "title": title, "status": "needsAction", **fields}
        self._touch(task)
        self.tasks[list_id][task_id] = task
        return task

    def change(self, list_id: str, task_id: str, **fields: object) -> None:
        task = self.tasks[list_id][task_id]
        for key, value in fields.items():
            if value is None:
                task.pop(key, None)
            else:
                task[key] = value
        self._touch(task)

    def remove(self, list_id: str, task_id: str) -> None:
        """Deleted on Google: listed as deleted when changes are asked for, gone otherwise."""
        self.change(list_id, task_id, deleted=True)

    def live(self, list_id: str) -> dict[str, dict[str, object]]:
        return {key: task for key, task in self.tasks[list_id].items() if task.get("deleted") is not True}

    def titled(self, list_id: str, title: str) -> dict[str, object]:
        return next(task for task in self.live(list_id).values() if task.get("title") == title)

    # --- the server -----------------------------------------------------------------------------------------------------------------------

    def __enter__(self) -> "FakeTasksApi":
        api = self

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _answer(self, status: int, body: dict[str, object] | None = None) -> None:
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _refused(self) -> bool:
                if api.no_scope:
                    self._answer(403, {"error": {"code": 403, "message": "Request had insufficient authentication scopes.", "status": "PERMISSION_DENIED",
                                                 "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}})
                    return True
                if api.api_off:
                    self._answer(403, {"error": {"code": 403, "message": "Google Tasks API has not been used in project 1 before or it is disabled.",
                                                 "errors": [{"reason": "accessNotConfigured"}], "details": [{"reason": "SERVICE_DISABLED"}]}})
                    return True
                return False

            def _body(self) -> dict[str, object]:
                length = int(self.headers.get("Content-Length") or 0)
                data: object = json.loads(self.rfile.read(length) or b"{}") if length else None
                return data if isinstance(data, dict) else {}

            def do_GET(self) -> None:  # noqa: N802
                if self._refused():
                    return
                with lock:
                    self._answer(*api.get(self.path))

            def do_POST(self) -> None:  # noqa: N802
                if self._refused():
                    return
                body = self._body()
                with lock:
                    self._answer(*api.post(self.path, body))

            def do_PATCH(self) -> None:  # noqa: N802
                if self._refused():
                    return
                body = self._body()
                with lock:
                    self._answer(*api.patch(self.path, body))

            def do_DELETE(self) -> None:  # noqa: N802
                if self._refused():
                    return
                with lock:
                    self._answer(*api.delete(self.path))

        lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *details: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/tasks/v1"

    # --- the API ------------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _split(path: str) -> tuple[list[str], dict[str, str]]:
        parts = urllib.parse.urlsplit(path)
        pieces = [urllib.parse.unquote(piece) for piece in parts.path.split("/") if piece][2:]  # after /tasks/v1
        return pieces, dict(urllib.parse.parse_qsl(parts.query))

    def get(self, path: str) -> tuple[int, dict[str, object] | None]:
        pieces, query = self._split(path)
        if pieces[:3] == ["users", "@me", "lists"]:
            if len(pieces) == 4:
                return (200, {"id": pieces[3], "title": self.lists[pieces[3]]}) if pieces[3] in self.lists else (404, {"error": {"message": "Not Found"}})
            return 200, {"items": [{"id": key, "title": title, "updated": "2026-11-21T00:00:00.000Z"} for key, title in self.lists.items()]}
        if pieces[0] != "lists" or pieces[1] not in self.tasks:
            return 404, {"error": {"message": "Not Found"}}
        tasks = self.tasks[pieces[1]]
        if len(pieces) == 4:
            task = tasks.get(pieces[3])
            return (200, dict(task)) if task is not None else (404, {"error": {"message": "Not Found"}})
        chosen = [task for task in tasks.values() if query.get("showDeleted") == "true" or task.get("deleted") is not True]
        if "updatedMin" in query:
            chosen = [task for task in chosen if str(task["updated"]) >= query["updatedMin"].replace(".000Z", "")]
        if query.get("showHidden") != "true":
            chosen = [task for task in chosen if task.get("hidden") is not True]
        size = int(query.get("maxResults", "20"))
        start = int(query.get("pageToken") or 0)
        page = chosen[start : start + size]
        answer: dict[str, object] = {"items": [dict(task) for task in page]}
        if start + size < len(chosen):
            answer["nextPageToken"] = str(start + size)
        return 200, answer

    def post(self, path: str, body: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        pieces, query = self._split(path)
        if self.refusing:
            return 400, {"error": {"message": "Invalid task"}}
        self.writes += 1
        tasks = self.tasks.get(pieces[1])
        if tasks is None:
            return 404, {"error": {"message": "Not Found"}}
        if len(pieces) == 3:
            self.counter += 1
            task_id = f"g{self.counter}"
            task: dict[str, object] = {"kind": "tasks#task", "id": task_id, **{key: value for key, value in body.items() if value is not None}}
            if isinstance(task.get("due"), str):
                task["due"] = str(task["due"])[:10] + "T00:00:00.000Z"  # only the day is kept
            self._touch(task)
            tasks[task_id] = task
            return 200, dict(task)
        task_id = pieces[3]
        found = tasks.get(task_id)
        if found is None:
            return 404, {"error": {"message": "Not Found"}}
        task = found
        target = query.get("destinationTasklist", "")
        self.moves.append((task_id, target, query.get("parent", "")))
        if target and target != pieces[1]:
            del tasks[task_id]
            self.tasks[target][task_id] = task
            task.pop("parent", None)
        if "parent" in query:
            task["parent"] = query["parent"]
        elif not target:
            task.pop("parent", None)
        self._touch(task)
        return 200, dict(task)

    def patch(self, path: str, body: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        pieces, _ = self._split(path)
        if self.refusing:
            return 400, {"error": {"message": "Invalid task"}}
        self.writes += 1
        if pieces[:3] == ["users", "@me", "lists"]:
            self.lists[pieces[3]] = str(body.get("title", ""))
            return 200, {"id": pieces[3], "title": self.lists[pieces[3]]}
        task = self.tasks.get(pieces[1], {}).get(pieces[3])
        if task is None or task.get("deleted") is True:
            return 404, {"error": {"message": "Not Found"}}
        for key, value in body.items():
            if value is None:
                task.pop(key, None)
            elif key == "due":
                task[key] = str(value)[:10] + "T00:00:00.000Z"  # only the day is kept
            else:
                task[key] = value
        if task.get("status") == "completed" and "completed" not in task:
            task["completed"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self._touch(task)
        return 200, dict(task)

    def delete(self, path: str) -> tuple[int, dict[str, object] | None]:
        pieces, _ = self._split(path)
        self.writes += 1
        task = self.tasks.get(pieces[1], {}).get(pieces[3])
        if task is None or task.get("deleted") is True:
            return 404, {"error": {"message": "Not Found"}}
        task["deleted"] = True
        self._touch(task)
        return 204, None
