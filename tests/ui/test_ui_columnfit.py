from chrono_mail.ui.columnfit import MINIMUM_ICON, MINIMUM_TEXT, ColumnLayout, drag, fit, layout_from_pixels, read_layout

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columnfit.py 0.1.89"

# --- Version history ----------------------------------------------------
# v0.1.89: Initial tests of the column fit arithmetic

ORDER = ("status", "flag", "from_to", "subject", "date", "trash")
ICON = (True, True, False, False, False, True)
DEFAULTS = (24, 24, 170, 300, 110, 24)


def test_the_columns_fill_the_width_exactly_and_icon_columns_keep_their_pixels() -> None:
    for available in (400, 777, 1500):
        widths = fit(ORDER, ICON, ColumnLayout(), DEFAULTS, available)
        assert sum(widths) == available and widths[0] == widths[1] == widths[5] == 24
    wide, narrow = fit(ORDER, ICON, ColumnLayout(), DEFAULTS, 1500), fit(ORDER, ICON, ColumnLayout(), DEFAULTS, 750)
    assert abs(wide[3] / wide[2] - narrow[3] / narrow[2]) < 0.05  # a resize changes the text columns in proportion


def test_a_text_column_is_never_narrower_than_its_smallest_width() -> None:
    layout = ColumnLayout(shares={"from_to": 0.01, "subject": 0.98, "date": 0.01})
    widths = fit(ORDER, ICON, layout, DEFAULTS, 600)
    assert widths[2] == widths[4] == MINIMUM_TEXT and sum(widths) == 600


def test_dragging_changes_the_column_and_the_text_columns_to_its_right_only() -> None:
    widths = [24, 24, 200, 300, 150, 24]
    wider = drag(ICON, widths, 2, 260)  # From / To 60 px wider
    assert wider[:3] == [24, 24, 260] and wider[5] == 24 and sum(wider) == sum(widths)
    assert wider[3] < 300 and wider[4] < 150  # Subject and Received give in proportion
    narrower = drag(ICON, widths, 2, 140)
    assert narrower[2] == 140 and sum(narrower) == sum(widths) and narrower[3] > 300 and narrower[4] > 150


def test_a_drag_stops_at_the_smallest_widths() -> None:
    widths = [24, 24, 200, 300, 150, 24]
    assert drag(ICON, widths, 2, 10)[2] == MINIMUM_TEXT
    assert drag(ICON, widths, 0, 5)[0] == MINIMUM_ICON  # an icon column stops at 24 px
    far = drag(ICON, widths, 2, 5000)
    assert far[3] == far[4] == MINIMUM_TEXT and sum(far) == sum(widths)


def test_widening_an_icon_column_takes_from_the_text_columns_to_its_right() -> None:
    widths = [24, 24, 200, 300, 150, 24]
    wider = drag(ICON, widths, 1, 40)
    assert wider[:2] == [24, 40] and wider[2] < 200 and sum(wider) == sum(widths)


WIDE_ICON = (True, True, False, False, False, False, True, True)  # Unread, Flagged, From / To, Subject, Received, Size, Contact, Delete
WIDE = [24, 24, 200, 300, 150, 80, 24, 24]


def test_the_line_left_of_an_icon_column_changes_that_column_and_every_text_column_gives_way() -> None:
    result = drag(WIDE_ICON, WIDE, 5, 60)  # the Size | Contact line 20 px to the left
    assert result[6] == 44 and result[7] == 24 and result[:2] == [24, 24] and sum(result) == sum(WIDE)
    assert all(result[position] < WIDE[position] for position in (2, 3, 4, 5))  # all text columns gave, in proportion
    assert drag(WIDE_ICON, WIDE, 5, 120)[6] == MINIMUM_ICON  # to the right: Contact narrower, down to 24 px


def test_between_two_icon_columns_the_one_farther_from_the_text_columns_changes() -> None:
    result = drag(WIDE_ICON, WIDE, 6, 14)  # the Contact | Delete line 10 px to the left
    assert result[6] == 24 and result[7] == 34 and sum(result) == sum(WIDE)  # Delete wider, Contact keeps its width and moves left
    result = drag(WIDE_ICON, WIDE, 0, 34)  # the Unread | Flagged line 10 px to the right
    assert result[0] == 34 and result[1] == 24 and sum(result) == sum(WIDE)


def test_the_last_line_does_nothing() -> None:
    assert drag(WIDE_ICON, WIDE, 7, 60) == WIDE


def test_the_layout_is_saved_and_read_back_and_old_pixels_are_taken_over() -> None:
    layout = layout_from_pixels(ORDER, ICON, [26, 30, 170, 300, 110, 24])
    assert layout.icons == {"status": 26, "flag": 30, "trash": 24} and abs(sum(layout.shares.values()) - 1) < 1e-9
    again = read_layout(layout.to_text())
    assert again is not None and again.icons == layout.icons and set(again.shares) == {"from_to", "subject", "date"}
    assert read_layout("") is None and read_layout("[1]") is None and read_layout("nonsense") is None
    odd = read_layout('{"icons": {"a": 3, "b": true, "c": 30}, "shares": {"x": 2, "y": 0.5}}')
    assert odd is not None and odd.icons == {"c": 30} and odd.shares == {"y": 0.5}
