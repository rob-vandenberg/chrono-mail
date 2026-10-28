import json

import pytest
from datetime import date
from pathlib import Path

from chrono_mail.ui.savedsearches import SavedSearches
from chrono_mail.ui.searchform import DateChoice, SearchForm, SearchLine, SearchScope, build_query, days, form_data, form_from, too_big, ways_count

# --- Version ------------------------------------------------------------
__version__ = "test_ui_searchform.py 4.2.410"

# --- Version history ----------------------------------------------------
# v4.2.410: Initial: the search words the lines make, the days a date choice stands for, the saved searches file (D-289)

TODAY = date(2026, 10, 28)  # a Wednesday


def test_the_lines_make_the_search_words() -> None:
    """D-289: every part and kind; all: every line must match, a line with any of its words a choice inside it; any: one line is
    enough; a None line always leaves out; empty lines and the search language's own words count for nothing."""
    assert build_query([SearchLine()]) == ""
    assert build_query([SearchLine(text="invoice march")]) == "invoice march"
    assert build_query([SearchLine(kind="any", text="invoice receipt")]) == "invoice OR receipt"
    assert build_query([SearchLine(kind="exact", text='monthly "invoice"')]) == '"monthly invoice"'
    assert build_query([SearchLine(kind="none", text="draft -spam")]) == "-draft -spam"
    assert build_query([SearchLine(part="from", kind="any", text="bob ann")]) == "from:bob OR from:ann"
    assert build_query([SearchLine(part="subject", kind="exact", text="hello world")]) == 'subject:"hello world"'
    assert build_query([SearchLine(part="to", kind="none", text="robbert")]) == "-to:robbert"
    lines = [SearchLine(text="march"), SearchLine(kind="any", text="invoice receipt"), SearchLine(), SearchLine(kind="none", text="draft")]
    assert build_query(lines) == "march invoice OR march receipt -draft"
    assert build_query(lines, "any") == "march OR invoice OR receipt -draft"
    assert build_query([SearchLine(kind="any", text="a b"), SearchLine(kind="any", text="c d")]) == "a c OR a d OR b c OR b d"
    assert build_query([SearchLine(text="x OR NOT | y")]) == "x y"


def test_every_date_choice_stands_for_its_days() -> None:
    """D-289: a span is counted from today; Between, Before and After keep their fixed days (Before and After leave the day itself out)."""
    expected = {
        "any": (None, None), "today": ("2026-10-28", "2026-10-28"), "yesterday": ("2026-10-27", "2026-10-27"),
        "tomorrow": ("2026-10-29", "2026-10-29"), "last7": ("2026-10-22", "2026-10-28"), "last30": ("2026-09-29", "2026-10-28"),
        "next7": ("2026-10-28", "2026-11-03"), "next30": ("2026-10-28", "2026-11-26"), "this_month": ("2026-10-01", "2026-10-28"),
        "last_month": ("2026-09-01", "2026-09-30"), "this_year": ("2026-01-01", "2026-10-28"), "last_year": ("2025-01-01", "2025-12-31"),
    }
    for choice, found in expected.items():
        assert days(DateChoice(choice), TODAY) == found, choice
    assert days(DateChoice("last_month"), date(2026, 1, 15)) == ("2025-12-01", "2025-12-31")
    assert days(DateChoice("between", "2026-01-01", "2026-02-01"), TODAY) == ("2026-01-01", "2026-02-01")
    assert days(DateChoice("before", None, "2026-01-01"), TODAY) == (None, "2025-12-31")
    assert days(DateChoice("after", "2026-01-01", None), TODAY) == ("2026-01-02", None)
    assert days(DateChoice("between", "garbage", None), TODAY) == (None, None)


def test_a_saved_last_30_days_counts_from_the_day_it_runs() -> None:
    """D-289: a saved span moves with today: the same search a year later looks at that year's last 30 days."""
    saved = form_from(form_data(SearchForm(received=DateChoice("last30"))))
    assert days(saved.received, date(2027, 10, 28)) == ("2027-09-29", "2027-10-28")


def test_the_file_keeps_the_saved_searches_and_the_form_last_left(tmp_path: Path) -> None:
    path = tmp_path / "saved-searches.json"
    store = SavedSearches(path)
    form = SearchForm(
        scope=SearchScope.SUBFOLDERS, match="any", lines=(SearchLine("from", "any", "bob ann"), SearchLine("subject", "none", "news")),
        received=DateChoice("between", "2026-01-01", "2026-02-01"), sent=DateChoice("last7"), minimum_kb=5, unread=True, tag="Work",
        snoozed=DateChoice("next7"),
    )
    store.put("Invoices", form)
    store.put("alpha", SearchForm())
    store.keep_last(form)
    again = SavedSearches(path)
    assert again.names() == ["alpha", "Invoices"] and again.form("Invoices") == form and again.last == form
    again.put("INVOICES", SearchForm())  # the same name in other letters replaces it
    again.rename("alpha", "Beta")
    again.delete("nothing")
    third = SavedSearches(path)
    assert third.names() == ["Beta", "INVOICES"] and third.form("INVOICES") == SearchForm()
    third.delete("Beta")
    assert SavedSearches(path).names() == ["INVOICES"]


