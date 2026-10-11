# ARCHITECTURE.md

Status: initial design, before any code. Details marked *(to be decided)* are settled in the roadmap task that needs them, and recorded in DECISIONS.md.

## 1. Goals that shape the design

1. **Never lose mail.** Crash-safe storage; the server copy is only removed after the local copy is verified.
2. **Confidential by default.** Message contents are encrypted on disk.
3. **Scale.** Responsive with 500,000 messages.
4. **One codebase** for KDE, GNOME, Windows and macOS.
5. **Testable without a screen.** Every action is an engine command, reachable from a headless CLI.
6. **Grows in steps.** POP3 + SMTP first, IMAP next, then Gmail, Exchange, calendar, contacts, tasks. The internals are shaped for IMAP from day one so later steps extend rather than rewrite.

## 2. Package layout (milestone 1)

All code lives in the package `chrono_mail` (src layout: `src/chrono_mail/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Account, Folder, MessageRef, Flags, error types. No I/O. |
| `crypto` | Key management, encryption and decryption of message files. |
| `mime` | Parsing and building messages (wraps the standard `email` package), robust against malformed input. |
| `store` | The encrypted Maildir message store: atomic writes, reads, flag changes, folders. |
| `index` | The rebuildable search and list index. |
| `config` | Settings, account definitions, credentials (through the OS credential store). |
| `protocols` | Protocol clients: `pop3`, `smtp` (milestone 1), `imap` (milestone 2). |
| `sync` | Moving mail between server and store (POP3 download, later IMAP sync). |
| `engine` | Session object and command registry. Every user action is a command here. |
| `cli` | Headless command-line front end (`chrono-mail-cli`). |
| `ui` | PySide6 user interface. Thin: calls engine commands only. |

## 3. Layers

A module may import only from its own layer or lower layers. Enforced by an automated check *(tool to be decided; import-linter is a candidate)*.

| Layer | Modules |
|---|---|
| L0 | `core` |
| L1 | `crypto`, `mime`, `config` |
| L2 | `store`, `index`, `protocols` |
| L3 | `sync` |
| L4 | `engine` |
| L5 | `cli`, `ui` |

Only `ui` may import PySide6.

## 4. Storage design

### 4.1 Message store: encrypted Maildir

- One directory tree per account, one Maildir per mail folder (Inbox, Sent, Drafts, Trash, user folders, nested folders).
- Each Maildir has `tmp`, `new` and `cur`. A message is written completely to `tmp`, flushed to disk, then moved into `new` or `cur` in one atomic rename. A crash damages at most the one message being written, never an existing one.
- **One file per message.** The file contains the complete raw message (headers, body, attachments), **encrypted** with authenticated encryption. Authentication also detects damage or tampering per message.
- **File names are random and meaningless** (nothing derived from subject, sender or date), plus the standard Maildir flag suffix (seen, replied, flagged, trashed, draft). Changing a flag is a rename, which is atomic.
- **Windows:** the standard Maildir info separator `:` is not allowed in Windows file names. A different separator is used on all platforms *(exact character to be decided)*, so a store can move between platforms.
- **Encryption is on by default.** The user can deliberately turn it off (per store or per account, *to be decided*).

### 4.2 Index

- Holds what the UI needs fast: folder contents, sorting, threading, search (sender, recipients, subject, date, size, flags, and full-text).
- Never the source of truth. It can be deleted at any time and rebuilt by reading every message in the store. A full rebuild is rare; for 500,000 messages, an hour or two is an acceptable reference.
- Normally updated incrementally.
- Version 1: SQLite with full-text search, **not encrypted**. The metadata it holds (dates, senders, recipients, subjects) is treated as lower sensitivity. Encrypting the index is a possible later option.

### 4.3 Keys

- The data key encrypts message files. Version 1 keeps it in the OS credential store (KWallet, GNOME Keyring/Secret Service, Windows Credential Manager, macOS Keychain) through the `keyring` package. A master-password mode may follow later (D-023).
- **Key backup and recovery is mandatory.** A lost key means all mail is lost. `crypto` provides two backup formats: a passphrase-protected recovery file (scrypt + AES-GCM) and a printable recovery code with a checksum. The tested end-to-end procedure is task 1.12.
- Algorithm and library: AES-256-GCM from the `cryptography` package, with a per-file key derived by HKDF-SHA256 (D-023).

## 5. Account model

- The model is shaped like IMAP: an account has a tree of folders; a message has a stable identity, flags, and a location (folder).
- **POP3** is a restricted account type: the server offers only an inbox; all folders are local. Downloaded messages are tracked by UIDL. Options: leave on server, delete from server after N days, delete when deleted locally.
- **SMTP** is the sending side. Sent messages are stored in the local Sent folder.
- **Security:** plain connections, STARTTLS, and implicit TLS for both POP3 (STLS, port 995) and SMTP (STARTTLS on 587, implicit TLS on 465). Certificate verification is always on.
- **Authentication in milestone 1:** username and password (stored in the OS credential store). OAuth2 (Gmail, Microsoft 365) comes in a later milestone.

## 6. User interface

- Main window: folder tree, message list, reading pane (the usual three-pane layout).
- The message list is virtualized (Qt model/view) so 500,000 messages do not slow it down.
- HTML messages render in Qt WebEngine with JavaScript off and remote content blocked by default; plain-text messages render without a web engine.
- All actions call engine commands. UI state that matters (selection, open folder) is inspectable, so tests and agents can drive the UI.

## 7. Testing

- Unit tests for every module (`pytest`).
- Protocol and sync tests against a real local test mail server started and stopped by the test suite *(server to be decided in task 0.3)*.
- Real-world tests against the user's DirectAdmin servers and test VMs.
- Interruption tests for every destructive or store-modifying operation.
- Fuzz-style tests for `mime` with malformed messages.
- A generated 500,000-message mailbox for performance tests.

## 8. IMAP design (milestone 2)

IMAP accounts use the same encrypted store, index, engine commands and window as POP3 accounts. What differs is where the truth about *which messages exist and what their flags are* lives: on the server. The local store is an offline mirror of it. Decision record: D-048.

### 8.1 Pieces

| Piece | Layer | Task |
|---|---|---|
| `protocols/imap.py`: own IMAP4rev1 client over a TLS or STARTTLS socket (tagged commands, response parser, literal handling), no `imaplib`. | L2 | 2.1 |
| `sync/imap_state.py`: per account, per folder: UIDVALIDITY, highest UID seen, the map store id ↔ UID, the queue of changes made offline. One small file per account, written atomically. | L3 | 2.2 |
| `sync/imap_sync.py`: folder list, download of new messages, server-side flag changes and removals brought into the store, local changes sent to the server. | L3 | 2.2, 2.3 |
| Engine: `mail.fetch` runs the right sync for the account kind; flag, move and delete commands record a pending change for IMAP accounts; sending appends the sent copy to the server's Sent folder. | L4 | 2.3, 2.4 |
| Interface: the account dialog gets "IMAP" as an account type; folders come from the server; a "sync" progress and error line. | L5 | 2.4 |
| IDLE: a push connection for the Inbox. | L3/L5 | 2.5 |

### 8.2 Identity and the mirror

- A message has a local `store_id` (as now) and, for IMAP, a server identity (folder, UIDVALIDITY, UID). The map lives in `imap_state`, not in the message file, so the store format does not change and POP3 accounts are unaffected.
- A folder is synchronised in this order, all steps idempotent so an interruption at any point is repaired by running again:
  1. `SELECT` the folder; compare UIDVALIDITY with the stored value.
  2. New messages: `UID FETCH <last+1>:* (FLAGS BODY.PEEK[])` in batches (a message is fetched whole, size-capped); each is delivered to the store with verification and only then entered in the map.
  3. Flags: `UID FETCH 1:* (FLAGS)` (or `CHANGEDSINCE` when CONDSTORE is offered) and local flags are updated where the server differs and nothing is pending for that message.
  4. Removals: UIDs that are in the map but no longer on the server are removed locally, *through the store's normal delete* (which keeps the file in `.deleted`), never by erasing.
  5. Pending local changes are sent last (section 8.3).
- Safety rules (they follow AGENTS.md 4.1): nothing is removed from the server unless the local copy is stored and verified; a changed UIDVALIDITY never deletes local mail: the local messages are kept until the folder has been downloaded again and matched by Message-ID, and those with no match are moved to a local folder `Recovered/<folder>`; a server that answers with something unexpected makes the sync of that folder fail, never the whole program.

### 8.3 Local changes (changed by D-050: compared, not queued)

The text below is the first design; D-050 replaced the pending list by a comparison of the store, the remembered state and the server, run at every sync.

- Flag changes, moves, deletes and sent or draft copies are applied to the store at once (the window stays responsive and offline use works) and recorded in a *pending changes* list in `imap_state` (flag set by UID, move/delete by UID, append of a stored message to a folder).
- The list is applied in order during a sync, with `UID STORE`, `UID MOVE` (or `COPY` + `STORE \Deleted` + `UID EXPUNGE` where MOVE is missing), and `APPEND`; an entry is removed only after the server confirmed it. A change that cannot be applied twice in a row is reported and kept, never silently dropped.
- Delete means "move to the Trash folder" (server-side role from SPECIAL-USE or the usual names); deleting in Trash expunges.

### 8.4 Folders

- `LIST` with the delimiter; names are decoded from modified UTF-7; a folder path stays a tuple of names (D-006). SPECIAL-USE attributes (`\Sent`, `\Drafts`, `\Trash`, `\Junk`, `\Archive`) set the folder role, with the usual English names as the fallback. Folders that are not subscribed are not shown.
- Creating, renaming and deleting a folder in the window are applied on the server first, then locally.

### 8.5 The client

- Built on `ssl` and `socket` like the POP3 client: every line and every literal has a size cap, every wait a timeout, unsolicited responses are tolerated, a tag mismatch or garbage is a `ProtocolError`. Required: IMAP4rev1, `LOGIN`/`PLAIN` over TLS (no password over an unencrypted link unless the person chose "none"). Used when offered: `UIDPLUS`, `MOVE`, `CONDSTORE`, `SPECIAL-USE`, `IDLE`, `LITERAL+`.
- Tests: a scripted fake server (hostile cases: giant literals, endless lines, wrong tags, drops in the middle of a literal) and the live Dovecot server on `mail.technova.us` (read and write, with a mailbox that is cleaned before and after).

## 9. Calendars, tasks and contacts (milestone 3 and later)

### 9.1 Goals

Calendars (events), tasks and contacts next to mail, for every kind of account: local (no server), CalDAV and CardDAV servers (Kerio, Nextcloud, Fastmail, iCloud, Google by CalDAV), the provider APIs (Google, Microsoft Graph) and, later and only when there is a way to test it, Exchange ActiveSync or EWS. The interface has four section buttons in the left sidebar (Mail, Calendar, Contacts, Tasks); each section has its own sidebar, toolbar and views. Tags work for mail, tasks and contacts alike.

### 9.2 Items are the standard text formats

- An **event** or a **task** is one *calendar object resource*: an iCalendar text (RFC 5545) with a VEVENT (or VTODO) and, for a repeating item, its changed occurrences, plus the VTIMEZONE it uses. A **contact** is a vCard text (RFC 6350, and 3.0 and 2.1 on input).
- The text is the source of truth and is kept as it came: properties we do not know are kept and sent back to the server. Changing an item changes only the properties that were edited.
- The same texts are what servers exchange (CalDAV and CardDAV), so no translation is needed for them; the provider APIs translate to and from them in `sync`.

### 9.3 Packages and layers

| Module | Layer | Responsibility |
|---|---|---|
| `core.pim` | L0 | Collection kinds, references to collections and items. |
| `pim` | L1 | vCard and iCalendar parsing and building into typed items (`Contact`, `Event`, `Task`), recurrence, time zones, reminders. No I/O. Wraps `icalendar` and `recurring-ical-events`. |
| `pimstore` | L2 | The encrypted item store and the rebuildable index for collections. |
| `sync` | L3 | CalDAV and CardDAV (milestone 4), the provider APIs (milestones 5 and 6). |
| `engine` | L4 | Commands: `calendar.*`, `event.*`, `task.*`, `contact.*`, `tag.*`. |
| `ui` | L5 | The four sections. |

### 9.4 Storage

- One directory per account: `pim/<collection>/<item>.enc`, an encrypted item text written atomically exactly as mail is (tmp, flush, rename), with the same data key. A damaged item is one lost item, never a stalled collection.
- The index (SQLite) holds what the views need: kind, uid, name or summary, start and end in UTC, due date, status, priority, tags, and full-text search. It is rebuilt from the items at any time; it is never the source of truth. Occurrences of repeating events are expanded when a range is asked for, not stored.
- Server state (CalDAV ctag and etags, sync tokens) is kept beside, like the POP3 and IMAP state files.
- Collections belong to an account. Local collections belong to a pseudo-account like Local Folders, so calendars, tasks and contacts work without any server.

### 9.5 Tags

A tag is a name with a colour. The names live in the items themselves (the CATEGORIES property of a task, event or contact, so they travel to servers), the colours in a small file of their own (`tags.json`). Mail messages get tags through a sidecar file per account (the mail store is not changed), mapped to IMAP keywords when an IMAP server supports them.

### 9.6 Time

An event with a start time has a time zone. The wall-clock time and the zone are kept as given; the UTC instant is derived (`zoneinfo`, and the `tzdata` package on systems without a zone database). Servers that send their own VTIMEZONE are understood. All-day events are dates, not instants.

