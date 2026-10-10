# STATUS.md

*Last updated: 2026-10-10, after task 1.9 (the headless client works end to end).*

## Where we are

- Design phase complete. All design decisions are in `DECISIONS.md` (D-001 to D-029).
- **Milestone 0 is done** (0.4, GitHub Actions, is optional and not started). **Milestone 1: tasks 1.1 to 1.9 are done**: `core`, `mime`, `crypto`, `store`, `index`, `config`, POP3 client and `sync`, SMTP client, `engine` and the command line. The application can be driven completely without a screen: `chrono-mail-cli` (26 commands) creates an account, fetches and sends mail, searches, moves, flags and deletes, verifies the store, and exports and restores the data key.
- Verified against the real DirectAdmin server `mail.technova.us` (Dovecot and Exim, test account): POP3 over TLS and STLS, SMTP over STARTTLS and TLS, a full CLI session, with a clean mailbox afterwards.
- Measured with a generated mailbox of 500,000 messages: folder pages under 10 ms, search 0.5 to 113 ms, flag change 0.13 ms; the full index rebuild takes about an hour (see DEVLOG.md, task 1.5, for all numbers and caveats).
- Project version 0.0.12 (`pyproject.toml`). About 5,200 lines in `src/`; 681 offline tests, 30 live tests, 1 credential-store test, 1 performance test.
- GitHub: `https://github.com/rob-vandenberg/chrono-mail`, branch `main`. The last push is recorded in DEVLOG.md. The test account password (`chronomail@bccbox.com` on `mail.technova.us`) is only in the ignored `tests/live/account.local.toml`; it is still to be changed once the user decides POP3 and SMTP are proven.
- KDEV is the development machine (Kubuntu 26). The project folder is `/home/rob/python/chrono-mail`.

## Next task

**1.10 `ui`** (main window, folder tree, virtualized message list, reading pane with plain text and safe HTML) is next, and it needs the user: the interface can only be judged by looking at it. Decide with the user first: what the main window should look like and how it should behave, and whether to show a mock-up or a first version early. The UI must contain no business logic: it calls the 26 engine commands, in worker threads with their own Engine and index connection (an Engine is for one thread).

Tasks 1.11 (composer), 1.12 (tested key recovery procedure), 1.13 (Flatpak) and 1.14 (Windows run) follow. Task 1.11 needs three commands that do not exist yet: save an attachment, save and send drafts, reply and forward.

How to run the checks:
- Offline gate: `.venv/bin/pytest`, `.venv/bin/pyrefly check`, `.venv/bin/lint-imports` (the layer check is also part of pytest).
- Against the real server: `.venv/bin/pytest -m live` (needs `tests/live/account.local.toml`, see `account.example.toml`).
- Real credential store (creates and removes one entry in the user's wallet): `.venv/bin/pytest -m oskeyring`.
- 500,000-message performance test (about an hour, 3 to 4 GB in `~/chrono-mail-perf`, which the test recreates): `.venv/bin/pytest -m perf -s`.
- The command line: `.venv/bin/chrono-mail-cli --help`; `--json` for machine-readable output; secrets only through `--<name>-stdin`; commands that delete need `--yes`.

## Open questions

1. Count unread takes 454 ms in a folder of 300,000 messages; fine once, too slow for every folder on every refresh. A counter or a partial index when the UI needs it.
2. The index rebuild time without other load was not measured (the 59 minutes were partly under load).
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
