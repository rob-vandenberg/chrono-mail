# ROADMAP.md

Status values: `todo`, `in progress`, `done`, `blocked`.

Tasks are deliberately small. Each one is planned, approved ("proceed"), built, tested and logged on its own. Task details are refined in the plan for that task.

## Milestone 0: Foundations

| # | Task | Status |
|---|---|---|
| 0.1 | Development environment on KDEV: Python version, virtual environment, PySide6, Pyrefly (strict), pytest, VS Code with the Pyrefly extension. Linux equivalent of the `release` tooling. Also created `pyproject.toml` (version 0.0.1). | done |
| 0.2 | Project skeleton: `src/chrono_mail/` package layout, Pyrefly strict config, test runner, automated layer check. | done |
| 0.3 | Live test setup against a real mail server (the user's DirectAdmin Dovecot/Exim): SMTP and POP3 over plain, STARTTLS/STLS and implicit TLS, opt-in `live` tests. IMAP live tests follow in milestone 2. | done |
| 0.4 | Optional: GitHub Actions running tests, Pyrefly and the layer check on every push. | done (0.0.39, green) |

## Milestone 1: POP3 + SMTP, a usable mail client

| # | Task | Status |
|---|---|---|
| 1.1 | `core`: data model (Account, Folder, MessageRef, Flags, errors). | done |
| 1.2 | `mime`: parse and build messages; never crashes on malformed input; fuzz-style tests. | done |
| 1.3 | `crypto`: key generation, storage in the OS credential store, encrypt/decrypt message files. | done |
| 1.4 | `store`: encrypted Maildir; atomic writes, random file names, flag renames, Windows-safe separator, folder create/rename/delete; interruption tests. | done |
| 1.5 | `index`: SQLite index with full-text search; incremental updates; full rebuild from the store; 500,000-message generated test mailbox and measurements. | done |
| 1.6 | `config`: settings and account definitions; credentials in the OS credential store. | done |
| 1.7 | `protocols.pop3` + `sync`: POP3 over plain, STLS and TLS; UIDL tracking; leave-on-server options; delete from server only after verified local storage. Also the offline scripted and fake test servers for hostile server behaviour. | done |
| 1.8 | `protocols.smtp`: sending over plain, STARTTLS and TLS; store sent messages in Sent. | done |
| 1.9 | `engine` + `cli`: command registry; headless CLI for every command. | done |
| 1.10 | `ui`: main window, folder tree, virtualized message list, reading pane (plain text and safe HTML). | done |
| 1.11 | `ui`: composer (new, reply, reply all, forward), attachments. First version: plain text only, no drafts. | done |
| 1.12 | Key backup and recovery procedure, tested end to end. Done in the interface: backup at account creation, restore dialog when the key is missing (11 scenarios tested). | done |
| 1.12a | Drafts: save, autosave, resume, attachments kept, Outbox messages editable (0.0.16). | done |
| 1.12b | Message actions: Archive, mark all as read, empty Trash and Junk, drag and drop to folders (0.0.17). | done |
| 1.12c | Conversations: size in the list, grouped list, conversation bar (0.0.18). | done |
| 1.12d | Advanced search: OR, NOT, dates, sizes, dialog (0.0.19). | done |
| 1.12e | Settings window, account editing and removal, signatures (0.0.20). | done |
| 1.12f | Tray icon, new mail notifications, unread count in the title (0.0.21). | done |
| 1.12g | Save as .eml, view source, print (0.0.22). | done |
| 1.12h | Address completion in the composer, from the addresses in the mail (0.0.23, index schema 4). | done |
| 1.12i | Rich text (HTML) composing (0.0.24). | done |
| 1.13 | Packaging: .deb and .rpm built here (Nuitka in an Ubuntu 22.04 container, nFPM), tested on bare Ubuntu 22.04, 26.04 and Fedora 43; Flatpak dropped (D-123). Not yet: a test on GNOME. | done (0.5.127) |
| 1.14 | Windows test run on DEVELOPMENT: the setup.exe recipe is written (`packaging/build_windows.ps1`, `packaging/chrono-mail.iss`, `packaging/PACKAGING.md`), not yet run there. | in progress |

## Milestone 2: IMAP

IMAP and IMAPS accounts: folder sync, server-side flags, offline cache in the same encrypted store, push (IDLE). Design: ARCHITECTURE.md section 8, D-048.

| # | Task | Status |
|---|---|---|
| 2.1 | `protocols.imap`: own client, fake-server and live tests. | done |
| 2.2 | `sync`: state file, folder list, download of new messages, flags and removals from the server. | done |
| 2.3 | Local changes sent to the server (flags, move, delete, append of sent mail). | done |
| 2.4 | Engine and interface: IMAP account type, sync in `mail.fetch`, folder commands on the server. | done |
| 2.5 | IDLE (push) for the Inbox. | done |

## Later milestones (order to be decided)

- Gmail and Microsoft 365 via OAuth2
- Exchange (EWS or Microsoft Graph)
- Calendar (CalDAV) and tasks
- Contacts (CardDAV)
- Notes
- PGP / S/MIME
- Templates, snooze, send later (rules are done: 0.0.33)
- Encrypted index (optional)

## Local Folders and Favorites (from 0.0.45)
- DONE (0.0.45) Local Folders as shared storage for POP3 accounts, choice at setup and in the account settings, safe move both ways, identical reply behaviour.
- Next: sidebar (Local Folders node, collapsible accounts, account groups), unified views with Favorites and Inboxes sections, pinned folders, switches in the settings.
- DONE (0.0.46) Favorites and Inboxes headings, combined views, Local Folders node, collapsible accounts, three switches in the settings.
- Next: pin any folder to Favorites (add, remove, rename, reorder), account groups with a combined inbox per group, search inside combined views.
- DONE (0.0.47) Pinned favorites (add, remove, rename, reorder), account groups with a combined inbox, search inside combined views.

## Milestone 3: Calendars, tasks and contacts, local (design: ARCHITECTURE.md section 9, D-071)

| # | Task | Status |
|---|---|---|
| 3.1 | Design, dependencies, vCard (contacts): parsing, building, keeping unknown properties. | done (0.0.57) |
| 3.2 | iCalendar (events and tasks): parsing, building, recurrence, time zones, reminders. | done (0.0.58) |
| 3.3 | `pimstore`: encrypted items and the rebuildable index; local collections. | done (0.0.59) |
| 3.4 | Engine commands for collections, events, tasks, contacts and tags. | done (0.0.60) |
| 3.5 | The four-button shell: sections with their own sidebar, toolbar and status line. | done (0.0.61) |
| 3.6 | Contacts section (address books, list, detail, tags, History). | done (0.0.62) |
| 3.7 | Tasks section (lists, quick add, detail pane, status marks). | done (0.0.63) |
| 3.8 | Calendar section (Day, Work Week, Week, Upcoming, Month, Agenda; event editor; mini calendar). | done (0.0.64) |
| 3.9 | Tags for mail; reminders and notifications. | open |

## Milestone 4: CalDAV and CardDAV (Kerio Connect test server, D-078)

| # | Task | Status |
|---|---|---|
| 4.1 | `protocols.dav`: discovery, collections, items, sync tokens, writing, certificate pinning; fake server and live tests. | done (0.0.65) |
| 4.2 | `sync`: state per account, pull and push, conflicts, commands (`dav.discover`, `account.set-dav`, `dav.sync`). | done (0.0.66) |
| 4.3 | Account settings and interface: the tab "Calendars and contacts", sync on Refresh and on a timer. | done (0.0.66) |
| 4.4 | Invitations and replies (iTIP); more live tests. | open |

## Milestone 5: Google (OAuth2, Gmail by IMAP, Calendar, People, Tasks)
## Milestone 6: Microsoft (OAuth2, Graph: mail, calendar, contacts, To Do)
## Milestone 7: On-premises Exchange (ActiveSync, testable on the Kerio server; or EWS), only when needed
- DONE (0.0.70) The Inboxes heading is removed from the folder tree (it was never requested, D-061).
- DONE (0.0.71) Add account dialog: a certificate accepted for a server address is dropped when that address, port or security changes.
- DONE (0.0.72) Accepted certificates are kept per server (host and port), D-082.
- DONE (0.0.73) New look of the main window, step 1: the Menu button, the toolbar with an orange New button, the pill-shaped search field, the folder list with a heading, section buttons with a line under the open one (D-083).
- DONE (0.0.74) A message window saves Always for this sender and knows the allowed senders before it shows the message.
- DONE (0.0.75) The folder list remembers which headings, accounts and folders are open or closed, also after a restart.
- DONE (0.0.76) Blue instead of orange; Settings, Accounts, Rules and Exit in the root of the Menu; plain section icons (blue when open); Contacts before Tasks (D-084).
- DONE (0.0.77) New look, step 2: the message list and the reading pane (Received instead of Date, blue dot for unread and an empty circle for read, flat list header, reading pane header and banner). Group headers in the list are not done.
- DONE (0.0.78) New look, step 3: the composer; rich text is the default for new messages (D-085).
- DONE (0.0.79) The column headers of the message list have a thin line between them again (removed by mistake in 0.0.77).
- DONE (0.0.80) The test run no longer crashes at the end (a web view waiting to be deleted at exit): the ui tests finish the pending deletions after each test.
- DONE (0.0.81) The Menu button is the first, blue button of every toolbar and the row of the menu bar is gone; New is a plain toolbar button.
- DONE (0.0.82) Text size of the interface under Appearance (8 to 48 pt; the first start saves the system size, so it is never 0), a button for the system size; the Menu button shows U+2630.
- DONE (0.0.83) The size and position of the message windows, the composers and the Settings window are remembered, and every saved position is only used when the window would be reachable on a connected screen (the main window too).
