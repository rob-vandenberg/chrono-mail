import dataclasses
import functools
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from chrono_mail.core import ChronoMailError, ProtocolError
from chrono_mail.core.appinfo import program_version
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.releases import Release, ReleaseFile, download, fetch_releases, parse_version, version_text
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "commands_updates.py 4.2.419"

# --- Version history ----------------------------------------------------
# v4.2.419: Initial: update.show, update.check, update.skip, update.download, update.stop-download, update.install and update.restart (D-293)

# Each installed copy is updated by the installer it came from: the Windows setup, or the Linux .deb or .rpm. A check reads GitHub's list
# of releases and offers the newest version above this one that carries that installer. It never connects by itself: the window asks for it
# with a Refresh that went online, at most once a day, or when the person clicks Check for updates (D-293).
FILE_ENDINGS: dict[str, str] = {"windows": "_setup.exe", "deb": "_amd64.deb", "rpm": "_x86_64.rpm"}
PROGRESS_WHAT: str = "Downloading the update"
NOT_FROM_AN_INSTALLER: str = "This copy runs from its project folder; it is not updated through installers."
NOT_FROM_A_PACKAGE: str = "This copy was not installed from a .deb, .rpm or setup.exe, so it is not updated through one."
PACKAGEKIT: str = "org.freedesktop.PackageKit"
PACKAGEKIT_SUCCESS: int = 1  # PackageKit's exit code of a transaction that ended well
PACKAGEKIT_WAIT_SECONDS: float = 900.0  # no word from the system's installer for this long (its password window included): it has stalled
RESTART_WAIT: str = 'i=0; while kill -0 "$1" 2>/dev/null; do i=$((i+1)); [ "$i" -gt 600 ] && exit 0; sleep 0.2; done; exec "$2"'


@functools.cache
def _detected_kind() -> str:
    if "__compiled__" not in globals():  # the packaged program (Nuitka); a run from the project folder is updated through git
        return ""
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        if Path("/var/lib/dpkg/info/chrono-mail.list").is_file():
            return "deb"
        try:
            found = subprocess.run(["rpm", "-q", "--quiet", "chrono-mail"], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return "rpm" if found.returncode == 0 else ""
    return ""


def install_kind() -> str:
    """How this copy was installed: "windows", "deb" or "rpm"; "" when it was not installed from one of them."""
    return _detected_kind()


def _not_installed_reason() -> str:
    return NOT_FROM_A_PACKAGE if "__compiled__" in globals() else NOT_FROM_AN_INSTALLER


@dataclass(frozen=True, slots=True)
class UpdateState:
    checked_on: str = ""  # the day (YYYY-MM-DD, this computer's time) GitHub last answered a check
    skipped: str = ""  # the version the person chose to skip


def _read_state(engine: Engine) -> UpdateState:
    """What is kept about updates; a missing or damaged file is no state (the next check runs, nothing is skipped)."""
    try:
        data = load_json(engine.directories.updates_file.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError):
        return UpdateState()
    if not isinstance(data, dict):
        return UpdateState()
    checked_on, skipped = data.get("checked_on"), data.get("skipped")
    return UpdateState(
        checked_on if isinstance(checked_on, str) and _a_day(checked_on) else "",
        skipped if isinstance(skipped, str) and parse_version(skipped) is not None else "",
    )


def _a_day(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _write_state(engine: Engine, state: UpdateState) -> None:
    path = engine.directories.updates_file
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"checked_on": state.checked_on, "skipped": state.skipped}) + "\n"
    replace_file_atomically(path, text.encode("utf-8"), engine.durable)


def _today(engine: Engine) -> str:
    return datetime.fromtimestamp(engine.clock()).date().isoformat()


def _user_agent() -> str:
    return f"Chrono-Mail/{program_version()}"  # GitHub refuses a request that names no program


def _installer(release: Release, kind: str) -> ReleaseFile | None:
    wanted = f"chrono-mail_{release.tag}{FILE_ENDINGS[kind]}"
    return next((item for item in release.files if item.name == wanted), None)


def _newest(releases: tuple[Release, ...], kind: str, running: tuple[int, ...]) -> tuple[Release, ReleaseFile] | None:
    """The newest release above the running version that carries this computer's installer."""
    found = [(release, installer) for release in releases if release.version > running for installer in (_installer(release, kind),) if installer is not None]
    return max(found, key=lambda pair: pair[0].version) if found else None


