# DECISIONS.md

Every design decision, with its reason and the alternatives that were rejected. Newest at the bottom. A decision is only changed by adding a new entry that supersedes it.

---

## D-001 Build an eM Client-like mail client for Linux, step by step
*2026-10-08*

**Decision:** Build Chrono Mail, functionally modelled on eM Client. Start with SMTP + POP3 (with secure variants), then IMAP, then further features (Gmail, Exchange, calendar, contacts, tasks) in separate steps.
**Reason:** eM Client has no Linux version and none is on its roadmap. Small steps work best with AI-assisted development across many sessions.

## D-002 Python with strict type checking (Pyrefly)
*2026-10-08*

**Decision:** Python, with Pyrefly in strict mode as a mandatory gate (zero type errors).
**Reason:** The user can read and review Python, which matters most when an AI writes most of the code. The standard library covers SMTP, POP3, IMAP, TLS and email parsing. An email client is I/O-bound, so Python's speed is not a limit. Strict Pyrefly turns type errors into "compile-time" errors, addressing the main weakness of dynamic typing. Pyrefly 1.0 is stable (May 2026) and used on very large codebases.
**Rejected:** Rust (user cannot review it, smaller contributor pool), C++ (memory-safety risk with hostile email input, limited user experience), C#/.NET (no user experience, little gain), JavaScript/Electron (heavy, webview approach).

## D-003 Qt 6 through PySide6
*2026-10-08*

**Decision:** Qt 6 via PySide6. No KDE Frameworks.
**Reason:** One codebase for KDE and GNOME, strong Windows and macOS support, built-in web engine for HTML mail, mature widgets for a three-pane mail layout. PySide6 is the official Qt for Python from the Qt project, LGPL-licensed (compatible with AGPL), and more downloaded than PyQt6. The user knows PyQt6 from knotifier; the APIs are nearly identical. KDE Frameworks would tie the app to KDE.
**Rejected:** GTK4 (weaker on Windows and macOS), PyQt6 (single small vendor; no practical advantage for an AGPL project).

## D-004 Encrypted Maildir as the message store
*2026-10-08*

**Decision:** Messages are stored as one file per message in Maildir layout, each file encrypted with authenticated encryption. Encryption is on by default; the user can deliberately turn it off. File names are random and meaningless, with the standard Maildir flag suffix kept so flag changes stay atomic renames.
**Reason:** Maildir is designed to be crash-safe (write to `tmp`, atomic rename); a crash damages at most one message. Per-file encryption keeps that property while protecting message contents. Random names avoid leaking content through file names; keeping the flag suffix keeps flag changes crash-safe without a separate mechanism.
**Rejected:**
- SQLite as message store: the user has repeatedly seen SQLite files corrupted after crashes; one damaged file would hold all mail.
- A single container file (Outlook PST style): a single point of failure, needs a self-built storage engine. PST's own encryption and password protection are weak (fixed-table substitution cipher; CRC32-based password check).
- Password-protected ZIP: performance with 500,000+ messages.
- Container encryption (BestCrypt-style): overkill for an email client.
- mbox: deleting a message rewrites the whole folder file; a crash can damage the folder.

## D-005 Rebuildable index, unencrypted in version 1
*2026-10-08*

**Decision:** A separate index (SQLite with full-text search) for lists, sorting and search. It is never the source of truth and can always be rebuilt from the store. Not encrypted in version 1.
**Reason:** Fast UI with 500,000 messages. Corruption of the index loses no mail. The metadata it contains (dates, senders, recipients, subjects) is treated as lower sensitivity; for comparison, mail servers like Exim log these in plain text. A full rebuild is rare; eM Client takes 1.5 to 2.5 hours for a comparable mailbox, which is acceptable.
**Later option:** encrypt the index.

## D-006 IMAP-shaped internals from day one
*2026-10-08*

**Decision:** The data model and store are designed for IMAP (folders, flags, stable message identity). POP3 is a restricted account type on top of that.
**Reason:** Designing only for POP3 would force a core rewrite when IMAP is added.

## D-007 Engine/UI separation; everything is a command
*2026-10-08*

**Decision:** All behaviour lives in an engine with a command registry. The UI and a headless CLI both call commands. Layering is enforced automatically.
**Reason:** Testable without a screen (also by AI agents), keeps the UI thin, prevents tangled dependencies. Pattern taken from the ArtCraft apps (PhotoCraft).