def test_a_damaged_file_is_never_written_over_and_odd_values_take_their_defaults(tmp_path: Path) -> None:
    """AGENTS 4.2: a file that cannot be read is put aside at the first write; a value that is unknown or out of range takes its default."""
    path = tmp_path / "saved-searches.json"
    path.write_text("{not json", encoding="utf-8")
    store = SavedSearches(path)
    assert store.names() == [] and store.last is None
    store.put("New", SearchForm())
    assert (tmp_path / "saved-searches.json.damaged").read_text(encoding="utf-8") == "{not json"
    assert SavedSearches(path).names() == ["New"]
    odd: dict[str, object] = {"saved": [{"name": "Odd", "form": {
        "scope": "everywhere", "match": "maybe", "lines": [{"part": "cc", "kind": "most", "text": 5}, "not a line"] + [{}] * 100,
        "received": {"choice": "soon", "first": "never"}, "minimum_kb": -1, "maximum_kb": True, "unread": "yes", "tag": 7,
    }}, {"name": "  "}, "nothing"]}
    path.write_text(json.dumps(odd), encoding="utf-8")
    found = SavedSearches(path).form("Odd")
    assert found is not None and found.scope is SearchScope.ALL and found.match == "all" and found.received == DateChoice()
    assert found.lines[0] == SearchLine() and len(found.lines) == 50 and (found.minimum_kb, found.maximum_kb, found.unread, found.tag) == (0, 0, False, None)
    assert SavedSearches(path).names() == ["Odd"]


def test_typed_fields_stay_text_a_phrase_keeps_its_words_and_too_many_combinations_are_refused() -> None:
    """D-289: a typed from: or tag: is text (the line's part says where it looks; tag: would bind the whole search); OR inside a
    phrase is a word; Any lines under all multiply, and more than a search can take is never built."""
    assert build_query([SearchLine(text="tag:x from:bob 10:30")]) == '"tag:x" "from:bob" "10:30"'
    assert build_query([SearchLine(kind="none", text="10:30")]) == '-"10:30"'
    assert build_query([SearchLine(kind="exact", text="re: tag:x")]) == '"re tag x"'
    assert build_query([SearchLine(kind="exact", text="to be or NOT to be")]) == '"to be or NOT to be"'
    assert ways_count([SearchLine(kind="any", text="a b c")] * 2) == 9 and ways_count([SearchLine(kind="any", text="a b c")] * 2, "any") == 6
    many = [SearchLine(kind="any", text=" ".join(f"w{number}" for number in range(10)))] * 4
    assert too_big(many) and build_query(many) == "" and not too_big(many, "any")
    assert too_big([SearchLine(text="x" * 9000)]) and not too_big([SearchLine(text="x" * 100)])


def test_the_first_and_the_last_day_a_search_can_ask_for() -> None:
    """After the last day finds nothing, never everything; Before the first day too (the engine takes 0002 to 9998, D-259)."""
    assert days(DateChoice("after", "9999-12-31", None), TODAY) == ("9998-12-31", None)
    assert days(DateChoice("before", None, "0001-01-01"), TODAY) == ("9998-12-31", "0002-01-01")
    assert days(DateChoice("between", "0001-01-01", "9999-12-31"), TODAY) == ("0002-01-01", "9998-12-31")


def test_an_unreadable_file_is_left_alone_older_damaged_copies_stay_and_long_names_come_back_the_same(tmp_path: Path) -> None:
    path = tmp_path / "saved-searches.json"
    path.mkdir()  # there, but it cannot be read as a file: nothing is written over it, and the change is refused, not lost quietly
    store = SavedSearches(path)
    with pytest.raises(OSError):
        store.put("New", SearchForm())
    assert path.is_dir() and store.names() == [] and not (tmp_path / "saved-searches.json.damaged").exists()
    path.rmdir()
    store.put("New", SearchForm())  # readable again (here: gone): the change is kept
    assert SavedSearches(path).names() == ["New"]
    path.unlink()
    for text in ("{first", "{second"):
        path.write_text(text, encoding="utf-8")
        SavedSearches(path).put("x", SearchForm())
    assert (tmp_path / "saved-searches.json.damaged").read_text(encoding="utf-8") == "{first"
    assert (tmp_path / "saved-searches.json.damaged2").read_text(encoding="utf-8") == "{second"
    long_name = "n" * 1500
    store = SavedSearches(path)
    store.put(long_name, SearchForm())
    store.put("N" * 1200, SearchForm())  # the same name as kept, in other letters: it replaces that one
    assert SavedSearches(path).names() == sorted(store.names(), key=str.casefold) and store.named(long_name) == "N" * 1000


def test_a_change_the_file_did_not_take_changes_nothing_and_a_file_readable_again_is_read_before_a_name_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-289: written first, kept here only when the file took it; a file that could not be opened at start and can now is read
    again before a change, so its searches stay and a name it has is known."""
    from chrono_mail.ui import savedsearches

    path = tmp_path / "saved-searches.json"
    SavedSearches(path).put("Kept", SearchForm())
    store = SavedSearches(path)

    def refused(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(savedsearches, "replace_file_atomically", refused)
    with pytest.raises(OSError):
        store.put("Other", SearchForm())
    with pytest.raises(OSError):
        store.delete("Kept")
    assert store.names() == ["Kept"] and store.last is None
    monkeypatch.undo()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    later = SavedSearches(blocked)  # cannot be opened as a file now
    blocked.rmdir()
    blocked.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")  # readable now
    later.ready()
    assert later.named("kept") == "Kept"
    later.put("New", SearchForm())
    assert SavedSearches(blocked).names() == ["Kept", "New"]
