# REFACTOR.md

*The senior refactor (started 2026-10-20, after 1.1.195): the inventory of everything written at junior level, the agreed order, and the progress. This file is the hand-over between chats: a new chat never repeats the audit, it continues at "Now".*

## Taking over in a new chat

- **When to read:** every session during the refactor, right after `STATUS.md`. Read this file whole (it is short); search `DEVLOG.md` for "refactor" only when the history of an item is needed.
- **Numbers:** the items were renumbered on 2026-10-20 so that they are done in number order. Each heading says "(was R-xx)": older DEVLOG and STATUS text uses those old numbers.
- **Now:** the refactor list is done: R-39 (contact groups) in 1.10.277, the last item (R-19 in 1.9.275). The owner's parked request is done (1.9.276, D-226). R-21 (1.9.268), R-18 skipped (the owner), R-45 (1.9.266), R-44 (1.9.265); R-17 (1.9.264), R-16 (1.8.263), R-43 (1.7.262), R-15 (1.6.260), R-14 (1.5.256), R-13 (1.3.255), R-42 (1.2.254), R-12 (1.2.253), R-41 (1.1.252), R-11 (1.1.251), R-10 (1.1.250), R-40 (1.1.249), R-09 (1.1.248), R-08 (1.1.247), R-07 (1.1.245), R-06 (1.1.244), R-05 (1.1.243), R-03 (1.1.242) and R-04 (documents) are done; R-25, R-28, R-29, R-30, R-35, R-37, R-31 and R-34 are done (1.1.234 to 1.1.241); R-32 and R-33 work as intended (D-201); R-38 dropped; R-39 (contact groups) later.
- **Working rules:**
  1. One item (or one round of items that belong together) at a time, in number order, each with its own changelist and the owner's Proceed. No item is started without it.
  2. Re-read the code an item names before writing its changelist: the findings below were true on 2026-10-20 (1.1.195); line numbers and names move.
  3. Every item types the engine commands it touches (R-19 is only what is left at the end, never one big step).
  4. The end-user experience decides every choice (D-146); fix the cause, never the symptom.
  5. When an item is done, in the same step as `STATUS.md`: its Status line here, the "Now" line, and a line at the top of the Done log; a DEVLOG entry and, for a design choice, a D-number in `DECISIONS.md`.
  6. An item found to be wrong or no fault at all is marked so (Status: dropped, with the reason), never silently removed. New findings get the next number (next: **R-46**) and a place in the order, agreed with the owner.

## The agreed order (the owner, 2026-10-20)

The order is the numbers. How it was chosen: bugs first, above all what can lose mail; then what the user notices, small to big; then the tidying the user never sees; a dependency always wins over size.

- **Bugs and quick checks:** R-01 to R-04.
- **What the user notices, small to big:** R-05 to R-15.
- **Invisible tidying:** R-16 to R-19.

## How the audit was done (rounds 1 and 2, 2026-10-20)

- All 172 files (40,600 lines) were swept for the known signs: full rebuilds and model resets, hand-made reselects, guard flags, signal blocking, timers of 0 ms, untyped values, broad error catches, duplicated functions, file and class sizes.
- Read in depth: the window's folder list, view switching, counting and refetching (`ui/mainwindow.py`), the message list model (`ui/models.py`), the Tasks and Contacts lists, the error handling of every lower layer.
- Round 2 (same day): the composer, the reader and message window, the Settings window and how OK is applied, the account dialogs, the engine's send, Outbox and schedule commands, the IMAP sync per folder, the calendar's fact handling, the importer, the longest functions outside the window.
- Sound apart from the untyped values of R-01: the composer, the reader, the importer (caps, streamed unpacking), the CLI (generated from the registry), the calendar (applies each fact to its own blocks).
- The lower layers (store, crypto, protocols, index, sync) look sound: atomic writes, caps on input, every broad catch deliberate and explained. The junior code is almost all in the window layer.

## Items

### R-01 Scheduled messages are found by opening the whole Outbox (S–M) (was R-18)
- **Status:** DONE in 1.1.196 (D-169). The fix went into the index instead of a separate file (see D-169 for why); research found a worse fault too: an unreadable repeating message lost its whole schedule.
- What: every minute every Outbox message of every mailbox is decrypted and parsed to see whether its time has come (`mail.send-due`, `engine/commands_schedule.py` `_scheduled`). A scheduled message that cannot be read is skipped without a word, so it is never sent and nobody is told.
- User: a scheduled message that silently never goes out; needless work every minute with a full Outbox.
- Fix: the schedule kept in a small file, as Snoozed already is (`snoozed.json`); a message that cannot be read is reported.

### R-02 A silent limit of 5,000 items (M) (was R-09)
- **Status:** DONE in 1.1.205 (D-174, D-175). Tasks hold every task's place and load rows as they come into view; Contacts take every row at once (a row comes from the index alone, so paging gained nothing); Calendar every event of the days, and says so when a limit against hostile calendars cuts. The first typed calls (D-175).
- What: Contacts, Tasks and a Calendar range ask for at most 5,000 items (`LIST_LIMIT`); more are not shown and nothing says so. Tasks drops the hidden lists after that limit, so tasks of hidden lists use up part of it.
- User: with many contacts or tasks some are simply missing.
- Fix: load as needed (like mail), never a hidden cut-off.

### R-03 Filters in Favorites and tag views (S, to verify) (was R-12)
- **Status:** DONE in 1.1.242 (D-203). A fault, not a choice: the window built Favorites and Tags views without the filters and the engine had no place for them; the days and sizes of Advanced search were lost there too. One field (`ViewSpec.tag`) meant both the Tags view's tag and the filter's: now `combined_tag` and `tag`.
- What: a Favorites or tag view is built without the Unread, Flagged and With attachments filters (`view_spec`).
- User: switching on a filter in Favorites may do nothing.
- To verify first: a fault or a choice.

### R-04 Check: was D-146 step 5 really done for Tasks and Contacts? (S) (new, split off from the old R-07)
- **Status:** DONE 2026-10-21 (documents only): Contacts and Calendar apply each fact to their own row or blocks (insert, remove, move, dataChanged; a full load only when the person changes the view); Tasks does not (`ui/tasksview.py` `apply_item` → `_show_rows` → `TaskListModel.load(keep_rows=True)`: a model reset per change, the selection put back by hand with blocked signals). D-146 corrected; R-06 narrowed to Tasks and the Contacts tag list.
- What: D-146 says step 5 is done (calendar, contacts and tasks apply each change as a fact, 0.5.155 to 0.5.159), but Tasks and Contacts still reset their list and put the selection back by hand after every change (R-06).
- Do: compare D-146, the DEVLOG entries of 0.5.155 to 0.5.159 and the code; tell the owner which is true. If the code is behind, R-06 is a fault the user meets; if the decision text is wrong, D-146 is corrected.

### R-05 Adding and editing an account are two dialogs (S–M) (was R-17)
- **Status:** DONE in 1.1.243 (D-204): `ui/serverfields.py` builds the server boxes for both; the port follows Security in both and a typed port stays; Edit's Test connection uses the stored passwords (the owner: never ask for a password again); the kinds as `AccountKind`.
- What: `ui/accountdialog.py` (add) and `ui/accountedit.py` (edit) each build the same server fields, the security choice, the connection test and the values sent; the default ports exist only in the first, the account kinds are plain text in both.
- User: a field or check that differs between adding and editing.
- Fix: one set of account fields used by both.

### R-06 Tasks and Contacts reset their list on every change (M) (was R-07)
- **Status:** DONE in 1.1.244: `TaskListModel.follow` and `drop` move, insert or remove the one task's row and the heading it needs or leaves (Qt keeps the selection, current task and scroll; `_show_rows` and the hand-made reselect are gone; `load` only for another view); Contacts' tag list follows in place (`_follow_tags`), the chosen tag stays, a failure is told.
- What (narrowed by R-04, 2026-10-21): Tasks lays its whole list out again after every change (`ui/tasksview.py` `apply_item` → `_show_rows` → `TaskListModel.load(keep_rows=True)`, a model reset) and puts the selection, current task and scroll back by hand with blocked signals. Contacts applies each change to its row (correct); only after a save it fetches the whole tag list again for its numbers and ignores an error of that.
- User: the same jumpy behaviour D-146 removed from mail; slow with many items.
- The Calendar does not have this fault: it applies each fact to that event's blocks.
- Fix: apply each fact to its row, as the message list does.

