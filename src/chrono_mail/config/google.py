import json
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "google.py 4.4.593"

# --- Version history ----------------------------------------------------
# v4.4.593: Initial: the Google project's client ID and secret, read from a private file outside the project (D-335)

GOOGLE_CLIENT_FILE: Path = Path.home() / ".claude" / "chrono-mail" / "google-client.json"  # never in the project folder (G-1, D-335)
MAXIMUM_FILE_BYTES: int = 16 * 1024


def google_client_codes(path: Path = GOOGLE_CLIENT_FILE) -> tuple[str, str] | None:
    """(client ID, client secret) of the Google project Chrono Mail signs in through; None when the file is missing or cannot be read, so
    Google sign-in is not offered (mail is never touched by it)."""
    try:
        if path.stat().st_size > MAXIMUM_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    client_id, secret = data.get("client_id"), data.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(secret, str) or not client_id.strip() or not secret.strip():
        return None
    return client_id.strip(), secret.strip()
