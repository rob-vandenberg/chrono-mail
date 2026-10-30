# PACKAGING.md: making the installers

*Version 0.5.131. Read this file completely before building. It is written for the agent (and the person) that builds
the installers on one of the two machines. The project rules (AGENTS.md, PY-DEVELOPMENT-RULES.md) apply: change no code and
release nothing without the owner's "Proceed"; report every problem instead of working around it.*

## 1. What we make, and where

| File | Made on | By |
|---|---|---|
| `dist/chrono-mail_<version>_amd64.deb` | the Linux machine (Kubuntu) | `packaging/package_linux.sh` |
| `dist/chrono-mail_<version>_x86_64.rpm` | the Linux machine (Kubuntu) | `packaging/package_linux.sh` |
| `dist\chrono-mail_<version>_x64_setup.exe` | the Windows machine (the Windows computer) | `packaging\build_windows.ps1` |

`<version>` is the version in `pyproject.toml`. All three are built from the **same pushed version** (the same git tag),
then attached to the GitHub release of that version (section 5).

The user downloads one file, runs one command (or double-clicks the setup), and has a working program with a menu or
Start-menu entry. Nothing else needs to be installed: each package carries its own Python 3.14, Qt (PySide6 6.12) and
Chrono Mail.

How: **Nuitka** compiles the program into a folder with everything it needs (`--mode=standalone`). On Linux **nFPM**
packs that folder into the .deb and the .rpm; on Windows **Inno Setup** makes the setup.exe.

## 2. Things that were learned the hard way (do not undo them)

1. **Nuitka is pinned to 4.2.2.** Nuitka 4.3 has a regression that corrupts compiled inner functions with PySide6 6.12 and
   Python 3.14: the program crashes at startup, often but not always (https://github.com/Nuitka/Nuitka/issues/4081).
   Only move to a newer Nuitka after checking that this issue is fixed, and test the start several times.
2. **The HTML viewer's helper program.** Nuitka puts `QtWebEngineProcess` (and its `.pak`, `icudtl.dat`,
   `qtwebengine_locales`) next to the program, but PySide6 makes Qt look in `PySide6/Qt/...`. Without a fix the viewer
   cannot start (the program aborts while making the main window), or worse, silently borrows the helper of a Qt that is
   installed on the system (another version). `packaging/chrono_mail_main.py` sets `QTWEBENGINEPROCESS_PATH`,
   `QTWEBENGINE_RESOURCES_PATH` and `QTWEBENGINE_LOCALES_PATH` before PySide6 is loaded. Keep it; on Windows the helper
   is `QtWebEngineProcess.exe` and the same code handles it.
3. **The password store backends** are found by keyring at run time, which Nuitka cannot see: they are included
   explicitly (`--include-package=keyring.backends`, and on Linux `secretstorage` and `jeepney`, on Windows `win32ctypes`).
4. **Data files** of `tzdata`, `dateutil` and `publicsuffixlist` (its `public_suffix_list.dat`, since 1.5.257) are included explicitly (else a warning and missing time-zone data).
5. **Linux: build on an old system.** A program built on a new Linux does not run on an older one. The Linux build runs
   in an Ubuntu 22.04 container (podman). PySide6 6.12 itself needs glibc 2.34, so 22.04 (glibc 2.35) is about the oldest
   possible; the packages run on Ubuntu 22.04+, Debian 12+, Fedora 36+, Linux Mint 21+, openSUSE with glibc 2.35+.
   **The old system brings old libraries, and the package always uses the copies it carries, never the system's.**
   SQLite is one of them: Ubuntu 22.04 has 3.37.2, the index needs 3.43 (D-025), so every installed program up to
   2.3.317 failed to open a mailbox ("SQLite 3.37.2 is too old"). Since 2.3.318 `build_in_container.sh` builds the
   official SQLite source of a fixed version (its SHA3-256 from sqlite.org/download.html checked), and the build stops
   when the program folder carries an SQLite older than the program needs. A newer SQLite: change its address and
   checksum there.
   **The app information** (`packaging/chrono-mail.metainfo.xml`, AppStream, installed in `/usr/share/metainfo`):
   without it Discover and GNOME Software do not list Chrono Mail under Installed, so it cannot be uninstalled there
   (the owner on Fedora, 2.3.317). It must name the package (`<pkgname>chrono-mail</pkgname>`): Discover skips every
   program whose app information does not (seen in Discover's code, 2.3.319; without it 2.3.318 was still not listed). Check it with `appstreamcli validate --no-net packaging/chrono-mail.metainfo.xml`.
6. **The program's own version.** The packaged program has no `pyproject.toml` next to it, so it learns its version
   from its package information; Nuitka includes that only when told: `--include-distribution-metadata=chrono-mail`
   (both build scripts). Without it the About window says "version unknown" (seen on Windows in 0.5.130).
7. **Only the needed files go into the Linux build container:** `pyproject.toml`, `README.md`, `LICENSE`, `src`,
   `packaging`. Never the whole project folder: it can hold private files (the owner's mail exports in
   `chrono-mail-import-samples/`, which must never leave the machine or reach git). Until 0.5.131 the whole folder was
   copied into the (throwaway) container.
8. **Test outside the build machine.** A test on the developer's own desktop can pass for the wrong reason (system Qt
   present). The Linux packages were tested in bare containers (Ubuntu 22.04, Ubuntu 26.04, Fedora 43) as a normal user.
   Never test as root: the HTML viewer refuses to run as root.

