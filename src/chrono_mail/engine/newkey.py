from pathlib import Path

from chrono_mail.core import LOCAL_ACCOUNT_ID, CryptoError
from chrono_mail.crypto import CredentialStoreError, DataKey, generate_data_key
from chrono_mail.engine.session import Engine
from chrono_mail.engine.walletlabels import label_key
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "newkey.py 3.6.371"

# --- Version history ----------------------------------------------------
# v3.6.371: a new key gets a readable label in the wallet (B-23, D-275)
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash; kept_other_key and usable: Local
#           Folders' mail kept with an older key is taken back when that key can be had; no new key while Local Folders' mail kept here
#           needs its own: that key is used when the password store has it, otherwise nothing is made and the person is told how to bring it
#           back (a new key left that mail unreadable for good)
# v3.6.349: kept_local_key: the key kept Local Folders needs when the settings name none, for its recovery code (RV-177)
# v3.5.346: Initial: one way to make this computer's data key, and the remembered fact that its recovery code was not stored yet (RV-03)

NOT_STORED_FILE: str = "recovery-code-not-stored"  # holds the id of the key whose recovery code the person has not confirmed yet
KEPT_KEY_MISSING: str = (
    "The mail of Local Folders on this computer needs its own key, and this computer's password store does not have it. Use File > Use a "
    "recovery code to bring it back, or choose Start without this mail there. No new key was made, so that mail stays safe."
)


def make_data_key(engine: Engine) -> DataKey:
    """A new data key, saved in the credential store and read back. Until the person confirms that its recovery code is stored, this is
    remembered (through a restart), so the code is shown again wherever it must be (before a restore, after adding an account).
    When Local Folders' mail kept on this computer names its own key (the settings were lost or reset), that key is this computer's key:
    it is used when the password store has it, and no new one is ever made while it is missing (the kept mail could never be opened again)."""
    kept = kept_local_key(engine)
    if kept is not None:
        if not usable(engine, kept):  # a locked wallet raises: it is no lost key
            raise CryptoError(KEPT_KEY_MISSING)
        return engine.keys.load(bytes.fromhex(kept))
    key = generate_data_key()
    engine.keys.save(key)
    label_key(engine, key.key_id)  # B-23
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
    return _written_with(engine)


def kept_other_key(engine: Engine) -> str | None:
    """The key id of Local Folders' mail kept on this computer, not in the settings, when it was written with another key than the one the
    settings name (an older version made a new key after the settings were lost). With that older key it is taken back, written again with
    this computer's key (local.py)."""
    config = engine.config()
    if config.key_id is None or config.local_folders() is not None:
        return None
    written = _written_with(engine)
    return written if written is not None and written != config.key_id else None


def usable(engine: Engine, key_id: str) -> bool:
    """Whether the password store has this key in a form that opens mail: not there, damaged, or an id no key can have is not usable. A store
    that cannot be asked (locked, not running) raises CredentialStoreError: that is no lost key."""
    try:
        engine.keys.load(bytes.fromhex(key_id))
    except CredentialStoreError:
        raise
    except (CryptoError, ValueError):
        return False
    return True


def _written_with(engine: Engine) -> str | None:
    """The key id of the encrypted Local Folders mail kept on this computer (its store.json), or None."""
    try:
        metadata = load_json((engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    key_id = metadata.get("key_id") if isinstance(metadata, dict) and metadata.get("encrypted") is True else None
    return key_id if isinstance(key_id, str) else None
