# ARCHITECTURE.md

The design as it stands now (version 1.6.260). The rules behind it, with their reasons, are in `DECISIONS.md` (the D-numbers below). Text that no longer holds is in the git-ignored `ARCHITECTURE-ARCHIVE.md` (search only).

## 1. Goals that shape the design

1. **Never lose mail.** Crash-safe storage; the server copy is only removed after the local copy is verified.
2. **Confidential by default.** Message contents are encrypted on disk.
3. **Scale.** Responsive with 500,000 messages.
4. **One codebase** for KDE, GNOME, Windows and macOS.
5. **Testable without a screen.** Every action is an engine command, reachable from a headless CLI.
6. **Grows in steps.** POP3 and SMTP, IMAP, calendars, contacts and tasks are done; Gmail and Exchange come later. The internals are shaped like IMAP, so later steps extend rather than rewrite.

## 2. Package layout

All code lives in the package `chrono_mail` (src layout: `src/chrono_mail/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Account, Folder, MessageRef, Flags, error types, colours (`core/colours.py`, D-158), the thin door to the OS credential store (`core/credentials.py`, D-220). No other I/O. |
| `crypto` | Key management, encryption and decryption of message files. |
| `mime` | Parsing and building messages (wraps the standard `email` package), robust against malformed input. |
| `config` | Settings, account definitions, credentials (through the OS credential store). |
| `pim` | vCard and iCalendar parsing and building, recurrence, time zones (section 7). No I/O. |
| `store` | The encrypted Maildir message store: atomic writes, reads, flag changes, folders. |
| `index` | The rebuildable search and list index; `index/database.py` opens every index file, also the one of `pimstore` (D-220). |
| `protocols` | Protocol clients: `pop3`, `smtp`, `imap`, `dav`; `lineconnection` sends to and reads lines from IMAP and POP3 servers (D-220). |
| `pimstore` | Storage for calendars, tasks and contacts, built on `store`. |
| `sync` | Moving mail and items between server and store (POP3 download, IMAP sync, DAV sync), index rebuild and repair. |
| `engine` | Session object and command registry. Every user action is a command here. |
| `cli` | Headless command-line front end (`chrono-mail-cli`). |
| `ui` | PySide6 user interface. Thin: calls engine commands only. |

## 3. Layers

A module may import only from lower layers. Modules in one layer are independent siblings and do not import each other. Enforced by import-linter (`.venv/bin/lint-imports`, contract in `pyproject.toml`, D-018).

| Layer | Modules |
|---|---|
| L0 | `core` |
| L1 | `crypto`, `mime`, `config`, `pim` |
| L2 | `store`, `index`, `protocols` |
| L2.5 | `pimstore` |
| L3 | `sync` |
| L4 | `engine` |
| L5 | `cli`, `ui` |

Only `ui` may import PySide6.

## 4. Storage

### 4.1 Message store: encrypted Maildir (D-004, D-024)

- One directory tree per account, one Maildir per mail folder. Folders are `f-<12 hex>` directories with a `folder.json` (path, role); the tree exists only in the paths.
- Each Maildir has `tmp`, `new` and `cur`. A message is written completely to `tmp`, flushed to disk, then moved into place in one atomic rename, and verified (read back, decrypt, compare). A crash damages at most the one message being written.
- **One file per message**, the complete raw message, **encrypted** with authenticated encryption, which also detects damage or tampering per message.
- **File names are random** (32 hex), plus the flag suffix. The info separator is `!` on every platform (Windows forbids `:`), so a store can move between platforms. Changing a flag is an atomic rename.
- **Encryption is on by default**, chosen per account. Switching an existing store is a migration and is not built.
- A deleted folder moves to `.deleted/`; purging is explicit.
- **A message has at most two files** (D-176): the message itself and, only when it has any, its properties file `properties/<store id>.json` in the store (tags, snooze time and origin, archive origin: what cannot be written into received mail). The properties folder is one per mailbox, so a move never touches it. Our own waiting messages carry their schedule in themselves (a repeating message its whole timetable); recording progress replaces the message under the same id, verified before the one rename.

### 4.2 Index (D-005, D-025)

- Holds what the window needs fast: folder contents, sorting, groups, threads, search (sender, recipients, subject, date, size, flags, tags, full text).
- Never the source of truth: it can be deleted at any time and rebuilt from the store. Normally updated one change at a time.
- SQLite with FTS5, one per account, **not encrypted** (dates, senders, recipients and subjects are treated as lower sensitivity). Format history in D-031.

### 4.3 Backup (D-244)

- File > Backup… writes a `.cmz`: a ZIP (AES-256 when protected) with `chrono-mail-backup.json` (format, version, date, parts, accounts, the files that are encrypted here: `sealed`), `config/` (the chosen accounts' definitions without this computer's key id, rules, sidebar, tags, the window's own settings as `window.json`, `passwords.json` only when protected) and `data/` (the files of the chosen accounts exactly as kept here, decrypted; never the indexes, `.deleted` or `tmp`). `engine/backup.py`, commands `backup.*`.
- The file list is taken under each mailbox's lock; a message that moves meanwhile is found again by its id and backed up once.
- File > Restore… (`engine/restore.py`): an account that is not here is written whole in `accounts/.restoring-<id>` and renamed into place, its `sealed` files encrypted with this computer's key and `store.json`/`pim.json` given its key id, the empty Maildir and collection folders made again (a ZIP keeps no empty folders); an account that is here gets the messages (by id, else folder and Message-ID) and items (by UID) it lacks, never what its IMAP state or DAV state shows the server had. Remove all data first moves `accounts`, the junk words and the rules, sidebar and tags files to `restore-aside-*` and takes the accounts out of the settings; a failure or cancel puts them back, success removes them.

### 4.4 Keys (D-023)

- The data key encrypts message files. It lives in the OS credential store (KWallet, GNOME Keyring/Secret Service, Windows Credential Manager, macOS Keychain) through `keyring`. A master-password mode is not built.
- **Key backup and recovery is mandatory:** a passphrase-protected recovery file (scrypt + AES-GCM) and a printable recovery code with a checksum.
- AES-256-GCM from `cryptography`, with a per-file key derived by HKDF-SHA256.

## 5. Accounts

- The model is shaped like IMAP: an account has a tree of folders; a message has a stable identity, flags and a location (D-006, D-021).
- **POP3** is a restricted account: the server offers only an inbox; all folders are local, in the account's own folders or the shared Local Folders (D-060). Downloads are tracked by UIDL; leave on server (the default), delete after download, after N days, or when deleted here (D-027, D-036).
- **SMTP** is the sending side; sent messages go to Sent (D-028).
- **IMAP** is an offline mirror of the server (section 6).
- An account is one login on one server. Mail accounts (POP3, IMAP) may also have a calendar and contacts server. An account of kind **Calendars and contacts** (`dav`, D-185) has only that: no mail, no address; the engine has no place for its mail (`storage_groups` leaves it out) and the window keeps it out of everything about mail.
- Every account may have an account name, shown only inside Chrono Mail (D-184).
- **Security:** plain, STARTTLS and implicit TLS; certificate verification is always on, and an exception is only ever accepted by the person (D-082).
- **Authentication:** user name and password, kept in the OS credential store. OAuth2 comes with Gmail and Microsoft 365.

## 6. IMAP (the server is the truth)

IMAP accounts use the same encrypted store, index, commands and window as POP3 accounts. What differs is where the truth about which messages exist and what their marks are lives: on the server (D-048).

### 6.1 Pieces

| Piece | Layer |
|---|---|
| `protocols/imap.py`: own IMAP4rev1 client over TLS or STARTTLS, no `imaplib` (it has no size caps). | L2 |
| `sync/imap_state.py`: per account and folder: UIDVALIDITY, the highest UID seen, the map store id ↔ UID (`imap-state.log`, append-only, compacted). | L3 |
| `sync/imap_sync.py`: the folder list, new mail, server-side marks and removals brought here, and the comparison that sends offline changes (D-050). | L3 |
| `engine/imap_online.py`: one connection per account for the person's actions, server first (D-117). | L4 |
| `mail.wait`: IDLE on the Inbox, which triggers a normal check (D-052). | L4 |

### 6.2 Identity and the mirror (D-049)

- A message has a local store id and, for IMAP, a server identity (folder, UIDVALIDITY, UID). The map lives in the state file, not in the message file, so POP3 accounts are unaffected.
- A folder is synchronised in this order, every step safe to repeat after an interruption:
  1. `EXAMINE` the folder; compare UIDVALIDITY with the stored value.
  2. New messages are fetched whole (size-capped), stored and verified, and only then entered in the map.
  3. Marks are updated here where the server differs.
  4. UIDs gone from the server are removed here through the store's normal delete (the file goes to `.deleted`), never erased.
  5. Changes made here while offline are sent (the comparison, section 6.3).
- A check reads this computer's mail once (not once per folder). A folder whose last check left nothing open remembers where the server stood (UIDNEXT, HIGHESTMODSEQ); the next check asks only what changed since (CONDSTORE, QRESYNC), nothing when the change counter stayed; without a counter (Kerio) the marks of the known messages and the sizes of the new ones; anything that does not add up asks for everything (D-207).
- Safety rules (AGENTS.md 4.1): nothing is removed from the server unless the local copy is stored and verified; removals are refused when more than half of at least 10 messages vanish; a changed UIDVALIDITY never deletes local mail (matched by Message-ID; unmatched mail goes to `Recovered/<folder>`); a server answering something unexpected fails that folder, never the program.

### 6.3 Changes by the person (D-117, D-050)

- Every action of the person in an IMAP account (marks, move, copy, archive, junk, snooze, delete, empty, drafts, the Sent copy, rules on new mail) is done **on the server first**, by UID, and here only after the server confirmed it. A refusal changes nothing here.
- When the server cannot be reached, the action is done here only. The next sync compares three states (the store, the remembered state, the server) and sends it; never a queue. A delete for good made offline is noted, so the safety rules never hold it back.
- Without MOVE: copy, then remove by UID. Without UIDPLUS a message is only marked deleted and the folder is never expunged.

### 6.4 Folders (D-118, D-120)

- `LIST` with the delimiter; names decoded from modified UTF-7; a folder path stays a tuple of names. SPECIAL-USE sets the role, the usual English names are the fallback. The server's list of folders to show (its subscriptions) is followed: a folder taken off it goes here after one last sync, one put on it comes (D-202); standard folders and the folders ticked here are synchronised whether or not they are on it. The ticks in Edit account > Folders are this computer's only; a folder left out (unticked) is ignored completely.
- Create, rename, delete, Empty folder and Mark all as read are done on the server first. Rename and delete are refused offline.

### 6.5 The client

- Built on `ssl` and `socket` like the POP3 client: every line and literal has a size cap, every wait a timeout; unsolicited responses are tolerated; a tag mismatch or garbage is a `ProtocolError`. Used when offered: `UIDPLUS`, `MOVE`, `SPECIAL-USE`, `IDLE`, `LITERAL+`, keywords for tags (D-132).
- Tests: a scripted fake server (giant literals, endless lines, wrong tags, drops mid-literal) and the live servers (D-020).

## 7. Calendars, tasks and contacts

### 7.1 Goals

Calendars, tasks and contacts next to mail, for every kind of account: local (no server) and CalDAV/CardDAV servers are done; the provider APIs (Google, Microsoft Graph) and, only when testable, Exchange follow (D-071). The window has four sections (Mail, Calendar, Contacts, Tasks), each with its own sidebar, toolbar row and views (D-074).

### 7.2 Items are the standard text formats

- An event or a task is one iCalendar text (RFC 5545) with its VEVENT or VTODO, its changed occurrences and the VTIMEZONE it uses. A contact is a vCard text (RFC 6350; 3.0 and 2.1 on input).
- The text is the source of truth and is kept as it came: unknown properties are kept and sent back. Editing changes only the properties that were edited.
- Servers exchange the same texts (CalDAV, CardDAV), so no translation is needed for them.

### 7.3 Modules

`core.pim` (L0, kinds and references), `pim` (L1, parsing, recurrence, time zones; wraps `icalendar` and `recurring-ical-events`), `pimstore` (L2.5), `protocols/dav.py` (L2, D-078), `sync/davsync.py` (L3, both ways, D-079), commands `pim.enable`, `collection.*`, `contact.*`, `contact-group.*` (contact groups are cards of their address book, D-227), `event.*`, `task.*`, `tag.*` (L4, D-073), the four sections (L5).

### 7.4 Storage (D-072)

- One directory per account with `pim.json` and `collections/<id>/`, each item an encrypted text written atomically like mail, with the same data key. A damaged item is one lost item, never a stalled collection. Deleted items go to `.deleted/` (purged after 30 days).
- The index (`pim.sqlite`) holds what the views need and is rebuilt from the items at any time. Occurrences of repeating events are expanded when a range is asked for, not stored.
- Server state (sync tokens, etags) is kept beside the items (`dav-state.json`). Local collections belong to a pseudo-account, like Local Folders.

### 7.5 Finding the server (D-180 to D-183)

- An account's calendar server is found by itself after Add account: the incoming mail server, then the domain of the address, at the well-known addresses (RFC 6764), each asked first without a login. The person ticks which folders to keep in step ("Additional folders found"); the account remembers the ones left out, so folders made on the server later arrive by themselves.
- Every folder is listed: the own ones and the public ones a server keeps in `#public` next to them (Kerio).
- A certificate already accepted for the account's mail is accepted for its calendar server when it is exactly the same one; no other certificate is ever accepted without the person.

- **Colours (D-191):** every account has one palette of named appointment colours, shared by its calendars, kept with the account; a calendar's own colour comes first. An appointment keeps the id of one of them (X-CHRONO-MAIL-COLOR-ID) plus RFC 7986 COLOR for other programs. The palette exists only on this computer (an approved exception to D-193).
- **Changes go to the server first (D-194):** every change of an item of a server's collection goes through `engine/pim_server.py` and `sync/davitems.py`: on the server first, here when it took it; offline here, with the agreed version kept, and sent by the next sync. A change on the server meanwhile is merged field by field (`pim/merge.py`) or becomes a sync conflict (`sync/davconflicts.py`, kept per account on this computer, settled with `conflict.settle`).
- **Names and colours of a server's collections (D-192):** changed on the server first and followed from it at every sync; what Chrono Mail holds differently (waiting offline, or refused) is kept with the account by the collection's place on the server, so it survives untick and re-tick.

### 7.6 Tags

A tag is a name with a colour. For tasks, events and contacts the names live in the items (CATEGORIES, so they travel to servers) and the colours in `tags.json`. Mail gets tags through a `tags.json` per mailbox, copied into the index (D-128), and IMAP keywords where the server keeps them (D-132).

### 7.7 Time

An event with a start time has a time zone. The wall-clock time and the zone are kept as given; the UTC instant is derived (`zoneinfo`, and `tzdata` where the system has no zone database). All-day events are dates, not instants.

## 8. User interface

- Main window (`ui/mainwindow.py`): it only builds the screen, the menus and the toolbar and puts the parts together (R-17, D-221). Each part keeps its own data and reaches the others only through what they offer: the folder list (`ui/folderpane.py`), the message list with the current view and the sort order (`ui/messagelistpane.py`), its columns (`ui/messagecolumns.py`), the Filter menu and the search field (`ui/searchfilters.py`), the actions on messages (`ui/messageactions.py`), the tags (`ui/mailtags.py`), reading (`ui/readingpane.py`), writing (`ui/composing.py`), the mail checks, timers and calendar sync (`ui/mailchecking.py`), the status line (`ui/statusreport.py`); what they share (accounts, folders, sidebar, tags, the current view) is `ui/mailstate.py`, each piece with one owner.
- The message list holds every kind of view (folder, search, Favorites, Tags) one way: the order of every message of the view, from one answer of the engine (a large view: its start first), and the rows loaded so far, asked by the message 100 at a time as they come into view; the facts heard while a question is on its way are applied again to its answer (D-218, `ui/messagelist.py`, `ui/messageorder.py`).
- **An action changes what it changes, and nothing else** (D-146): the engine tells every change as a fact, and the list, the folder list and the other sections apply exactly that change. Never a full reload after a local change.
- The folder list too: it is built once; every change brings it in line in place (only the entries that differ are added, removed, moved or changed), so the selection, the open branches and the scroll stay by themselves (D-209).
- HTML mail renders in Qt WebEngine with JavaScript off and remote content blocked by default; plain-text mail renders without a web engine (D-030).
- All actions call engine commands. UI state that matters (selection, open folder) is inspectable, so tests and agents can drive the window.
- Colours: every colour is in `core/colours.py` (a dark and a light theme, and the colours outside a theme); nothing else writes a colour, and nothing is taken from the platform. Fusion draws on every platform (D-158).
- Dialogs are built from Qt Designer forms (`ui/forms/*.ui`, D-276) through `ui/forms.py`: the form gives the parts, texts and layout; the code fills in values and behaviour, puts its own special parts in the form's placeholders and scales spacers with the size factor; the look comes from `ui/theme.py` by a part's role. The Settings window and its 13 pages are the first (3.6.373); every other dialog follows in four batches (4.0.375 on). A button box with buttons of the program's own gets them from the program; repeated rows (an account's row in Operations) are small forms of their own.
- The window has one toolbar; every section has a row in it, and the toolbar shows the row of the section on screen (`ui/toolbar.py`, D-154, D-156). Menu and the search field always stay; the other buttons fold into More when the window is narrow.

## 9. Testing

- Unit tests for every module (`pytest`), interface tests offscreen (no screen needed).
- Protocol tests against the real servers through opt-in live tests (`pytest -m live`, DirectAdmin and Kerio, D-020); the default run is offline.
- Interruption tests for every destructive or store-changing operation.
- Fuzz-style tests for `mime` with malformed messages.
- A generated 500,000-message mailbox for performance tests (`pytest -m perf`).
