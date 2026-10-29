import json
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_mail.core import ChronoMailError, ProtocolError
from chrono_mail.engine import Engine, commands_updates
from chrono_mail.engine.catalog import build_registry
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_updates import (
    UPDATE_CHECK,
    UPDATE_DOWNLOAD,
    UPDATE_INSTALL,
    UPDATE_SHOW,
    UPDATE_SKIP,
    UPDATE_STOP_DOWNLOAD,
    CheckParameters,
    DownloadParameters,
    InstallParameters,
    StopParameters,
    UpdateCheck,
    VersionParameters,
)
from chrono_mail.engine.registry import UsageError
from chrono_mail.protocols.releases import DownloadStopped
from tests.enginefactory import make_engine
from tests.fakegithub import FakeGitHub

# --- Version ------------------------------------------------------------
__version__ = "test_engine_updates.py 4.2.427"

# --- Version history ----------------------------------------------------
# v4.2.427: the stand-in's release pages are on github.com; the list reader takes what lets a download stop while it reads
# v4.2.419: Initial: update.check, update.skip, update.download and update.install against a stand-in for GitHub (D-293)

REGISTRY = build_registry()
DAY: float = 86_400.0
DEB = "_amd64.deb"


@pytest.fixture
def github() -> Generator[FakeGitHub]:
    with FakeGitHub() as server:
        yield server


def installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub, kind: str = "deb", version: str = "4.2.418") -> Engine:
    """An engine of a copy installed from a .deb (or another installer) of this version, reading the stand-in for GitHub."""
    monkeypatch.setattr(commands_updates, "install_kind", lambda: kind)
    monkeypatch.setattr(commands_updates, "program_version", lambda: version)
    engine = make_engine(tmp_path)
    engine.releases_address = github.releases_address
    return engine


def check(engine: Engine, by_hand: bool = False) -> UpdateCheck:
    return REGISTRY.ask(engine, UPDATE_CHECK, CheckParameters(by_hand))


def releases_asked(github: FakeGitHub) -> int:
    return sum(1 for path in github.requests if path.startswith("/releases"))


def test_a_copy_run_from_its_project_folder_asks_github_nothing(tmp_path: Path, github: FakeGitHub) -> None:
    engine = make_engine(tmp_path)
    engine.releases_address = github.releases_address
    github.publish("9.9.999", {f"chrono-mail_9.9.999{DEB}": b"new"})
    assert check(engine).outcome == "not_installed"
    assert check(engine, by_hand=True).outcome == "not_installed"
    assert github.requests == []
    setup = REGISTRY.ask(engine, UPDATE_SHOW, NoParameters())
    assert setup.available is False and setup.reason == commands_updates.NOT_FROM_AN_INSTALLER


def test_the_newest_release_with_this_computer_s_installer_is_offered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.417", {f"chrono-mail_4.2.417{DEB}": b"old"})
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb", "chrono-mail_4.2.419_x86_64.rpm": b"rpm"})
    github.publish("4.2.420", {"chrono-mail_4.2.420_x86_64.rpm": b"rpm", "chrono-mail_4.2.420_setup.exe": b"exe"})  # no .deb yet
    github.publish("4.2.421", {f"chrono-mail_4.2.421{DEB}": b"draft"}, draft=True)
    github.publish("4.2.422", {f"chrono-mail_4.2.422{DEB}": b"pre"}, prerelease=True)
    found = check(engine)
    assert (found.outcome, found.version, found.running, found.file_name, found.size) == ("available", "4.2.419", "4.2.418", f"chrono-mail_4.2.419{DEB}", 3)
    assert found.page == "https://github.com/rob-vandenberg/chrono-mail/releases/tag/4.2.419"


def test_nothing_newer_is_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github, version="4.2.419")
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    assert check(engine).outcome == "current"


