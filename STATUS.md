# STATUS.md

*Last updated: 2026-10-13, version 0.2.101. Kept up to date before every push.*

## Where we are

- **Milestones 0 to 4 are done:** POP3 and SMTP, IMAP with IDLE, local calendars, tasks and contacts, CalDAV and CardDAV sync, scheduled and recurring mail, accounts told apart by login, server and protocol. Open in milestone 1: 1.13 (Flatpak) and 1.14 (Windows run).
- **UI restyle (D-083, D-084): steps 1 to 3 of 5 are done** (0.0.73 to 0.0.82): main window with the blue Menu button and section buttons, message list and reading pane, composer (rich text by default, D-085). Settings > Appearance has the text size of the interface (0.0.82).
- **Main screen restyle, second round (D-086), eight steps, one at a time.** Step 1 done (0.0.85): window title "<folder> (<unread>) - Chrono Mail", icons on Mark and Move to, Archive in the toolbar, menu arrows beside the text; all icons are now the program's own copy of the Breeze icons (D-087). Step 2 done (0.0.86): Refresh sends and receives and has a menu (send and receive all, receive all, send all, one account); Send All (`mail.send-outbox`); our Outbox and Sent copies keep Bcc (D-088); a Snooze button with ready-made times (a click anywhere opens its menu); Forward has Forward as attachment; message buttons are greyed out while no message is selected. Step 3 done (0.0.87, D-089): folder names are plain (never bold) and the unread number is drawn in bold at the right edge, only when above zero; other folders use `folder-open-symbolic`, Local Folders `user-home-symbolic`. Step 4 done (0.0.88, D-090): the selection in the folder list and the message list is blue only with the focus and grey without it; 1 px black lines between the panes and the column headers; "Sorted by" in bold. Step 5 done (0.1.89, D-091): the columns always fill the list (column fit, no horizontal scrollbar, no drag line at the right edge), icon columns 24 px, rows 22/29/36 px, a Contact column (opens the contact) and a Delete column (bin on the row under the pointer). Step 6 done (0.1.90 and 0.1.91, D-092): the Unread, Flagged and Attachments buttons are replaced by a Filter button (funnel, "Filter", ▾) with a menu: Filter by, Flagged, Unread, With attachments (several at once); the funnel, "Filter" and the ▾ are blue ("Filter" also bold) while a filter is on; the filters that are on are bold in the menu, and "Clear filter" at the bottom (below a line) turns them all off. Step 7 done (0.1.92, D-093): the search box says "Search (type '?' for help)"; typing "?" opens a help list under it (words, subject, body, from, to, "exact phrase", -word); picking a line puts its search word in the box. 0.1.93 (D-094): the search field has three looks (not in use: grey border, magnifying glass at the start, hint in italics; clicked in: blue border, "Start search", magnifying glass at the end; search active: orange border and a red ✕ that clears the search) and clicking in it shows the last 10 searches (one history for all accounts; typing narrows it; a click runs a search again, its ✕ removes it). Step 8 done (0.2.94 and 0.2.95, D-095): group headers in the message list by the sort order (Received: Today, Yesterday, Earlier this week, Last week, Earlier this month, Last month, Older, No date; Size: Tiny to Huge; one per sender, recipients or subject), in both layouts and in searches and combined views; a click folds a group (kept per folder and sort until the program closes); View > Show groups; a new sort by To; searches and combined views follow the sort order. 0.2.96: the card layout fills the list again; View > Message list chooses Columns or Cards; group headers in the card layout are 36 px; the columns are chosen right on Settings > Message list, and Message list… is only in the menu of the column headers (D-096). 0.2.97: the Message list window has Row height too, and the search field has fully round ends at every text size (D-097). 0.2.99: after a delete, move (also by dragging), archive, junk or snooze the list keeps its place and the next message is selected; F2 renames the folder (D-098). 0.2.100: the Snoozed folder has a Snoozed until column (after Received by default, sortable, groups Today, Tomorrow, This week, Next week, Later; cards say "Until …") (D-099). 0.2.101: Cancel snooze (Snooze button menu and right-click menu in Snoozed); fixed: several snoozes or archives at once could make a folder twice on disk (messages hidden, snoozes forgotten); such folders are merged when the account is opened (D-100).
- 0.0.83: the size and position of the main window, message windows, composers and the Settings window are remembered, and only used when the window is reachable on a connected screen.
- Design decisions: D-001 to D-100 in `DECISIONS.md`.
- Tests: 1643 offline tests, 48 opt-in (live, credential store, performance).
- GitHub: `https://github.com/rob-vandenberg/chrono-mail`, branch `main`. The repository was recreated on 2026-10-12; the author of the first commit was changed to the owner (history rewritten, all tags kept).

