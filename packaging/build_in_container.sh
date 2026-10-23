#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_in_container.sh 2.3.318
# --- Version history ----------------------------------------------------
# v2.3.318: Our own SQLite 3.54.1 (checksum checked) instead of Ubuntu 22.04's 3.37.2, which is too old for the index; the build stops when the packed SQLite is older than the program needs
# v1.5.257: The Public Suffix List file of publicsuffixlist (the main domain of a link, D-215)
# v0.5.131: Only pyproject.toml, README.md, LICENSE, src and packaging are copied into the container (never private files); the program's own package information, so that it knows its version (About said "version unknown")
# v0.5.129: Time-zone files once (tzdata.zoneinfo comes with the package; only the file zones is added)
# v0.5.127: Initial: runs inside the Ubuntu 22.04 container of build_linux.sh
#
# Runs inside the container started by build_linux.sh: /src is the project (read only), /out receives the program folder,
# /cache keeps downloads between builds (pip and Nuitka's own cache).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PIP_CACHE_DIR=/cache/pip NUITKA_CACHE_DIR=/cache/nuitka

# Python 3.14 (the program's minimum) from the deadsnakes archive for Ubuntu, a compiler for Nuitka, and the system
# libraries Qt and its HTML viewer load, so that Nuitka can follow their dependencies.
apt-get update -qq
apt-get install -y -qq software-properties-common >/dev/null
add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
apt-get update -qq
apt-get install -y -qq python3.14 python3.14-venv python3.14-dev gcc g++ patchelf ccache \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libnss3 libxcomposite1 libxdamage1 \
    libxrandr2 libxtst6 libxi6 libasound2 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 \
    libxcb-image0 libxcb-render-util0 >/dev/null

# SQLite: Ubuntu 22.04 has 3.37.2, too old for the index (contentless FTS5 tables that allow deletes need 3.43, D-025),
# and the package always uses the copy it carries. So the official source of a fixed version is built here and put
# before the system's, so that Python and then Nuitka take it. A new version: its SHA3-256 from sqlite.org/download.html.
SQLITE_URL="https://sqlite.org/2026/sqlite-amalgamation-3540100.zip"
SQLITE_SHA3="92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30"
mkdir -p /cache/sqlite /work/sqlite
python3.14 - "$SQLITE_URL" "$SQLITE_SHA3" <<'PYTHON'
import hashlib, pathlib, sys, urllib.request, zipfile
url, expected = sys.argv[1], sys.argv[2]
archive = pathlib.Path("/cache/sqlite") / url.rsplit("/", 1)[1]
if not archive.exists() or hashlib.sha3_256(archive.read_bytes()).hexdigest() != expected:
    archive.write_bytes(urllib.request.urlopen(url, timeout=120).read())
if hashlib.sha3_256(archive.read_bytes()).hexdigest() != expected:
    sys.exit(f"{archive.name}: the checksum does not match sqlite.org's; nothing is built with it.")
with zipfile.ZipFile(archive) as files:
    for name in files.namelist():
        if name.endswith("/sqlite3.c"):
            pathlib.Path("/work/sqlite/sqlite3.c").write_bytes(files.read(name))
PYTHON
gcc -O2 -fPIC -shared -Wl,-soname,libsqlite3.so.0 \
    -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS3_PARENTHESIS \
    -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_LOAD_EXTENSION \
    /work/sqlite/sqlite3.c -o /usr/local/lib/libsqlite3.so.0 -lpthread -lm -ldl
ldconfig

# Only what the build needs is copied (so that building does not write into the read-only source). Never the whole
# project folder: it may hold private files (chrono-mail-import-samples/ holds the owner's own mail).
mkdir -p /work
tar -C /src --exclude='*/__pycache__' -cf - pyproject.toml README.md LICENSE src packaging | tar -C /work -xf -
python3.14 -m venv /work/venv
/work/venv/bin/pip install -q --upgrade pip
# Nuitka 4.3 corrupts compiled inner functions with PySide6 and Python 3.14 (startup crash, Nuitka issue 4081); 4.2.2 is
# the last good version. Move on to a newer one only after checking that the issue is fixed.
/work/venv/bin/pip install -q /work nuitka==4.2.2 ordered-set zstandard

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
    --output-dir=/out \
    --output-filename=chrono-mail \
    packaging/chrono_mail_main.py
# Nuitka names the folder after the script; the packages expect chrono-mail.dist.
mv /out/chrono_mail_main.dist /out/chrono-mail.dist

# The program uses the SQLite in its folder, whatever the system has: it must be new enough.
/work/venv/bin/python - <<'PYTHON'
import ctypes, sys
from chrono_mail.index import MINIMUM_SQLITE_VERSION
packed = ctypes.CDLL("/out/chrono-mail.dist/libsqlite3.so.0")
packed.sqlite3_libversion.restype = ctypes.c_char_p
version = packed.sqlite3_libversion().decode()
if tuple(int(part) for part in version.split(".")[:3]) < MINIMUM_SQLITE_VERSION:
    sys.exit(f"The program folder carries SQLite {version}, older than the {MINIMUM_SQLITE_VERSION} it needs.")
print(f"The program folder carries SQLite {version}.")
PYTHON
