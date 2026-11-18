from pathlib import Path

import pytest

from chrono_mail.pim.ical import new_uid
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient
from chrono_mail.sync.davsync import sync_dav
from tests.live.test_live_dav import collection, dav, remove_from_deleted_items  # noqa: F401 - dav is a fixture

pytestmark = pytest.mark.live

# --- Version ------------------------------------------------------------
__version__ = "test_live_dav_names.py 4.4.624"

# --- Version history ----------------------------------------------------
# v4.4.624: Initial live test: an appointment an older Chrono Mail named with an @ is renamed on the real server by a synchronisation (D-356)


def test_an_own_appointment_named_with_an_at_is_renamed_by_a_synchronisation(dav: DavClient, tmp_path: Path) -> None:
    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    remote = collection(dav, "events")
    uid = new_uid()
    assert uid.endswith("@chrono-mail")
    old = remote.href.rstrip("/") + "/" + uid + ".ics"
    new = old.replace("@chrono-mail", "")
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Chrono Mail//Chrono Mail//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:Chrono Mail live name test\r\nDTSTART;VALUE=DATE:20301121\r\nDTEND;VALUE=DATE:20301122\r\nDTSTAMP:20261118T120000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")
    dav.put(old, text, "events", create=True)  # as an older Chrono Mail named it
    try:
        sync_dav(dav, store, state, durable=False)  # takes it in under its old name, or renames it at once
        sync_dav(dav, store, state, durable=False)
        assert dav.get(old) is None
        found = dav.get(new)
        assert found is not None and f"UID:{uid}" in found[1]
        assert state.read_text().count("live name test") == 0 and "@chrono-mail.ics" not in state.read_text()
    finally:
        for href in (old, new):
            if dav.get(href) is not None:
                dav.delete(href)
    remove_from_deleted_items(uid, 1)
