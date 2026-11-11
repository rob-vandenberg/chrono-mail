#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_linux.sh 4.4.570
# --- Version history ----------------------------------------------------
# v4.4.570: The build runs in the builder image, made once (packaging/Containerfile.builder) and reused: a build downloads nothing
# v0.5.127: Initial: the Linux program folder, built in an Ubuntu 22.04 container
#
# Builds the Linux program folder of Chrono Mail (Python, Qt and the program in one folder; nothing needs to be installed
# by the user). It is built inside an Ubuntu 22.04 container: a program built on an older system also runs on newer ones,
# not the other way round. PySide6 6.12 needs glibc 2.34 itself, so 22.04 (glibc 2.35) is about the oldest that can work.
# The result is build/linux/chrono-mail.dist/ in the project; packaging/package_linux.sh makes the .deb and .rpm from it.
# Needs podman. See packaging/PACKAGING.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/linux"

# The builder image (Ubuntu 22.04, its system packages, Python 3.14, SQLite) is made once and kept by podman; it is made
# again only when its recipe changes (its name carries a checksum of packaging/Containerfile.builder and builder_setup.sh).
RECIPE="$(cat "$ROOT/packaging/Containerfile.builder" "$ROOT/packaging/builder_setup.sh" | sha256sum | cut -c1-12)"
IMAGE="localhost/chrono-mail-builder:$RECIPE"
if ! podman image exists "$IMAGE"; then
    echo "Making the builder image once (it downloads Ubuntu's packages, Python 3.14 and SQLite; later builds reuse it)..."
    podman build --tag "$IMAGE" --file "$ROOT/packaging/Containerfile.builder" "$ROOT/packaging"
fi

rm -rf "$OUT"
mkdir -p "$OUT"
podman run --rm \
    --volume "$ROOT:/src:ro,z" \
    --volume "$OUT:/out:z" \
    --volume chrono-mail-build-cache:/cache \
    "$IMAGE" bash /src/packaging/build_in_container.sh
echo "Built: $OUT/chrono-mail.dist"
