"""The templates and signatures of one New message window (B-24, D-358).

When the window opens, the account's template for that kind of message is put at the start and its signature where the account
says: at the end of a new message, and in a reply or forward before or after the quoted text. Ctrl+1 to Ctrl+7 and the Signature
button put in a text of the library as the signature: a message holds one signature, the same key again does nothing, another key
replaces it. Changing From swaps a default signature for the new account's (or takes it out when it has none); one put in by key
stays. In rich text the signature stands in a frame of its own that starts with an invisible anchor, so it is found again, also in a
draft opened again; in plain text it is the usual block from the "-- " line to the next empty line, so it is found also after the
person changed it. A switch between plain and rich text takes the signature along."""

import html
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QTextCursor, QTextDocument, QTextFormat, QTextFrame, QTextFrameFormat
from PySide6.QtWidgets import QPlainTextEdit

from chrono_mail.config.model import AccountTexts
from chrono_mail.engine.commands_signatures import SnippetText
from chrono_mail.ui.richeditor import PICTURE_MARK, RichEditor

# --- Version ------------------------------------------------------------
__version__ = "composertexts.py 4.5.626"

# --- Version history ----------------------------------------------------
# v4.5.626: Initial version: the account's template and signature when a message opens, the keys, the From swap (B-24, D-358)

SIGNATURE_MARK: str = "-- \n"  # the usual separator of a plain-text signature: two dashes and a space on a line of their own
SIGNATURE_PROPERTY: int = QTextFormat.Property.UserProperty + 58  # marks the frame that holds the signature in rich text
ANCHOR: str = "chrono-signature"  # an invisible anchor at its start: it stays when a draft is saved and opened again (the mark above does not)
KEYED: int = 7
SENT_FROM: str = "Sent from Chrono Mail"


def signature_block(text: str) -> str:
    return SIGNATURE_MARK + text.strip("\n") if text.strip() else ""


def with_signature(body: str, text: str, below: bool = False) -> str:
    """A plain-text body with the signature block: at the end of an empty body or with below, else before the quoted text."""
    block = signature_block(text)
    if not block:
        return body
    if not body.strip():
        return f"\n\n{block}\n"
    if below:
        return f"{body.rstrip(chr(10))}\n\n{block}\n"
    rest = body[2:] if body.startswith("\n\n") else body
    return f"\n\n{block}\n\n{rest}"


def plain_of(html: str) -> str:
    """A text of the library as plain text (its pictures left out)."""
    document = QTextDocument()
    document.setHtml(html)
    return document.toPlainText().replace(PICTURE_MARK, "").strip("\n")


def kind_of_message(kind: str) -> str:
    """new, reply or forward: which of an account's choices a window of this kind uses."""
    return "reply" if kind in ("reply", "reply_all") else "forward" if kind in ("forward", "forward_attachment") else "new"


def chosen_texts(texts: AccountTexts, kind: str) -> tuple[str, str]:
    """(template, signature) an account chose for this kind of message ("" none)."""
    which = kind_of_message(kind)
    if which == "reply":
        return texts.template_reply, texts.signature_reply
    if which == "forward":
        return texts.template_forward, texts.signature_forward
    return texts.template_new, texts.signature_new


