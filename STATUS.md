# STATUS.md

*Last updated: 2026-10-08, after task 1.3.*

## Where we are

- Design phase complete. All design decisions are in `DECISIONS.md` (D-001 to D-023).
- Tasks 0.1 to 0.3 and 1.1 to 1.3 are done: Python 3.14 + `.venv`, `pyproject.toml` (version 0.0.6, setuptools src layout, Pyrefly strict, import-linter contracts), the `src/chrono_mail/` package skeleton with the `core` data model the `mime` parser and builder and the `crypto` module, 198 offline tests plus 21 opt-in live tests against `mail.technova.us` and 1 opt-in `oskeyring` test, and the Linux `release` command at `~/.local/bin/release`.
- `core`, `mime` and `crypto` exist; no store, protocol or UI code yet.
- Pushed to GitHub on the user's request (2026-10-10) as version 0.0.6, release message "Initial release". The test account password lives only in the ignored `tests/live/account.local.toml` and is not in the repository; it is still to be changed once POP3 and SMTP work.
- KDEV is the development machine (Kubuntu 26). The project folder is `/home/rob/python/chrono-mail`.

## Next task

**1.4 `store`** (see ROADMAP.md): the encrypted Maildir. Atomic writes, random file names, flag renames, a Windows-safe separator, folder create/rename/delete, interruption tests. It settles open questions 3 to 5 below. Plan first, wait for "proceed".

Run the offline gate with: `.venv/bin/pytest` and `.venv/bin/pyrefly check`. Run the live tests with `.venv/bin/pytest -m live` (needs `tests/live/account.local.toml`, see `account.example.toml`) and the real credential-store test with `.venv/bin/pytest -m oskeyring` (creates and removes one entry in the user's wallet).

## Open questions

1. Offline fake servers for hostile and misbehaving server behaviour (task 1.7/1.8 or later). The live tier cannot test them.
2. Master-password mode, key rotation and key change are not built (D-023); the end-to-end recovery procedure is task 1.12.
3. Maildir info separator on all platforms instead of `:` (task 1.4).
4. Encryption off: per store or per account? (task 1.3/1.4)
5. Does the store preserve unknown Maildir flag letters (such as P) on a flag rename? (task 1.4, see D-021)
6. Whether the document structure becomes a gitinit template (after milestone 1).

## Notes for the user

- Workflow changes from chat to Claude Code are described in `AGENTS.md` §6.
- `~/.local/bin` joins `PATH` at the next login (or run `export PATH="$HOME/.local/bin:$PATH"`).
- The user is moving from Notepad++ to VS Code as the main editor.
