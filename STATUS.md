# STATUS.md

*Last updated: 2026-10-08, end of the design session (chat 1).*

## Where we are

- Design phase complete. All design decisions are in `DECISIONS.md`.
- No code exists yet. The repository contains only the project documents and the license.
- The repository exists on GitHub: `https://github.com/rob-vandenberg/chrono-mail` (branch `main`).
- A local clone exists on DEVELOPMENT at `C:\Python\chrono-mail` (Windows test copy, may need `git pull`).
- KDEV is the development machine (Kubuntu 26). The project folder there is `/home/rob/python/chrono-mail`.

## START HERE (first session on KDEV)

The previous session could not be linked to KDEV, so this session starts the KDEV setup.

1. Read `AGENTS.md`, then `DEVELOPMENT-RULES.md` completely, then the rest of this file.
2. Confirm the session is linked to KDEV (the device reports Linux, not "development"), then request folder access to `/home/rob/python/chrono-mail`.
3. Check the state of that folder. If the user has already cloned the repository there, verify it (`git status`, `git remote -v`, `git log`). If not, clone `https://github.com/rob-vandenberg/chrono-mail` into it.
4. Then start **task 0.1** (development environment). Plan first, wait for "proceed".

## Next task

**0.1 Development environment on KDEV** (see ROADMAP.md). Includes:
- choosing the Python version (must be supported by PySide6 and Pyrefly; verify, don't assume),
- virtual environment, PySide6, Pyrefly, pytest,
- VS Code with the Pyrefly extension,
- a Linux equivalent of the user's `release` command (the current one is a Windows batch script; check the user's gitinit / gitinit-py repositories for how it works today).

## Open questions

1. Linux `release` tooling: port the existing script, or extend gitinit-py? (task 0.1)
2. Test mail server for automated tests (task 0.3). Candidates to evaluate: a Python SMTP server (`aiosmtpd`) plus a POP3 solution, GreenMail (Java, SMTP/POP3/IMAP with TLS), or Dovecot + Postfix in a container. Verify before deciding.
3. Layer-check tool (task 0.2). Candidate: import-linter.
4. Encryption details (task 1.3): exact algorithm, key storage (OS credential store, master password, or both), key backup and recovery procedure.
5. Maildir info separator on all platforms instead of `:` (task 1.4).
6. Encryption off: per store or per account? (task 1.3/1.4)
7. Whether the document structure becomes a gitinit template (after milestone 1).

## Notes for the user

- Workflow changes from chat to Claude Code are described in `AGENTS.md` §6.
- The user is moving from Notepad++ to VS Code as the main editor.
