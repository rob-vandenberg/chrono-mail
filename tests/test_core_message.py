import dataclasses

import pytest

from chrono_mail.core import MessageRef

# --- Version ------------------------------------------------------------
__version__ = "test_core_message.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial core tests


def test_equality_and_hashing() -> None:
    first = MessageRef("a1", ("Inbox",), "x7f3")
    second = MessageRef("a1", ("Inbox",), "x7f3")
    assert first == second
    assert len({first, second}) == 1
    assert first != MessageRef("a1", ("Sent",), "x7f3")
    assert first != MessageRef("a2", ("Inbox",), "x7f3")


def test_message_ref_is_immutable() -> None:
    reference = MessageRef("a1", ("Inbox",), "x7f3")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(reference, "store_id", "other")
