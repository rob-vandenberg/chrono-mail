# STATUS.md

*Last updated: 2026-10-10, after the first testable user interface (tasks 1.10 and 1.11).*

## Where we are

- Design phase complete. All design decisions are in `DECISIONS.md` (D-001 to D-033).
- **Milestone 0 is done** (0.4, GitHub Actions, is optional and not started). **Milestone 1: tasks 1.1 to 1.11 are done.** Open in milestone 1: 1.12 (tested key recovery procedure; the pieces exist and the interface shows the recovery code when the key is created), 1.13 (Flatpak) and 1.14 (Windows run).
- **There is a working graphical program to test**: `chrono-mail` (see below). Three-pane window in the style of eM Client 9 and 10: folders, message list, reading pane; get mail, read, reply, reply all, forward, new message, attachments (save and send), flag, mark read or unread, move, delete (Trash), folders (create, rename, delete), search with quick filters, first-run account setup with a connection test and the key recovery code screen. HTML mail is shown locked down (no scripts, remote content blocked until allowed per message).
- The headless client still works: `chrono-mail-cli` (26 commands).
- Verified against the real DirectAdmin server `mail.technova.us` through the engine and the CLI. The graphical program has been tested without a screen, with fake servers, and (one live test) against the real server through the window: compose, send, fetch, read, delete. **Nobody has used it on a real screen yet**: the first real use is the next step.
- Project version 0.0.14. About 8,100 lines in `src/` (2,500 of them the interface), 8,300 in `tests/`; 915 offline tests, 31 live tests (one drives the window against the real server), 1 credential-store test, 1 performance test.
- GitHub: `https://github.com/rob-vandenberg/chrono-mail`, branch `main`. The last push is recorded in DEVLOG.md (version 0.0.12); the interface work (0.0.13) was pushed; the polish after the first real use (0.0.14) is **not pushed yet**. The test account password (`chronomail@bccbox.com` on `mail.technova.us`) is only in the ignored `tests/live/account.local.toml`; it is still to be changed once the user decides POP3 and SMTP are proven.
- KDEV is the development machine (Kubuntu 26). The project folder is `/home/rob/python/chrono-mail`.

## Next task

**The user tries the program** (`/home/rob/python/chrono-mail/.venv/bin/chrono-mail`) and reports what is wrong or missing. Then, in this order of likely value: fix what the real use shows; drafts; the recovery procedure end to end (1.12); HTML composing; conversation view; drag and drop; Flatpak (1.13); the Windows run (1.14).

How to run the checks:
- Offline gate: `.venv/bin/pytest`, `.venv/bin/pyrefly check`, `.venv/bin/lint-imports` (the layer check is also part of pytest). The interface tests need no screen (Qt offscreen).
- Against the real server: `.venv/bin/pytest -m live` (needs `tests/live/account.local.toml`, see `account.example.toml`).
- Real credential store (creates and removes one entry in the user's wallet): `.venv/bin/pytest -m oskeyring`.
- 500,000-message performance test (about an hour, 3 to 4 GB in `~/chrono-mail-perf`, which the test recreates): `.venv/bin/pytest -m perf -s`.
- The command line: `.venv/bin/chrono-mail-cli --help`; `--json` for machine-readable output; secrets only through `--<name>-stdin`; commands that delete need `--yes`.

## Open questions

1. The index rebuild time without other load was not measured (the 59 minutes were partly under load), and not since the index got previews (schema 2).
2. Drafts, HTML composing, conversation view, drag and drop and a settings dialog are not built (D-030). The interface language is English while dates follow the system language; translation is a later decision. The launcher file `packaging/chrono-mail.desktop` exists but is not installed on KDEV.
3. Master-password mode, key rotation and key change are not built (D-023); the end-to-end recovery procedure is task 1.12.
4. Switching a store between encrypted and plain is a migration and is not built (D-024).
5. Certificate exceptions for servers with self-signed certificates: the clients refuse an untrusted certificate (AGENTS.md section 4.6 allows an explicit user exception; not built).
6. POP3 uses USER and PASS only, SMTP uses PLAIN and LOGIN only; OAuth2 comes with Gmail and Microsoft 365.
7. Proper message threading (the index has a simplified thread key).
8. Whether the document structure becomes a gitinit template (after milestone 1).

## Notes for the user

- Workflow changes from chat to Claude Code are described in `AGENTS.md` section 6.
- `~/.local/bin` joins `PATH` at the next login (or run `export PATH="$HOME/.local/bin:$PATH"`) so that `release` works by name.
- The test account `chronomail@bccbox.com` must be a disposable mailbox: the live tests and the delete-after-download tests remove messages from it.
- The user is moving from Notepad++ to VS Code as the main editor.