def test_github_is_asked_at_most_once_a_day_unless_the_person_asks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    assert check(engine).outcome == "available"
    assert check(engine).outcome == "checked_today"
    assert releases_asked(github) == 1
    assert check(engine, by_hand=True).outcome == "available"  # Check for updates in About: also today
    assert releases_asked(github) == 2
    now = engine.clock()
    engine.clock = lambda: now + DAY
    assert check(engine).outcome == "available"
    assert releases_asked(github) == 3


def test_a_check_github_did_not_answer_is_tried_again_at_the_next_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    engine.releases_address = "http://127.0.0.1:9/releases"  # nothing answers there: offline
    with pytest.raises(ProtocolError):
        check(engine)
    assert not engine.directories.updates_file.exists()
    engine.releases_address = github.releases_address
    assert check(engine).outcome == "available"


def test_a_skipped_version_is_offered_again_only_by_hand_or_when_a_newer_one_comes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    assert REGISTRY.ask(engine, UPDATE_SKIP, VersionParameters("4.2.419")).skipped == "4.2.419"
    assert check(engine).outcome == "skipped"
    found = check(engine, by_hand=True)
    assert (found.outcome, found.version) == ("available", "4.2.419")
    github.publish("4.2.420", {f"chrono-mail_4.2.420{DEB}": b"newer"})
    now = engine.clock()
    engine.clock = lambda: now + DAY
    found = check(engine)
    assert (found.outcome, found.version) == ("available", "4.2.420")


def test_a_download_is_complete_and_checked_and_forgets_the_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    content = bytes(range(256)) * 10_000  # more than one piece of a download
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": content})
    REGISTRY.ask(engine, UPDATE_SKIP, VersionParameters("4.2.419"))
    shown: list[tuple[int, int | None]] = []
    engine.progress = lambda what, done, total: shown.append((done, total)) if what == commands_updates.PROGRESS_WHAT else None
    result = REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads")))
    assert Path(result.path) == tmp_path / "Downloads" / f"chrono-mail_4.2.419{DEB}"
    assert Path(result.path).read_bytes() == content and (result.version, result.size) == ("4.2.419", len(content))
    assert sorted(path.name for path in (tmp_path / "Downloads").iterdir()) == [f"chrono-mail_4.2.419{DEB}"]  # no piece left beside it
    assert shown and shown[-1] == (2, 2)  # in MB
    assert REGISTRY.ask(engine, UPDATE_SHOW, NoParameters()).skipped == ""


def test_a_download_with_the_wrong_fingerprint_is_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.wrong_digest.add(f"chrono-mail_4.2.419{DEB}")
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"changed on the way"})
    with pytest.raises(ProtocolError, match="damaged"):
        REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads")))
    assert list((tmp_path / "Downloads").iterdir()) == []


def test_a_download_the_person_stopped_leaves_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"x" * (3 * 1024 * 1024)})

    def cancel(what: str, done: int, total: int | None) -> None:
        REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t1"))  # Cancel at the first piece

    engine.progress = cancel
    with pytest.raises(DownloadStopped):
        REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t1"))
    assert list((tmp_path / "Downloads").iterdir()) == []


def test_an_update_is_only_taken_from_github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    assets = github.releases[0]["assets"]
    assert isinstance(assets, list)
    assets[0]["browser_download_url"] = "https://downloads.example.com/chrono-mail_4.2.419_amd64.deb"
    with pytest.raises(ProtocolError, match="only taken from GitHub"):
        REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads")))


@pytest.mark.parametrize("body", [b"not json", b"[" * 100_000, json.dumps({"message": "rate limit"}).encode()], ids=["garbled", "nested", "not-a-list"])
def test_a_list_of_releases_that_is_not_one_is_an_error_never_a_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub, body: bytes) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.body = body
    with pytest.raises(ProtocolError):
        check(engine)


def test_a_release_file_without_a_fingerprint_or_a_real_size_is_never_offered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    github.publish("4.2.420", {f"chrono-mail_4.2.420{DEB}": b"deb"})
    assets_419, assets_420 = github.releases[1]["assets"], github.releases[0]["assets"]
    assert isinstance(assets_419, list) and isinstance(assets_420, list)
    assets_420[0]["digest"] = ""
    assets_419[0]["size"] = True
    assert check(engine).outcome == "current"


