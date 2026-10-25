import json
from pathlib import Path

from chrono_mail.core import LOCAL_ACCOUNT_ID, CryptoError
from chrono_mail.crypto import DataKey, generate_data_key
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "newkey.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: kept_local_key: the key kept Local Folders needs when the settings name none, for its recovery code (RV-177)
# v3.5.346: Initial: one way to make this computer's data key, and the remembered fact that its recovery code was not stored yet (RV-03)

NOT_STORED_FILE: str = "recovery-code-not-stored"  # holds the id of the key whose recovery code the person has not confirmed yet


def make_data_key(engine: Engine) -> DataKey:
    """A new data key, saved in the credential store and read back. Until the person confirms that its recovery code is stored, this is
    remembered (through a restart), so the code is shown again wherever it must be (before a restore, after adding an account)."""
    key = generate_data_key()
    engine.keys.save(key)
    if engine.keys.load(key.key_id) != key:  # never use a key whose copy in the credential store cannot be read back
        raise CryptoError("The credential store did not return the key that was just saved")
    marker = _marker(engine)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(key.key_id.hex(), "utf-8")
    return key


def recovery_code_not_stored(engine: Engine) -> bool:
    """Whether the recovery code of this computer's key was never confirmed as stored (Done with the tick). A key made before 3.5.346 counts
    as stored: that cannot be known any more."""
    key_id = engine.config().key_id
    try:
        return key_id is not None and _marker(engine).read_text("utf-8").strip() == key_id
    except OSError:
        return False


def recovery_code_stored(engine: Engine) -> None:
    """The person confirmed that the recovery code is stored in a safe place."""
    _marker(engine).unlink(missing_ok=True)


def _marker(engine: Engine) -> Path:
    return engine.directories.data_directory / NOT_STORED_FILE


def kept_local_key(engine: Engine) -> str | None:
    """The key id the Local Folders mail kept on this computer was written with, when it is encrypted and the settings name no key (they
    were lost or reset): that key, never a new one, opens it."""
    if engine.config().key_id is not None:
        return None
    try:
        metadata = json.loads((engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    key_id = metadata.get("key_id") if isinstance(metadata, dict) and metadata.get("encrypted") is True else None
    return key_id if isinstance(key_id, str) else None
