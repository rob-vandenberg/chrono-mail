# FIXES-2.4.326.md

The inventory of every problem the Windows computer's full test of 2.0.296 to 2.4.326 found (its private report `REPORT-2.4.326.md` and log `TESTLOG-2.4.326.md`, W-numbers and log sections kept), each cause checked in the code on 2026-10-23, and the plan to fix them all. Read after STATUS.md while this work is open. Each phase is one version with its own changelist and Proceed; update the Status lines and the Done log as phases land.

**Closed (the owner, 2026-10-24):** the full review and its fixes up to and including 2.4 are done; the Windows computer's check of 2.4.334 found every fix working, its two new findings done in 2.5.335 (section 7). Left outside: W13's slowness on Windows (the owner's own work); S1, S2 (later, measured first).

## 1. The inventory, by category

### A. Mail sent or lost without the person (the most serious)

| # | What the person sees | Cause (checked in the code) | Status |
|---|---|---|---|
| W6 | A restore sends the Outbox of the backup by itself, about 2 s after the restore (both test servers). An old backup can send old mail. | After a restore the window runs `check_mail()` (ui/mailchecking.py), which is a *scheduled refresh*; with Send and Receive (the default) a scheduled refresh sends the Outbox first. The same call runs after an index rebuild, an import and a first account. **Why that is wrong:** these checks exist to bring the server's state in, not to act for the person; "a refresh the person did not click" and "a check after a change of data" were made one thing. | Done 2.4.327 |
| W6b | (Found while checking W6, not in the report.) A one-off scheduled message whose time passed while it sat in an old backup is sent at the next minute check after the restore. | `mail_send_due` sends every one-off scheduled message whose time has come; it cannot tell "the computer was off at that minute" from "this came back from a backup a year later". Repeating ones send the last missed one once (D-080). | Done 2.4.327 (paused) |
| W1 | A backup with a POP3 account that keeps its mail in Local Folders and has fetched nothing yet cannot be restored: "Unexpected error: FileNotFoundError …". Every demo backup and the owner's own store (25 such accounts) fail. | engine/restore.py `_restore_whole`: the account's folder `.restoring-<id>` is only made as a side effect of writing its first file; such an account has no files, so the folder never exists and the final rename fails. **Why:** the code assumes every account has files. | Done 2.4.328 |
| W5 | One damaged message in a backup stops the whole restore; everything after it (other accounts, settings, rules, passwords) does not come back. | engine/restore.py `_read` raises for a damaged entry and nothing catches it per file. Backup already keeps going past an unreadable file ("unreadable"); restore does not. Also found: damaged compressed data raised a zlib error nothing caught. | Done 2.4.328 |
| W4 | That damaged backup fills the report box with 206,074 characters of raw bytes. A cut-off backup is called "not a backup". | `_read` puts pyzipper's error text, which holds the entry's raw bytes, into the message; a cut-off file is caught by `_open` with the "not a backup" words. | Done 2.4.328 |

### B. Large selections (wrong result and a frozen window)

| # | What the person sees | Cause | Status |
|---|---|---|---|
| W12 | Ctrl+A on more than 100 messages, then Delete (or Move, Mark, Junk, Archive, Tag): only 100 are done, without a word. Shift+Delete's question counts 100. | ui/messagelistpane.py `selected_rows` skips every selected row whose data is not loaded yet (rows load 100 at a time). **Why:** the actions ask for the full row data, while they only need what the list already holds for every message (its mailbox, folder and id are in the message order, `MessageOrder`). | Done 2.4.329 |
| W13 | The window does not answer at all after an action on a large selection: 13 s for 311 messages, about 139 s for 1,042; it grows faster than the count. | Not found yet by reading. The time grows with selected × removed, so something does work per selected row for every row that leaves. [Speculation] candidates: each fact removes one row at a time while the selection model holds every selected row; per-row redraws in `take_away`/`mark_pending`. **Measured (1,000 messages, profiler):** every row greyed at the key press was redrawn alone (`mark_pending`), and every redraw recomputed the Archive/Unarchive label over the whole selection with Qt's `selectedRows`, which asks every cell of every selected row: 100 redraws × 1,000 rows × 19 columns, about 1.9 million calls, 19.5 s. Now one redraw per run of rows, the label once per moment, the selection read from its ranges: key press 0.03 s, longest pause 0.3 s. | Done 2.4.329 |

### C. IMAP synchronisation

| # | What the person sees | Cause | Status |
|---|---|---|---|
| W7 | On a server whose Junk folder exists but is not subscribed (the DirectAdmin test server), nothing can go to Junk: the filter, the server's spam mark and Junk by hand all fail with "Mailbox already exists". | sync/imap_sync.py `plan_folders` drops unsubscribed folders **before** it looks for the standard roles, so the special-use `\Junk` folder is never mapped and a move tries to CREATE it. This breaks D-202 ("standard folders are always synced"). Same risk for an unsubscribed Sent, Drafts, Trash or Archive. | Done 2.4.330 (offline tests; not yet checked live) |

### D. Attachment safety on Windows

| # | What the person sees | Cause | Status |
|---|---|---|---|
| W10 | .msix, .appx, .appinstaller, .application, .url, .chm, .msc, .scf, .ws, .wsc, .xll, .vhd, .vhdx, .cab open without "Open a program?". | Missing from `PROGRAM_EXTENSIONS` (engine/commands_attachments.py). | Done 2.4.331 |
| W11 | An attachment named CON, NUL, AUX, PRN, COM1–9 or LPT1–9 (any extension) is written to the device (data gone) or fails. | engine/compose.py `suggest_filename` does not make Windows device names safe. | Done 2.4.331 |
| W11b | A name with a right-to-left mark ("invoice‮gpj.exe") is shown reversed in the question ("invoiceexe.jpg"). | `suggest_filename` keeps the invisible direction characters. | Done 2.4.331 |

### E. Smaller faults in the window

| # | What the person sees | Cause | Status |
|---|---|---|---|
| W9 | New (Ctrl+N) does nothing, without a word, while no folder is selected (just after start, after a restore). | ui/composing.py `new_message` stops when no folder is open, even when a default account is set. | Done 2.4.332 |
| R1 | After a restore no folder is selected and the list is empty. | Checked in a window test (fresh profile, restore): the Inbox is selected once the indexes are rebuilt; during the rebuild (about 11 s for the demo) the list is empty by design (D-229). No fault found; W9 makes New work meanwhile. | Checked 2.4.332 |
| R2 | The rules of accounts not ticked in the Restore window come back too, naming accounts that are not here. | `_restore_rules` restores every rule. | Done 2.4.328 |
| W3 | Exit during a backup: "The backup stops and no file is kept", but Cancel on the next question (Outbox) keeps the program open and the backup goes on and is saved. | ui/mainwindow.py `closeEvent` asks the backup question first and the Outbox and message-window questions after it; the promise is made before the later questions can still say no. | Done 2.4.332 (asked last) |
| W2 | A new computer: the first-account window offers no way to restore a backup. | Not built. | Done 2.4.332 |

### F. Wording

| # | Where | Status |
|---|---|---|
| F1 | "1 messages" (Restore window rows and report); 21 places say "message(s)", about 12 more build "n messages" by hand. | Done 2.4.333 (D-246) |
| F2 | "1041 messages … are gone from the server" (the Messages out of step question) without the thousands comma used elsewhere. | Done 2.4.333 |
| F3 | tools/demo/build.py: "built … in 1 minutes" (40 s rounded) and "about a quarter of an hour for 100,000 messages" (measured: 6 minutes). | Done 2.4.333 |

### G. Speed (low)

| # | What | Status |
|---|---|---|
| S1 | Restoring messages into an account that is already here takes 9 ms per message, three times the whole-account path (about 21 minutes for 140,000). | Later, measured first |
| S2 | A backup of the owner's store spends about 1.5 minutes listing files before writing anything. | Later, measured first |

### H. Not faults, or already covered

- 10.3: the guard "Messages out of step" after the test tool killed the program mid-move: the guard did its job.
- The Outbox question at Exit still counting a message just sent by the restore: gone with W6.
- The Windows computer needs `pip install -e .` after 2.4.322 (pyzipper): known, in STATUS.md.
- Not tested, for the owner by hand: window settings restored into the Windows registry; Archive/Unarchive (2.0.306) and the Go to submenu (2.0.305) beyond the owner's own check.
- Withdrawn by the tester: W8.

## 2. Why the tests did not catch these

The cases had no test: an account without files (W1), a damaged entry (W4, W5), a selection larger than one page of rows (W12, W13), an unsubscribed standard folder (W7), a restore with mail in the Outbox (W6). Every fix below comes with a regression test built from the tester's case (AGENTS.md 4.2.4), and phase 3 adds a test that counts the work of one key press (deterministic, never a timing), so a large selection can never freeze the window again unnoticed.

## 3. Decisions for the owner

3.1 W6b: **decided by the owner: paused** (a restore never sends anything by itself; repeating ones too).

3.2 Mail deleted on an IMAP server does not come back from a backup into an account that is here (D-193): **decided by the owner: keep**.

3.3 The junk filter learns read mail as good only once: **decided by the owner: keep**.

Decided by the plan, stated here (no question): a damaged message is skipped and listed, the restore goes on (W5); when **Remove all data first** was ticked and anything was skipped, the data set aside is kept (not removed) and the report says where (never lose mail, 4.1). The first-account window gets "Restore a backup…" (W2: what a person on a new computer would look for).

## 4. The plan: seven phases, most serious first

Each phase: changelist, Proceed, the related tests (found by grep) plus a new test that fails on 2.4.326, Pyrefly (Linux and win32), the layer check, the documents, the push line.

| Phase | What | Size | Status |
|---|---|---|---|
| 1 | **Nothing is sent without the person.** The check after a restore, an index rebuild, an import or a first account only receives (never sends); a refresh a rebuild held back runs after it with its own choice; the timers and Refresh unchanged. A restore puts the Outbox's mail in Drafts (else the next automatic refresh would send it) and pauses overdue scheduled messages; one-off scheduled messages can be paused and resumed. | Small | Done 2.4.327 |
| 2 | **Every backup restores.** W1 (the account's folder made first, so an account without files comes back), W5 (a damaged file skipped, listed in the report, the rest goes on; the set-aside data kept when something was skipped), W4 (the entry's name only; "damaged or incomplete" for a cut-off file), R2 (only the rules of the accounts ticked, and those of no account). Proven on a demo backup (14 accounts) and a backup with one damaged entry. | Medium | Done 2.4.328 |
| 3 | **Actions take the whole selection, at once.** W12: every action works from the messages' identities, which the list holds for every message (loaded or not): Delete, Shift+Delete, Move, Copy, Archive, Unarchive, Junk, Not junk, Snooze, marks, tags; what needs a message's marks or tags (Toggle read, Toggle flag, a tag's shortcut, the Tag menu's ticks) asks the rows not loaded yet first, then decides over all of them. W13: measured on a generated folder of 1,000 messages, the step that grows with the selection found and removed; a test counts the work of one key press. | Large | Done 2.4.329 |
| 4 | **Standard folders are found whether subscribed or not.** W7: the roles are decided over every folder the server lists; the subscription filter applies only to the person's own folders (D-202 as written). A folder the server has is never created again (asked first, online and in the sync). Live check on both test servers: by the Windows computer. | Medium | Done 2.4.330 |
| 5 | **Attachment names and programs on Windows.** W10 (the 14 types added), W11 (device names get "_" on every system), W11b (direction characters taken out of names). | Small | Done 2.4.331 |
| 6 | **The small window faults.** W9 (New uses the default account, else the open one, else the first; greyed only without a mail account), R1 (the startup folder opens after a restore), W3 (the backup and restore question asked last, right before quitting), W2 (Restore a backup… in the first-account window). | Medium | Done 2.4.332 |
| 7 | **Wording.** One way to write a count ("1 message", "2 messages", "1,041") used everywhere, the "message(s)" texts included (F1, F2); the demo builder's texts (F3). Numbers stay in the English form until B-04-1. | Small | Done 2.4.333 |
| later | S1, S2: measured first, then a changelist. | — | Later |

## 5. Done log

- 2.4.327 phase 1 (W6, W6b; D-245): the check after a restore, rebuild, import or new account only receives; restored Outbox mail in Drafts; overdue scheduled messages paused; Pause and Resume for one-off scheduled messages.
- 2.4.328 phase 2 (W1, W5, W4, R2): an account without files comes back; a damaged message or item is left out and told, the rest comes back, the set-aside data kept then; a damaged entry is named, never its bytes; a cut-off backup is "damaged or incomplete"; only the rules of the accounts that came back.
- 2.4.329 phase 3 (W12, W13): every action takes every selected message, loaded or not; choices of marks and tags count them all; the freeze measured and its cause removed.
- 2.4.330 phase 4 (W7): standard folders the server marks or has under their standard name are used whether subscribed or not, never created twice.
- 2.4.331 phase 5 (W10, W11, W11b): 14 Windows types asked about first; device names and direction marks made safe.
- 2.4.332 phase 6 (W9, W3, W2; R1 checked): New without a selected folder; the backup question last at Exit; Restore a backup… in the first-account window.
- 2.4.333 phase 7 (F1 to F3, D-246): every count written one way; the demo builder's texts.

## 6. The check of 2.4.333 and 2.4.334

The Windows computer's REPORT-2.4.333.md: W6, W6b, W1, W5, W4, R2, W13, W7 (live, both servers), W10, W11b, W3, W2, F2 and W9 at start fixed as checked; four partial, all done in 2.4.334:

| # | What was left | Cause | Status |
|---|---|---|---|
| W12b | Ctrl+A in the first 0.4-1.6 s after a large folder opened acted on 100 | the list knows its first 100 messages until the rest of the order arrives; the actions took only what had a place | Done 2.4.334: an action whose selection holds messages not placed yet waits for the rest of the order (Qt keeps the selection), then takes them all |
| W9b | New did nothing during the index rebuild after a restore | the window learns its accounts only after the rebuild | Done 2.4.334: New asks the engine for the accounts while the window does not know them (reading them earlier for the whole window made the check after the rebuild a second check at start: rejected) |
| W11c | CONIN$, CONOUT$, COM/LPT with ¹ ² ³ still devices | missing from the list | Done 2.4.334 |
| F1b | ten "(s)" texts | only message, item, folder and account were searched | Done 2.4.334 |
| big | 15,927 messages: rows left about 78 per second, the window slow | every fact removed one row (the list laid out and painted again) and recounted the folder list | Done 2.4.334: facts that take messages out right after another go together, ten times a second, per run of neighbouring rows; the folder numbers likewise; the first of a quiet moment at once. Measured here: 16,000 deleted 81 s, longest pause 1.7 s -> 23 s, 0.55 s; 4,000: 6.8 s, 1.7 s -> 5.2-6.9 s, 0.19 s. Ctrl+A here 0.18 s for 16,000 (the 4 s on Windows to measure again there) |
| note 3 | the damaged message was only counted | | Done 2.4.334: "1 message in Inbox of Work" (folder or calendar and account) |
| note 1 | `mail_account_ids` defined twice | not so: one definition | No change |

## 7. The check of 2.4.334

The Windows computer's REPORT-2.4.334.md: W12b, W9b, W11c, F1b, note 3, W1 on the owner's real store and Undo fixed as checked; note 1 withdrawn by it.

| # | What was found | Cause | Status |
|---|---|---|---|
| T1 | test_ui_sync_conflicts failed once in a combined run: "The index is damaged", then WinError 32 on pim.sqlite | two openers of a new calendar index both saw it empty; the second's make failed, was called damaged, and the file was removed while the first used it (the mail index likewise for an upgrade in place) | Done 2.5.335 (D-247) |
| T2 | index.prepare answered 0 messages after 21.5 minutes; the index window said "0 messages indexed" | it opened the index through the general way, which drops the rebuild's report | Done 2.5.335 |
| T3 | rebuilding the restored 140,402 messages took 21.5 minutes, no mail shown meanwhile | 15.3 GB read and decrypted once, about 12 MB/s | Not a fault; dropped (the owner, 2026-10-24). Measured here: understanding each message (Python's mail reader) is about 85% of the index time; skipping attachment contents would save only about 30% |
| W13 | the large delete about 75 per second on Windows | the engine 160 per second there (700 here) | The owner's own work |