## Next task

The main screen restyle (D-086) is finished. Next: ask the owner (the waiting items below, or the backlog). "Select" next to the Filter button comes later (the owner's instruction).

Also waiting:
1. UI restyle step 4 of the first round: the Settings window pages and the other dialogs.

## Backlog

- UI restyle step 5: Calendar, Tasks and Contacts.
- Text-size settings together in one place (now on four pages; not decided).
- The "•••" button in the section bar with its menu (needs features Chrono Mail does not have yet).
- A right-hand strip like the reference's.
- Composer additions (attachment chips, emoji, font colour, importance, quick text, signature button, "…" menu, split Send button): only what the owner approves.
- Small look items: unread count in the window title; the "Mail" heading and the icons do not scale with the text size; the size of the Menu symbol; the Alt+M shortcut of the old menu bar is gone.
- Gmail and Google accounts: on hold until the owner says so.
- Microsoft 365; calendar invitations and replies; tags and reminders for mail; creating calendars and address books on the server; own timer for the calendar sync; a warning when two accounts share a mailbox with delete after download; calendar extras; contacts as business cards with a photo; Windows check; re-measure the 500,000-message performance; CONDSTORE and IMAP folder rename on the server; Flatpak and Windows run.

## Problems

- Folder creation is safe between commands of one running program, not between the program and the command line tool creating the same folder at the same moment.
- In the card layout a list of 500,000 messages takes about 2.7 s to show (0.5 s in the column layout): the card rows have two heights since the group headers became lower (the owner's choice).

- Two tests fail now and then: `tests/ui/test_ui_window.py::test_mark_all_as_read_and_empty_folder` and `tests/ui/test_ui_shortcuts.py::test_marking_as_read_redraws_every_cell_of_the_selected_rows`. Not fixed.
- Not verified whether the real program can crash when it quits (the crashes seen were all from test runs).
- Older certificate exceptions apply to every server of an account; Settings has no way to see or remove exceptions per server.
- `AGENTS.md` section 7 says all tests must pass; the owner's instruction is to run only the tests related to a change, plus Pyrefly and the layer check. `AGENTS.md` is not changed yet.

## Open questions

1. The index rebuild time without other load was not measured, and not since the index got previews.
2. The interface language is English while dates follow the system language; translation is a later decision.
3. Master-password mode, key rotation and key change are not built (D-023).
4. Switching a store between encrypted and plain is a migration and is not built (D-024).
5. POP3 uses USER and PASS only, SMTP uses PLAIN and LOGIN only; OAuth2 comes with Gmail and Microsoft 365.
6. Message threading has no subject-based fallback for replies that lost their References and In-Reply-To headers (D-039).
7. Whether the document structure becomes a gitinit template.

## How to run the checks

- Run only the tests related to a change, never the full suite (owner's instruction): `.venv/bin/pytest <test files>`, then `.venv/bin/pyrefly check --min-severity warn` and `.venv/bin/lint-imports`. The interface tests need no screen (Qt offscreen).
- Against the real server: `.venv/bin/pytest -m live` (needs `tests/live/account.local.toml`, see `account.example.toml`).
- Real credential store (creates and removes one entry in the owner's wallet): `.venv/bin/pytest -m oskeyring`.
- 500,000-message performance test (about an hour, 3 to 4 GB in `~/chrono-mail-perf`): `.venv/bin/pytest -m perf -s`.
- The command line: `.venv/bin/chrono-mail-cli --help`; `--json` for machine-readable output; secrets only through `--<name>-stdin`; commands that delete need `--yes`.

## Notes for the owner

- The test account must be a disposable mailbox: the live tests and the delete-after-download tests remove messages from it. Its password is only in the ignored `tests/live/account.local.toml`.
- Release: `release "<message>"` (commit, tag, push), only when the owner says push.
