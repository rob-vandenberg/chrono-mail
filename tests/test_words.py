from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "test_words.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: Initial: a count is written one way (the Windows computer's notes: "1 messages", "1041", "message(s)")


def test_a_count_is_written_as_a_person_writes_it() -> None:
    assert counted(1, "message") == "1 message" and counted(0, "message") == "0 messages" and counted(2, "message") == "2 messages"
    assert counted(1041, "message") == "1,041 messages" and counted(1, "calendar entry", "calendar entries") == "1 calendar entry"
    assert counted(506, "calendar entry", "calendar entries") == "506 calendar entries"
