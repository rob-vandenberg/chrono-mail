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
- Never the source of truth. It can be deleted at any time and rebuilt by reading every message in the store. A full rebuild is rare; for 500,000 messages, eM Client's 1.5 to 2.5 hours is an acceptable reference.
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

- Main window: folder tree, message list, reading pane (three-pane layout like eM Client).
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