def test_an_update_is_installed_only_when_it_is_the_file_that_was_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"the package"})
    installs: list[Path] = []
    monkeypatch.setattr(commands_updates, "_install_package", installs.append)
    path = REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"))).path
    Path(path).write_bytes(b"changed since")
    with pytest.raises(ChronoMailError, match="changed since it was checked"):
        REGISTRY.ask(engine, UPDATE_INSTALL, InstallParameters(path))
    assert installs == []
    Path(path).write_bytes(b"the package")
    assert REGISTRY.ask(engine, UPDATE_INSTALL, InstallParameters(path)).restart is True
    assert installs == [Path(path)]


def test_a_file_downloaded_in_another_run_is_checked_against_github_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"the package"})
    installs: list[Path] = []
    monkeypatch.setattr(commands_updates, "_install_package", installs.append)
    path = tmp_path / f"chrono-mail_4.2.419{DEB}"
    path.write_bytes(b"the package")
    REGISTRY.ask(engine, UPDATE_INSTALL, InstallParameters(str(path)))
    assert installs == [path]


def test_only_an_installer_of_chrono_mail_for_this_computer_is_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    for name in ("chrono-mail_4.2.419_setup.exe", "other_4.2.419_amd64.deb", "chrono-mail_latest_amd64.deb"):
        (tmp_path / name).write_bytes(b"x")
        with pytest.raises(UsageError):
            REGISTRY.ask(engine, UPDATE_INSTALL, InstallParameters(str(tmp_path / name)))


def test_windows_starts_the_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github, kind="windows")
    github.publish("4.2.419", {"chrono-mail_4.2.419_setup.exe": b"setup"})
    started: list[Path] = []
    monkeypatch.setattr(commands_updates, "_start_setup", started.append)
    path = REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path))).path
    assert REGISTRY.ask(engine, UPDATE_INSTALL, InstallParameters(path)).restart is False
    assert started == [Path(path)]


def test_a_cancel_while_github_s_list_comes_in_stops_the_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    reading = commands_updates.fetch_releases

    def cancelled_meanwhile(address: str, user_agent: str, **options: object) -> tuple[commands_updates.Release, ...]:
        found = reading(address, user_agent)
        REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t1"))  # the person pressed Cancel before the first piece came
        return found

    monkeypatch.setattr(commands_updates, "fetch_releases", cancelled_meanwhile)
    with pytest.raises(DownloadStopped):
        REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t1"))
    assert list((tmp_path / "Downloads").iterdir()) == []


@pytest.mark.parametrize("text", ['{"checked_on": "2026-1\\u00e9-29", "skipped": "4.2.1"}', '{"checked_on": "29-10-2026", "skipped": 4}', "[]", "garbled"], ids=["not-ascii", "no-day", "a-list", "garbled"])
def test_a_damaged_updates_file_is_no_state_and_is_written_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub, text: str) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    engine.directories.updates_file.parent.mkdir(parents=True, exist_ok=True)
    engine.directories.updates_file.write_text(text, "utf-8")
    assert check(engine).outcome == "available"
    assert REGISTRY.ask(engine, UPDATE_SKIP, VersionParameters("4.2.419")).skipped == "4.2.419"
    assert json.loads(engine.directories.updates_file.read_text("utf-8"))["skipped"] == "4.2.419"


def test_a_cancel_that_comes_before_its_download_began_still_stops_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    engine = installed(monkeypatch, tmp_path, github)
    github.publish("4.2.419", {f"chrono-mail_4.2.419{DEB}": b"deb"})
    REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t2"))  # ran first on another thread
    with pytest.raises(DownloadStopped):
        REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t2"))
    another = REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t3"))  # a new download is not stopped
    assert Path(another.path).read_bytes() == b"deb"