## D-008 Platforms
*2026-10-08*

**Decision:** Linux first: Fedora (RPM family) and Kubuntu (Debian family), KDE primary, GNOME supported. Distribution as Flatpak first. Windows and macOS as a bonus.

## D-009 License: AGPL-3.0
*2026-10-08*

**Decision:** AGPL-3.0, as for all the user's projects.

## D-010 Project memory lives in the repository
*2026-10-08*

**Decision:** No handover documents between chats. The repository holds the project memory: AGENTS.md, DEVELOPMENT-RULES.md, ARCHITECTURE.md, ROADMAP.md, STATUS.md, DECISIONS.md, DEVLOG.md. STATUS.md and DEVLOG.md are updated after every task, not at the end of a session.
**Reason:** Handover documents written at the end of a chat, from memory, always lose information. Whether this structure becomes a gitinit template is decided after milestone 1.

## D-011 Workflow: "proceed" maps onto plan approval
*2026-10-08*

**Decision:** DEVELOPMENT-RULES.md applies. In Claude Code, sessions start in plan mode; approving a plan is the "proceed" for exactly that plan's scope. The user moves along a learning path: approve each edit, then review per task, then (optionally) auto mode for routine tasks.
**Reason:** Keeps the user in control and prevents code being written during design discussions, while allowing agent work to scale up as trust grows.

## D-012 Naming
*2026-10-08*

**Decision:** Project name "Chrono Mail"; identifier and repository `chrono-mail`; `chrono_mail` where hyphens are not allowed; release files `chrono-mail_x.y.z.ext`.
**Reason:** Consistent with the user's chrono family of projects.

## D-013 Version in pyproject.toml
*2026-10-08*

**Decision:** The project version lives only in `pyproject.toml`. Per-file version numbers and history blocks follow DEVELOPMENT-RULES.md Rule 4. Delivered files carry the version in their file name; the user renames them for use in the project.

## D-014 Development machine: KDEV
*2026-10-08*

**Decision:** Main development on KDEV (Kubuntu 26) with VS Code as the editor. DEVELOPMENT (Windows 10) is the Windows test machine. Fedora and Kubuntu VMs are test machines; an Ubuntu/GNOME VM can be added.
**Reason:** Develop on the primary target platform; test mail servers, Flatpak builds and Claude Code all fit Linux best. VS Code has the Pyrefly extension (type errors while typing), a compare view and git integration.

## D-015 Python 3.14, pip and venv
*2026-10-08*

**Decision:** Python 3.14 (the system Python on KDEV), a virtual environment in `.venv`, packages installed with pip. `requires-python = ">=3.14"`.
**Reason:** Verified on PyPI that PySide6 6.12 (`Requires-Python <3.16`) and Pyrefly 1.3 support it. pip and venv are standard and need no extra tool.
**Rejected:** uv (an extra tool; not needed).

## D-016 `.vscode/` is local only; dev dependencies in `pyproject.toml`
*2026-10-08*

**Decision:** `.vscode/` is listed in `.gitignore` and never pushed to GitHub. `pyproject.toml` was created in task 0.1 (version 0.0.1) and holds the dev dependencies in `[project.optional-dependencies] dev`.
**Reason:** Editor settings are personal. `pyproject.toml` is the single source of the project version (D-013), so it exists from the start.

## D-017 Linux `release` command
*2026-10-08*

**Decision:** `~/.local/bin/release` (bash, outside the repository) reads the version from `pyproject.toml`. If no git tag with that version exists it is RELEASE mode: message required, commit, annotated tag, push. If the tag exists it is UPDATE mode: amend the last commit, move the tag, force-push. No backup copy and no `release/` build folder. The first release message is "Initial release".
**Reason:** Port of the user's `release.bat`; backup and build steps were specific to single-file JS cards, and git tags hold every version.
**Note:** UPDATE mode force-pushes `main`; revisit when GitHub Actions (task 0.4) or other contributors exist.

## D-018 Layer check with import-linter; modules in a layer are independent siblings
*2026-10-08*

