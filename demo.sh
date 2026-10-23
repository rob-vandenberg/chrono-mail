#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# demo.sh 2.3.320
# --- Version history ----------------------------------------------------
# v2.3.320: Initial: builds the demo profile when it is missing (--fresh: builds it again), then starts Chrono Mail with it (D-243)
#
# Starts Chrono Mail with the demo profile of the documentation (~/chrono-mail-demo): a made-up person's mail, apart from your own.
# Your own Chrono Mail must be closed first: one copy runs at a time.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
FOLDER="$HOME/chrono-mail-demo"
.venv/bin/python -m tools.demo.build --folder "$FOLDER" "$@"
exec .venv/bin/chrono-mail --profile "$FOLDER"
