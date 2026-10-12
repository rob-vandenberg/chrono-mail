# STATUS.md

*Last updated: 2026-10-12, version 0.0.84. Kept up to date before every push.*

## Where we are

- **Milestones 0 to 4 are done:** POP3 and SMTP, IMAP with IDLE, local calendars, tasks and contacts, CalDAV and CardDAV sync, scheduled and recurring mail, accounts told apart by login, server and protocol. Open in milestone 1: 1.13 (Flatpak) and 1.14 (Windows run).
- **UI restyle (D-083, D-084): steps 1 to 3 of 5 are done** (0.0.73 to 0.0.82): main window with the blue Menu button and section buttons, message list and reading pane, composer (rich text by default, D-085). Settings > Appearance has the text size of the interface (0.0.82).
- 0.0.83: the size and position of the main window, message windows, composers and the Settings window are remembered, and only used when the window is reachable on a connected screen.
- Design decisions: D-001 to D-085 in `DECISIONS.md`.
- Tests: 1621 offline tests, 48 opt-in (live, credential store, performance).
- GitHub: `https://github.com/rob-vandenberg/chrono-mail`, branch `main`. The repository was recreated on 2026-10-12; the author of the first commit was changed to the owner (history rewritten, all tags kept).

## Next task

The owner chooses. Waiting first:
1. After deleting a message, the selection stays in place with the next message selected (it now jumps to the top).
2. F2 renames the selected folder.
3. UI restyle step 4: the Settings window pages and the other dialogs.

## Backlog

- UI restyle step 5: Calendar, Tasks and Contacts.
- Text-size settings together in one place (now on four pages; not decided).
- Collapsible group headers in the message list.
- Composer additions (attachment chips, emoji, font colour, importance, quick text, signature button, "…" menu, split Send button): only what the owner approves.
- Small look items: unread count in the window title; the "Mail" heading and the icons do not scale with the text size; the size of the Menu symbol; the Alt+M shortcut of the old menu bar is gone.
- Gmail and Google accounts: on hold until the owner says so.
- Microsoft 365; calendar invitations and replies; tags and reminders for mail; creating calendars and address books on the server; own timer for the calendar sync; a warning when two accounts share a mailbox with delete after download; calendar extras; contacts as business cards with a photo; Windows check; re-measure the 500,000-message performance; CONDSTORE and IMAP folder rename on the server; Flatpak and Windows run.

## Problems

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
