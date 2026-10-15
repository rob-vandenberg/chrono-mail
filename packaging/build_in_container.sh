#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_in_container.sh 0.5.129
# --- Version history ----------------------------------------------------
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

# The project is copied, so that building it does not write into the read-only source.
mkdir -p /work
tar -C /src --exclude=./.venv --exclude=./.git --exclude=./build --exclude=./dist --exclude='*/__pycache__' -cf - . | tar -C /work -xf -
python3.14 -m venv /work/venv
/work/venv/bin/pip install -q --upgrade pip
# Nuitka 4.3 corrupts compiled inner functions with PySide6 and Python 3.14 (startup crash, Nuitka issue 4081); 4.2.2 is
# the last good version. Move on to a newer one only after checking that the issue is fixed.
/work/venv/bin/pip install -q /work nuitka==4.2.2 ordered-set zstandard

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
    --include-package=tzdata \
    --include-package-data=tzdata:zones \
    --include-package-data=dateutil \
    --output-dir=/out \
    --output-filename=chrono-mail \
    packaging/chrono_mail_main.py
# Nuitka names the folder after the script; the packages expect chrono-mail.dist.
mv /out/chrono_mail_main.dist /out/chrono-mail.dist
