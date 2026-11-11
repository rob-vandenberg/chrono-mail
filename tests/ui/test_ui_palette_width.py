from chrono_mail.ui import forms

# --- Version ------------------------------------------------------------
__version__ = "test_ui_palette_width.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: The Event colours window is gone (D-329): only the owner's widths stay
# v4.3.503: Event colours opens at 550 pixels on the owner's screen, never below 500 (the owner)
# v4.3.502: a long calendar name never makes Event colours wider; the Calendar field is read-only text in the normal colour with its whole
#           name as hover text; the window opens at the width last given it, and as the first time when what was kept is no plain number (the owner)
# v4.2.445: the 500 pixels are the owner's screen's (125 %, slider 0.95), in Qt's units at the size slider
# v4.2.442: Initial version: Event colours opens at, and never goes below, 500 pixels on the screen (the owner)


def test_a_width_the_owner_gives_is_exactly_that_on_his_screen() -> None:
    assert forms.owner_width(500) == 421 and forms.owner_width(700) == 589
    for pixels in (500, 700):  # Qt's units at his slider (rounded as wpx does), then his screen's scale
        assert round(round(forms.owner_width(pixels) * forms.OWNER_WINDOWS_FACTOR) * forms.OWNER_SCREEN_SCALE) == pixels
