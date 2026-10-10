# STATUS.md

*Last updated: 2026-10-10, after the first testable user interface (tasks 1.10 and 1.11).*

## Where we are

- Design phase complete. All design decisions are in `DECISIONS.md` (D-001 to D-040).
- **Milestone 0 is done** (0.4, GitHub Actions, is optional and not started). **Milestone 1: tasks 1.1 to 1.12 are done.** Open in milestone 1: 1.13 (Flatpak) and 1.14 (Windows run); 1.12 (key recovery) is done.
- **There is a working graphical program to test**: `chrono-mail` (see below). Three-pane window in the style of eM Client 9 and 10: folders, message list, reading pane; get mail, read, reply, reply all, forward, new message, attachments (save and send), flag, mark read or unread, move, delete (Trash), folders (create, rename, delete), search with quick filters, first-run account setup with a connection test and the key recovery code screen. HTML mail is shown locked down (no scripts, remote content blocked until allowed per message).
- The headless client still works: `chrono-mail-cli` (26 commands).
- Verified against the real DirectAdmin server `mail.technova.us` through the engine and the CLI. The graphical program has been tested without a screen, with fake servers, and (one live test) against the real server through the window: compose, send, fetch, read, delete. **Nobody has used it on a real screen yet**: the first real use is the next step.
- Project version 0.0.19. About 8,100 lines in `src/` (2,500 of them the interface), 8,300 in `tests/`; 926 offline tests, 31 live tests (one drives the window against the real server), 1 credential-store test, 1 performance test.
- GitHub: `https://github.com/rob-vandenberg/chrono-mail`, branch `main`. The last push is recorded in DEVLOG.md (version 0.0.12); the interface work (0.0.13) was pushed; everything up to 0.0.14 was pushed; later versions are pushed as they are finished (see the queue). The test account password (`chronomail@bccbox.com` on `mail.technova.us`) is only in the ignored `tests/live/account.local.toml`; it is still to be changed once the user decides POP3 and SMTP are proven.
- KDEV is the development machine (Kubuntu 26). The project folder is `/home/rob/python/chrono-mail`.

## Autonomous work queue (the user is away until 2026-10-11; see D-034)

Each step: tests, full gate, new version and tag, documents, push. Done steps are struck through in the log (DEVLOG.md), not here.

1. DONE (0.0.15) Key recovery in the interface (task 1.12).
2. DONE (0.0.16) Drafts: save and auto-save to the Drafts folder, resume editing from Drafts and Outbox, remove after sending.
3. DONE (0.0.17) Message actions: drag and drop to folders, Archive, mark all as read, empty Trash and Junk, richer context menus.
4. DONE (0.0.18) Conversation view (counts in the list, grouped list option, conversation bar in the reading pane).
5. DONE (0.0.19) Advanced search dialog, with "does not contain" and "or", dates and sizes.
6. Settings window and account management (edit, remove), signatures.
7. Desktop notifications and a tray icon.
8. Save as .eml, print, view source.
9. Address autocomplete in the composer.
10. HTML composing.
11. IMAP (milestone 2): design first, then implementation against the test account.
Also when time allows: re-measure the 500,000-message performance (index schema 3), and the language of the interface (a later decision).

If a step cannot be made green it is not pushed; the reason is written in STATUS.md under "Problems".

## Problems

None so far.

How to run the checks:
- Offline gate: `.venv/bin/pytest`, `.venv/bin/pyrefly check`, `.venv/bin/lint-imports` (the layer check is also part of pytest). The interface tests need no screen (Qt offscreen).
- Against the real server: `.venv/bin/pytest -m live` (needs `tests/live/account.local.toml`, see `account.example.toml`).
- Real credential store (creates and removes one entry in the user's wallet): `.venv/bin/pytest -m oskeyring`.
- 500,000-message performance test (about an hour, 3 to 4 GB in `~/chrono-mail-perf`, which the test recreates): `.venv/bin/pytest -m perf -s`.
- The command line: `.venv/bin/chrono-mail-cli --help`; `--json` for machine-readable output; secrets only through `--<name>-stdin`; commands that delete need `--yes`.

## Open questions

1. The index rebuild time without other load was not measured (the 59 minutes were partly under load), and not since the index got previews (schema 2).
2. HTML composing and a settings dialog are not built (D-030). The interface language is English while dates follow the system language; translation is a later decision. The launcher file `packaging/chrono-mail.desktop` exists but is not installed on KDEV.
3. Master-password mode, key rotation and key change are not built (D-023); the end-to-end recovery procedure is task 1.12.
4. Switching a store between encrypted and plain is a migration and is not built (D-024).
5. Certificate exceptions for servers with self-signed certificates: the clients refuse an untrusted certificate (AGENTS.md section 4.6 allows an explicit user exception; not built).
6. POP3 uses USER and PASS only, SMTP uses PLAIN and LOGIN only; OAuth2 comes with Gmail and Microsoft 365.
7. Message threading has no subject-based fallback for replies that lost their References and In-Reply-To headers (D-039).
8. Whether the document structure becomes a gitinit template (after milestone 1).

## Notes for the user

- Workflow changes from chat to Claude Code are described in `AGENTS.md` section 6.
- `~/.local/bin` joins `PATH` at the next login (or run `export PATH="$HOME/.local/bin:$PATH"`) so that `release` works by name.
- The test account `chronomail@bccbox.com` must be a disposable mailbox: the live tests and the delete-after-download tests remove messages from it.
- The user is moving from Notepad++ to VS Code as the main editor.