### R-07 Tasks and Contacts are two copies of one design (M) (was R-08)
- **Status:** DONE in 1.1.245: `ui/pimlist.py` (`PimItemModel`, `MarkDelegate`, `PimListPage`) holds Delete, Move to, New tag…, the greying at the key press, the search delay, focus, the triangle's click and hover, the sync marks; the words are class attributes; `PimPage._failed` with `section_name` is the error line of all three sections (the Calendar shares nothing else: one selection, occurrences). Nothing the user sees changed. tasksview 909 → 821 lines, contactsview 732 → 647.
- What: about 33 methods exist in both `ui/tasksview.py` and `ui/contactsview.py` with the same job (menus, search timer, delete, edit, take away, selection); 13 also in `ui/calendarview.py`.
- User: a fault fixed in one is still there in the other.
- Fix: one shared base for the three sections; best done together with R-06.

### R-08 Many messages = many separate commands (M–L) (was R-10)
- **Status:** DONE in 1.1.247 (D-206): `message.*-many` per account and folder; on IMAP one command per 500 messages (prepare_*, COPYUID sets); each message's own step then runs unchanged; one error line for the failures. 1,000 moves: 0.37 s and 8 commands instead of 7.6 s and 3,002 (test server).
- What: delete, move, copy, archive and junk send one command per selected message (`ui/mainwindow.py` `_delete_rows`, `move_selected`, `copy_selected`, …); for IMAP each is its own server step.
- User: deleting or moving 1,000 messages takes 1,000 round trips; slow on a big selection.
- Also: each failed message is reported on its own (`_answers`), so 500 failures are 500 error reports.
- Fix: one command per folder with a set of messages (one UID MOVE or STORE for the set), facts told per message as now; one report for the failures.

### R-09 Every mail check reads every folder whole (M–L) (was R-16)
- **Status:** DONE in 1.1.248 (D-207): this computer's mail read once per check; a folder with a checkpoint asks the server only what changed (CONDSTORE, QRESYNC; nothing when the counter stayed), without a counter the marks without sizes; anything that does not add up asks for everything. 20,000 messages, nothing changed: 1.35 s → 0.37 s (QRESYNC), 1.49 s → 0.85 s (no counter, Kerio). Found: R-40.
- What: for each IMAP folder, each check asks the server for the marks of every message (`UID FETCH 1:*`), lists every local file of the folder, scans every folder of the whole store (`store.locate_all()`) and reads the tags file: the last two again for every folder (`sync/imap_sync.py` `_sync_folder`).
- User: with a large mailbox and many folders each check (every 5 minutes) does folders × messages work: slow checks, a busy disk.
- Fix: scan the store once per check; on servers that offer it (CONDSTORE, QRESYNC) ask only what changed since the last check. The three-way comparison of D-050 stays as it is.

### R-10 The window counts folders itself (M) (was R-04)
- **Status:** DONE in 1.1.250 (D-208): the index keeps every folder's numbers (triggers, format 10); every fact carries the new numbers of its folders; the window puts them in place and counts nothing. `folder.list` typed.
- What: the window keeps its own copy of every folder's numbers (`self._folders`) and adds and subtracts per fact (`_folder_fact`: +1, −1, unread changes). Business logic in the window (AGENTS.md 4.3), and a second truth beside the engine's.
- User: numbers that can drift from the truth until the next full count.
- Fix: the engine sends the folder's new numbers with each fact; the window only shows them.

### R-11 The folder list rebuilds itself (M–L) (was R-03)
- **Status:** DONE in 1.1.251 (D-209): a model of its own; plan() and populate() bring the list in line in place (add, remove, move, change); every case uses it; no guard, nothing put back by hand; the sidebar answers used; Show on startup reads the plan. Found: R-41.
- What: `FolderTreeModel.populate` clears the whole tree and builds it again; it runs at start (twice since the D-168 stopgap), when a pinned folder is renamed or deleted, and whenever a sync brings a folder that came or went (`_recount_mailbox`). Afterwards the selection, open branches and scroll are put back by hand, behind an `_updating_tree` guard (`ui/mainwindow.py` `_populate_tree`, `_folder_structure`).
- User: a flicker, a moment where the selection is gone; the D-168 problem (the startup choice lost) came from this.
- Also a full reload of every account's folder list plus a rebuild: every sidebar change (pin, rename or move a pin, a group, `_sidebar_call`), the Favorites changes of Settings (`_change_favorites`), Settings OK (`apply_preferences`).
- Fix: the tree takes each answer and each fact in its place (add, rename, remove one entry); built once, never rebuilt.
- Also: the Show on startup list of 1.1.195 builds a second, hidden tree to know what exists; with the tree knowing "what exists" and "what is shown" apart, that goes.

### R-12 Preferences are untyped text keys (S–M) (was R-11)
- **Status:** DONE in 1.2.253 (D-211): `Preferences`, `StartupView`, `Remembered` and the size readers in `ui/prefs.py`; the window, the Settings window, the appearance and the Operations window use them; a guard test (56 reads by name before, 0 now). Measured honestly: the damaged layout and reading pane values were corrected later in the old start too, so no visible "does not stick" was shown for them; the gain is that it can no longer happen.
- What: Settings returns a `dict[str, object]`; the window reads `QSettings` directly in 23 places outside `ui/prefs.py`, with its own defaults and its own splitting of values (for example `startup_view`).
- User: a default that differs between two places shows as a setting that "does not stick".
- Fix: one typed preferences object, read and written in one place.

### R-13 Settings OK applies everything again (M) (was R-15)
- **Status:** DONE in 1.3.255 (D-213): the window remembers the settings in use and OK applies only the groups that differ; the list holds every column and shows or hides them (a model reset lost the selection; Qt also loses it for an inserted column, so moves and hidden sections instead); `SettingsContext`; found and fixed: OK reset a dragged reading pane width, and closing Settings after a Scheduled page action fetched the list again.
- What: OK takes every setting into use whether it changed or not: the palette and the whole style sheet of the program, fonts and sizes, the list look, the shortcuts, and the folder list is rebuilt (`apply_preferences`). Changing the columns resets the message list instead of adding or removing a column (`set_columns`). The Settings window is handed 15 pieces of the window's inner state to work with.
- User: a flash and a moment of work after OK, also when nothing changed; after a column change the selected messages are no longer selected.
- Fix: apply only what changed, each change in its own small step; columns added and removed in place.
- Addressed meanwhile (1.1.209, D-179, the owner's bug report): the column reset no longer jumbles the order. A dragged column was kept twice (Qt's header and the saved order) and every reset applied the drag again; now the list's own columns are the one record. Still open here: adding or removing a column resets the list (selection lost).

### R-14 The current view is spread over loose fields and widgets (M) (was R-06)
- **Status:** DONE in 1.5.256 (D-214): `CurrentView` and `EntryKind` (`ui/currentview.py`); one step per change (`_change_view`); `view_spec` reads no widget; the filters answer the person only; 0 `blockSignals` in the window (9 on 1.3.255). Found: a sort, filter click or refetch searched text typed but not run.
- What: what the list shows is kept in about eleven separate fields (`_account_id`, `_folder`, `_combined`, `_combined_accounts`, `_combined_tag`, `_tree_kind`, `_tree_key_kind`, `_tree_path`, `_folder_role`, `_filter_tag`, `_advanced`) plus the ticked state of the filter buttons and the text of the search box; `view_spec()` assembles the view by reading the widgets. Kinds are free text ("favorite", "pinlink", "tagview", …).
- User: buttons and the list can disagree after a quick sequence of clicks.
- Fix: one "current view" value that the window changes in one step; the widgets only show it.
- With it: the 31 places that block a widget's signals to stop it from reacting to its own update (18 in the window) mostly go.

