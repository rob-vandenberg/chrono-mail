#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# builder_setup.sh 4.4.570
# --- Version history ----------------------------------------------------
# v4.4.570: Initial: what every build needs from outside, done once when the builder image is made (the system packages,
#           Python 3.14 and SQLite), taken out of build_in_container.sh, which downloaded them at every build
#
# Runs once, inside packaging/Containerfile.builder, when podman makes the builder image (packaging/build_linux.sh makes
# it when its recipe changed). Everything it downloads stays in the image: a build downloads none of it again.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# A compiler for Nuitka and the system libraries Qt and its HTML viewer load, so that Nuitka can follow their dependencies.
# Python 3.14 (the program's minimum) is a ready-made build of python-build-standalone (the Pythons of uv, built for old
# Linux systems), downloaded once into the image, its SHA-256 checked: no build depends on Launchpad's
# deadsnakes archive any more, whose servers stopped answering on 2026-11-11 and hung the build. Ubuntu's own packages
# come from its archive (a slow download is tried again).
printf 'Acquire::Retries "5";\nAcquire::http::Timeout "60";\nAcquire::https::Timeout "60";\n' > /etc/apt/apt.conf.d/80retries
apt-get update -qq
apt-get install -y -qq ca-certificates curl gcc g++ patchelf ccache \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libnss3 libxcomposite1 libxdamage1 \
    libxrandr2 libxtst6 libxi6 libasound2 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 \
    libxcb-image0 libxcb-render-util0 \
    libglib2.0-0 libgssapi-krb5-2 libexpat1 libbz2-1.0 libzstd1 libbrotli1 libfreetype6 libharfbuzz0b libdrm2 libgbm1 \
    libxkbfile1 libx11-xcb1 libwayland-client0 libwayland-cursor0 libwayland-egl1 >/dev/null  # (glib and the rest came with the deadsnakes tools before)

PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.14.10%2B20261110-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_SHA256="3fe0d7c0538eb5d190c34582992d26230532551355cbd789fabc6591621cfc07"
PYTHON_ARCHIVE="/tmp/cpython-3.14.10-20261110.tar.gz"
curl -fsSL --retry 5 -o "$PYTHON_ARCHIVE" "$PYTHON_URL"
echo "$PYTHON_SHA256  $PYTHON_ARCHIVE" | sha256sum -c --quiet || { echo "Python 3.14: the checksum does not match the release's; nothing is built with it."; exit 1; }
tar -C /opt -xzf "$PYTHON_ARCHIVE"
rm "$PYTHON_ARCHIVE"
ln -sf /opt/python/bin/python3.14 /usr/local/bin/python3.14

# SQLite: Ubuntu 22.04 has 3.37.2, too old for the index (contentless FTS5 tables that allow deletes need 3.43, D-025),
# and the package always uses the copy it carries. So the official source of a fixed version is built here and put
# before the system's, so that Python and then Nuitka take it. A new version: its SHA3-256 from sqlite.org/download.html.
SQLITE_URL="https://sqlite.org/2026/sqlite-amalgamation-3540100.zip"
SQLITE_SHA3="92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30"
mkdir -p /tmp/sqlite /work/sqlite
python3.14 - "$SQLITE_URL" "$SQLITE_SHA3" <<'PYTHON'
import hashlib, pathlib, sys, urllib.request, zipfile
url, expected = sys.argv[1], sys.argv[2]
archive = pathlib.Path("/tmp/sqlite") / url.rsplit("/", 1)[1]
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
rm -rf /tmp/sqlite /work/sqlite /var/lib/apt/lists/*
