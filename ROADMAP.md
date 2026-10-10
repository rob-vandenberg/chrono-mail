# ROADMAP.md

Status values: `todo`, `in progress`, `done`, `blocked`.

Tasks are deliberately small. Each one is planned, approved ("proceed"), built, tested and logged on its own. Task details are refined in the plan for that task.

## Milestone 0: Foundations

| # | Task | Status |
|---|---|---|
| 0.1 | Development environment on KDEV: Python version, virtual environment, PySide6, Pyrefly (strict), pytest, VS Code with the Pyrefly extension. Linux equivalent of the `release` tooling. Also created `pyproject.toml` (version 0.0.1). | done |
| 0.2 | Project skeleton: `src/chrono_mail/` package layout, Pyrefly strict config, test runner, automated layer check. | done |
| 0.3 | Live test setup against a real mail server (the user's DirectAdmin Dovecot/Exim): SMTP and POP3 over plain, STARTTLS/STLS and implicit TLS, opt-in `live` tests. IMAP live tests follow in milestone 2. | done |
| 0.4 | Optional: GitHub Actions running tests, Pyrefly and the layer check on every push. | todo |

## Milestone 1: POP3 + SMTP, a usable mail client

| # | Task | Status |
|---|---|---|
| 1.1 | `core`: data model (Account, Folder, MessageRef, Flags, errors). | done |
| 1.2 | `mime`: parse and build messages; never crashes on malformed input; fuzz-style tests. | done |
| 1.3 | `crypto`: key generation, storage in the OS credential store, encrypt/decrypt message files. | done |
| 1.4 | `store`: encrypted Maildir; atomic writes, random file names, flag renames, Windows-safe separator, folder create/rename/delete; interruption tests. | todo |
| 1.5 | `index`: SQLite index with full-text search; incremental updates; full rebuild from the store; 500,000-message generated test mailbox and measurements. | todo |
| 1.6 | `config`: settings and account definitions; credentials in the OS credential store. | todo |
| 1.7 | `protocols.pop3` + `sync`: POP3 over plain, STLS and TLS; UIDL tracking; leave-on-server options; delete from server only after verified local storage. | todo |
| 1.8 | `protocols.smtp`: sending over plain, STARTTLS and TLS; store sent messages in Sent. | todo |
| 1.9 | `engine` + `cli`: command registry; headless CLI for every command. | todo |
| 1.10 | `ui`: main window, folder tree, virtualized message list, reading pane (plain text and safe HTML). | todo |
| 1.11 | `ui`: composer (new, reply, reply all, forward), attachments. | todo |
| 1.12 | Key backup and recovery procedure, tested end to end. | todo |
| 1.13 | Packaging: Flatpak for Fedora and Kubuntu; test on GNOME. | todo |
| 1.14 | Windows test run on DEVELOPMENT. | todo |

## Milestone 2: IMAP

IMAP and IMAPS accounts: folder sync, server-side flags, offline cache in the same encrypted store, push (IDLE). Broken into tasks when milestone 1 is done.

## Later milestones (order to be decided)

- Gmail and Microsoft 365 via OAuth2
- Exchange (EWS or Microsoft Graph)
- Calendar (CalDAV) and tasks
- Contacts (CardDAV)
- Notes
- PGP / S/MIME
- Templates, snooze, send later, rules
- Encrypted index (optional)
