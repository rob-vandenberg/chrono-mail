from chrono_mail.mime import as_it_goes_out

# --- Version ------------------------------------------------------------
__version__ = "test_mime_as_it_goes_out.py 4.2.428"

# --- Version history ----------------------------------------------------
# v4.2.428: Initial: what goes to the server never carries the Bcc nor the program's own X-Chrono-Mail notes (a draft moved into the Outbox),
#           also with plain LF line ends, and the header block ends at its first blank line whatever the line ends


def test_the_bcc_and_the_programs_own_notes_never_go_out_and_nothing_else_changes() -> None:
    raw = (
        b"From: a@example.com\r\nBcc: hidden@example.com\r\nX-Chrono-Mail-Kind: new\r\nX-CHRONO-MAIL-Source: Inbox/1\r\n continued\r\n"
        b"Subject: X-Chrono-Mail-Kind: in the subject stays\r\nX-Other: kept\r\n\r\nX-Chrono-Mail-Kind: in the body stays\r\n"
    )
    assert as_it_goes_out(raw) == b"From: a@example.com\r\nSubject: X-Chrono-Mail-Kind: in the subject stays\r\nX-Other: kept\r\n\r\nX-Chrono-Mail-Kind: in the body stays\r\n"
    assert as_it_goes_out(b"no header end") == b"no header end"


def test_a_message_with_plain_line_ends_loses_them_too() -> None:
    raw = b"From: a@example.com\nBcc: hidden@example.com\nX-Chrono-Mail-Kind: new\nSubject: Hi\n\nBcc: in the body stays\n"
    assert as_it_goes_out(raw) == b"From: a@example.com\nSubject: Hi\n\nBcc: in the body stays\n"


def test_the_header_block_ends_at_the_first_blank_line_whatever_the_line_ends() -> None:
    raw = b"From: a\nBcc: secret@example.com\nSubject: s\n\nbody line\r\n\r\nmore\r\n"
    assert as_it_goes_out(raw) == b"From: a\nSubject: s\n\nbody line\r\n\r\nmore\r\n"


def test_a_message_of_headers_only_and_lf_headers_before_a_crlf_empty_line_lose_them_too() -> None:
    assert as_it_goes_out(b"From: a\r\nBcc: secret@example.com\r\nX-Chrono-Mail-Kind: new\r\n") == b"From: a\r\n"
    assert as_it_goes_out(b"From: a\nBcc: secret@example.com\n\r\nbody\r\n") == b"From: a\n\r\nbody\r\n"


def test_a_bare_cr_inside_a_header_never_lets_a_bcc_go_out() -> None:
    assert as_it_goes_out(b"To: a\r\nBcc: s1@x\rs2@y\r\nSubject: z\r\n\r\nbody") == b"To: a\r\nSubject: z\r\n\r\nbody"
    assert as_it_goes_out(b"Subject: a\r\r\nBcc: q\r\n\r\nbody") == b"Subject: a\r\r\n\r\nbody"
