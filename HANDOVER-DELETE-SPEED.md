# HANDOVER-DELETE-SPEED.md

*Written 2026-10-26 at the owner's request, for a new chat that takes over. The task is not finished. Nothing is committed or pushed; the last pushed version is 3.6.351.*

## 1. The owner's report

In Favorites > Unread (about 600 messages; the number does not matter, 60 is just as slow), **column layout, reading pane OFF**, pressing Delete shows three separate steps on screen:
1. the selection moves down one row,
2. the deleted row goes,
3. the rows below move up.

It takes about half a second by eye. It must be **instant**: holding the Delete key (key repeat about 30 per second) must be handled without the list falling behind.

## 2. Mistakes not to repeat (they cost most of the 725,000 tokens)

1. **Use the owner's setup:** column layout, reading pane off. The first rounds tested cards with the reading pane: wrong.
2. **Measure what reaches the screen, not how long code runs.** The owner rejected code timings: they are not what the eye sees.
3. **Do not blame the display.** The owner works through xrdp on the Linux computer (display `:10.0`, **device pixel ratio 1.25**). The reference client is fast on the same display, and a plain Qt QTreeView repaints in 14 ms there, the same as offscreen. The display is not the cause.
4. **Never paint rows off-screen into a QPixmap or QImage and blit them.** On Linux, Qt draws text with the desktop's colour sub-pixel smoothing only when it paints onto a widget. Text painted into any QImage or QPixmap (RGB32, ARGB32 and RGB888 were tested) gets grey smoothing, which is a visible change. This is proven in `scratchpad/probe_subpixel.py`, with 0 fringe pixels in an image against 840 on a widget. The row-picture cache built in this chat failed for that reason, and it was removed.
5. **Account for the 125 % scaling.** Rows are 29 logical px = 36.25 device px. Any approach that assumes whole pixels must handle this.
6. **Probes have a 60-second limit.** A probe that does not answer after one minute will not answer after five.
7. **Proceed:** the owner approved a changelist built on the picture cache. That approach is dead, so a new approach needs a new changelist and Proceed.

## 3. Measurement tools (in `scratchpad/`, git-ignored)

1. `record_owner.py <seconds> <out dir>`: records the owner's own running Chrono Mail window.
   1. It takes key presses from `xinput test-xi2 --root`, through `stdbuf -oL`. Delete is keycode **107** on the xrdp keyboard.
   2. It takes the window's pixels with XGetImage, one frame about every 8 ms, and keeps the frames that differ.
   3. Run it with `DISPLAY=:10.0` while the owner presses Delete.
   4. **Delete the frames afterwards: they show real mail.**
2. `probe_screen.py` (run with `DISPLAY=:10.0 PYTHONPATH=.`, and `COUNT=100` for the held-key part): a test window on the real display.
   1. Setup: 60 to 100 made-up unread messages, Favorites > Unread, columns, reading pane off.
   2. It runs 3 single presses, then 60 presses posted every 33 ms from a timer (as X repeats a held key) while the event loop runs freely.
   3. `screen_capture.py` records the screen meanwhile.
   4. A sampler thread records the GUI thread's Python stack every 2 ms.
   5. Env options: `PAINTPROFILE=1` times one full list repaint (inside `paintEvent`) and exits. `HOLDSAMPLE=1` samples during the hold.
3. `probe_screen_report.py scratchpad/probe-screen`: per press, the screen changes in ms after the press; for the hold, the removals done and how long the screen kept changing after the last press.
4. `probe_samples_report.py scratchpad/probe-screen`: what the GUI thread did, ms by ms, after press 1.
5. `probe_compare.py`: compares, pixel by pixel, the list painted one way against another. Use it to prove that any fix leaves the look unchanged.
6. Patching a Qt virtual method on the class after the object exists does not take effect. Patch the instance instead: `view.drawRow = types.MethodType(...)`.

## 4. What was measured

### 4.1 The owner's own window (3.6.351)
1. **One Delete:** three screen changes, at about 60, 115 and 175 ms after the press.
2. **Holding Delete:** 71 presses in 2.2 s gave only 23 screen changes, and the screen was still catching up **3 s after the key was released**.

### 4.2 Ruled out (measured)
1. The engine: `message.delete-many` takes 15 to 35 ms.
   1. This includes Local Folders with 140,782 messages and a 400 MB index, and a copy of the demo profile.
   2. It includes IMAP on both test servers, with an open connection about 20 to 35 ms, and about 90 ms when the connection is made again.
2. Disk fsync: 2 calls, about 1 ms.
3. The reading pane: the owner has it off.
4. QtWebEngine: about 6 ms extra per frame.
5. The display: shown in section 2, item 3.

### 4.3 Timeline of one Delete in the test window (3.6.351, real display)
1. **0 to 15 ms:** at the key press, `take_away` greys the row and moves the selection. The busy bar is shown, and the whole window re-lays out (`toolbar.sizeHint` and `toolbar._needed` are called about 1,680 times per Delete).
2. **About 24 ms:** the engine fact arrives and `rowsRemoved` fires. `_show_archive_action` sets the Archive button's icon. A new QIcon always counts as a change, so the toolbar lays itself out again.
3. **About 38 ms:** the folder list paints its new number (picture 2). The message list does not repaint yet: QTreeView lays itself out again only on its delayed-layout timer, a later pass.
4. **About 60 ms:** the busy bar is hidden, and the window lays itself out again.
5. **70 to 100 ms:** the list repaints all its rows (picture 3).

