# ROADMAP.md

Status values: `todo`, `in progress`, `done`, `blocked`.

Tasks are deliberately small. Each one is planned, approved ("proceed"), built, tested and logged on its own. Task details are refined in the plan for that task.

## Milestone 0: Foundations

| # | Task | Status |
|---|---|---|
| 0.1 | Development environment on the Linux computer: Python version, virtual environment, PySide6, Pyrefly (strict), pytest, VS Code with the Pyrefly extension. Linux equivalent of the `release` tooling. Also created `pyproject.toml` (version 0.0.1). | done |
| 0.2 | Project skeleton: `src/chrono_mail/` package layout, Pyrefly strict config, test runner, automated layer check. | done |
| 0.3 | Live test setup against a real mail server (the user's DirectAdmin Dovecot/Exim): SMTP and POP3 over plain, STARTTLS/STLS and implicit TLS, opt-in `live` tests. IMAP live tests follow in milestone 2. | done |
| 0.4 | Optional: GitHub Actions running tests, Pyrefly and the layer check on every push. | removed (1.1.246, D-205): it failed at every push since the tests left git (D-161) |

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
| 1.12c | Junk filter: the server's spam verdict and a learning filter on new mail, before the rules (2.1.308, D-238). The $Junk/$NotJunk marks on the server: B-19. | done |
| 1.12d | Undo and Redo of the actions on mail: Ctrl+Z, Ctrl+Y (B-18 step 1, 2.2.313, D-240). Steps 2 (folders) and 3 (calendar, contacts, tasks) open. | done |
| 1.12e | Delayed sending (a countdown with Send now and Cancel, or until the next refresh), the scheduled refresh sends and receives, the question on Quit with mail in the Outbox (B-21, B-20, 2.2.314, D-241); one Automatically row with Send only (2.3.315). | done |
| 1.12c | Conversations: size in the list, grouped list, conversation bar (0.0.18). | done |
| 1.12d | Advanced search: OR, NOT, dates, sizes, dialog (0.0.19). | done |
| 1.12e | Settings window, account editing and removal, signatures (0.0.20). | done |
| 1.12f | Tray icon, new mail notifications, unread count in the title (0.0.21). | done |
| 1.12g | Save as .eml, view source, print (0.0.22). | done |
| 1.12h | Address completion in the composer, from the addresses in the mail (0.0.23, index schema 4). | done |
| 1.12i | Rich text (HTML) composing (0.0.24). | done |
| 1.12j | The Operations window of send and receive: a row per account with its step and bar, Errors, Log; three settings for when it opens and closes (D-138). | done (0.5.145) |
| 1.13 | Packaging: .deb and .rpm built here (Nuitka in an Ubuntu 22.04 container, nFPM), tested on bare Ubuntu 22.04, 26.04 and Fedora 43; Flatpak dropped (D-123). Not yet: a test on GNOME. | done (0.5.127) |
| 1.14 | Windows test run on the Windows computer: the setup.exe recipe is written (`packaging/build_windows.ps1`, `packaging/chrono-mail.iss`, `packaging/PACKAGING.md`), not yet run there. | in progress |

## Milestone 2: IMAP

IMAP and IMAPS accounts: folder sync, server-side flags, offline cache in the same encrypted store, push (IDLE). Design: ARCHITECTURE.md section 8, D-048.

| # | Task | Status |
|---|---|---|
| 2.1 | `protocols.imap`: own client, fake-server and live tests. | done |
| 2.2 | `sync`: state file, folder list, download of new messages, flags and removals from the server. | done |
| 2.3 | Local changes sent to the server (flags, move, delete, append of sent mail). | done |
| 2.4 | Engine and interface: IMAP account type, sync in `mail.fetch`, folder commands on the server. | done |
| 2.5 | IDLE (push) for the Inbox. | done |

## Senior refactor (from 2026-10-20)

The inventory, the order and the progress are in `REFACTOR.md` (not repeated here).

- [x] Outside the refactor, 1.1.206: a message has at most two files, itself and its properties file (D-176); fixes a repeating message from an account in Local Folders that was never sent.
- [x] Outside the refactor, 1.1.207: Edit on Settings > Scheduled messages; a scheduled message is edited where it waits and stays scheduled (D-177).
- [x] Outside the refactor, 1.1.208: Edit works every time; Edit and New in the menu of a Scheduled view; back to drafts by dragging to Drafts; the page keeps New, Edit, Send now, Pause (D-178).
- [x] Outside the refactor (the owner's bug report), 1.1.209: a dragged column keeps its place when a column is added or another folder opens (D-179).
- [x] Outside the refactor (the owner), 1.1.210: a new account finds its calendars, contacts and tasks by itself; Additional folders found with a tick per folder; Find calendars, contacts and tasks in the account editor (D-180 to D-183, task 4.5).
- [x] Outside the refactor (the owner), 1.1.211: an optional Account name, shown only in Chrono Mail; Name is called Your name (D-184).
- [x] Outside the refactor (the owner), 1.1.212: Add account… in the Calendar, Contacts and Tasks sections; accounts of calendars, contacts and tasks only (Nextcloud, shared calendars) (D-185).
- [x] Outside the refactor (the owner), 1.1.213: the login per server with "Use the same user name and password as for incoming mail"; no help lines in the account windows (D-186).
- [x] Outside the refactor (the owner), 1.1.214: the folders of the calendar server with their ticks in the account editor, applied on Save; "Select the folders you would like to add:" (D-187).
- [x] Outside the refactor (the owner), 1.1.215: one 3 px separator with centred dots between every pair of panes (D-188).
- [x] Outside the refactor (the owner), 1.1.216: the contact details without a border of their own, a 50 px avatar.
- [x] Outside the refactor (the owner's report), 1.1.217: the calendar sync with a server that writes addresses two ways (Kerio: no item came in); every mail check also syncs the calendars; the mouse wheel goes through the months and weeks (D-078, D-079, D-189).
- [x] Outside the refactor (the owner's report and the Windows computer's report), 1.1.218: Refresh in the sections synchronises and says so; a cut-short or emptied listing never empties a calendar; one slow item costs only itself; an event every second no longer freezes a view (D-079, D-174, D-190).
- [x] Outside the refactor (the owner), 1.1.219: one colour per appointment from its calendar's own palette, the palette editor, appointments redrawn at once (D-191).
- [x] Outside the refactor (the Windows computer's report), 1.1.220: a real mass delete on the server is done here at the second synchronisation (D-079).
- [x] R-27 and R-26 (audit round 3), 1.1.221: one palette per account; a server calendar's name and colour on the server first and followed from it, kept through untick and re-tick (D-191, D-192); the basic rule D-193.
- [x] R-36, 1.1.223: one connection kept open to a calendar server (Kerio closes every connection: no gain there).
- [x] R-23 with R-24 step B, 1.1.225: appointments, tasks and contacts on the server first; sync conflicts kept and settled by the engine (D-194).
- [x] R-23 with R-24 step C, 1.1.226: Settings > Sync conflicts, the triangle, the sidebar entry, the waiting icon and count (D-194).
- [x] Step D, 1.1.227: IMAP refusals as sync conflicts (Keep here only for mail that exists only here); marks never clash; the Mail entry; the waiting mark and count for mail (D-195).
- [x] R-25 step A, 1.1.234: a calendar, task list or address book deleted on the server goes here too, only on the server's confirmation; with changes here a sync conflict (D-196).
- [x] R-28 step B, 1.1.235: Delete of a server's calendar deletes it on the server first; offline later; changed there or refused: a sync conflict (D-197).
- [x] R-29 step C, 1.1.236: New in the account chosen, on its server first; offline later; refused then: Keep here only or Retry sync (D-198).
- [x] R-30, 1.1.237: IMAP folders deleted or renamed on the server followed here, only on its confirmation; changes here: a sync conflict (D-199).
- [x] R-35, 1.1.238: an expunge never removes more than the messages named; one way of moving (D-117).
- [x] R-37, 1.1.239: the sync-conflict commands go through the mailbox; the mailbox guard test passes again.
- [x] R-31, 1.1.240: IMAP folder rename and delete work offline, done on the server at the next check; refused or changed there: a sync conflict (D-200).
- [x] R-34, 1.1.241: the server's list of folders to show is followed: a folder taken off it goes here after one last sync, one put on it comes; ticks, standard folders and mail only here win (D-202). Contact groups: R-39, later.
- [x] R-03, 1.1.242: the filters and Advanced search's days and sizes narrow Favorites and Tags views (D-203).
- [x] R-04, documents only: D-146 step 5 checked: done for Calendar and Contacts, not for Tasks (R-06); D-146 corrected.
- [x] R-05, 1.1.243: one set of server boxes for Add and Edit account; a typed port stays; Test connection never asks for a stored password (D-204).
- [x] R-06, 1.1.244: Tasks moves only the task that changed and its heading; Contacts' tag list follows in place (D-146).
- [x] R-07, 1.1.245: Tasks and Contacts share one copy of their list actions (`ui/pimlist.py`); nothing visible changed.
- [x] R-17, 1.9.264: the main window is made of parts with one job each (folder list, message list, columns, filters and search, actions, tags, reading, writing, mail checks, status line); nothing visible changed (D-221).
- [x] R-44 part 1, 1.9.265: the missing-file warning again, Tasks fetches only the rows in view, a calendar change asks nothing more, the minimum width at start, the count line with "…" (D-222).
- [x] R-08, 1.1.247: actions on several messages go as one command per folder, on IMAP in groups of 500; one error line for what failed (D-206).
- [x] R-09, 1.1.248: a mail check reads this computer's mail once and asks the server only what changed since the last check where it can tell (D-207).
- [x] R-40, 1.1.249: a message removed on the server gives no false error line.
- [x] R-10, 1.1.250: the folder numbers come from the engine with every fact; the window counts nothing itself (D-208).
- [x] R-11, 1.1.251: the folder list is built once and never again; every change happens in place (D-209).
- [x] R-41, 1.1.252: a renamed folder takes its pins in Favorites along (D-210).
- [x] R-12, 1.2.253: every setting is read and written in one place, typed (D-211).
- [x] R-42, 1.2.254: an imported rule that moves to a standard folder by its name goes to Chrono Mail's own (Junk E-mail: Move to Junk) (D-212).
- [x] R-13, 1.3.255: Settings OK takes into use only what changed; a column comes or goes in place, the selection stays (D-213).
- [x] R-14, 1.5.256: what the list shows is one value; text being typed is never searched by a sort or a filter; no signals blocked (D-214).
- [x] Outside the refactor (the owner), 1.5.257: the address of the link under the pointer in the bar at the bottom (message window and reading pane), with the orange triangle when it leads to another main domain than the sender's (D-215).
- [x] Outside the refactor (the owner's bug report), 1.5.258: the search history no longer takes the mouse or the keyboard from the search field; text in the field can be selected with a double click or a drag while it is shown (D-216).
- [x] Outside the refactor (the owner's crash and bug reports), 1.5.259: the search history watches only the main window (a program-wide Python filter crashed PySide); Enter and Insert act on messages only while the message list has the focus (D-216, D-217).
- [x] R-15, 1.6.260: the message list holds every view one way: the order of every message and the rows loaded; Favorites, Tags and searches count every message at once; nothing is asked again (D-218).
- [x] Outside the refactor (the owner's observation), 1.6.261: the folder list appears in one step; the start asks each place where mail is kept once; the tray counts Local Folders once (D-219).
- [x] R-43, 1.7.262: every key acts on the part that has the focus; Delete in the folder list and in the sections' sidebars deletes the folder or calendar after its question, never the messages or items (D-217).
- [x] R-16, 1.8.263: the wallet access, the opening of the index files and the reading from IMAP and POP3 servers each exist once (D-220); nothing visible changed.
- [x] R-21, 1.9.268: Settings > Scheduled messages changes only the line an action touched; the selection stays; a failed Send now says why (D-224). R-18 skipped (the owner: no file over 2,000 lines).
- [x] Outside the refactor (the owner), 2.0.283: no personal detail in the project folder or on GitHub; a new repository with a fresh history (D-230).
- [x] Outside the refactor (the owner), 2.0.281: the index rebuild window: checked at start, rebuilt before the mail is shown, Run in background, Abort after a question; format 11 upgraded in place (D-229).
- [x] B-16 (the owner), 2.0.280: columns per folder: every folder and view keeps its own columns, order, widths and sort; six kinds of folder each start with their own setup; Apply to all folders (header menu, Menu > Folder); the Sent column (Saved in Drafts); index format 12 (D-228).
- [x] Outside the refactor (the owner), 1.10.279: an item deleted here offline and changed on the server before the next sync is a sync conflict, as online (D-194).
- [x] Outside the refactor (found in R-39), 1.10.278: one item is read from a calendar server by the multiget first; Kerio's 404 on a contact's GET no longer makes a false "deleted on the server" conflict (D-078).
- [x] R-39, 1.10.277: contact groups: under their address book, made, renamed, deleted, members added and removed (also by dragging), written to, offered by name in the composer; on the server first like every card (D-227). The refactor list is done.
- [x] Outside the refactor (the owner), 1.9.276: Received is when the mail server received the message (D-226); "1 message".
- [x] R-19 round 5, 1.9.275: R-19 done: every answer kept as its type; 22 getattr left, each with its reason; guards.
- [x] R-19 round 4, 1.9.274: the mail parts keep the engine's answers as their real types; nothing visible changed.
- [x] R-19 round 3, 1.9.273: the calendar, contacts and tasks windows typed; no untyped engine call left. 1.9.272: the sections' certificate look works.
- [x] R-19 round 2, 1.9.271: every engine call of the account, settings, rules, tags, key and import windows typed; nothing visible changed.
- [x] R-19 round 1, 1.9.270: every engine call of the mail parts typed; nothing visible changed.
- [x] Outside the refactor (the owner), 1.9.269: Drafts, Outbox, Scheduled and Snoozed show how many messages wait, the other folders their unread messages (D-225).

## Later milestones (order to be decided)

- Gmail and Microsoft 365 via OAuth2
- Exchange (EWS or Microsoft Graph)
- Calendar (CalDAV) and tasks
- Contacts (CardDAV)
- Notes
- PGP / S/MIME
- Templates, snooze, send later (rules are done: 0.0.33)
- Encrypted index (optional)

## Import from the reference client (from 0.5.132, D-124)

| # | Task | Status |
|---|---|---|
| I.1 | Import from the reference client's three exports (settings .xml, mail .eml folder, backup .zip): POP3 accounts with passwords, rules, contacts, all mail into Local Folders with its read, flagged and replied marks; resumable, no duplicates. | done (0.5.132); the owner's own import and check to do |
| I.2 | The rest of the password key (passwords longer than 12 characters), from a test export with a known long password. | open |

## Backup and restore (B-22, from 2.4.322, D-244)

| # | Task | Status |
|---|---|---|
| K.1 | Menu > File > Backup…: the parts (Messages, Mail rules, Calendar items, Contacts, Tasks, Settings) and the accounts (Select all; Local Folders and On this computer apart); Include account passwords forces Protect the backup with a password; the backup password made by the program (24 capitals, lower-case letters, digits) with Copy and "I have saved the backup password"; progress, Cancel, Run in background, the busy bar brings the window back; the file written under a temporary name; Restore… greyed; the key items renamed "Show the recovery code…" and "Use a recovery code…". | done (2.4.322; "Backup…" one word in 2.4.323) |
| K.2 | Menu > File > Restore… (the agreed design): 1. no key on this computer yet: it is made first and its recovery code shown in its own window (the recovery code window during a restore: Escape and close ask first, one line "This code is for this computer. The password of your backup is asked for in the next step.", Copy says "Copied" for 2 s, "this computer's password store", no "Save a recovery file…"); 2. then the .cmz is chosen; 3. its backup password asked only when it is protected. Options, both off: "Remove all data on this computer first" (this computer's copy only, never the server; set aside and removed only after the restore finished and checked, put back on a failure or cancel) and "Replace items that already exist" (off: skipped). Everything comes back with its account ids, the sealed files encrypted again with this computer's key (store.json and pim.json get its key id); the indexes rebuilt; the server wins at the first check (dav-state and imap-state only for what was restored). Afterwards every account made is tested like Add account and a short list says per account: connected, or why not (no connection, certificate, login); an accepted certificate is carried over without asking. | done (2.4.324) |

## Documentation (B-07, from 2.3.317, D-242)

| # | Task | Status |
|---|---|---|
| U.1 | `README.md` for the people who use the program (what it is, install and uninstall the easy way first, the first start, the recovery code); the developer notes moved to `CONTRIBUTING.md`. | done (2.3.317) |
| U.2 | The user guide in `docs/`, one page at a time, each read by the owner before the next: Getting started, Accounts, Reading mail, Writing and sending, Keeping order, Junk mail, Searching, Calendar, Contacts, Tasks, Working offline and sync conflicts, Keeping your mail safe, Moving from another program, Keyboard shortcuts, Questions and problems. | in progress: the contents page `docs/README.md` and Getting started done (2.3.317) |
| U.3 | Screenshots with made-up mail, after the restyle of Settings and of Calendar, Contacts and Tasks. The demo profile for them (D-243, `./demo.sh`): done: the 14 accounts, folders, Local Folders, rules and settings (2.3.320); the 100,000 messages with spam, the calendars, tasks and contacts, and `tools/demo/COVERAGE.md`, what in the demo shows every action and setting (2.3.321). The screenshots themselves: open. | in progress |
| U.4 | Windows in the README once a setup.exe is on the release page. | open |

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
| 3.9 | Tags for mail (D-128, five steps: engine, window, rules, importer, IMAP keywords); reminders and notifications. | tags done (0.5.134 to 0.5.138: engine, window, rules, importer, IMAP keywords); reminders and notifications open |

## Milestone 4: CalDAV and CardDAV (Kerio Connect test server, D-078)

| # | Task | Status |
|---|---|---|
| 4.1 | `protocols.dav`: discovery, collections, items, sync tokens, writing, certificate pinning; fake server and live tests. | done (0.0.65) |
| 4.2 | `sync`: state per account, pull and push, conflicts, commands (`dav.discover`, `account.set-dav`, `dav.sync`). | done (0.0.66) |
| 4.3 | Account settings and interface: the tab "Calendars and contacts", sync on Refresh and on a timer. | done (0.0.66) |
| 4.4 | Invitations and replies (iTIP); more live tests. | open |
| 4.5 | The server found by itself (the mail server and the address's domain, RFC 6764 well-known), the public folders, a tick per folder, the mail certificate accepted for it (D-180 to D-183). | done (1.1.210) |
| 4.6 | "Add account" in the Calendar, Contacts and Tasks sections: a calendar server for a mail account, or an account of calendars, contacts and tasks only (D-185). | done (1.1.212) |
| 4.7 | Contact groups (vCard 4 and Apple's form, Kerio checked live), kept in step like every card (R-39, D-227). | done (1.10.277) |

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
- DONE (0.5.167) The toolbar of every section folds when the window is narrow: Menu and the search field always stay, the other buttons fold into More from the right; This folder shows only its ▾ first, the calendar's views merge into one button first; Calendar, Contacts and Tasks get Mail's toolbar look, icons on every button and Mail's search field (D-154).
- DONE (0.5.168) The search field of Calendar, Contacts and Tasks is as wide as Mail's without This folder; the small month calendar can be 240 px narrow, so the sidebar of every section can be dragged to 240 px and the window can be 768 px narrow (D-155).
- DONE (0.5.169) The day columns of the small month calendar shrink with it: at 240 px all seven days are shown (in 0.5.168 the last days were cut off) (D-155).
- DONE (0.5.170) The window has one toolbar that shows the row of the section on screen; showing another section no longer makes the window wider (D-156).
- DONE (1.0.170) The first public release: the owner moved the major version to 1; the .deb and .rpm are on the GitHub release page (the Windows setup.exe follows from the Windows computer).
- DONE (1.0.171) The actions of a rule can be moved up and down in the rule editor (D-157).
- DONE (1.1.172) Every colour is the program's own, in one file (`core/colours.py`): a dark theme (the colours Linux showed) and a light theme (white, from the owner's reference screenshot); Fusion on every platform; the Style setting is gone; a thin line under group headings (D-158).
- DONE (1.1.173) The window's minimum width is 768 px in every section (more only when the contents need it at a larger size), so showing Mail never makes the window wider (D-159).
- DONE (1.1.174) "Change…" is "Edit…" on Settings > Accounts and Settings > Rules; in the rule editor the actions below Stop processing rules are greyed out (D-160).
- DONE (1.1.204) Settings > Sidebar: a tick for each standard folder of Local Folders that exists, Snoozed, Archive and Scheduled included (D-173).