@dataclass(frozen=True, slots=True)
class UpdateSetup:
    available: bool  # this copy is updated through an installer
    reason: str  # why not, when it is not
    running: str  # the version running now
    skipped: str  # the version the person skipped ("" none)


def update_show(engine: Engine, parameters: NoParameters) -> UpdateSetup:
    """Whether this copy can be updated, the version running and the version skipped. Nothing is asked of GitHub."""
    kind = install_kind()
    return UpdateSetup(bool(kind), "" if kind else _not_installed_reason(), program_version(), _read_state(engine).skipped)


@dataclass(frozen=True, slots=True)
class CheckParameters:
    by_hand: bool = field(default=False, metadata=option("The person asked (also today, and a skipped version is offered again)"))


@dataclass(frozen=True, slots=True)
class UpdateCheck:
    outcome: str  # available, current (nothing newer), skipped (only the version skipped, or older), checked_today, not_installed
    running: str
    version: str = ""  # the newer version, when one is available
    page: str = ""  # its page on GitHub
    file_name: str = ""  # its installer for this computer
    size: int = 0  # bytes


def update_check(engine: Engine, parameters: CheckParameters) -> UpdateCheck:
    """Look on GitHub for a newer version with an installer for this computer. Without by_hand: at most once a day (only a check GitHub answered
    counts), and a skipped version is not offered again."""
    running_text = program_version()
    running = parse_version(running_text)
    kind = install_kind()
    if not kind or running is None:
        return UpdateCheck("not_installed", running_text)
    state = _read_state(engine)
    today = _today(engine)
    if not parameters.by_hand and state.checked_on == today:
        return UpdateCheck("checked_today", running_text)
    releases = fetch_releases(engine.releases_address, _user_agent())
    _write_state(engine, dataclasses.replace(_read_state(engine), checked_on=today))
    newest = _newest(releases, kind, running)
    if newest is None:
        return UpdateCheck("current", running_text)
    release, installer = newest
    skipped = parse_version(state.skipped)
    if not parameters.by_hand and skipped is not None and release.version <= skipped:
        return UpdateCheck("skipped", running_text)
    return UpdateCheck("available", running_text, release.tag, release.page, installer.name, installer.size)


@dataclass(frozen=True, slots=True)
class VersionParameters:
    version: str = field(metadata=option("The version, as 4.2.420"))


def _version(text: str) -> tuple[int, ...]:
    version = parse_version(text)
    if version is None:
        raise UsageError(f"Not a version of Chrono Mail: {text}")
    return version


def update_skip(engine: Engine, parameters: VersionParameters) -> UpdateSetup:
    """Do not offer this version again; a newer one is offered."""
    _write_state(engine, dataclasses.replace(_read_state(engine), skipped=version_text(_version(parameters.version))))
    return update_show(engine, NoParameters())


@dataclass(frozen=True, slots=True)
class DownloadParameters:
    version: str = field(metadata=option("The version to download, as 4.2.420"))
    folder: str = field(metadata=option("The folder the installer is saved in"))
    ticket: str = field(default="", metadata=option("A number of the caller's own for this download, named again by update.stop-download"))


@dataclass(frozen=True, slots=True)
class UpdateDownloaded:
    path: str  # the installer, complete and checked
    version: str
    size: int


def _release_file(engine: Engine, kind: str, version: tuple[int, ...]) -> ReleaseFile:
    for release in fetch_releases(engine.releases_address, _user_agent()):
        installer = _installer(release, kind) if release.version == version else None
        if installer is not None:
            return installer
    raise ProtocolError(f"Chrono Mail {version_text(version)} is no longer on GitHub")