**Decision:** The layer check is import-linter (2.15, BSD-2-Clause), configured in `pyproject.toml` and run by `tests/test_layers.py`. A `layers` contract encodes ARCHITECTURE.md §3 with `|` between modules of the same layer, so modules in one layer may not import each other (`cli` and `ui` included). A `forbidden` contract allows only `ui` to import PySide6. If a real need for a same-layer import appears, the contract is relaxed with a new entry here.
**Reason:** Keeps modules small and decoupled; chosen by the user.

## D-019 Build backend setuptools; strictness proven by a test
*2026-10-08*

**Decision:** setuptools (src layout) as build backend, so `pip install -e ".[dev]"` works. Pyrefly runs with the `strict` preset from `[tool.pyrefly]`. `tests/test_type_strictness.py` proves strict mode is active by checking a deliberately wrong file, `tests/data/bad_types.py`, which is excluded from the project check.
**Reason:** setuptools is the standard backend and needs no extra tool. Without the proof test, a lost or misconfigured preset would silently fall back to a mode that reports no errors (seen in task 0.1).

## D-020 Protocol tests run live against the user's DirectAdmin server; no local mail server
*2026-10-08*

**Decision:** No mail server is installed on the development machine. SMTP and POP3 (later IMAP) are tested against `mail.technova.us`, a DirectAdmin server running Dovecot and Exim, using a disposable account. These tests carry the pytest marker `live` and are excluded from the default run; `pytest -m live` runs them. Credentials come from environment variables or from `tests/live/account.local.toml`, which is in `.gitignore` and never pushed. Wrong-password attempts per run are configurable (default 1, 0 disables). This supersedes the wording of AGENTS.md §4.5.3 ("real local test mail server"), which is updated.
**Reason:** The user does not want a mail server on the development machine, and the production servers run Dovecot, so the tests exercise the software the client will meet. A self-written server would only test the client against its own author's reading of the protocol.
**Consequence:** The default gate (`pytest`, Pyrefly strict, layer check) contains no automated protocol test and runs offline. Live tests need network access to the server and a working test account. Dovecot-specific behaviour (for example the refused plaintext login before STLS) is observed, not controlled; a server-side change can break a live test. Hostile or misbehaving server behaviour cannot be tested this way and needs an offline fake in a later task.

## D-021 Core data model details
*2026-10-08*

**Decision:**
- Five message flags: draft, flagged, replied, seen, trashed (Maildir D F R S T; IMAP \Draft \Flagged \Answered \Seen \Deleted). Maildir `P` (passed) is not modelled; adding it later is one enum value plus a new entry here. Letters from other clients that we do not model are ignored when parsing, never an error, and are not preserved when a flag change rewrites the suffix.
- A folder path is a tuple of names; no IMAP delimiter or directory layout in the model.
- `MessageRef` identity is (account id, folder path, store id). The store id is the random store identity from D-004 and survives flag changes.
- Account ids are plain strings; generating them belongs to `config`.
- Invalid server settings raise `ConfigurationError` (they come from user input); invalid folder paths raise `ValueError` (a programming error).
- The model holds no passwords (AGENTS.md section 4.6).
**Reason:** Keeps the model small, IMAP-shaped (D-006) and free of I/O; unknown letters must not make a foreign Maildir unreadable (AGENTS.md section 4.2).
**Open consequence:** dropping an unknown letter such as `P` on a flag rename loses information another client stored. Task 1.4 must decide whether the store preserves unknown letters.

## D-022 Message parsing and building rules
*2026-10-08*

**Decision:**
- `parse_message` never raises; every failure becomes a short kebab-case defect string and the rest of the message is still returned. `build_message` raises `MessageFormatError` for invalid outgoing input, with an error text that never contains message content.
- Caps (`mime/limits.py`): MIME depth 20, 1,000 parts, header section 1 MiB, 256 MiB per part, 50 defects. The values are proposals and easy to change.
- A date without a time zone is taken as UTC. Text bodies are normalised from CRLF to LF. The first text/plain and the first text/html part are the bodies; further inline text parts stay attachments, so nothing is lost. `message/*` parts are attachments holding the embedded message.
- The original text of From, To, Cc and Reply-To is kept next to the parsed addresses, because Python's default policy silently reduces a garbled header (`<<<@@@` becomes `<>`).
- Bcc is never written into a built message; `envelope_recipients` supplies the SMTP envelope. Non-ASCII addresses are rejected (no SMTPUTF8 yet).
- Fuzz-style tests use the standard library `random` with a fixed seed, not Hypothesis.
- No HTML sanitising or HTML-to-text conversion in `mime`: safe display is the UI's job, text for search belongs to the index.
**Reason:** AGENTS.md section 4.2 (never crash on input, validate sizes and counts) and 4.6 (no message contents in errors).
**Open consequence:** invalid base64 in an attachment is decoded leniently by Python without a defect.

