#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# upload_release.sh 0.5.127
# --- Version history ----------------------------------------------------
# v0.5.127: Initial: attaches the installers in dist/ to the GitHub release of the version
#
# Attaches the installers of the version in pyproject.toml (dist/chrono-mail_<version>_*) to the GitHub release of that
# version, after the version was pushed with its tag. The release page is made from the tag when it does not exist yet.
# Files already attached under the same name are replaced. The Windows machine runs the same command (in Git Bash) for
# its setup.exe, or uploads it with gh directly; see packaging/PACKAGING.md. Needs the GitHub CLI (gh), logged in.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
REPOSITORY="rob-vandenberg/chrono-mail"

shopt -s nullglob
FILES=(dist/chrono-mail_"${VERSION}"_*)
if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No installers for version $VERSION in dist/: run packaging/package_linux.sh first." >&2
    exit 1
fi
if ! git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null; then
    echo "The tag $VERSION is not on GitHub yet: push the version first." >&2
    exit 1
fi
if ! gh release view "$VERSION" --repo "$REPOSITORY" >/dev/null 2>&1; then
    gh release create "$VERSION" --repo "$REPOSITORY" --verify-tag --title "Chrono Mail $VERSION" \
        --notes "Installers: .deb (Debian, Ubuntu, Mint), .rpm (Fedora, openSUSE) and setup.exe (Windows). Linux needs a system from 2022 or later (glibc 2.35)."
fi
gh release upload "$VERSION" "${FILES[@]}" --repo "$REPOSITORY" --clobber
echo "Attached to https://github.com/$REPOSITORY/releases/tag/$VERSION:"
printf '  %s\n' "${FILES[@]}"