## 3. Linux (the Kubuntu machine)

Needs: `podman` (Ubuntu package) and `nfpm` (https://github.com/goreleaser/nfpm/releases, the `_amd64.deb`; check its
checksum). Both are installed on the owner's Kubuntu machine.

```bash
packaging/package_linux.sh --build
```

- `--build` builds the program folder first (`packaging/build_linux.sh`, about 5 minutes with a warm cache; it runs
  `packaging/build_in_container.sh` inside an Ubuntu 22.04 container). Without `--build` an existing
  `build/linux/chrono-mail.dist` is packed again (use that after changing only `packaging/nfpm.yaml`).
- The result is in `dist/`. The packages install the program in `/opt/chrono-mail`, the command `/usr/bin/chrono-mail`,
  the menu entry `/usr/share/applications/chrono-mail.desktop` and the icon. `packaging/nfpm.yaml` lists the system
  libraries the packages require (by package name for .deb, by library name for .rpm, so that the .rpm works on
  Fedora and openSUSE alike).
- The user installs with `sudo apt install ./chrono-mail_<version>_amd64.deb` or `sudo dnf install ./chrono-mail_<version>_x86_64.rpm`
  (openSUSE: `sudo zypper install ./…rpm`).

**Test before uploading** (the start must keep running; exit 124 from `timeout` means it was still running):

```bash
podman run --rm -v "$PWD/dist:/dist:ro,z" docker.io/library/ubuntu:22.04 bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq /dist/chrono-mail_*_amd64.deb >/dev/null; useradd -m t; su t -c "cd; QT_QPA_PLATFORM=offscreen timeout 8 chrono-mail; echo exit \$?"'
```

and the same with `registry.fedoraproject.org/fedora:43`, `dnf install -y /dist/chrono-mail_*_x86_64.rpm util-linux shadow-utils`.

## 4. Windows (the Windows computer)

### 4.1 Once: what the machine needs

1. **Python 3.14** from https://www.python.org (64-bit, with the `py` launcher). Check: `py -3.14 --version`.
2. **Inno Setup 6** from https://jrsoftware.org/isinfo.php. The script finds `ISCC.exe` in the usual install folders.
3. **A C compiler for Nuitka.** Either install "Build Tools for Visual Studio" (C++ workload), or let Nuitka download
   its own compiler: the script passes `--assume-yes-for-downloads`, so Nuitka may download it on the first run
   (ask the owner before letting it download, as with any download).
4. **git** and the **GitHub CLI** (`gh`, logged in) to clone and to upload.

### 4.2 Each version

