import struct

import pytest

from chrono_mail.pst.rtf import RtfError, decompress, html_from_rtf, is_html, text_from_rtf

# --- Version ------------------------------------------------------------
__version__ = "test_pst_rtf.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: compressed RTF (the worked examples of MS-OXRTFCP section 4), HTML kept inside RTF, text from RTF

SIMPLE = bytes.fromhex("2d0000002b0000004c5a4675f1c5c7a703000a0072637067313235423 20af320 68656c090020627705b06c647d0a800fa0".replace(" ", ""))
CROSSING = bytes.fromhex("1a0000001c0000004c5a4675e2d44b51410004205758595a0d6e7d010eb0")


def test_worked_example_simple() -> None:
    assert decompress(SIMPLE) == b"{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n"


def test_worked_example_run_crossing_the_write_position() -> None:
    assert decompress(CROSSING) == b"{\\rtf1 WXYZWXYZWXYZWXYZWXYZ}"


def test_uncompressed_form() -> None:
    rtf = b"{\\rtf1 plain}"
    data = struct.pack("<IIII", len(rtf) + 12, len(rtf), 0x414C454D, 0) + rtf
    assert decompress(data) == rtf


def test_damaged_input_raises_its_own_error() -> None:
    with pytest.raises(RtfError):
        decompress(b"short")
    with pytest.raises(RtfError):
        decompress(struct.pack("<IIII", 100, 10, 0x12345678, 0) + b"x" * 10)
    with pytest.raises(RtfError):
        decompress(struct.pack("<IIII", 100, 2**31, 0x75465A4C, 0))
    with pytest.raises(RtfError):
        decompress(struct.pack("<IIII", 14, 10, 0x75465A4C, 0) + b"\x01\x00")  # a reference cut in half


HTML_RTF = (
    b"{\\rtf1\\ansi\\ansicpg1252\\fromhtml1 \\deff0{\\fonttbl{\\f0\\fswiss Arial;}}"
    b"{\\*\\htmltag19 <html>}{\\*\\htmltag34 <head>}{\\*\\htmltag41 </head>}{\\*\\htmltag50 <body>}"
    b"\\htmlrtf {\\htmlrtf0 {\\*\\htmltag64 <p>}caf\\'e9 \\{ok\\}\\htmlrtf\\par\\htmlrtf0 {\\*\\htmltag72 </p>}"
    b"\\u8364?{\\*\\htmltag58 </body>}{\\*\\htmltag27 </html>}}}"
)


def test_html_is_taken_out_of_rtf() -> None:
    assert is_html(HTML_RTF)
    html = html_from_rtf(HTML_RTF)
    assert html == "<html><head></head><body><p>café {ok}</p>€</body></html>"


def test_rtf_without_html_gives_none_and_text() -> None:
    rtf = b"{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}{\\*\\generator x;}\\pard Line one\\par Line\\tab two {\\b bold}\\par}"
    assert html_from_rtf(rtf) is None
    assert text_from_rtf(rtf) == "Line one\nLine\ttwo bold"


def test_text_from_rtf_unicode_and_fields() -> None:
    rtf = b"{\\rtf1\\uc1 Gr\\u252?\\u223?e {\\field{\\*\\fldinst HYPERLINK \"x\"}{\\fldrslt link}}}"
    assert text_from_rtf(rtf) == "Grüße link"


def test_unbalanced_rtf_does_not_raise() -> None:
    assert "text" in text_from_rtf(b"{\\rtf1 {{{{ text \\'zz \\")
    assert text_from_rtf(b"}}}}") == ""


def test_a_damaged_hex_escape_is_text_not_a_crash() -> None:
    """Review fault 1: \\'-1 made a byte of -1 and raised."""
    for damaged in (b"{\\rtf1 a\\'-1b}", b"{\\rtf1 a\\'+fb}", b"{\\rtf1 a\\' fb}", b"{\\rtf1 a\\'"):
        assert text_from_rtf(damaged).startswith("a")
    assert html_from_rtf(b"{\\rtf1\\fromhtml1 {\\*\\htmltag <p>}a\\'-1{\\*\\htmltag </p>}}") is not None


def test_surrogate_pairs_make_one_character() -> None:
    """Review fault 5: \\u-10179?\\u-8704? is one character above the first 65,536."""
    assert text_from_rtf(b"{\\rtf1 x\\u-10179?\\u-8704?y}") == "x\U0001f600y"
    assert text_from_rtf(b"{\\rtf1 x\\u-10179?y}") == "x�y"  # a lone half
    assert text_from_rtf(b"{\\rtf1 x\\u-8704?y}") == "x�y"


def test_binary_data_cannot_end_groups() -> None:
    """Review fault 18: \\binN is followed by N raw bytes, braces included."""
    assert text_from_rtf(b"{\\rtf1 A{\\pict\\bin4 }xyz}B}") == "AB"
    assert text_from_rtf(b"{\\rtf1 A{\\pict\\bin999 }}") == "A"  # a length past the end