### 4.4 THE MAIN CAUSE
1. **One full repaint of the message list takes 26 to 33 ms, inside `paintEvent` alone.** That was measured at 26 visible rows and 19 columns (about 9 visible).
2. The cost is Python called from C++ for every cell, about 230 cells per repaint:
   1. `ColumnDelegate.paint`.
   2. About 8 `MessageListModel.data()` calls per cell, made by `QStyledItemDelegate.initStyleOption`: about 1,850 calls per repaint, each doing `_group_at` (bisect) and `row_at`.
   3. `ScaledStyle.pixelMetric`: about 600 calls.
   4. `MessageListView.drawRow`.
3. After a Delete **every row below the deleted one is repainted**, although nothing in them changed.
4. A held key repeats every 33 ms, so the window cannot keep up.
5. Even with each cell already painted and kept (a per-cell picture test), the repaint still took about 23 ms offscreen. The Python call overhead per cell, not the drawing itself, is the main cost.

### 4.5 Held key, test window, current working copy (no pictures, fixes A to C below)
1. Of 60 presses posted at 33 ms, **only 30 removals were done.**
2. The 60 timer ticks took 3.4 s, so the GUI thread was busy about 57 ms per tick.
3. **Why half the presses did nothing is not yet known.** Possible causes:
   1. The deferral in `MessageListPane.deferred()`, or the one action queue (D-218).
   2. `take_away` moving the selection onto a row still pending.
   3. A press arriving while the order is still being placed.

   This must be investigated: a press must never be lost.

## 5. State of the working copy (uncommitted)

1. **A. `ui/messagelistview.py`:** `setModel` connects `rowsRemoved` to `_rows_left`, which calls `viewport().update()`. The removal is then painted in the same pass as the folder numbers, so there are two steps instead of three. Its paint executes the posted layout, which a test proves.
2. **B. `ui/mainwindow.py` `_show_archive_action`:** it sets the text and icon only when the button really switches between Archive and Unarchive.
3. **C. `ui/operations.py` `ClickableBar` and `ui/statusreport.py`:** the busy bar is never shown or hidden. `set_busy()` changes it; while idle it paints nothing, shows no tooltip and ignores clicks. A tooltip left open is hidden when work ends. Visible difference: 120 px at the right of the status line are always reserved.
4. **D. `ui/delegate.py` `ColumnDelegate._named_icon`:** the Contact and Delete icons are made once. TintedSvgEngine reads the palette when it draws, so a theme change is still followed.
5. **`tests/ui/test_ui_listpainting.py`: half trimmed.** The picture tests were removed. The three tests that remain (removal, Archive, busy bar) are valid and each failed on 3.6.351. **The imports at the top are stale** (`messagelistview`, `on_whole_pixels`, `datetime`, `types`, `ColumnDelegate`, `QImage`, `QPainter` and so on) and the version-history comment must be shortened. Clean it up, then run Pyrefly on the file.
6. **DECISIONS.md D-266, DEVLOG.md (3.6.352 entry) and STATUS.md (3.6.352 line and Problems line)** still describe the row-picture cache. They **must be rewritten**, or the cache text removed.
7. `pyproject.toml` is still at 3.6.351; bump it last.
8. The related window tests (34 files listed in `scratchpad/related_ui.txt`), the guard tests, Pyrefly and the layer check last passed **with the picture cache in place**. They have not been run on the trimmed state.

## 6. The fix still to build (proposal; needs the owner's changelist and Proceed)

The goal: a Delete repaints only what changed. The rows below the removed one must be **moved, not repainted**, which is what other mail programs do. Painting stays direct on the widget, so the text looks exactly the same.

1. **Move the rows below up:** use `viewport().scroll(0, -height, rect_below)` at the removal. Qt moves the pixels inside its backing store, and the sub-pixel text stays.
   1. The viewport needs `WA_OpaquePaintEvent`, so Qt does not erase rows that are skipped.
   2. The view then fills the background of the region it paints itself.
2. **Keep a map of what is on screen:** for each display row, the key it was last painted with. Use the same idea as the removed `_picture_key`: the MessageRow, selected, current, hovered (taken from QAbstractItemView's own `entered` and `viewportEntered` signals plus Leave), pending, sync mark, the columns, the minute.
   1. The map shifts with the scroll.
   2. In `paintEvent`, pass `super()` a reduced QPaintEvent that covers only the rows whose key changed, the strip that came into view and the area below the last row.
3. **Clear the map when pixels may move or be lost:**
   1. `scrollContentsBy`, resize, show and hide.
   2. Look events (palette, style, font).
   3. `modelReset`, `layoutChanged`, `rowsInserted`, `headerDataChanged`, and columns moved, inserted or removed.
   4. A full-range `dataChanged` (`refresh_cells`).
   5. `redraw()`, the tag colours.
4. **The traps the reviewers found for the picture cache apply here too:**
   1. Hover: use Qt's `entered` signal, not hover events; the viewport has no `WA_Hover`.
   2. Dates: use the minute, not only the day.
   3. Columns moved between two equally wide columns.
   4. The 125 % scaling.
   5. `grab()` and `render()` paint off-screen: never skip rows there.
5. **Alternative or addition:** make the direct paint itself much cheaper.
   1. Answer `data()` from a per-row tuple instead of looking the row up again for every role.
   2. Or paint whole rows in one Python call in `drawRow`, instead of about 9 delegate calls and about 70 data calls per row.
   3. Prove any change pixel-identical with `probe_compare.py`.
6. **Find and fix the lost presses** (section 4.5).
7. **Verify in this order:**
   1. `probe_screen.py`: one or two screen steps; with the posted held key, all 60 removals done and nothing left changing after the last press.
   2. `probe_compare.py`: no pixel different from 3.6.351.
   3. Then the owner's own window with `record_owner.py`: single presses and a held key.

## 7. Messages moved to Trash during the recordings

About 200 of the owner's real unread messages were deleted during the two recordings. They are in Trash; Ctrl+Z or a move restores them.