@dataclass
class TextsInMessage:
    """The templates and signatures of one window: the library, and the signature the message holds now."""

    body: QPlainTextEdit
    rich: RichEditor
    is_rich: Callable[[], bool]
    kind: str  # the window's kind of message (new, reply, reply_all, forward, forward_attachment)
    writing_below: bool = False  # Settings > Writing: the person writes under the quote (where the own part of a reply is)
    library: tuple[SnippetText, ...] = ()
    signature_id: str = ""  # the text of the library that is the message's signature now ("" none)
    by_key: bool = False  # it was put in by a key or the Signature button: changing From leaves it
    signature_below: bool = False  # where it stands in a reply or forward
    plain_signature: str = ""  # plain text: the block as it was put in, to find it again
    resumed: bool = False  # a draft taken up again: a "-- " block in it is its signature

    def text(self, item_id: str) -> SnippetText | None:
        return next((entry for entry in self.library if entry.item_id == item_id), None)

    def keyed(self, place: int) -> SnippetText | None:
        return self.library[place] if 0 <= place < min(KEYED, len(self.library)) else None

    # --- when the window opens ------------------------------------------------------------------------

    def open_with(self, texts: AccountTexts, template: str = "") -> None:
        """The account's signature, then its template (or the one New > Mail chose) at the start."""
        chosen_template, chosen_signature = chosen_texts(texts, self.kind)
        self.signature_below = texts.signature_below
        if self.text(chosen_signature) is not None:
            self.put(chosen_signature, by_key=False)
        start = self.text(template or chosen_template)
        if start is not None:
            self._put_template(start)

    def _put_template(self, entry: SnippetText) -> None:
        """A template at the start of the message, where the person writes (at the end when they write under the quote)."""
        if self.is_rich():
            cursor = QTextCursor(self.rich.edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End if self.writing_below and self.kind != "new" else QTextCursor.MoveOperation.Start)
            cursor.insertHtml(self.rich.absorb(entry.html))
            return
        text = plain_of(entry.html)
        body = self.body.toPlainText()
        self.body.setPlainText(f"{body.rstrip(chr(10))}\n\n{text}\n" if self.writing_below and self.kind != "new" else f"{text}\n{body}")

    # --- the signature ---------------------------------------------------------------------------------

    def put(self, item_id: str, by_key: bool = True) -> None:
        """This text as the message's signature: the same one again changes nothing, another replaces it."""
        entry = self.text(item_id)
        if entry is None:
            return
        if item_id == self.signature_id and self._present():
            self.by_key = self.by_key or by_key
            return
        if self.is_rich():
            self._put_rich(entry)
        else:
            self._put_plain(entry)
        self.signature_id = item_id
        self.by_key = by_key

    def take_out(self) -> None:
        if self.is_rich():
            frame = self._frame()
            if frame is not None:
                cursor = QTextCursor(self.rich.edit.document())
                cursor.setPosition(frame.firstPosition() - 1)
                cursor.setPosition(frame.lastPosition() + 1, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
        else:
            body = self.body.toPlainText()
            found = self._plain_range(body)
            if found is not None:
                self.body.setPlainText((body[:found[0]] + body[found[1]:]).replace("\n\n\n\n", "\n\n"))
        self.signature_id, self.by_key, self.plain_signature = "", False, ""

    def account_changed(self, texts: AccountTexts) -> None:
        """Another sender: a default signature follows the new account; one put in by key stays."""
        self.signature_below = texts.signature_below
        if self.by_key:
            return
        wanted = chosen_texts(texts, self.kind)[1]
        if self.text(wanted) is None:
            if self.signature_id:
                self.take_out()
            return
        if wanted != self.signature_id:
            self.put(wanted, by_key=False)

    def _present(self) -> bool:
        if self.is_rich():
            return self._frame() is not None
        return self._plain_range(self.body.toPlainText()) is not None

    def _frame(self) -> QTextFrame | None:
        """The frame of the signature: marked, or (in a draft opened again) the one that starts with the anchor."""
        for frame in self.rich.edit.document().rootFrame().childFrames():
            if frame.frameFormat().property(SIGNATURE_PROPERTY) is True:
                return frame
            first = frame.firstCursorPosition().block()
            part = first.begin()
            while not part.atEnd():
                if ANCHOR in part.fragment().charFormat().anchorNames():
                    return frame
                part += 1
        return None

    def _first_signature_wins(self) -> bool:
        """Before the quoted text the signature is the first "-- " block; at the end (a new message, or after the quote) the last."""
        return self.kind != "new" and not self.signature_below

    def _plain_range(self, body: str) -> tuple[int, int] | None:
        """Where the signature block stands in plain text: only one this window put in, or the one of a draft taken up again (never the
        signature of a forwarded or quoted mail). From its "-- " line: to the end when it stands last; else as many lines as it was put in
        with, or to the next empty line."""
        if not self.plain_signature and not self.resumed:
            return None
        starts = [0] if body.startswith(SIGNATURE_MARK) else []
        place = body.find("\n" + SIGNATURE_MARK)
        while place >= 0:
            starts.append(place + 1)
            place = body.find("\n" + SIGNATURE_MARK, place + 1)
        if not starts:
            return None
        start = starts[0] if self._first_signature_wins() else starts[-1]
        if not self._first_signature_wins():
            return start, len(body.rstrip("\n"))
        if self.plain_signature:
            end = start
            for _ in range(self.plain_signature.count("\n") + 1):
                found = body.find("\n", end + 1 if end > start else end)
                end = len(body.rstrip("\n")) if found < 0 else found
            return start, end
        end = body.find("\n\n", start)
        return start, len(body.rstrip("\n")) if end < 0 else end

    # --- a switch between plain and rich text ------------------------------------------------------------

    def before_switch(self) -> str:
        """The signature's text as it stands now ("" none), taken along by after_switch."""
        if self.is_rich():
            frame = self._frame()
            if frame is None:
                return ""
            cursor = QTextCursor(self.rich.edit.document())
            cursor.setPosition(frame.firstPosition())
            cursor.setPosition(frame.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
            return cursor.selectedText().replace("\u2029", "\n").replace(PICTURE_MARK, "").strip("\n")
        found = self._plain_range(self.body.toPlainText())
        if found is None:
            return ""
        return self.body.toPlainText()[found[0] + len(SIGNATURE_MARK):found[1]].strip("\n")

    def after_switch(self, text: str) -> None:
        """The signature taken along: in plain text it becomes the "-- " block again, in rich text its frame (the library's own
        formatting when it is still the text chosen)."""
        if not text:
            self.plain_signature = ""
            return
        if not self.is_rich():
            body = self.body.toPlainText()
            place = body.rfind(text)
            if place >= 0:
                block = signature_block(text)
                self.body.setPlainText(body[:place] + block + body[place + len(text):])
                self.plain_signature = block
            return
        document = self.rich.edit.document()
        marks = []
        block = document.firstBlock()
        while block.isValid():  # only a line that is the "-- " line itself, never a "--" inside a text or a header
            if block.text().rstrip() == "--":
                marks.append(block)
            block = block.next()
        if not marks:
            return
        mark = marks[0] if self._first_signature_wins() else marks[-1]
        lines = text.count("\n") + 1
        cursor = QTextCursor(document)
        cursor.setPosition(mark.position())
        for _ in range(lines):
            cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        entry = self.text(self.signature_id)
        frame_format = QTextFrameFormat()
        frame_format.setProperty(SIGNATURE_PROPERTY, True)
        frame_format.setTopMargin(8)
        cursor.insertFrame(frame_format)
        cursor.insertHtml(f'<a name="{ANCHOR}"></a>' + (self.rich.absorb(entry.html) if entry is not None else html.escape(text).replace("\n", "<br>")))

    def _put_rich(self, entry: SnippetText) -> None:
        document = self.rich.edit.document()
        absorbed = self.rich.absorb(entry.html)
        frame = self._frame()
        cursor = QTextCursor(document)
        if frame is not None:  # replaced where it stands
            cursor.setPosition(frame.firstPosition())
            cursor.setPosition(frame.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(f'<a name="{ANCHOR}"></a>' + absorbed)
            return
        if self.kind == "new" or self.signature_below:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        else:  # before the quoted text: where the own part ends (it is the first paragraph when the person writes above the quote)
            first = document.firstBlock()
            cursor.setPosition(0 if self.writing_below else first.position() + first.length() - 1)
        frame_format = QTextFrameFormat()
        frame_format.setProperty(SIGNATURE_PROPERTY, True)
        frame_format.setTopMargin(8)
        cursor.insertFrame(frame_format)
        cursor.insertHtml(f'<a name="{ANCHOR}"></a>' + absorbed)

    def _put_plain(self, entry: SnippetText) -> None:
        block = signature_block(plain_of(entry.html))
        body = self.body.toPlainText()
        found = self._plain_range(body)
        if found is not None:  # replaced where it stands, also after the person changed it
            self.body.setPlainText(body[:found[0]] + block + body[found[1]:])
        else:
            below = self.kind == "new" or self.signature_below  # a new message: at the end, after what is written
            self.body.setPlainText(with_signature(body, plain_of(entry.html), below=below))
        self.plain_signature = block


def with_sent_from(text: str, html: str | None) -> tuple[str, str | None]:
    """The message as sent with "Sent from Chrono Mail" at its end (Settings > Templates and Signatures)."""
    plain = f"{text.rstrip(chr(10))}\n\n{SENT_FROM}\n"
    if html is None:
        return plain, None
    line = f"<p>{SENT_FROM}</p>"
    return plain, html.replace("</body>", f"{line}</body>", 1) if "</body>" in html else html + line