### R-15 The message list has two ways of holding rows (L) (was R-05)
- **Status:** DONE in 1.6.260 (D-218): one way for every view: the order of every message (`ui/messageorder.py`) and the rows loaded, by the message (`ui/messagelist.py`, out of `ui/models.py`); `message.order`, `message.combined-order`, `message.rows` (typed); facts heard during a question applied again to its answer; an arrival put before the message the engine names. Qt's tree view stays (no change the user would see; the owner left the choice to me).
- What: a folder view holds a slot per message (`_Rows.slots`, pages loaded anywhere); a Favorites, tag or search view holds only the rows loaded from the top (`_Rows.loaded`). Around it: generation, mark, shifts, heard, waiting pages, answers thrown away and asked again when a fact came between (`_request_page`, `_request_more`, `rearrange`).
- User (checked in the code, 2026-10-21): in Favorites, tags and searches the count line counted only the loaded rows (All Inboxes of 3,000: "100 messages") and the scrollbar grew while scrolling; after a sort a selected message far down was reached by loading every row above it at once (it did jump: "cannot jump" was wrong); in a folder a message gone from a part not loaded rearranged the whole list.
- Fix: the owner's design (D-147): one list of every message id of the view in order, plus a store of loaded rows; one path for every kind of view. Most of the re-asking machinery goes with it.
- Open with it: whether the list needs Qt's tree view at all.

### R-43 Every key acts on the part that has the focus (S–M) (new, 2026-10-21, the owner: right after R-15)
- **Status:** DONE in 1.7.262 (the owner chose 1.7; D-217): every key probed in every part; fixed: Delete and Shift+Delete in the folder list (they trashed and deleted the grey messages), the letters there, Delete in the sections' sidebars (it deleted their items); added: the letters and Insert in the reading pane. A guard test presses every key in every part.
- What: D-217 holds for Enter and Insert (1.5.259); 1.5.259's probe found Ctrl keys that act on a selection elsewhere while typing in the search field. Every key is checked against D-217 (which part has the focus, what is selected there) and fixed where it acts outside its part.
- The owner (2026-10-21): standard behaviour, as every program; never a key-by-key review with the owner.

### R-16 Duplicated code in the lower layers (S) (was R-13)
- **Status:** DONE in 1.8.263 (the owner chose 1.8; D-220): `core/credentials.py` `CredentialEntries`, `index/database.py` `IndexDatabase` (also the integrity check and `close`), `protocols/lineconnection.py` `LineConnection` (also sending, closing and the IDLE wait); every text unchanged. SMTP had no copy (standard library).
- What: the credential-store wrapper twice (`crypto/keystore.py`, `config/passwords.py`, the same `_reason`); opening SQLite and its transactions twice (`index/index.py`, `pimstore/index.py`); the protocol line reader twice (`protocols/imap.py`, `protocols/pop3.py` `_fill`).
- Fix: one shared copy each.

### R-17 The main window does everything (L) (was R-02)
- **Status:** DONE in 1.9.264 (the owner chose 1.9; D-221): `ui/mainwindow.py` 3,444 → about 1,000 code lines (the screen, the actions, the menus); eleven parts, each the owner of its data (`folderpane`, `messagelistpane`, `messagecolumns`, `searchfilters`, `messageactions`, `mailtags`, `readingpane`, `composing`, `mailchecking`, `statusreport`, `mailstate`); the Tab order stated by the window (the parts are made in another order); unused code gone. All 769 window tests run before and after (the same 12 known failures, one fewer); `tests/ui/test_ui_parts.py` guards it.
- What: `MainWindow` is one class of 3,481 lines with 266 methods: folder list, message list, search, filters, columns, every mail action, operations, tags, groups and pins, settings, timers, tray, import.
- User: nothing directly; every change risks side effects elsewhere in the window, which is where most faults of the last weeks came from.
- Fix: split into parts with one job each (folder list, message list and view, mail actions, search and filters, columns, operations), the window only putting them together.

### R-18 Files that are too large (S–M) (was R-14)
- **Status:** skipped (the owner, 2026-10-22): no file of the program is over 2,000 lines (the largest, `ui/mainwindow.py`, 1,291 on 1.9.267).
- What: `ui/models.py` 1,568 lines (two models in one file), `engine/commands_mail.py` 1,078, `ui/settingsdialog.py` 903.
- Meanwhile (R-15, 1.6.260): the message list left `ui/models.py` (now the folder list only, 594 lines) for `ui/messagelist.py` (1,220 lines, still large) and `ui/messageorder.py`.
- Fix: split, mostly as part of R-17 and R-15.

