#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# package_linux.sh 0.5.127
# --- Version history ----------------------------------------------------
# v0.5.127: Initial: the .deb and .rpm from the program folder
#
# Makes dist/chrono-mail_<version>_amd64.deb and dist/chrono-mail_<version>_x86_64.rpm from the program folder that
# packaging/build_linux.sh built (it runs that first when the folder is missing or --build is given). Needs nfpm.
# The version is the one in pyproject.toml. See packaging/PACKAGING.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
export VERSION

if [[ "${1:-}" == "--build" || ! -x build/linux/chrono-mail.dist/chrono-mail ]]; then
    packaging/build_linux.sh
fi

mkdir -p dist
nfpm package --config packaging/nfpm.yaml --packager deb --target "dist/chrono-mail_${VERSION}_amd64.deb"
nfpm package --config packaging/nfpm.yaml --packager rpm --target "dist/chrono-mail_${VERSION}_x86_64.rpm"
ls -lh dist/chrono-mail_"${VERSION}"_*