def update_download(engine: Engine, parameters: DownloadParameters) -> UpdateDownloaded:
    """Download the installer of this version for this computer into the folder and check its fingerprint; one that does not match is removed.
    Downloading a version the person skipped forgets the skip."""
    kind = install_kind()
    if not kind:
        raise UsageError(_not_installed_reason())
    version = _version(parameters.version)
    installer = _release_file(engine, kind, version)
    folder = Path(parameters.folder)
    target = folder / installer.name
    partial = folder / f"{installer.name}.part"
    shown = [-1]

    def stopped() -> bool:
        """Cancel named this download (at any moment, also before it began), or the program is ending."""
        return (bool(parameters.ticket) and parameters.ticket in engine.stopped_downloads) or engine.stop_event.is_set()

    def progress(received: int, size: int) -> None:
        megabytes = received // (1024 * 1024)
        if megabytes != shown[0]:
            shown[0] = megabytes
            engine.progress(PROGRESS_WHAT, megabytes, max(1, size // (1024 * 1024)))

    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(partial, "wb") as sink:
            fingerprint = download(installer, sink, _user_agent(), progress, stopped)
            sink.flush()
            os.fsync(sink.fileno())
        if fingerprint != installer.sha256:
            raise ProtocolError("The update was damaged on the way, so it was removed. Please try again later.")
        os.replace(partial, target)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ChronoMailError(f"The update could not be saved in {folder}: {error.strerror or error}") from None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    engine.verified_updates[str(target)] = installer.sha256
    state = _read_state(engine)
    if state.skipped == version_text(version):
        _write_state(engine, dataclasses.replace(state, skipped=""))
    return UpdateDownloaded(str(target), version_text(version), installer.size)


@dataclass(frozen=True, slots=True)
class StopParameters:
    ticket: str = field(metadata=option("The number given to update.download"))


def update_stop_download(engine: Engine, parameters: StopParameters) -> UpdateSetup:
    """Stop that download, also when it has not begun yet; what it saved so far is removed."""
    if parameters.ticket:
        engine.stopped_downloads.add(parameters.ticket)
    return update_show(engine, NoParameters())


@dataclass(frozen=True, slots=True)
class InstallParameters:
    path: str = field(metadata=option("The installer downloaded by update.download"))


@dataclass(frozen=True, slots=True)
class UpdateInstalled:
    restart: bool  # Linux: the new version is installed and starts with a restart; Windows: the setup runs and starts it when done


def _fingerprint(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as file:
        while piece := file.read(1024 * 1024):
            hasher.update(piece)
    return hasher.hexdigest()


def _start_setup(path: Path) -> None:
    """Windows: the setup without its questions (Windows' own question for an install for all users still comes); it starts Chrono Mail when done."""
    if sys.platform == "win32":
        os.startfile(str(path), arguments="/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS")
    else:
        raise UsageError("The setup runs on Windows only")


def _install_package(path: Path) -> None:
    """Linux: install the package through the system's own software installer (PackageKit), which asks for the system's password itself."""
    from jeepney import DBusAddress, HeaderFields, MatchRule, new_method_call  # Linux only: the program's D-Bus package
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse, unwrap_msg

    problem = "This system's software installer could not install the update"
    try:
        with open_dbus_connection(bus="SYSTEM") as connection:
            packagekit = DBusAddress("/org/freedesktop/PackageKit", bus_name=PACKAGEKIT, interface=PACKAGEKIT)
            transaction_path = str(unwrap_msg(connection.send_and_get_reply(new_method_call(packagekit, "CreateTransaction"), timeout=60))[0])
            transaction = DBusAddress(transaction_path, bus_name=PACKAGEKIT, interface=f"{PACKAGEKIT}.Transaction")
            rule = MatchRule(type="signal", interface=f"{PACKAGEKIT}.Transaction", path=transaction_path)  # its signals carry PackageKit's own bus number, never its name
            unwrap_msg(connection.send_and_get_reply(message_bus.AddMatch(rule), timeout=60))
            with connection.filter(rule, bufsize=1000) as signals:
                unwrap_msg(connection.send_and_get_reply(new_method_call(transaction, "SetHints", "as", (["interactive=true"],)), timeout=60))
                unwrap_msg(connection.send_and_get_reply(new_method_call(transaction, "InstallFiles", "tas", (0, [str(path)])), timeout=60))
                details = ""
                while True:
                    signal = connection.recv_until_filtered(signals, timeout=PACKAGEKIT_WAIT_SECONDS)
                    member = signal.header.fields.get(HeaderFields.member)
                    if member == "ErrorCode" and len(signal.body) > 1:
                        details = str(signal.body[1])
                    elif member == "Finished":
                        if signal.body and signal.body[0] == PACKAGEKIT_SUCCESS:
                            return
                        raise ChronoMailError(f"{problem}: {details}" if details else f"{problem}.")
    except DBusErrorResponse as error:
        raise ChronoMailError(f"{problem}: {error.data[0] if error.data else error.name}") from None
    except (OSError, TimeoutError, KeyError) as error:
        raise ChronoMailError(f"{problem}: {error}") from None


def update_install(engine: Engine, parameters: InstallParameters) -> UpdateInstalled:
    """Install a downloaded update, only when it is still exactly the file that was checked (else GitHub's fingerprint is asked again)."""
    kind = install_kind()
    if not kind:
        raise UsageError(_not_installed_reason())
    path = Path(parameters.path)
    version = parse_version(path.name.removeprefix("chrono-mail_").removesuffix(FILE_ENDINGS[kind]))
    if not path.name.startswith("chrono-mail_") or not path.name.endswith(FILE_ENDINGS[kind]) or version is None:
        raise UsageError("This is not an update of Chrono Mail for this computer")
    expected = engine.verified_updates.get(str(path)) or _release_file(engine, kind, version).sha256
    try:
        matches = _fingerprint(path) == expected
    except OSError as error:
        raise ChronoMailError(f"The update could not be read: {error.strerror or error}") from None
    if not matches:
        raise ChronoMailError("The update has changed since it was checked, so it was not installed.")
    if kind == "windows":
        _start_setup(path)
        return UpdateInstalled(False)
    _install_package(path)
    return UpdateInstalled(True)


def update_restart(engine: Engine, parameters: NoParameters) -> UpdateSetup:
    """Linux, after an update: start the new version as soon as this program has ended (the window quits right after)."""
    if not sys.platform.startswith("linux") or not install_kind():
        raise UsageError("A restart after an update is only for an installed copy on Linux")
    subprocess.Popen(  # noqa: S603 - the shell gets this program's own number and path only
        ["/bin/sh", "-c", RESTART_WAIT, "chrono-mail-restart", str(os.getpid()), sys.executable],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return update_show(engine, NoParameters())


UPDATE_SHOW: TypedCommand[NoParameters, UpdateSetup] = TypedCommand("update.show", NoParameters, UpdateSetup)
UPDATE_CHECK: TypedCommand[CheckParameters, UpdateCheck] = TypedCommand("update.check", CheckParameters, UpdateCheck)
UPDATE_SKIP: TypedCommand[VersionParameters, UpdateSetup] = TypedCommand("update.skip", VersionParameters, UpdateSetup)
UPDATE_DOWNLOAD: TypedCommand[DownloadParameters, UpdateDownloaded] = TypedCommand("update.download", DownloadParameters, UpdateDownloaded)
UPDATE_STOP_DOWNLOAD: TypedCommand[StopParameters, UpdateSetup] = TypedCommand("update.stop-download", StopParameters, UpdateSetup)
UPDATE_INSTALL: TypedCommand[InstallParameters, UpdateInstalled] = TypedCommand("update.install", InstallParameters, UpdateInstalled)
UPDATE_RESTART: TypedCommand[NoParameters, UpdateSetup] = TypedCommand("update.restart", NoParameters, UpdateSetup)


def register(registry: CommandRegistry) -> None:
    registry.register(UPDATE_SHOW.id, "Show whether this copy can be updated, its version and the version skipped", NoParameters, update_show, doing="Reading the update settings")
    registry.register(UPDATE_CHECK.id, "Look on GitHub for a newer version of Chrono Mail", CheckParameters, update_check, doing="Checking for updates")
    registry.register(UPDATE_SKIP.id, "Do not offer this version of Chrono Mail again", VersionParameters, update_skip, doing="Skipping an update")
    registry.register(UPDATE_DOWNLOAD.id, "Download a newer version of Chrono Mail and check it", DownloadParameters, update_download, doing="Downloading the update")
    registry.register(UPDATE_STOP_DOWNLOAD.id, "Stop the download of an update", StopParameters, update_stop_download, doing="Stopping the download")
    registry.register(UPDATE_INSTALL.id, "Install a downloaded update of Chrono Mail", InstallParameters, update_install, doing="Installing the update")
    registry.register(UPDATE_RESTART.id, "Start the updated Chrono Mail when this one has ended", NoParameters, update_restart, doing="Restarting Chrono Mail")