## D-023 Encryption design
*2026-10-08*

**Decision:**
- Cipher: AES-256-GCM. Each file gets its own key, derived from the data key with HKDF-SHA256 and a random 16-byte salt, plus a random 12-byte nonce. The header (magic CMF1, format version, algorithm id, 8-byte key id) is authenticated as associated data. Library: `cryptography` (Apache-2.0 or BSD-3).
- The key id is derived from the key (first 8 bytes of a SHA-256 over a context string and the key), so a key restored from a backup has the same id. The id names the key in the file header, which prepares key changes without breaking old files.
- Key storage in version 1: the operating system credential store through `keyring` (MIT), as base64 text under service `chrono-mail`, entry `data-key-<key id>`. A master-password mode is a later option and would reuse the recovery file code.
- Backup: a passphrase-protected recovery file (scrypt N=2^17, r=8, p=1, about 0.36 s and 128 MiB on KDEV; AES-GCM; settings stored in the file; a file may not ask for more than 2^18 for N, 16 for r, 4 for p, or N times r above 2^21) and a printable recovery code (58 Base32 characters, 32-bit checksum, tolerant of case, spaces, dashes and 0, 1, 8 for O, I, B).
- Python cannot reliably wipe bytes from memory: the key stays in memory while its `DataKey` lives. Known limitation. The key is never shown in repr, str, errors or pickles.
- `crypto` imports only `core`; the `CredentialBackend` protocol keeps test doubles out of keyring's global backend registry.
**Reason:** AGENTS.md sections 4.1.5 and 4.6. Per-file keys avoid the limit on random nonces under one key; scrypt needs no extra dependency; the settings in the file keep old files readable when the defaults change.
**Rejected:** a single key with random nonces (works at 500,000 messages, but leaves no margin and no per-file separation), Argon2 (extra dependency), plain files for the key (AGENTS.md section 4.6).
**Open consequences:** the end-to-end recovery procedure is task 1.12; a key change (re-encrypting the store) needs an interruption-safe design and its own task; master-password mode is not built.

## D-024 Store layout
*2026-10-10*