```powershell
git clone https://github.com/rob-vandenberg/chrono-mail.git   # or: git fetch --tags; git checkout <version>
cd chrono-mail
git checkout <version>                                          # the same tag the Linux packages were built from
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

The result is `dist\chrono-mail_<version>_x64_setup.exe` (named `_setup.exe` before 4.2.435). The setup installs in `C:\Program Files\Chrono Mail` (or, if the
person chooses, for themselves only), makes a Start menu entry (and a desktop icon if ticked) and registers an
uninstaller under Settings > Apps. Its `AppId` in `packaging/chrono-mail.iss` must never change: Windows recognises
updates by it.

### 4.3 The first runs of this script

`packaging/build_windows.ps1` and `packaging/chrono-mail.iss` were written on Linux. Their first run on the Windows computer
(2026-10-15, `chrono-mail_0.5.128_setup.exe`) worked after three fixes, made in 0.5.129:

1. Inno Setup in `Program Files (x86)` was never found: PowerShell reads `$env:ProgramFiles(x86)` as the variable
   `ProgramFiles` followed by the text `(x86)`; it must be written `${env:ProgramFiles(x86)}`.
2. The time-zone files were included twice (604 "Duplicate data file" warnings): `--include-package=tzdata` already
   brings `tzdata\zoneinfo`; only the one file `zones` is added (`--include-package-data=tzdata:zones`). The Linux build
   had the same fault and got the same fix (605 of 605 files, no warnings).
3. `--include-package=win32ctypes` also took `win32ctypes.tests`; only `win32ctypes.core` and `win32ctypes.pywin32` (the
   parts keyring's Windows backend uses) are included now.

Build facts of that run: Nuitka 4.2.2 with Visual Studio Build Tools 18 (cl 14.5), Inno Setup 6.7.3 (installed for the
user), program folder 485 MB, setup.exe 130 MB.

**Still to test on Windows:** the password store (Windows Credential Manager) in the installed program.

Report every new problem to the owner with the exact error, fix it only after the owner's "Proceed", and write what
changed in this file and in DEVLOG.md.

### 4.4 Test before uploading

On the Windows machine (ideally also on a clean Windows without Python, for example a Windows Sandbox):

1. Run the setup; check the Start menu entry and that the program starts and shows its window.
2. **Check that the program uses its own HTML-viewer helper:** while it runs, Task Manager > Details shows
   `QtWebEngineProcess.exe`; its path (column "Command line") must be inside the install folder.
3. Open a message with HTML content (or the composer in Rich text) and check that it is shown.
4. Uninstall under Settings > Apps and check that the folder and the Start menu entry are gone. The person's mail and
   settings (in `%APPDATA%` / `%LOCALAPPDATA%`) stay; that is intended.

## 5. Attaching the installers to the GitHub release

Only after the owner says so, and only for a version that was pushed with its tag. **The owner's rule:** in-between
versions are only pushed (no installers); a full release (the three installers attached to the release page) is made
for milestone versions only, and only after the owner has tested that pushed version (on Windows from the code, or with
a locally built setup.exe). Never automatic.

- Linux: `packaging/upload_release.sh` attaches `dist/chrono-mail_<version>_*` to the release of that version (it makes
  the release page from the tag when it does not exist yet). Files with the same name are replaced.
- Windows: `gh release upload <version> dist\chrono-mail_<version>_x64_setup.exe --repo rob-vandenberg/chrono-mail --clobber`
  (the Linux machine usually makes the release page first; if it does not exist yet, `gh release create <version>
  --verify-tag --repo rob-vandenberg/chrono-mail` makes it).

Check afterwards on https://github.com/rob-vandenberg/chrono-mail/releases that the three files are there.

## 6. Updates from inside the program (D-293, since 4.2.419)

An installed Chrono Mail looks for the newest release whose installer fits it: the setup.exe on Windows, the `.deb` or the
`.rpm` on Linux, whichever it was installed from (dpkg's list of `chrono-mail`, else `rpm -q chrono-mail`). So the file names
of section 1 must never change, and a release counts for a computer only once its installer is attached. GitHub's own
fingerprint of each file (the `digest` of the release asset) is checked after the download.

1. Linux: the program hands the downloaded package to PackageKit (the system's software installer, the one the software
   centres use) over D-Bus; the system asks for its password itself. Checked on Kubuntu with a made-up package in PackageKit's
   simulate mode (nothing installed, 2026-10-29). Not yet tried: a real install, and Fedora and openSUSE.
2. Windows: the program closes as Exit does and starts the setup with `/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS`;
   the `[Run]` line with `Check: WizardSilent` in `chrono-mail.iss` opens Chrono Mail again when the setup is done, as the person
   (`runasoriginaluser`). **To try on the Windows computer**: an install for all users (Windows' own question still comes) and one
   for the person only; that the program comes back after the update.

## 7. Sizes

The program folder is about 480 MB on disk (the HTML viewer is the largest part); the .deb and .rpm are about 190 MB.
