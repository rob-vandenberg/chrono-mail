#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_in_container.sh 4.4.570
# --- Version history ----------------------------------------------------
# v4.4.570: Runs in the builder image (system packages, Python 3.14, SQLite in it: packaging/builder_setup.sh); the Python
#           packages come from the wheel folder /cache/wheels, fetched only when one is missing: a build downloads nothing
# v4.4.570: Python 3.14 from python-build-standalone (GitHub), downloaded once into /cache/python with its SHA-256 checked, instead of Launchpad's deadsnakes archive, whose servers stopped answering and hung the build; the system libraries Qt loads that came with its tools before (glib first); apt retries a slow download; the SQLite check also takes the SQLite built into the sqlite3 module
# v2.4.322: The AES code of pycryptodomex (Cryptodome), which pyzipper uses for a protected backup (D-244)
# v2.3.318: Our own SQLite 3.54.1 (checksum checked) instead of Ubuntu 22.04's 3.37.2, which is too old for the index; the build stops when the packed SQLite is older than the program needs
# v1.5.257: The Public Suffix List file of publicsuffixlist (the main domain of a link, D-215)
# v0.5.131: Only pyproject.toml, README.md, LICENSE, src and packaging are copied into the container (never private files); the program's own package information, so that it knows its version (About said "version unknown")
# v0.5.129: Time-zone files once (tzdata.zoneinfo comes with the package; only the file zones is added)
# v0.5.127: Initial: runs inside the Ubuntu 22.04 container of build_linux.sh
#
# Runs inside the builder image started by build_linux.sh: /src is the project (read only), /out receives the program folder,
# /cache keeps what was fetched once between builds (the wheels and Nuitka's own cache).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PIP_CACHE_DIR=/cache/pip NUITKA_CACHE_DIR=/cache/nuitka

# Only what the build needs is copied (so that building does not write into the read-only source). Never the whole
# project folder: it may hold private files (chrono-mail-import-samples/ holds the owner's own mail).
mkdir -p /work
tar -C /src --exclude='*/__pycache__' -cf - pyproject.toml README.md LICENSE src packaging | tar -C /work -xf -
python3.14 -m venv /work/venv
# Nuitka 4.3 corrupts compiled inner functions with PySide6 and Python 3.14 (startup crash, Nuitka issue 4081); 4.2.2 is
# the last good version. Move on to a newer one only after checking that the issue is fixed.
# The Python packages come from the wheel folder kept in /cache/wheels, without asking PyPI. Only when one is missing (the
# first build, or a new or changed dependency) is it fetched once and kept; the program's own wheel never stays there.
WANTED=(nuitka==4.2.2 ordered-set zstandard)
mkdir -p /cache/wheels
if ! /work/venv/bin/pip install -q --no-index --find-links /cache/wheels /work "${WANTED[@]}" 2>/dev/null; then
    /work/venv/bin/pip download -q --dest /cache/wheels "setuptools>=80"  # what building the program's own package needs (pyproject.toml)
    /work/venv/bin/pip wheel -q --wheel-dir /cache/wheels /work "${WANTED[@]}"
    rm -f /cache/wheels/chrono_mail-*.whl
    /work/venv/bin/pip install -q --no-index --find-links /cache/wheels /work "${WANTED[@]}"
fi

# Python must now use our SQLite, and it must do what the index needs.
/work/venv/bin/python - <<'PYTHON'
import sqlite3, sys
from chrono_mail.index import MINIMUM_SQLITE_VERSION
if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
    sys.exit(f"Python still uses SQLite {sqlite3.sqlite_version}; {MINIMUM_SQLITE_VERSION} or newer is needed.")
sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x, content='', contentless_delete=1)")
print(f"SQLite {sqlite3.sqlite_version} is used.")
PYTHON

cd /work
/work/venv/bin/python -m nuitka \
    --mode=standalone \
    --assume-yes-for-downloads \
    --enable-plugin=pyside6 \
    --include-qt-plugins=sensible \
    --include-package=chrono_mail \
    --include-package-data=chrono_mail \
    --include-package=keyring.backends \
    --include-package=secretstorage \
    --include-package=jeepney \
    --include-distribution-metadata=keyring \
    --include-distribution-metadata=chrono-mail \
    --include-package=tzdata \
    --include-package-data=tzdata:zones \
    --include-package-data=dateutil \
    --include-package-data=publicsuffixlist:public_suffix_list.dat \
    --include-package=Cryptodome \
    --output-dir=/out \
    --output-filename=chrono-mail \
    packaging/chrono_mail_main.py
# Nuitka names the folder after the script; the packages expect chrono-mail.dist.
mv /out/chrono_mail_main.dist /out/chrono-mail.dist

# The program uses the SQLite in its folder, whatever the system has: it must be new enough.
/work/venv/bin/python - <<'PYTHON'
import ctypes, pathlib, sqlite3, sys
from chrono_mail.index import MINIMUM_SQLITE_VERSION
library = pathlib.Path("/out/chrono-mail.dist/libsqlite3.so.0")
if library.exists():
    packed = ctypes.CDLL(str(library))
    packed.sqlite3_libversion.restype = ctypes.c_char_p
    version = packed.sqlite3_libversion().decode()
else:  # python-build-standalone builds SQLite into the sqlite3 module itself, which goes into the folder as it is
    version = sqlite3.sqlite_version
if tuple(int(part) for part in version.split(".")[:3]) < MINIMUM_SQLITE_VERSION:
    sys.exit(f"The program folder carries SQLite {version}, older than the {MINIMUM_SQLITE_VERSION} it needs.")
print(f"The program folder carries SQLite {version}.")
PYTHON