**Decision:**
- The Maildir info separator is `!` on every platform, instead of `:`. Two programs use `!` (OfflineIMAP, Evolution's Camel); no agreed standard exists. Our files are encrypted and randomly named, so other clients cannot read them anyway.
- Folders are directories with random names (`f-<12 hex>`) holding `tmp`, `new`, `cur` and a `folder.json` with the folder path and role. The folder hierarchy exists only in the paths, never on disk. Renaming a folder is one atomic rewrite of `folder.json`; no folder name needs encoding, and names that differ only in case, contain `/`, `:` or are reserved on Windows are all safe.
- Renaming a subtree rewrites one `folder.json` per folder, deepest first. After an interruption a folder may lack its parent (a "virtual parent"); the tree stays valid and repeating the rename finishes it.
- Deleting a folder moves it to `.deleted/` in one rename; the files are removed only by an explicit `purge_deleted_folders()`.
- Message files: random 128-bit id as 32 lower-case hex characters. New mail goes to `new/<id>`; a flag change moves it to `cur/<id>!2,<letters>` (letters in ASCII order). Letters the app does not model (for example `P`) are preserved on every flag change.
- `store.json` records the format, the account id, whether the store is encrypted and the key id. Encryption off is per account (the store is per account); switching a store between encrypted and plain is a migration and is not built.
- Delivery writes to `tmp` (exclusive create, flush, fsync), renames into `new` or `cur`, fsyncs the directory, and with `verify` reads the message back, decrypts it and compares it with the input. A failed verification removes the copy and raises, so the caller keeps the server copy.
- Leftover `tmp` files are removed only when older than 36 hours (the Maildir convention), because another process may still be writing.
- `durable=False` (no fsync) exists for benchmarks and bulk test generation only.
**Reason:** AGENTS.md section 4.1 (never lose mail) and D-004.
**Rejected:** nested directories per folder (names need encoding; a rename moves directories), encoded folder names (case-insensitive file systems, reserved names), hard-link or copy-based moves.

## D-025 Index design
*2026-10-10*

**Decision:**
- One SQLite database per account, WAL mode, with an FTS5 table (`unicode61 remove_diacritics 2`, contentless with deletes, so SQLite 3.43 or newer is required) for subject, sender, recipients and body. The messages table keeps the folder, the relative path hint, flags as a bit mask, the date as UTC seconds, size, subject, sender, recipients, message id, a thread key and an attachment flag; sorting by date, subject, sender and size has an index per folder.
- Body text for search is the plain text, or the visible text of the HTML when there is no plain part, with whitespace collapsed and capped at 100,000 characters per message.
- The thread key is simplified: the first Reference, else In-Reply-To, else the message's own id. Proper threading (JWZ) is a later task.
- Search words match as prefixes, quotes make phrases, and `from:`, `to:`, `subject:`, `body:` limit a word to one field. The expression is always built from quoted terms, so no input can inject FTS syntax. Results are newest first.
- Paging uses LIMIT and OFFSET (random access for a scroll bar).
- A rebuild writes a complete new database next to the old one and replaces it with one rename; a failure or an interruption leaves the old index untouched. An unreadable message appears as a "(damaged message)" placeholder instead of vanishing. `reconcile_index` repairs the index after an interruption by comparing it with directory listings of the store; the store is the truth.
- `index` and `store` are siblings in layer L2 and may not import each other, so rebuild and reconcile live in `sync` (L3).
**Reason:** D-005 and AGENTS.md section 4.1.3; 500,000 messages.
**Open:** the measured performance is in DEVLOG.md; threading and encrypting the index are later options.

## D-026 Configuration, paths and credentials
*2026-10-10*

**Decision:**
- Settings are one JSON file, written atomically with owner-only permissions, in the platform's configuration directory (platformdirs); data (stores, indexes, POP3 state) is in the platform's data directory. Unknown settings are ignored; a damaged file is copied to `config.json.bad` and reported, never overwritten silently.
- Account ids are random 128-bit hex strings and are validated against that pattern everywhere, because they are used in directory names (no path traversal from a settings file).
- The default POP3 policy is KEEP: the server copy is never deleted unless the user chooses otherwise.
- Passwords live only in the operating system's credential store, per account and direction (incoming, outgoing). There is one data key per installation (one backup); the settings file holds only its id.
- `CredentialBackend` moved to `core`, because `config` and `crypto` are same-layer siblings that both need it. The small atomic-write code exists once in `store` and once, simplified, in `config` for the same reason.
**Reason:** AGENTS.md sections 4.1.5, 4.4 and 4.6.

## D-027 POP3 client, state and delete policies
*2026-10-10*

**Decision:**
- An own POP3 client instead of `poplib`, which rejects every line over 2,048 bytes, message bodies included (verified); real mail has longer lines. Caps: line 1 MiB, message 256 MiB (enforced while receiving), 1,000,000 messages, connect and read timeouts. Certificates are always verified; STLS is never silently skipped; data that arrives before the TLS handshake is refused; a server `-ERR` to USER or PASS is an AuthenticationError, everything else a ProtocolError. Text from the server is made printable before it reaches an error message; the password never appears in one.
- Deletions are marked with DELE and only committed with QUIT; a session that ends without QUIT drops them.
- Download order: retrieve, store and verify (the store reads the file back and compares), append to the state log with fsync, and only then delete on the server. The state is an append-only text file (`D` and `X` lines); a line cut off by a crash is ignored. A crash between storing and logging can produce a duplicate on the next run, never a loss.
- Policies: KEEP; DELETE_AFTER_DOWNLOAD; DELETE_AFTER_DAYS (the local copy must still exist and decrypt); DELETE_WHEN_DELETED_LOCALLY (the message is no longer anywhere in the store, judged from directory listings, not from the index; moving to Trash is not deleting). Deletion is blocked, and reported, when the store has no folders, a damaged folder, or (at ten or more candidates) when more than half the downloaded messages are missing, which looks like a wrong mount or a wiped disk.
- A problem with one message (refused, too large, cannot be stored safely) is reported and the message stays on the server; a connection failure ends the run, keeps what is done, and is reported as `aborted`.
**Reason:** AGENTS.md sections 4.1.2 and 4.2.

## D-028 SMTP client and sending order
*2026-10-10*

**Decision:**
- `protocols.smtp` wraps `smtplib` (its limits apply only to replies) with a reply reader that caps the number of lines (200) and bytes (64 KiB), because `smtplib` would read an endless `250-` reply for ever. Plain, STARTTLS and TLS; STARTTLS that is not offered is an error, never a fallback to plain; the login is only sent after the secure connection; addresses are checked (ASCII, one `@`, no spaces, brackets, quotes or control characters) before anything is sent; the size the server advertises and 8-bit data are checked first.
- Sending: build the message, store a verified copy in the Outbox, send, then move the copy to Sent with the Seen flag. A message that fails stays in the Outbox and nothing resends it by itself (a crash after the server accepted it would send it twice). The Sent copy is exactly what was sent (without Bcc).
**Reason:** AGENTS.md section 4.1 and 4.2.

## D-029 Engine, command registry and command line
*2026-10-10*

**Decision:**
- A command is a typed parameter dataclass plus a handler; the registry derives the parameter specification (kind, default, choices, help, secret) from the dataclass. Allowed parameter types: text, whole number, true/false, list of text, choices (`Literal`), and optional versions. The CLI is generated from the registry, so there is one definition of every action. Results are dataclasses and become JSON with `to_data`.
- 26 commands cover accounts and keys, folders, fetching, sending, listing, showing, searching, flagging, moving, copying, deleting, and index and store maintenance.
- Secrets are never taken from the command line: `--<name>-stdin` or a prompt on a terminal. Commands that delete need `--yes` (account.remove, folder.delete, message.delete). Exit codes are stable: 0 ok, 1 other error, 2 usage, 3 authentication, 4 network or protocol, 5 storage or crypto, 6 configuration, 7 message format, 130 interrupted.
- `account.add` saves the key in the credential store and reads it back before creating an encrypted store, writes the store, and saves the settings last (the commit point); a failure removes what was made. `account.remove` forgets the account and its passwords but keeps the mail on disk. `folder.delete` keeps the files in `.deleted`. The standard folders (Inbox, Sent, Drafts, Trash, Junk, Outbox) cannot be deleted.
- At the command level a folder name cannot contain `/` (it is the separator). An account is found by its id, the start of its id (6 or more characters) or its e-mail address.
- A missing or damaged index is rebuilt automatically when an account is opened.
- An Engine is used from one thread at a time; a UI runs commands in a worker with its own index connection.
**Reason:** AGENTS.md sections 4.3 and 4.6; a UI and agents must be able to do everything the CLI can.

## D-030 The user interface
*2026-10-10*

**Decision:**
- Qt widgets (PySide6), a three-pane main window in the style of eM Client 9 and 10 (checked against its official 10.0 documentation, as a reference only; nothing is copied): folder tree on the left, message list in the middle, reading pane on the right, a top toolbar with the actions and a search field, and quick filters above the list. The look is the platform's own (palette, light and dark, theme icons); eM Client's colours and artwork are not used.
- The interface contains no mail logic. A `Backend` runs engine commands on a thread pool and delivers each answer on the GUI thread, dropping answers for objects that no longer exist. A view that changes (another folder, a new search) makes every older answer obsolete; so does any change to the store while a message is being opened.
- The message list is a model that knows its size from the first page and fetches 100 rows at a time when a row is needed (folders); searches and quick filters grow as the list is scrolled. Unread counts adjust locally when a flag changes.
- A row shows an avatar with initials in a colour taken from the sender, sender or (in Sent, Drafts and Outbox) recipient, date, subject, a line of preview and marks for flags and attachments. Unread rows are bold with a marker in the margin.
- HTML mail is shown in a Qt WebEngine view that: runs no JavaScript, plug-ins or local storage; is off the record (nothing on disk); follows no link and opens no window by itself (a clicked link is reported and only web and mail addresses are ever offered, after a question); blocks every request that would leave the computer until the person allows remote content for that one message; and shows pictures that came with the message by embedding them as data. Subject and header lines are never interpreted as markup. Plain-text mail is shown as text with clickable links.
- Folder deletion in the interface removes the folder, its subfolders and their messages from the store, but the files stay in `.deleted`. Delete moves to Trash; deleting from Trash asks first.
- The program checks for mail when it starts and every 5 minutes (switchable); sending keeps the message in the Outbox on failure and says so.
- UI tests run without a screen (Qt `offscreen`, pytest-qt). The web view cannot be captured by `grab()` in that mode; its rendering is checked through a PDF export, its security through a local web server that counts the requests that really arrive.
**Reason:** the user's goal is an eM Client for Linux; the rules in AGENTS.md sections 4.3 and 4.6.
**Open:** drafts, HTML composing, drag and drop, conversation view, a window icon and the Flatpak packaging (task 1.13).

## D-031 What the interface needed from the engine
*2026-10-10*

**Decision:**
- Index schema 2: a preview (the first 160 characters of the body) per message, and a partial index for unread counts. Unread count in a folder of 300,000 messages: 454 ms before, 0.5 ms with 3 percent unread and 13 ms with every message unread, measured on a synthetic folder. An index with another schema is rebuilt automatically when an account is opened.
- New commands: `message.attachment.save` (safe suggested file names), `message.compose` (reply, reply all and forward drafts; Re: and Fwd: only once; the quoted text of an HTML message keeps its paragraphs), `account.check` (connection test of both servers before the account exists, nothing stored or sent). `message.show` returns the pictures the HTML refers to with cid: (at most 5 MiB each, 20 MiB together). `mail.send` accepts a message with only a blind-copy recipient, the source message to take attachments from, the references of the conversation, and marks the original as replied when the reply was sent. `folder.delete` can include subfolders.
- The engine keeps the data key for the session after the first read from the credential store (a UI runs many commands) and is safe for concurrent commands. SQLite waits up to 30 seconds for another writer.
- `protocols.smtp` has a connection test (`check_login`) that shares its connection and error handling with `send_message`.
**Reason:** a responsive interface; AGENTS.md section 4.5.

## D-032 Text from mail is always valid Unicode
*2026-10-10*

**Decision:** the parser never returns a lone surrogate character. Python's email package returns raw 8-bit bytes in a header as surrogate-escaped characters; the parser recovers the bytes and reads them as UTF-8 (so a header sent as raw UTF-8 reads correctly), and replaces whatever is invalid with U+FFFD. The composer reads recipient fields address by address with its own splitter, because `getaddresses()` of Python 3.14 silently returns nothing for the whole field when one part is malformed.
**Reason:** AGENTS.md section 4.2; both bugs were found by the interface tests (see DEVLOG.md). Headers sent as raw UTF-8 are common, so before this fix such a message crashed indexing and with it the download.

## D-033 Polish after the first real use
*2026-10-10*

**Decision:**
- The preview line of a message has web addresses, rows of dashes and link leftovers removed (newsletters start with things like `[<https://tracker...>` and `------ HELLO`). Index schema 3; an older index is rebuilt automatically when the account is opened. The searchable body is not changed.
- Avatar colours come from 72 hues (one per 5 degrees) at saturation 0.55 and lightness 0.30, which keeps white initials at a contrast of 4.7:1 or better on every hue (checked for all of them in a test).
- On a selected row secondary text is as strong as the main text. The theme's own pairing of white on a light blue highlight is only 2.2:1 and is not overridden.
- The program has an icon (an envelope with a clock, `ui/resources/chrono-mail.svg`, our own design), used for the windows and, through the launcher file `packaging/chrono-mail.desktop`, for the menu and the taskbar. The folder tree has theme icons (freedesktop names, best match first, nothing when the theme lacks one).
- The program announces itself to the desktop as `chrono-mail`; the portal looks up a launcher file by that name (without one it reports "App info not found").
- The interface language stays English while dates follow the system language; translating the interface is a later decision.
**Reason:** the first look at the program on a real screen with real mail.

## D-034 Autonomous development while the user is away
*2026-10-10*

**Decision:** on 2026-10-10 the user asked for development to continue without confirmation and without questions while they are away until the next day, with every step pushed to GitHub. The rules of DEVELOPMENT-RULES.md stay in force, with these consequences: every step gets its own version number and tag (no force-push, so any step can be returned to); a step is pushed only when the whole gate is green (offline tests, Pyrefly strict, layer check, and the live tests when the server is involved); decisions are made by the agent and written down here with their reasons; the user's real mailbox, wallet entries and data directories are never touched (tests use temporary directories, fake servers and the test account); no system packages are installed. The queue of steps is in STATUS.md.
**Reason:** the user's instruction; keeping every step reversible and every decision visible is how that trust is kept.

## D-035 The key can be restored from inside the program
*2026-10-10*

**Decision:** at start-up the program checks whether the encryption key is in the credential store (`key.status`). If it is not (new computer, reset or locked wallet), a dialog explains that the mail is safe and offers: the recovery code, a recovery file with its passphrase, or "try again" (for a wallet that was only locked). "Not now" leaves the program running with a red message in the status bar; File > Restore the encryption key does the same later. Nothing is asked when no account uses a key or the account is unencrypted. A wrong code, a mistyped code, a wrong passphrase and the key of another installation are each explained without repeating the secret. This completes task 1.12.
**Reason:** a lost key means lost mail (AGENTS.md section 4.1.5); before this, a missing key showed up as an error message in the status bar.

## D-036 Leave mail on the server is the default
*2026-10-10*

**Decision:** the user confirmed that "leave all mail on the server" is on and is the default for every POP3 account (engine, CLI and account dialog; it was already `keep`). The dialog option is now labelled "Leave all mail on the server (default)".
**Reason:** with POP3 the local copy may be the only copy once the server copy is removed (AGENTS.md section 4.1); deleting from the server must be an explicit choice.

## D-037 Drafts are ordinary messages in Drafts
*2026-10-10*

**Decision:** a draft is a Maildir message in the Drafts folder with the flags DRAFT and SEEN, written by `draft.save`. It carries the Bcc header (only drafts do; sent messages never) and the headers `X-Chrono-Mail-Kind` and `X-Chrono-Mail-Source`, so a reply or forward goes on where it stopped. Saving again stores the new version first and removes the old one only after that succeeded (a failed save never loses the earlier version). Attachments of a stored draft are kept by position, so they are not read from disk or re-uploaded. The composer saves on request (Ctrl+S, button), every 30 seconds while the text changed, and asks Save / Discard / Cancel on close; Discard removes only a draft that this window itself created. A message in Outbox can be opened and sent again the same way; the stored message is removed when the send succeeded. Drafts open from Drafts and Outbox by double-click, Return or the context menu.
**Reason:** nothing a person has written may be lost (AGENTS.md section 4.1), and drafts stay readable by the same code, index and search as all other mail.

## D-038 Message actions: Archive, mark all as read, empty folder, drag and drop
*2026-10-10*

**Decision:** three engine commands carry these actions: `message.archive` (moves to the folder with the archive role, else a folder called Archive, else creates Archive with the archive role; a message already there is refused), `folder.mark-read` and `folder.empty`. `folder.empty` works on Trash and Junk only (any other folder is refused with a message), deletes for good, and is marked destructive (the CLI needs `--yes`; the window asks first). Messages are dragged from the list to a folder of the tree of the same account; the drag data holds only the account and (folder, message id) pairs, never message content, and the tree refuses drops on the same folder, on Outbox, on other accounts and on anything that is not this drag data. A drop is a `message.move`.
**Reason:** everything is a command (AGENTS.md section 4.3); emptying is the one new way to lose mail, so it is limited to the folders where deleting is the point and asks for confirmation.

## D-039 Conversations
*2026-10-10*

**Decision:** a conversation is the set of messages with the same thread key, which is the first Message-ID in References, else In-Reply-To, else the message's own id (unchanged since D-017). There is no subject-based fallback: a reply that lost its headers is a conversation of its own. Rows from `message.list` and `message.search` carry `thread_size` (all folders together, so a reply in Sent counts). `message.thread` lists a conversation oldest first across folders. The `conversations` option of `message.search` (and the window's View > Show conversations, off by default and remembered) lists only the newest message of each conversation within its folder; it applies to a plain folder view only, and is ignored with a text query and switched off in the window for unread, flagged and attachment filters and for search, because an older matching message must not be hidden by a newer one that does not match. The grouped view is sorted by date (the sort menu applies to the ungrouped list). In the window a count box shows the size of a conversation, and a bar above the reading pane lists its messages; clicking one shows it.
**Reason:** grouping through the index (a NOT EXISTS probe per row on the thread index) keeps paging and the 500,000-message goal without a separate thread table; measured on 200,000 messages: first page 1.5 ms, a full scan 275 ms.