### R-19 The engine and the window do not know each other's types (L) (was R-01)
- **Status:** DONE in 1.9.275, in five rounds (the owner: rounds until it is fully done). The calls: done in 1.9.273, in three rounds (the owner, 2026-10-22). Round 1 DONE in 1.9.270 (mail: reading pane, actions, writing, mail checks, message list, tags, folder list and Sidebar page; 40 calls, 106 → 66). Round 2 DONE in 1.9.271 (accounts and settings, rules, tags, key, import; 35 calls, 66 → 31); found: certificate.inspect refuses kind https, so Look at the server's certificate in the sections' Add account… always fails (since 1.1.212; told to the owner, not changed; that one call stays untyped until decided). Round 3 DONE in 1.9.273 (calendar, contacts, tasks, address completion; 31 calls, 0 left; `tests/test_typed_calls.py` guards it). The answers kept by the windows were still read by guessed names (485 getattr): round 4 DONE in 1.9.274 (mail: rows, accounts, folders, tags, change notices; 485 → 305, the mail parts 13, all Qt painting objects or the window's own actions); round 5 DONE in 1.9.275 (calendar, contacts, tasks, scheduled messages, rules, certificates, reader, printing, tags, the list's painting: 305 → 22 getattr, each with its reason `# getattr:` on its line: PySide's stubs give a style option's fields no type, the window's own actions by name, settings and dataclasses compared or copied field by field, Qt's colour roles by name, ctypes.windll; `tests/test_typed_calls.py` refuses any other). The certificate fault was fixed first in 1.9.272 (the owner: an obvious fix needs no decision).
- What: the window calls every command by a text name with a dictionary of values and gets the answer back as an untyped `object`; it then reads fields by name (`getattr(row, "message_id")`), about 590 times (`ui/mainwindow.py` 124, `ui/models.py` 83, `ui/tasksview.py` 47, …). The engine's answers are typed dataclasses, but the type is lost in `ui/backend.py` (`Success = Callable[[object], None]`).
- User: nothing directly; but a renamed or mistyped field is found only when someone clicks, never by Pyrefly. Strict typing is in effect switched off across the most important border of the program.
- Fix: a typed call per command (the parameters and the answer as the engine's own dataclasses), so Pyrefly checks every use.
- Done alongside every item (each types the commands it touches); this item is only what is left at the end.

### R-20 The key dialog guessed instead of naming the cause (S) (new, 2026-10-20)
- **Status:** DONE in 1.1.197 (D-170)
- What: at start the key check threw away the credential store's answer; "the key is not there" and "the wallet could not be asked" showed the same text ("Perhaps the wallet is locked…").
- User: the dialog at the owner's start on 2026-10-20 (Try again worked at once), with no way to know why.
- Fix: two kinds of failure, each with the store's own reason, shown in the dialog. The cause of that failed start is still to be found when it shows again: the dialog now names it.

### R-21 The Scheduled messages page rebuilds its list after every action (S) (new, 2026-10-20)
- **Status:** DONE in 1.9.268 (D-224; the owner chose it before R-19): the answers carry the message's line as it is now; the page puts it in place, selected, or takes it out and selects the next; showing the page again brings the list in line in place; a failed Send now says why (it said "Done").
- What: after Send now, Take back, Pause, Resume and Stop, the page asks for everything again, empties the list and fills it anew, and selects the first row (`ui/scheduledpanel.py` `_done` → `reload`). Since 1.1.198 a new message is added in place (D-171); the other actions still rebuild.
- User: the selection jumps to the first row after each action.
- Fix: apply each change to its own row, as D-146 asks.

### R-22 The status line is sometimes empty after a snooze (S) (new, 2026-10-20)
- **Status:** DONE in 1.9.266 with R-45: a test fault, proven (the answer made 0.3 s slower fails it every time): the row leaves at the engine's fact, the line comes with the answer later; both snooze tests now wait for the line itself. The program was right.
- What: `tests/ui/test_ui_schedule.py::test_the_snooze_button_offers_ready_made_times` fails now and then (0.5.170, and twice on 2026-10-20). On 1.1.201 the output showed the cause's symptom: the message was snoozed (`message.snoozed` lists it) but the status line was empty instead of "Snoozed 1 message(s)". So something clears the status line after the snooze's own text, depending on timing.
- User: an action that worked does not say so (now and then).
- Fix: find what clears the status line (which answer or timer writes it after the snooze), and fix that order; not a retry or a wait in the test.

### R-40 A message removed on the server gives a false error line (S) (new, 2026-10-21, found in R-09)
- **Status:** DONE in 1.1.249 (the owner: before R-10): `_remove_gone` takes the removed message out of the check's list of the folder, so it is never tried as an upload; no error line, nothing sent back.
- What: when a check removes here a message the server no longer has (`sync/imap_sync.py` `_remove_gone`), the folder's list of messages of that check still holds it, so `_upload` then tries to send it to the server and reports "A local message could not be read for sending: Message not found" (seen on 1.1.247 and 1.1.248, the fake server).
- User: an error line at every check in which another computer or the phone deleted mail.
- Fix: the cause, `_remove_gone` takes the removed message out of the folder's list too.

### R-41 A pinned folder that is renamed loses its Favorites entry (S) (new, 2026-10-21, found in R-11)
- **Status:** DONE in 1.1.252 (D-210; the owner: before R-12): every rename moves the pins along (and those of the folders inside it) before it is told; the window keeps the pin's entry and selection.
- What: the sidebar file keeps a pin by the folder's path; a rename (here, on the server, followed by a check) never moves the pin along (`engine/commands_sidebar.py`, `config` Sidebar), so the pin points at a name that no longer exists and its Favorites entry goes (the same before R-11).
- User: a pinned folder vanishes from Favorites when it is renamed, and has to be pinned again (its own name and place in the order lost).
- Fix: the engine moves the pins along with every rename of a folder (and the folders inside it), in the same step as the rename is told.

### R-42 An imported rule that moves to a standard folder by its name makes an ordinary folder of it (S) (new, 2026-10-21, found while measuring the start)
- **Status:** DONE in 1.2.254 (D-212; the owner: fix it).
- What: the importer mapped the export's standard folders (Junk E-mail, Deleted Items, Sent Items …) onto Chrono Mail's own for the mail, but a rule naming such a folder kept the name (`emclient_source._move`); at its first run the rule made an ordinary folder of that name.
- User: the owner's spam went to an ordinary folder "Junk E-mail" (32 messages, counted as unread) instead of Junk.
- Fix: one table for the mail and the rules. Not changed by it: the owner's existing rule and the 32 messages (the owner fixes those by hand).

### R-44 Twelve window tests failed for weeks (new, 2026-10-21, found in R-17; the owner: now)
- **Status:** part 1 DONE in 1.9.265 (D-222, D-109): the four faults the tests found in the program: no warning for a missing file in an action on several messages (since R-08); Tasks fetched every row (R-02 on paper only); a calendar change asked for every calendar (since R-27), also read-only now in the fact; the minimum width at start, the count line with "…". Part 2 DONE (tests only, private): rewritten the tests that fell behind the program (folderchoices stand-in, the Menu items in theme and rules (drop the rules copy), the selection colour reads the unread dot, the narrowest toolbar looks for keys on the window (D-217), tray reads the file (D-211), the grey header icons need a fair colour check).

### R-45 Every test unambiguous; no test can hang (new, 2026-10-21, the owner: now)
- **Status:** DONE in 1.9.266 (D-223, AGENTS.md 4.5.5). Proven: 2,495 of 2,495 tests pass each in a process of its own (`tests/run_isolated.py`, 278 s), and all pass in one process in normal, reverse and random order (`tests/runorder.py`).
- Found and fixed (all test faults; the program was right): window tests left their windows alive (an old message window's autosave failed in a later test and its warning box waited for a click: the hang) and left the dark colours on (the key recovery test); the snooze tests read the status line before the answer (R-22); three IMAP state tests left a program-wide setting lowered (reverse order failed); a config test named its cases with random ids; the legal names test read private files and found the name inside "SlowItemClient"; the calendar list test asked for more than the cap of D-174; the registry test missed `dav.link`.
- Made: a message box no test answers fails the test at once; every test stopped after 20 s with where it waited printed (`tests/conftest.py`, `faulthandler_timeout`).
- Found for the owner: a failed automatic save of a message window showed a warning box, again at every next try; changed in 1.9.267 (the owner): red on the window's bottom line.

## Audit round 3: the server copy (rule 1, 2026-10-20)

*The owner's rule: an account here is a faithful copy of the account on its server. 1.1 every action on server data is done on the server first, here only after it confirms; 1.2 every change on the server shows here at the next sync; 1.3 offline, an action is done here, survives a restart and is sent when the connection is back; 1.4 a refusal of the server is undone here and told in plain words; 1.5 two computers on one account end up showing the same. Exceptions: local data, POP3 by nature, local-only data the owner approved case by case. Reading only: nothing was changed. "Read" = read from the code, not tested. The owner decides the order; the Windows computer checks the list with its own tests.*

### R-23 Appointments, tasks and contacts are saved here first (L)
- **Status:** DONE in 1.1.225 (engine) and 1.1.226 (window, D-194).
- Accounts: IMAP or POP3 with a calendar server, and Calendars and contacts only.
- Now: `event.add/edit/delete/move/import` (`engine/commands_calendar.py`), `task.*` (`engine/commands_tasklist.py`), `contact.*` (`engine/commands_addressbook.py`) only write the item here (`store.put`, `store.delete`, `store.move`); `sync/davsync.py` `_push` sends it at the next sync. The window starts no sync after a change.
- Breaks: 1.1.
- Owner notices: makes an appointment, the phone shows it only after the next check (up to 5 minutes; not at all while Chrono Mail is closed). A change the server would refuse looks done here.
- Sure: read; no engine command of the three files contacts the server.
- Fix: every item action on the server first (PUT/DELETE with the etag, a move as PUT in the new calendar, then DELETE), here after the server confirms; offline: here, kept by the existing comparison, sent when the connection is back. Touches the three command files, `sync/davsync.py`, `protocols/dav.py`, the window's error handling.

### R-24 A change the server refuses later is never undone here (M)
- **Status:** DONE: calendars, tasks and contacts in 1.1.225 and 1.1.226 (D-194), mail in 1.1.227 (D-195)
- Accounts: IMAP, and every account with a calendar server.
- Now: a change made offline (IMAP) or any item change (DAV, R-23) that the server refuses at the sync is written as a line in the report every sync, and the change stays here for ever: `sync/davsync.py` `_push` (`report.errors`), `sync/imap_sync.py` `_move_pass`, `_push_flags`, `_create_on_server` (`notes`, `counts.failed`).
- Breaks: 1.4, 1.5.
- Owner notices: an appointment added offline to a read-only calendar stays here, never reaches the server, and an error comes back at every check; the second computer never shows it.
- Sure: read.
- Fix (the owner, 2026-10-20; replaces "undo here"): a lasting refusal becomes a row in Settings > Sync conflicts; the item keeps Chrono Mail's version until the person chooses Use Server data, or leaves it. A passing failure is tried again at the next sync. See "Sync conflicts" below. Touches the two sync files.

### Sync conflicts: the owner's design for R-23 and R-24 (discussion of 2026-10-20; agreed, no changelist yet)
- A sync conflict is only the same data changed on both sides. Changes to different data are both kept, without a word. Finding it needs the version both sides last agreed on, compared field by field.
- A lasting refusal of the server (read only, no permission, the item rejected) that leaves the data different is also a sync conflict: it can only be settled with the server's data, or left; it is never undone here by itself (this replaces "undone here" of rule 1.4). A passing failure (no connection, a timeout, the server busy or failing: HTTP 5xx) is no conflict: it is tried again at the next sync.
- Deleted on the server and unchanged here: deleted here too. Deleted on the server and changed here: a sync conflict (Use Chrono Mail puts it back on the server, Use Server data deletes it here; the owner, 2026-10-20). Deleted here and the delete refused: a sync conflict.
- The agreed version: today the sync keeps only a fingerprint (`ItemLink`: id, etag, digest), which cannot show what changed. When an item is changed here, the version both sides last agreed on is kept beside it (encrypted, like the items) until the server has the change; an item changed only on the server needs none.
- While a row is open: the item is not sent to the server; a new change here or on the server updates the same row, never a second one. Online with server first (R-23), "changed meanwhile" (HTTP 412) is compared at once: no clash, merged and sent again; a clash, a row.
- The alarm of an item in conflict is Chrono Mail's version (the owner, 2026-10-20).
- The icon is added to the program's own Breeze copy (`ui/resources/icons`, D-087), like every icon; nothing comes from the system.
- Both sides made the same change (the same field to the same value): no conflict, no row.
- An item changed here before this design was built has no agreed version: when the server changed it too, the whole item is one row (a whole side is chosen).
- What a field is (the owner, 2026-10-20, as the Linux computer and the Windows computer advised): each property (title, start, end, place, description) is a field, a text is one field (never merged inside); each changed occurrence of a repeating appointment is its own item; lists (skipped dates, categories and tags, phone numbers, e-mail addresses, attendees) are merged per entry: additions and removals of both sides are kept, a conflict only when the same entry changed on both sides; all alarms of an item together are one field; a property of another program is a field by its name and is always kept.
- Mail marks (the owner): each mark (read, flagged, replied, each tag) is a field, so a row only when one side set a mark and the other cleared it.
- Changes waiting for the server (the owner: both): a count in the status line ("30 changes wait for the server") and a small, quiet icon on each waiting item, gone once sent. Which icon is still to choose (the Breeze clouds do not read at 22 px).
- The Windows computer reviewed this design twice on 2026-10-20; its points are in.
- Until settled, the item shows what Chrono Mail holds, with Breeze `emblem-warning` (the triangle with "!") on it. No question boxes during the work; at most a line in the status line or the Operations window.
- **Settings > Sync conflicts** (the one name everywhere): a table, one row per item (tick box, triangle, kind, name, account, which fields differ), Check all at the top, buttons **Use Chrono Mail**, **Use Server data**, **Invert selection**. The buttons settle every clashing field of every ticked item, all from that side. "Use Chrono Mail" is greyed for a refusal.
- A double click on a row opens a popup with every detail that helps to choose (dates, times, folder, account), one row per clashing field with a choice Chrono Mail / Server, "All from Chrono Mail", "All from Server", Apply and Cancel.
- No Ignore button: leaving a row is ignoring it.
- The sidebar shows an entry **Sync conflicts** (with its count) only while there are any; a click opens Settings > Sync conflicts.
- Rejected: last change wins (it depends on clocks agreeing), server or Chrono Mail always wins (silent loss), a question every time (nagging, Thunderbird), a bug icon (too much work for its gain), a colour mark (clashes with the person's own colours).

### R-25 A calendar, task list or address book deleted on the server stays here (S–M)
- **Status:** DONE in 1.1.234 (step A, D-196): only on the server's confirmation of the deletion (the owner); a home that could not be listed is an error (found while doing it: it looked like "no calendars"); changed here: a sync conflict, Use Chrono Mail makes it again on the server
- Now: `sync/davsync.py` `sync_dav`, end: a collection gone from the server is kept here, "no longer tied to it" (`update_collection(server={})`).
- Breaks: 1.2, 1.5.
- Owner notices: deletes the calendar "Work" on the phone; Chrono Mail keeps it with all its appointments, and changes to it go nowhere.
- Sure: read.
- Fix: it goes here too (its items to the deleted items, as the other removals); with the same guard against a short listing (D-079). Touches `sync/davsync.py`.

### R-26 A calendar renamed or recoloured on the server keeps its old name and colour here (S)
- **Status:** DONE in 1.1.221 with R-27 (D-192)
- Now: `sync/davsync.py` `sync_dav` takes the server's name and colour only when it makes the collection here; afterwards only read-only is followed.
- Breaks: 1.2, 1.5.
- Owner notices: renames "Calendar" to "Home" on the server; Chrono Mail still says "Calendar".
- Sure: read.
- Fix: every sync takes the name and colour the server gives. Touches `sync/davsync.py`, `pimstore/store.py`.

### R-27 Rename, Colour and Edit colours of a server calendar are done here only, and lost on untick (M)
- **Status:** DONE in 1.1.221 (D-191, D-192, D-193): one palette per account, kept with the account; name and colour on the server first, followed from it, kept with the account when waiting or refused; Rename and Colour greyed when read only
- Now: `collection.edit` and `collection.set-palette` (`engine/commands_pim.py`) change only `collection.json` here (`pimstore/store.py`); unticking the calendar in the account window deletes that copy (`engine/commands_dav.py` `dav_link`), so re-ticking makes a new copy without them (the Windows computer's report).
- Breaks: 1.1, 1.5; for the palette: the owner's design.
- Owner notices: renames a calendar here, the phone keeps the old name; unticks and ticks a calendar, all its colours are gone.
- Sure: read, and the Windows computer's report.
- Fix (agreed in the chat, not yet approved with Proceed): name and colour on the server first (PROPPATCH `displayname` and `calendar-color`, Kerio to be checked live), every sync takes them; read-only calendars: Rename greyed; the palette with the account, the calendar's own colour too where the server keeps none. Touches `protocols/dav.py`, `sync/davsync.py`, `engine/commands_pim.py`, `config/model.py`, `pimstore/store.py`, the palette and event editors.

### R-28 Deleting a server calendar here deletes only the copy, and it comes back (S–M)
- **Status:** DONE in 1.1.235 (step B, D-197). The owner (2026-10-21): Delete removes it from the server, after a clear question; every program that syncs with that server follows by itself. Deleted here only after the server confirmed the delete; offline: here at once, remembered, deleted on the server at the next sync (never made again meanwhile); changed on the server meanwhile: nothing deleted, a sync conflict; refused for good: a sync conflict (Use Server data brings it back, Retry sync); Delete greyed for a read-only one, with a hover text (untick stops keeping it here).
- Now: `collection.delete` (`engine/commands_pim.py`) removes the copy here; the folder is still on the server and not left out, so the next sync makes it here again (without its colours).
- Breaks: 1.1.
- Owner notices: deletes the calendar "Work"; five minutes later it is back.
- Sure: read.
- Fix: Delete deletes it on the server first (for every device), after a clear question; or Delete is only for local calendars and untick is the way to stop syncing one. Touches `engine/commands_pim.py`, `protocols/dav.py`, `ui/collectionpanel.py`.

### R-29 A new calendar, task list or address book is always made on this computer (M; a missing action)
- **Status:** DONE in 1.1.236 (step C, D-198). Kerio makes calendars and task lists (listed at an address of its own) but no address books over CardDAV: refused in plain words. Agreed 2026-10-21: the New window gets an Account choice set to the account right-clicked (the local ones on the empty area); a server account: made on the server first; offline: here, made on the server with its items at the next sync; refused for good: a sync conflict with Keep here only (moved to the local calendars).
- Now: `ui/collectionpanel.py` `new_collection` calls `collection.create` without an account, so it is always made under the local account (`CreateParameters.account` defaults to `LOCAL_ACCOUNT_ID`), also when the person right-clicks a calendar of a server account. Making one on the server does not exist.
- Breaks: 1.1 (the person expects it in the account they chose).
- Owner notices: right-clicks the Kerio account's calendars, New calendar…; it appears under the local calendars and never on the server.
- Sure: read.
- Fix: New asks or follows the account it was opened on; for a server account MKCALENDAR / extended MKCOL on the server first. Touches `ui/collectionpanel.py`, `engine/commands_pim.py`, `protocols/dav.py`.

### R-30 An IMAP folder deleted or renamed on the server stays here; a rename doubles its mail (M)
- **Status:** DONE in 1.1.237 (D-199): only on the server's confirmation (an exact-name LIST); a rename recognised by UIDVALIDITY (Dovecot and Kerio keep it, checked live); changes here: a sync conflict; standard folders kept and made again (the owner). A rename on the server that leaves the new name unsubscribed is seen as a delete (the folder is not shown, R-34).
- Now: `sync/imap_sync.py` `sync_imap` makes local folders from the server's list but never removes one that left it; `_create_on_server` skips a folder that was synchronised before. A folder renamed on the server arrives as a new folder and its mail is downloaded again (the old folder's messages are mapped, so they are no match), while the old folder stays.
- Breaks: 1.2, 1.5.
- Owner notices: renames "Projects" to "Work" in webmail; Chrono Mail shows both, each with the same mail.
- Sure: read; to be proven with a live test on the test account.
- Fix: a folder gone from the server's list goes here (to `.deleted`, with the same guards as for messages: never when the listing looks short); a rename is recognised (same UIDVALIDITY, or the same messages) and done here as a rename. Touches `sync/imap_sync.py`, `sync/imap_state.py`.

### R-31 IMAP folder rename and delete are refused offline (M)
- **Status:** DONE in 1.1.240 (D-200). Answered by the owner's rule D-193 1.3 (and again 2026-10-21): allowed offline, kept, done on the server when the connection is back. Also fixed: a folder deleted offline used to be only hidden by the next check, never deleted on the server.
- Now: `engine/imap_online.py` `rename_folder`, `delete_folder` raise "only renamed or deleted while the server can be reached" (D-118).
- Breaks: 1.3.
- Owner notices: on a train, renames a folder: refused.
- Sure: read.
- Fix if the owner wants it: done here, remembered (the state file), done on the server at the next check, undone with a message if refused. Touches `engine/imap_online.py`, `sync/imap_sync.py`, `sync/imap_state.py`.

### R-32 Snooze time and archive origin exist only on this computer (M)
- **Status:** works as intended (the owner, 2026-10-21, D-201): the server has no such function, so they stay on this computer only; no code.
- Now: kept in the message's properties file (`store/properties.py`, D-176). For IMAP the message itself is in the Snoozed or Archive folder on the server.
- Breaks: 1.5.
- Owner notices: snoozes a message on the Linux computer; the Windows computer shows it in Snoozed but never brings it back. Unarchive on the Windows computer sends it to the Inbox instead of where it came from.
- Sure: read.
- Fix: depends on the owner's choice. Touches `engine/commands_schedule.py`, `engine/commands_actions.py`, the IMAP sync.

### R-33 Tags on Kerio, and every tag's colour and shortcut, exist only on this computer (M)
- **Status:** works as intended (the owner, 2026-10-21, D-201): tags travel where the server keeps keywords (Dovecot); on Kerio, and for colours and shortcuts, this computer only; no code.
- Now: Kerio keeps no keywords, so mail tags there stay here (D-132, `engine/imap_online.py` `set_tags`); a tag's colour, shortcut and kinds live in `tags.json` here for every account; the tag names of appointments, tasks and contacts do travel (CATEGORIES).
- Breaks: 1.5.
- Owner notices: tags a mail "Invoice" on the Linux computer; the Windows computer shows no tag (Kerio), or the tag in a default colour (DirectAdmin).
- Sure: read.
- Fix: depends on the owner's choice.

### R-34 IMAP subscribe and unsubscribe, and contact groups, do not exist (missing actions)
- **Status:** DONE in 1.1.241 for the IMAP part (D-202). The owner: the ticks in Edit account > Folders are this computer's only (no subscribe or unsubscribe from Chrono Mail); the server's list is followed: a folder taken off it goes here after one last sync, one put on it comes; ticks, standard folders and mail only here win. Found on the way: an unsubscribed standard folder was silently no longer kept in step (fixed). Contact groups moved to R-39 (the owner: later).
- Now: only subscribed folders are shown, and nothing in Chrono Mail subscribes or unsubscribes one (except making and renaming folders). Contact groups (vCard 4 `KIND:group`, Apple's group cards) are not supported: nothing reads `MEMBER`; how such a card shows now is not checked.
- Breaks: 1.1, 1.2 (these server things cannot be changed or seen here).
- Sure: read.
- Fix: the owner decides whether and when.

### R-39 Contact groups (new, 2026-10-21, split from R-34; the owner: later)
- **Status:** DONE in 1.10.277 (D-227; the owner chose 1.10). Kerio checked live first: it keeps vCard 4's form as written and makes Apple's a group of its own, so new groups take Apple's form unless the address book uses vCard 4's. Found on the way and fixed: a vCard 4 card was written back as 3.0 at every change. Found, not fixed (outside R-39): Kerio answers a plain GET of an existing contact with 404, which the "changed on the server meanwhile" paths of `sync/davitems.py` read (told to the owner).
- Now: a group card (vCard 4 `KIND:group` with `MEMBER`, Apple's `X-ADDRESSBOOKSERVER-KIND` / `-MEMBER`) shows as a contact with a name and no address; its lines are kept and sent back unchanged (nothing lost).
- To build: groups under each address book, their members, members added and removed on the server first, writing to a group puts its members in To. Not checked: whether Kerio keeps group cards.

### R-35 Found in passing: a move made offline can remove other deleted-marked mail on servers without MOVE and UIDPLUS (S)
- **Status:** DONE in 1.1.238: the cause was `ImapClient.uid_expunge`, which fell back to a plain EXPUNGE; it now refuses without UIDPLUS, `uid_move` removes the original only with UIDPLUS, and the online move uses `uid_move` too. Neither test server is affected (Dovecot has MOVE, Kerio UIDPLUS; checked live).
- Now: the sync sends moves made offline with `ImapClient.uid_move` (`protocols/imap.py`); without MOVE it copies, marks and expunges; without UIDPLUS that expunge is a plain EXPUNGE, which also removes every other message in the folder marked deleted (by another program). D-117 says the folder is never expunged then. The online move (`engine/imap_online.py` `move`) does check.
- Owner notices: rarely anything; another program's "deleted but not yet expunged" mail in that folder is gone.
- Sure: read.
- Fix: the same check as the online move. Touches `protocols/imap.py` or `sync/imap_sync.py` `_move_pass`.

### R-37 The sync-conflict commands use a mailbox's store directly (S) (new, 2026-10-21)
- **Status:** DONE in 1.1.239 (the owner: "through the mailbox"): `Mailbox.sync_conflicts`, `forget_sync_conflict`, `keep_sync_conflict_here`, `waiting_for_server`; `commands_conflicts.py` and `commands_mail.py` use them; the guard passes again.
- What: `engine/commands_conflicts.py` hands `mailbox.store` to the mail conflict functions (`listed`, `forget`, `keep_here`, `imap_waiting`); `tests/test_mailbox_guard.py::test_the_store_or_index_is_handed_over_only_where_allowed` fails since 1.1.227.
- Fix: `Mailbox` methods for the conflict list (list, forget, keep here) and what waits, so the guard holds again.

### R-38 Without UIDPLUS a message moved or deleted comes back here marked deleted (S) (new, 2026-10-21)
- **Status:** dropped (the owner, 2026-10-21): servers that old are not supported; no extra work for them.
- What: on a server without UIDPLUS the original of a move (or a delete for good) stays on the server marked deleted (D-117, R-35); the next check downloads it again as a new message, so it shows twice (in its old folder, marked deleted). Neither test server is affected.
- Fix to discuss: a message the server marks deleted that is not known here is not downloaded (it is on its way out).

### R-36 Every request to a calendar server opens a new secure connection (S)
- **Status:** DONE in 1.1.223. Found while measuring: Kerio 8.5.2 answers every request with "Connection: Close", so with Kerio nothing is reused and nothing is gained. The slow first sync had another cause: each request looked up the Kerio test server's name again, 0.25 s each on the Linux computer (LLMNR; the name is not in /etc/hosts). Fixed in 1.1.224 (the owner's Proceed): the address is looked up once per client; 20 requests through the Kerio test server's name 0.31 s instead of about 5.4 s.
- Was: `protocols/dav.py` `DavClient.request` made a connection (TLS handshake included), sends one request and closes it, for every request.
- Owner notices: the first sync of 1.1.221 sent about 450 changed appointments and took minutes (seen 2026-10-20). After R-23 every single change goes to the server at once, so each one pays the handshake.
- Fix: keep one connection per client open and reuse it; when the server has closed it meanwhile, make a new one and send again only when the request never reached the server (never a write twice). Touches `protocols/dav.py` and its tests.

### What is right (read; most also covered by tests and live tests)
1. IMAP, online: marks, tags (servers with keywords), move, copy, archive, junk, snooze (the move), delete, delete for good, Empty Trash and Junk, Mark all as read, drafts, the Sent copy and the actions of rules: on the server first, here after it confirms; a refusal is an error and nothing changes here (D-117, D-118).
2. IMAP, offline: those actions are done here, survive a restart (the comparison of D-050, nothing queued) and are sent at the next check; a folder made offline is made on the server then.
3. IMAP folders online: make, rename, delete, empty: on the server first.
4. IMAP from the server: new mail, marks, tags, removals (with the guards) at every check; push for the Inbox.
5. Calendar servers to here: new, changed and deleted items, and new calendars on the server (D-079, D-183).
6. Calendar items made offline are kept and sent later (they always are, R-23).
7. Unticking a folder or a calendar in the account window: a choice of this computer, the server untouched; right.
8. POP3 (D-027) and the local account: right by nature.
9. Local-only by the owner's choice: Scheduled messages (D-172), the Outbox, sidebar and settings, rules.

### Questions for the owner (from the audit)
1. 3.1 Conflict: the same item changed here offline and on the server: the server wins and ours is kept as a "[Conflict]" copy (now), or ask?
2. 3.2 Should the window show what still waits for the server?
3. 3.3 Where should these live (snooze time, archive origin, Kerio tags, tag colours and shortcuts, the calendar palette)? Answered 2026-10-21 (D-201): synced where the server has the function, else on this computer only.
4. 3.4 IMAP folder rename and delete offline: answered by D-193 1.3: done later (R-31, D-200).
5. 3.5 Delete of a server calendar, task list or address book: answered 2026-10-21: deleted on the server (R-28).
6. 3.5 New calendar: answered 2026-10-21: an Account choice set to the account it was opened on (R-29).

## Dependencies

- R-04 decides how urgent R-06 is.
- R-06 and R-07 belong together (the sections); R-07 after R-06.
- R-08 and R-09 are both about the work per mail action and per check on IMAP; they can share a changelist round.
- R-10 and R-11 belong together (the folder list and its numbers); R-11 after R-10.
- R-13 needs R-12 (the preferences) and R-11 (no rebuild of the folder list).
- R-14 and R-15 belong together (the view and its rows); R-15 after R-14.
- R-17 is easier after R-10 to R-15, which move code out of the window anyway; R-18 after R-15 and R-17.
- R-19: typing goes along with every item; what is left at the end is R-19.

## Done log

*Newest first: date, item, version, one line.*

- 2026-10-22, R-39, 1.10.277: contact groups, kept in step like every card; the composer offers a group by name; a vCard 4 card stays vCard 4 (D-227). The refactor list is done.

- 2026-10-22, outside the refactor (the owner), 1.9.276: Received is when the mail server received the message, as Thunderbird (D-226); "1 message".
- 2026-10-22, R-19 round 5, 1.9.275: R-19 done: every answer is kept as its type and read by its fields; 22 getattr left, each with its reason; two guards.
- 2026-10-22, R-19 round 4, 1.9.274: the mail parts keep the engine's answers as their real types and read them by name (485 → 305 guessed reads); nothing visible changed.
- 2026-10-22, R-19 round 3, 1.9.273: the calendar, contacts and tasks windows typed; no untyped engine call left in the window, a guard keeps it so. 1.9.272: the sections' certificate look works (kind https).
- 2026-10-22, R-19 round 2, 1.9.271: every engine call of the account, settings, rules, tags, key and import windows is typed (35 calls; 31 left); found: the sections' certificate look always fails (kind https).
- 2026-10-22, R-19 round 1, 1.9.270: every engine call of the mail parts is typed (40 calls; 66 left); nothing visible changed.
- 2026-10-22, outside the refactor (the owner), 1.9.269: Drafts, Outbox, Scheduled and Snoozed show how many messages wait; the other folders their unread messages (D-225).
- 2026-10-22, R-21, 1.9.268: the Scheduled messages page changes only the line an action touched, the selection stays; a failed Send now says why (D-224). R-18 skipped (the owner: no file over 2,000 lines).
- 2026-10-21, R-45 (and R-22), 1.9.266: every test unambiguous and never hanging; 2,495 of 2,495 pass alone and in any order (D-223).
- 2026-10-21, R-44 part 2: the tests that fell behind the program rewritten; all twelve old failures are gone.
- 2026-10-21, R-44 part 1, 1.9.265: four program faults found by old failing tests fixed at their cause; the count line gets "…" (D-222).

- 2026-10-21, R-17, 1.9.264: the main window split into parts with one job each, every piece of data with one owner; nothing visible changed (D-221).

- 2026-10-21, R-16, 1.8.263: the wallet access, opening an index file and reading from a mail server each exist once (D-220); nothing visible changed.

- 2026-10-21, R-43, 1.7.262: every key acts on the part with the focus; Delete in the folder list and the sidebars is the folder's or calendar's (asked first), never the messages'; letters jump to folders there (D-217).

- 2026-10-21, R-15, 1.6.260: the message list holds every view one way (the order of every message, the rows by the message); Favorites, Tags and searches count every message at once; nothing is ever asked again (D-218); R-43 added (the keys).

- 2026-10-21, R-14, 1.5.256: what the list shows is one value changed in one step; typed text never searched by a sort or a filter; no signals blocked (D-214).

- 2026-10-21, R-13, 1.3.255: Settings OK takes into use only what changed; columns shown and hidden in place, the selection stays; one SettingsContext (D-213).

- 2026-10-21, R-42, 1.2.254: an imported rule that moves to a standard folder by its name goes to Chrono Mail's own (D-212).

- 2026-10-21, R-12, 1.2.253: every setting is read and written in one place, typed; a guard test keeps it so (D-211).

- 2026-10-21, R-41, 1.1.252: a renamed folder takes its pins in Favorites along; the pin keeps its entry and selection (D-210).

- 2026-10-21, R-11, 1.1.251: the folder list is built once and never again; every change happens in place (D-209); found R-41.

- 2026-10-21, R-10, 1.1.250: the folder numbers are the engine's, kept by the index and sent with every fact; the window counts nothing (D-208).

- 2026-10-21, R-40, 1.1.249: a message removed on the server no longer gives a false "could not be read for sending" line.

- 2026-10-21, R-09, 1.1.248: a check reads this computer's mail once and asks the server only what changed where it can tell (D-207); found R-40.

- 2026-10-21, R-08, 1.1.247: actions on several messages go as one command per folder, on IMAP in groups of 500; one error line (D-206).

- 2026-10-21, R-07, 1.1.245: Tasks and Contacts share one copy of their list actions, greying, triangle and search delay (`ui/pimlist.py`); nothing visible changed.

- 2026-10-21, R-06, 1.1.244: Tasks changes only the row and heading of the task that changed (never a reset); Contacts' tag list follows in place (D-146 now done everywhere).

- 2026-10-21, R-05, 1.1.243: one set of server boxes for Add and Edit; a typed port stays; Test connection in Edit never asks for the password again (D-204).

- 2026-10-21, R-04, documents only: D-146 step 5 holds for Calendar and Contacts, not for Tasks (a reset per change); D-146 corrected, R-06 narrowed.

- 2026-10-21, R-03, 1.1.242: Flagged, Unread, With attachments, Tagged with and Advanced search's days and sizes narrow Favorites and Tags views (D-203).

- 2026-10-21, R-34, 1.1.241: the server's list of folders to show is followed (a folder taken off it goes here after one last sync; ticks stay local; standard folders always kept in step) (D-202); contact groups split off as R-39 (later).

- 2026-10-21, R-32 and R-33, documents only: work as intended by the owner's rule D-201 (synced where the server has the function, else this computer only).

- 2026-10-21, R-31, 1.1.240: IMAP folder rename and delete offline: here at once, on the server at the next check; new mail there meanwhile or a refusal: a sync conflict (D-200). R-38 dropped (the owner: servers without UIDPLUS are not supported).

- 2026-10-21, R-37, 1.1.239: the sync-conflict commands ask and settle mail conflicts through the mailbox; the mailbox guard test passes again.

- 2026-10-21, R-35, 1.1.238: an expunge never removes more than the messages named (the cause, in the IMAP client); one way of moving online and offline; found: R-38.

- 2026-10-21, R-30, 1.1.237: IMAP folders deleted or renamed on the server followed here, only on the server's confirmation; a rename keeps the mail (no new download); changes here: a sync conflict; standard folders kept and made again (D-199).

- 2026-10-21, R-29 (step C), 1.1.236: New with an Account choice set to the account right-clicked; on its server first; offline made here and there later with its items; refused then: Keep here only or Retry sync (D-198).

- 2026-10-21, R-28 (step B), 1.1.235: Delete of a server's collection on the server first (not when the server changed it since the last sync); offline here at once and there later; conflicts for a change there or a refusal; Use Chrono Mail of R-25 ties to the address Kerio lists (D-197).

- 2026-10-21, R-25 (step A), 1.1.234: a collection deleted on the server goes here only on the server's confirmation (404/410 to a direct question); a home that could not be listed stops the sync; changed here: a sync conflict, Use Chrono Mail makes it again on the server (D-196).

- 2026-10-21, with R-23/R-24, 1.1.228 and 1.1.229: Retry sync; Sync conflicts in a window of its own, Why as icons with a legend, smaller marks that can be clicked and hovered, the entries at the top (D-194).
- 2026-10-21, R-24 step D (mail), 1.1.227: refused marks, moves, messages and folders made offline are sync conflicts, never retried while open; Keep here only; marks shown to never clash; the Mail entry; the waiting mark and count for mail (D-195).
- 2026-10-20, R-23 and R-24 step C (window), 1.1.226: Settings > Sync conflicts with its popup per field; the triangle and the waiting mark on items; Sync conflicts (n) under the sections' sidebars; the waiting count in the status line (D-194).
- 2026-10-20, R-23 and R-24 step B (engine), 1.1.225: appointments, tasks and contacts on the server first; offline kept with the agreed version and sent later; changes meanwhile merged field by field or a sync conflict; conflict.list, conflict.settle, sync.waiting (D-194).
- 2026-10-20, with R-36, 1.1.224: a calendar server's name is looked up once per client, not per request (0.25 s each on the Linux computer for the Kerio test server's name): 20 requests 0.31 s instead of about 5.4 s.
- 2026-10-20, R-36, 1.1.223: one connection to a calendar server kept open and reused (reads sent once more on a dropped connection, writes never twice); Kerio closes every connection, so it gains nothing there.
- 2026-10-20, R-27 and R-26, 1.1.221: one palette of appointment colours per account; a server calendar's name and colour on the server first, followed from it, and kept with the account through untick and re-tick (D-191, D-192); the basic rule D-193 written first in DECISIONS.md and AGENTS.md.
- 2026-10-20, outside the refactor (the Windows computer's report), 1.1.220: a refused listing given twice in a row is trusted, so a real mass delete settles (D-079).
- 2026-10-20, outside the refactor (the owner), 1.1.219: appointment colours from each calendar's own palette (D-191); `collection.set-palette` typed.
- 2026-10-20, outside the refactor (the owner's and the Windows computer's reports), 1.1.218: Refresh in the sections synchronises and says so; four calendar sync faults fixed (D-079, D-174, D-190).
- 2026-10-20, outside the refactor (the owner's report), 1.1.217: calendar addresses in one form (Kerio), the calendars sync with every check, the mouse wheel in the calendar (D-078, D-079, D-189).
- 2026-10-20, outside the refactor (the owner), 1.1.216: the contact details reach the separator; a 50 px avatar.
- 2026-10-20, outside the refactor (the owner), 1.1.215: one 3 px separator with centred dots between every pair of panes (D-188).
- 2026-10-20, outside the refactor (the owner), 1.1.214: the calendar folders with ticks in the account editor, applied on Save (D-187).
- 2026-10-20, outside the refactor (the owner), 1.1.213: the login per server with Use the same user name and password; no help lines (D-186).
- 2026-10-20, outside the refactor (the owner), 1.1.212: Add account… in the sections; accounts of calendars, contacts and tasks only (D-185). The new commands are typed.
- 2026-10-20, outside the refactor (the owner), 1.1.211: an optional Account name shown only in Chrono Mail; Name is Your name (D-184).
- 2026-10-20, outside the refactor (the owner), 1.1.210: a new account finds its calendars, contacts and tasks; a tick per folder; the mail certificate accepted for the calendar server (D-180 to D-183). The new commands are typed (`DAV_FIND`, `DAV_LINK`).
- 2026-10-20, outside the refactor (the owner's bug report), 1.1.209: a dragged column keeps its place when a column is added or another folder opens; the order has one record (D-179; noted under R-13).
- 2026-10-20, outside the refactor (the owner's test), 1.1.208: a closed message window is deleted (Edit worked once); Scheduled views: Edit and New in the menu; back to drafts by a drag to Drafts; the page keeps New, Edit, Send now, Pause (D-178).
- 2026-10-20, outside the refactor (the owner), 1.1.207: Edit on the Scheduled messages page; a scheduled message is changed in place and never becomes a draft; the fact changed (D-177).
- 2026-10-20, outside the refactor (the owner's bug report), 1.1.206: a message has at most two files, itself and its properties file (D-176); a repeating message from an account in Local Folders is listed and sent again; Unarchive finds the origin whichever account archived.
- 2026-10-20, R-02, 1.1.205: no silent cut of 5,000 in Contacts, Tasks and Calendar; hidden lists take no room; Tasks load rows as they come into view; the first typed engine calls.

- 2026-10-20, 1.1.203: Send now or Cancel snooze instead of "Send or bring back now"; Schedule… instead of Send later…; the Scheduled folder named where the Outbox was meant.
- 2026-10-20, 1.1.202: Favorites > Scheduled like Snoozed and Archive; a "shown once used" Favorites entry now appears the moment its folder is made (it needed a restart).
- 2026-10-20, 1.1.201: "Take back as a draft" is called "Move back to drafts" (the owner); found meanwhile: R-22.
- 2026-10-20, with R-01, 1.1.200: scheduled and repeating messages wait in their own Scheduled folder with a Send at column; the Outbox is only for what failed (D-172); IMAP server folders named like a local-only folder are no longer synchronised.
- 2026-10-20, with R-01, 1.1.199: the message window stops autosaving the moment the message is scheduled or sent (an autosave under the confirmation made a stray draft or "Message not found").
- 2026-10-20, with R-01, 1.1.198: Settings > Scheduled messages > New… makes a scheduled message (D-171); found while doing it: R-21.
- 2026-10-20, R-20, 1.1.197: the key dialog names the cause: the store's own reason, and a wallet that could not be opened apart from a missing key (D-170).
- 2026-10-20, R-01, 1.1.196: scheduled messages are found in the index (only due ones are opened); an unreadable one is told once and never sent; a repeat schedule is never lost to a failed read (D-169).
