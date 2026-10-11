# DECISIONS.md

Every design decision, with its reason and the alternatives that were rejected. Newest at the bottom. A decision is only changed by adding a new entry that supersedes it.

---

## D-001 Build a full-featured mail client for Linux, step by step
*2026-10-08*

**Decision:** Build Chrono Mail, aiming at the functions of a full desktop mail program. Start with SMTP + POP3 (with secure variants), then IMAP, then further features (Gmail, Exchange, calendar, contacts, tasks) in separate steps.
**Reason:** No full-featured desktop mail program of this kind exists for Linux. Small steps work best with AI-assisted development across many sessions.

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
**Reason:** Fast UI with 500,000 messages. Corruption of the index loses no mail. The metadata it contains (dates, senders, recipients, subjects) is treated as lower sensitivity; for comparison, mail servers like Exim log these in plain text. A full rebuild is rare; an hour or two for a mailbox of this size is acceptable.
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
- Qt widgets (PySide6), a three-pane main window (folder tree, message list, reading pane; own design): folder tree on the left, message list in the middle, reading pane on the right, a top toolbar with the actions and a search field, and quick filters above the list. The look is the platform's own (palette, light and dark, theme icons); all artwork is our own.
- The interface contains no mail logic. A `Backend` runs engine commands on a thread pool and delivers each answer on the GUI thread, dropping answers for objects that no longer exist. A view that changes (another folder, a new search) makes every older answer obsolete; so does any change to the store while a message is being opened.
- The message list is a model that knows its size from the first page and fetches 100 rows at a time when a row is needed (folders); searches and quick filters grow as the list is scrolled. Unread counts adjust locally when a flag changes.
- A row shows an avatar with initials in a colour taken from the sender, sender or (in Sent, Drafts and Outbox) recipient, date, subject, a line of preview and marks for flags and attachments. Unread rows are bold with a marker in the margin.
- HTML mail is shown in a Qt WebEngine view that: runs no JavaScript, plug-ins or local storage; is off the record (nothing on disk); follows no link and opens no window by itself (a clicked link is reported and only web and mail addresses are ever offered, after a question); blocks every request that would leave the computer until the person allows remote content for that one message; and shows pictures that came with the message by embedding them as data. Subject and header lines are never interpreted as markup. Plain-text mail is shown as text with clickable links.
- Folder deletion in the interface removes the folder, its subfolders and their messages from the store, but the files stay in `.deleted`. Delete moves to Trash; deleting from Trash asks first.
- The program checks for mail when it starts and every 5 minutes (switchable); sending keeps the message in the Outbox on failure and says so.
- UI tests run without a screen (Qt `offscreen`, pytest-qt). The web view cannot be captured by `grab()` in that mode; its rendering is checked through a PDF export, its security through a local web server that counts the requests that really arrive.
**Reason:** the user's goal is a full-featured mail program for Linux; the rules in AGENTS.md sections 4.3 and 4.6.
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

## D-040 Advanced search: OR, NOT, dates and sizes
*2026-10-10*

**Decision:** the search words understand `OR` (capitals, or `|`) and exclusions (`NOT term`, or a minus: `-spam`, `-from:bob`, `-"a phrase"`). Terms next to each other all have to match and that binds tighter than OR (`a b OR c` is `(a and b) or c`); exclusions apply to the whole search, not to one side of an OR; a lowercase `or` or `not` is an ordinary word. Exclusions run as `id NOT IN (full-text hits)`, so a search of only exclusions lists everything else. `message.search` gains `date_from`, `date_to` (days, YYYY-MM-DD, local time zone, both inclusive), `minimum_size` and `maximum_size` (bytes). The Advanced search dialog (View menu, Ctrl+Shift+F) turns its fields into the same search words (an "any of these words" list is spread over the other terms, since OR binds loosely), writes them in the search box so they can be edited there, and keeps dates and sizes in a chip above the list that removes them; changing folder or clearing the search drops them.
**Reason:** one query language for the box, the dialog, the CLI and automation (AGENTS.md section 4.3); users expect OR and a minus.

## D-041 Settings window, account editing and signatures
*2026-10-10*

**Decision:** File > Settings (Ctrl+,) has two pages. General: check for mail automatically and how often (1 to 240 minutes, default 5) and the conversation grouping. Accounts: add, change and remove. `account.edit` changes any part of an account (name, address, signature, servers, ports, security, user names, new passwords, the server-copy policy); what is not given stays as it was, a password field left empty keeps the stored password, bad values change nothing (validation happens before any password is stored), and the stored mail and the account id never change, so an edited account keeps its mail. Editing keeps the account's place in the list. A signature is plain text of at most 4,000 characters, kept per account in the settings file (not secret); it is added as `-- ` and the text before the quoted part of a reply or forward, and at the end of a new message; a resumed draft is not given a second one; choosing another sender in the composer swaps the signature. Removing an account forgets it and its passwords but does not delete mail on this computer or on the server (unchanged from `account.remove`). Changing the encryption of an existing store is still not possible (D-024).
**Reason:** the first real use needs a place to fix a wrong server or password and to set a signature; keeping the account id stable is what keeps the mail attached to the account.

## D-042 Tray icon, notifications and the unread count
*2026-10-10*

**Decision:** the program puts an icon in the notification area (Qt's `QSystemTrayIcon`) when the desktop has one: click shows or hides the window, the menu has Show or hide, Get mail, Settings and Quit, and the tooltip tells the number of unread messages in all Inbox folders. The window title carries the same count. When mail arrives during a check and the window is not the active window, one desktop notification says how many new messages came (per account when there are several); it never contains a sender, a subject or any part of a message, because notifications can be seen by others and can be logged by the desktop. Settings: notifications (on), tray icon (on), and closing the window keeps the program running in the tray (off by default, and only honoured while the icon is really visible, so the window can never be lost). Quit in the File menu or the tray menu always ends the program. Without a tray the three settings are disabled and everything else works unchanged. The program no longer ends when its last window is hidden: the main window announces `closed_for_good`, and only that ends it.
**Reason:** a mail program is expected to tell about new mail without being watched; privacy comes before convenience in the notification text (AGENTS.md section 4.6).

## D-043 Save as .eml, view source and print
*2026-10-10*

**Decision:** three more engine commands: `message.export` writes the message exactly as received to a file (mode 0600, atomic: a temporary file and one rename, an existing file only with `overwrite`), `message.source` returns the raw source for display (UTF-8 with replacement characters, at most 2,000,000 characters, flagged when cut), and `message.text` returns the message as plain text (the text part, or the visible text of the HTML part). The window offers Save as (Ctrl+Shift+S), Print (Ctrl+P) and View source (Ctrl+U) in the Message menu and in the list's context menu. Printing builds a document from `message.text` and our own markup only, with everything escaped: the HTML of a message is never given to the print engine, so printing cannot fetch anything from the internet or run anything. An exported .eml is not encrypted; the file belongs to the person who saved it.
**Reason:** getting mail out of the program in a standard form (leaving is a feature), checking what a message really contains, and a paper copy, without opening a new way for hostile mail to act.

## D-044 Address completion from the mail itself
*2026-10-10*

**Decision:** the index keeps a table of the addresses seen in the From, To and Cc of the messages (schema 4: address, newest non-empty name, how often as sender, how often as recipient, last date). It is part of the rebuildable index, not a separate address book: a message added again is not counted twice, deleting a message does not lower the counts (they only grow until a rebuild recounts exactly), and an older index is rebuilt automatically at the next start. Odd addresses (more than one @, spaces, quotes, longer than 254 characters) and more than 60 per message are skipped. `contact.suggest` searches names and addresses (contains, with the start of a name or address first), ranks by how often the address was written to (three times the weight) plus how often it wrote to us, never suggests the account's own addresses, adds up all accounts unless one is named, and returns the form that goes into the field (`"Archer, Ann" <ann@x.org>`). The composer's To, Cc and Bcc fields complete the address being typed (after two characters, 150 ms after the last key, stale answers dropped), also after commas and semicolons but not inside quotes or angle brackets. A real address book with contacts, groups and CardDAV comes with the Contacts milestone and will feed the same command.
**Reason:** typing addresses in full is the most repeated chore of writing mail, and the mail already holds the answer.

## D-045 Rich text composing
*2026-10-10*

**Decision:** the composer has a "Rich text" switch (default for new windows from Settings, off). In rich mode the message is written in a small editor (bold, italic, underline, bulleted and numbered lists, links, clear formatting) and sent as `multipart/alternative` with a plain-text part (the editor's text) and an HTML part. Drafts store both parts (`draft.save` has `html`, `draft.open` returns it) and a rich draft reopens in rich mode. The outgoing HTML is cleaned: pictures are removed (a pasted picture or a tracking pixel never goes out and nothing in the message is fetched when it is read), only http, https and mailto links survive (an unsafe link typed in is refused, one that came in by pasting is emptied), and the editor's own font settings are dropped so the reader's apply. Going back to plain text asks first when formatting would be lost. In rich mode a signature is not swapped when the sender changes (that would flatten the formatting). Inline pictures, fonts, colours and tables are not offered.
**Reason:** most people expect bold and lists; keeping to a small, safe subset keeps the output clean and keeps hostile or tracking content out of what we send.

## D-046 No other product is named in the project
*2026-10-10*

**Decision:** for legal reasons (the user's instruction) neither the repository nor the program refers to another mail program by name. The About text, the README, the package description, the design documents and the decision log were reworded without it, and a test (`tests/test_legal_names.py`) fails if such a name appears in the documents, the source or the tests. Earlier commits in the git history still contain the old wording; removing it from the history would mean rewriting published history and is left to the user to decide.
**Reason:** avoiding any suggestion of affiliation with, or imitation of, another company's product.

## D-047 The About dialog shows the version
*2026-10-10*

**Decision:** the About dialog states "Chrono Mail version x.y.z". The version comes from `pyproject.toml` when the program runs from the source tree (the single place the version lives, so it is always current), otherwise from the installed package's metadata, otherwise "unknown" (`core/appinfo.py`).
**Reason:** the user's request; the installed metadata of an editable install is only as new as the last install, so it cannot be trusted while developing.

## D-048 IMAP: an offline mirror of the server, own client, pending changes
*2026-10-10*

**Decision:** IMAP accounts are a mirror of the server in the same encrypted store (design in ARCHITECTURE.md section 8). The server decides which messages exist and what their flags are; the map between local ids and (folder, UIDVALIDITY, UID), the pending local changes and the sync positions are kept in one small state file per account, not in the message files. The IMAP client is our own (like POP3, because the standard library's `imaplib` has no size limits on lines and literals, which hostile servers can use). A changed UIDVALIDITY never deletes local mail (unmatched messages go to `Recovered/<folder>`); removals seen on the server go through the store's delete (kept in `.deleted`); local changes are applied at once and sent to the server later from a persisted list, each entry removed only after the server confirmed it. Order of work: client, state and read direction, write direction, interface, IDLE.
**Reason:** AGENTS.md section 4.1 (never lose mail) and 4.2 (hostile servers are normal); offline use and a responsive window need local changes first and the server second.

## D-049 IMAP sync, server to store
*2026-10-10*

**Decision:** `sync_imap` (layer `sync`) brings the store in line with the server, folder by folder, in this order: list the folders and decide which local folder each goes to; for each folder `EXAMINE`, check UIDVALIDITY, remove messages that are gone from the server, apply changed flags, download the new messages. The standard roles (inbox, sent, drafts, trash, junk, archive) go to the standard local folders whatever the server calls them (SPECIAL-USE attribute first, then the usual names; a role is given to one folder only); other folders keep their path with INBOX written as Inbox; a folder name with control characters or empty parts is not synchronised (and is reported). Folders the person has not subscribed to are not shown when at least one folder is subscribed; when none is subscribed all are. Only five flags are kept in step (seen, answered, flagged, deleted, draft); the server's other marks (\Recent, keywords such as $HasNoAttachment) are ignored. A message is entered in the state file only after it is stored and verified. Flags that changed on the server are applied unless the local flags differ from the last known ones too (a local change waiting to be sent wins). Messages gone from the server are removed here (index first, then the file, then the state, so that a crash is repaired by the next run) but never when more than half of at least 10 vanish at once. A changed UIDVALIDITY keeps all local messages: they are matched to the renumbered messages by Message-ID and the ones without a match are moved to `Recovered/<folder>`. The first sync of a folder leaves local-only messages alone. Folders that vanish from the server keep their local mail and are no longer synchronised. The state is an append-only file of JSON lines (`sync/imap_state.py`), compacted when it has grown eightfold.
**Reason:** AGENTS.md 4.1 and 4.2 (see D-048): every step is safe to repeat, and no single odd answer from a server can empty a folder.

## D-050 IMAP sync, store to server: by comparison, not by queue
*2026-10-10*

**Decision:** this replaces the "pending changes list" of D-048 and ARCHITECTURE.md section 8.3. Local changes are not queued: each run finds them by comparing the store with what the state file remembers about the server copy. A mapped message whose local flags differ from the flags last seen gets the difference sent (`+FLAGS` and `-FLAGS`, so the server's own keywords stay); a mapped message that now lives in another local folder is moved on the server (`UID MOVE`, or copy and delete without MOVE), mapped again at once when the server tells the new UID and otherwise matched by Message-ID in the next run; a mapped message that is gone from the store is deleted on the server (marked `\Deleted`, then `UID EXPUNGE`; without UIDPLUS it is only marked); a local message the server does not know is uploaded with `APPEND` after the pull has had its chance to match it by Message-ID, and is entered in the state as uploaded so that it is not uploaded twice. The order inside a folder is: removals seen on the server, deletions here, flags sent, flags received, new messages (matched by Message-ID with local ones before anything is downloaded), uploads. Moves between folders come first, once, before the folders are visited. If both sides changed different flags both changes survive. Every step can be repeated after an interruption without harm; the only duplicate a crash can cause is an upload that reached the server before the state was written, and the next run matches it by Message-ID instead of downloading it again. Deleting on the server is the one step that destroys mail there, so it is refused when the store looks damaged or empty, and when more than half of at least 10 mapped messages of a folder are missing from the store.
**Reason:** a queue can lose an entry or apply it to the wrong message after a crash or a restored backup; a comparison of three states (store, remembered, server) cannot, and needs no work in the engine commands: a flag change, move or delete made by any command or by hand on the store is found the same way.

## D-051 IMAP accounts in the engine and the window
*2026-10-10*

**Decision:** an account has a kind, `pop3` or `imap` (`account.add --kind`, `account.check --kind`, chosen in the account dialog, shown in the settings list; POP3 stays the default). For an IMAP account `mail.fetch` runs the two-way sync of D-049 and D-050 and reports it in the same result (downloaded, remarks, aborted); every other command works on the store as before and its effect reaches the server at the next sync: nothing needs an extra queue (D-050). Sending is unchanged (SMTP); the copy kept in Sent is uploaded by the next sync. The option "server copies" (what happens to downloaded mail on a POP3 server) does not apply to IMAP and is disabled or left out. A folder made here is made on the server (and subscribed); a folder deleted here is hidden from then on (kept in the state as hidden) and stays on the server with its mail, because deleting a folder must never delete mail on a server as a side effect; renaming a folder here is a new folder to the server (its mail is moved there by the next syncs and the old folder stays, empty). The folders that exist only here are Outbox and Recovered. The account's state is the file `imap-state.log` in the account directory.
**Reason:** one account model and one set of commands for both protocols, as D-006 intended; destructive server actions need an explicit, tested path, and only message deletion has one.

## D-052 Push for IMAP accounts: IDLE on its own connection
*2026-10-10*

**Decision:** the engine command `mail.wait` opens a connection of its own, selects the Inbox read-only, and waits in IDLE (at most 5 minutes in the window, 29 at most in the command) until the server reports a change or the program is ending (`Engine.stop_event`, checked every second); it downloads and changes nothing. The window calls it for every IMAP account while automatic checking is on and runs a normal mail check whenever it returns "changed", then waits again; a lost connection is tried again after a minute; a server without IDLE (or a POP3 account) answers "not supported" once and is left to the timer, which stays on in every case. Waits have a worker pool of their own and do not count as busy; closing the window ends them at once. Only the Inbox is watched.
**Reason:** new mail shows up within a second or two instead of after the next timer tick, without a second code path for fetching: the push only triggers the same sync as the button.

## D-053 Rules (filters)
*2026-10-10*

**Decision:** rules live in one file, `rules.json` next to `config.json` (readable by the owner only, written atomically, a damaged file is copied to `rules.json.bad` and reported), not in the account's store. A rule has a name, conditions that must all hold or of which any one is enough, actions, and optionally the one account it is for. Conditions: From, To, Cc, Subject, message text and any named header with contains, does not contain, is, is not, starts with, ends with; Size larger or smaller than a number of kilobytes; Attachment yes or no. Matching ignores case and has no regular expressions on purpose (a rule written by a person must never be able to hang the program on hostile mail). A negative condition is true when the thing is absent. Actions: move to a folder (made when it does not exist), mark read or unread, flag or unflag, move to Trash, mark as junk (move to the Junk folder), and stop (no further actions or rules for that message). "Delete" is always a move to Trash. The rules run in their order on messages that have just arrived in the Inbox, during `mail.fetch` of both account kinds, and never on old mail again; `rule.run` applies them to what is in a folder (one rule, or all that are switched on; `--test` only counts). The actions are done through the same mailbox operations as the commands of the person, so on an IMAP account they reach the server at the next sync. A failure of a rule, or of the rules file, is reported in the fetch result and never stops mail from arriving. A rule that is named explicitly runs even when it is switched off.
**Reason:** the usual way to keep an inbox in order; safe because nothing is deleted for good and nothing can loop (rules see only new mail).

## D-054 Junk and unread navigation
*2026-10-10*

**Decision:** two commands, `message.junk` (to the Junk folder; refused when already there) and `message.not-junk` (from Junk back to the Inbox; refused elsewhere). The window offers Mark as junk (Ctrl+J, not in Junk) and Not junk (Ctrl+Shift+J, only in Junk) in the Message menu and the list menu. There is no learning filter: junk here is a place, and rules and the server's own filter do the sorting. Next and previous unread message (Ctrl+Shift+Down and Up) move through the loaded rows of the list and, at its end, to the first unread message of the next folder of the account that has any; on IMAP the moves reach the server at the next sync.
**Reason:** the two most frequent mailbox chores, with no new machinery.

## D-055 Send later and snooze
*2026-10-10*

**Decision:** `mail.send --send-at <local time>` keeps the message in the Outbox as a draft-form message (Bcc and attachments kept, a note with the time) instead of sending it. `mail.send-due` sends the ones whose time has come (or one named message now): it builds the message afresh (new date and Message-ID), stores a verified copy in the Outbox, removes the scheduled original, and only then asks the SMTP server, so a crash or a refusal leaves one ordinary Outbox message that is never sent again by itself. `mail.unschedule` turns a scheduled message back into a draft. The window runs `mail.send-due` and `message.wake` every minute while it is open: a message is sent at its time only if the program is running then (said so when scheduling). `message.snooze` moves a message to the local folder Snoozed (made when needed, synchronised like any folder on IMAP) and records the time and the folder it came from in `snoozed.json` in the account directory (written before the move); `message.wake` moves due messages back, unread; a record whose message is gone is dropped, a damaged record file counts as empty. Times are local, written like 2026-10-11T09:00, may not be in the past or more than five years ahead. The composer has Send later…, the window Snooze… and Tools > Scheduled and snoozed messages (send or bring back now, take back as a draft).
**Reason:** two features people use daily, built on the existing Outbox, drafts and folders, with the same rule as everywhere: nothing is lost and nothing is sent twice.

## D-056 The message list as columns, with a column editor and a movable reading pane
*2026-10-10*

**Decision:** the message list is a table by default (the user's choice); the card layout of version 0.0.13 stays as an option. Columns: Unread, Flagged, Attachment, Replied (symbols), From / To (the recipient in Sent, Drafts and Outbox), From, To, Subject, Date, Size, Folder, Messages in conversation and Preview; the standard set is Unread, Flagged, Attachment, From / To, Subject, Date, Size. Unread rows are bold. A click on the header of From, Subject, Date or Size sorts by it and a second click reverses it (dates and sizes start with the newest and largest); the other columns do not sort because the index cannot sort by them. Columns can be dragged into another order and resized; order and widths are remembered; the header's context menu ticks columns on and off (never the last one). View > Message list opens the editor: layout, which columns in which order, where the reading pane is (right, below, off) and the date style (short, date and time, ISO). With the reading pane off, nothing is opened and so nothing is marked read; Enter or a double click opens a message in a window of its own (with Reply, Reply all, Forward), or edits it in Drafts and Outbox. The list view is a `QTreeView` without tree decoration; the list model is a table model whose single "card" column keeps the old delegate.
**Reason:** the user asked for an overview in columns with a column editor and the choice to switch the preview off; a table with sortable headers is the usual way to scan many messages.

## D-057 The settings window has pages for everything
*2026-10-10*

**Decision:** Settings has a list of pages. General (check on start, automatic checking and its interval, notifications, tray icon, close to tray; the language is stated: English, dates follow the system). Message list (layout, reading pane, date style, row height compact/normal/roomy, text size, conversations, the columns). Reading (when a message is marked read: after a moment, only when opened in its own window, or never by itself; the delay; text size of messages; plain text first; ask before opening links; pictures and other remote content: never until asked, always, or for the senders on a list that the banner's "Always for this sender" adds to). Writing (the account new messages are sent from, rich text by default, replies with my answer above, below or without the quote, save a draft every N seconds or never, ask about a missing subject, text size). Accounts (as before). Security and storage (where the data and the settings are, the state of the encryption key with the recovery code and the restore dialog, a check that every stored message can be read, a rebuild of the search index). Keyboard (every action can be given keys; keys used twice are refused; only the differences from the standard are saved). OK writes everything into the settings file and the window takes it into use at once (`apply_preferences`); the keys of the settings are in `ui/prefs.py`. `message.compose` takes `--quote above|below|none`. Not offered because Qt gives no way to do it: spell checking, and a choice of interface language (the program is not translated). Certificate exceptions are the next step.
**Reason:** the user asked for "all of it": the settings a mail program is expected to have, each of them wired to real behaviour and tested.

## D-058 Certificate exceptions: accept one exact certificate, never a server
*2026-10-10*

**Decision:** AGENTS.md 4.6.4 allows an explicit exception; this is it. When a server's certificate fails the usual checks (self-signed, private authority, wrong name), the account dialog and the account editor offer "Look at the server's certificate…": the program connects without logging in (`certificate.inspect`, which speaks only as far as STLS, STARTTLS or the TLS handshake), and shows who it was issued to and by, the names, the validity, the SHA-256 fingerprint, and why it is not trusted. Only the button "Accept this certificate" stores anything: the fingerprint of exactly that certificate, per account, in the settings file (at most 10). For an account with accepted certificates every connection (fetch, send, watch, connection test) uses a context that does not look at chain or host name and refuses every certificate whose fingerprint is not in the list: a renewed or replaced certificate is refused with "it is not the certificate you accepted" until the person looks at it and accepts it again, so a man in the middle gets nothing. An account without accepted certificates keeps the full checks. Commands: `certificate.inspect`, `account.trust-certificate`, `account.forget-certificates`; `account.add` and `account.check` take `--trusted-certificate`. Nothing is ever accepted by the program on its own, by looking, or by a test result.
**Reason:** AGENTS.md section 4.6 item 4; people with their own servers need this to use the program at all, and pinning the exact certificate is safer than switching verification off.

## D-059 Folder choices, standard-folder roles and a check interval per account
*2026-10-10*

**Decision:** for an IMAP account the account editor has a Folders tab (the folders on the server, loaded when the tab is built): a tick to synchronise each folder (the Inbox always is), and a choice of which standard folder a server folder is (Sent, Drafts, Trash, Junk, Archive) or automatic. Leaving a folder out stops its synchronisation; the mail already stored stays, and nothing is deleted on either side. Putting it back (also for a folder the server does not list as subscribed) synchronises it again. A role chosen by the person beats the automatic one (special-use attributes and the usual names); a role is given to one folder only, and the folder that had it becomes an ordinary folder. The choices are kept in the account's IMAP state file (`d` and `r` events) and take effect at the next sync; commands `account.folders` and `account.set-folders`. Every account can have its own interval for automatic checking (0 follows the general setting): the general timer checks the accounts without one, an account with one has a timer of its own, and none of them runs while automatic checking is off. The Settings window also opens Rules and the scheduled and snoozed messages.
**Reason:** the last of the settings the user asked for; folders and roles differ between servers, and detection by name cannot be right everywhere.

## D-060 Local Folders: POP3 accounts keep their mail in a mailbox of their own or in a shared one
*2026-10-10*

**Decision:** an account has a setting `storage`: `own` (the default; the account has folders of its own) or `local` (its mail goes into the shared Local Folders). Only POP3 accounts may use `local`; an IMAP account always has the folders of its server. The choice is asked on the account dialog when an account is added and, can also be changed afterwards in the account's settings (`account.set-storage`). Local Folders is a pseudo-account (kind `local`, fixed id `00000000000000000000000000000001`, no servers) with its own store and index and the standard folders; it is created when it is first needed and cannot be removed while an account uses it. The state of every account (which messages were downloaded, rules) stays per account; only the place where messages are kept is shared. Changing the storage copies the messages with the same store ids, reads every copy back and compares it, writes the new setting, and only then removes the originals; run again after an interruption it finishes the job. Moving back to own folders takes only the messages the account itself downloaded or sent; everything else stays in Local Folders. Replying is the same wherever the mail is kept: the reply goes from the account the message was addressed to (To, Cc, Delivered-To, X-Original-To, Envelope-To); when nothing matches, the account whose folders it is in, or the account the user chose as default. Scheduled messages are sent from the account named in their From address. Contacts and checks count shared mail once.
**Reason:** with 30 or more accounts a full folder tree per account fills the sidebar; the user asked for the choice at setup and for the option to change it later, and for replies that do not depend on where the mail is stored. Never losing mail (AGENTS.md 4.1) decides the order copy, verify, commit, remove.

## D-061 Favorites, Inboxes and combined views
*2026-10-10*

**Decision:** the folder list starts with a Favorites heading: All Inboxes, Unread, Flagged, Outbox, Sent, Drafts, Junk and Trash, each showing the same kind of folder of every mailbox in one list, newest first (`message.combined`, paged by offset; every row says which account it belongs to, and every message action uses that account). With two or more accounts an Inboxes heading follows, with the inbox of each account. Then come the accounts (open only when they hold the selection or the user opened them; with more than three accounts nothing else is opened by default) and Local Folders last. POP3 accounts that keep their mail in Local Folders have no tree of their own, and their mail is counted once. Three settings switch Favorites, Inboxes and Local Folders off. Unread covers every folder except Trash, Junk and Outbox. Searching inside a combined view is not done yet. Pinning other folders to Favorites, renaming and reordering them, and account groups follow.
**Reason:** with 30 or more accounts the user needs one place for all mail and a sidebar that stays short; the combined views are queries over the existing per-mailbox indexes, so nothing new is stored.

## D-062 Pinned favorites, account groups and search in combined views
*2026-10-10*

**Decision:** any folder can be pinned to Favorites (right-click > Add to Favorites). A pinned folder can be renamed (the folder itself keeps its name), moved up or down and removed; the standard favorites can be hidden and shown again. Accounts can be put in named groups (an account is in one group at most). A group is a heading in the folder list with a combined "All Inboxes" of its accounts, followed by those accounts; accounts outside any group follow, and Local Folders is last. Pins and groups are kept in `sidebar.json` next to `rules.json` (atomic write, a damaged file is copied to `.bad` and reported) and are driven by commands (`sidebar.show`, `favorite.pin/unpin/rename/reorder/show`, `group.add/rename/remove/assign`); removing an account removes its pins and its place in a group. The search box works inside combined views (`message.combined` takes a query and an account list). Reordering is done with Move up and Move down in the menu, not by dragging.
**Reason:** the user asked for these next to the standard Favorites; the file is separate from the account settings because it is about the look of the folder list, not about the accounts, and it can be lost without losing mail.

## D-063 Dragging messages shows a badge, not the rows
*2026-10-10*

**Decision:** when messages are dragged out of the list, the drag image is a small badge ("5 messages") that hangs right of and below the pointer. The default image of the selected rows is not used.
**Reason:** with many messages selected the default image covered the folder list, so the user could not see which folder the drop would go to.

## D-064 A rule may have no conditions when it is for one account
*2026-10-10*

**Decision:** a rule needs at least one condition, unless it is limited to one or more accounts; then it acts on all the mail of those accounts ("mail that arrives through this account"). In the editor, a condition row in which nothing was typed is left out, and all condition rows can be removed. A rule with no conditions and no account is refused, because it would act on all mail. The account of a rule is the account that downloaded the mail, also when its mail is kept in Local Folders; a folder in a move action is a folder of the place where that account keeps its mail.
**Reason:** the user could not write "everything from rob@bccbox.com goes to its own folder in Local Folders, and stop": the editor demanded a condition with a value.

## D-065 Rules and scheduled messages are pages of the Settings window
*2026-10-10*

**Decision:** Rules and Scheduled messages are pages of the Settings window (between Accounts and Security and storage), not windows opened from the Tools menu. The Tools menu is gone. The two actions remain for keyboard shortcuts and open Settings at their page. Rules and scheduled messages act at once, so what they did (rules applied to a folder, messages sent or taken back) is shown in the message list when Settings is closed, also when it is cancelled.
**Reason:** the user asked for it: settings belong in one place, and the Tools entries only existed because they were built before the Settings window.

## D-066 Colour scheme, style, and colours and icons that suit both
*2026-10-11*

**Decision:** Settings has an Appearance page: colours (Follow the system, Light, Dark; standard: follow the system) and style (the platform's own, or Fusion). They take effect at once. Following the system leaves the platform's palette alone. Light or Dark is asked of Qt (`setColorScheme`) and also brings the program's own palette, because platforms do not all honour the request (this Linux desktop does not). A dark scheme needs a style that can draw it: when the platform's style is the classic Windows style (`windowsvista`, `windows`), Fusion is used while the colours are dark, and the platform's style comes back when they are not. A style given with `-style` or `QT_STYLE_OVERRIDE` is never changed; the scheme still is. Error and success colours, used in text, come from `ui/colours.py` and differ for light and dark (contrast of at least 4.5 on the window and the base, checked in a test for both). The avatars keep white initials on dark circles (contrast at least 4.5 on all 72 hues). Where the platform has no icon theme, the program uses its own set of line icons (Lucide, ISC licence, `ui/resources/icons`), drawn in the colour the palette gives text at that moment, so they follow the scheme. Web pages (HTML mail) stay on a white page and are told that the scheme is light (a Chromium flag), so a mail that has styles for dark screens does not turn the page dark.
**Reason:** on Windows 10 the classic style ignores dark mode, so the program was drawn light on a dark system, and the user asked for the appearance to be configurable. Verified on Linux with both schemes (screenshots) and in tests; the classic Windows style itself could not be run here.

## D-067 Ctrl+Q marks as read; Quit is Ctrl+Shift+Q
*2026-10-11*

**Decision:** the standard key of Mark as read is Ctrl+Q, and Quit moves to Ctrl+Shift+Q. Both can be changed in Settings > Keyboard.
**Reason:** the user expects Ctrl+Q to mark mail as read, a key that is used all day, and quitting should take a deliberate key press.

## D-068 The standard keyboard shortcuts
*2026-10-11*

**Decision:** the standard keys are the ones users of a full desktop mail program expect, and all of them can be changed in Settings > Keyboard. Message list: F5 get mail; Ctrl+N new; Ctrl+R reply; Ctrl+Shift+R reply all; Ctrl+F forward; Insert toggle flag; R toggle read; Ctrl+Q mark as read; Ctrl+U mark as unread; S snooze; M quick move and Ctrl+Shift+V move to folder; C quick copy and Ctrl+Shift+C copy to folder; A archive; Ctrl+J junk (a switch: in Junk it brings the message back to the Inbox); Ctrl+P print; Delete delete; Shift+Delete delete permanently (after asking); Ctrl+Shift+S save as; Return open; Ctrl+E search; Ctrl+Shift+F advanced search; F9 folder list on or off; G go to folder; F next and B previous message; N next and P previous unread message. Message window: Ctrl+R, Ctrl+Shift+R, Ctrl+F, Esc closes. Composer: Ctrl+Enter send, Ctrl+S save draft, Esc close (asks when there is something to save), Ctrl+B, Ctrl+I, Ctrl+U, Ctrl+K in rich text. A key that is a plain letter works only while the message list or the folder list has the focus, so it never takes over typing in the search box or the composer. View source moved to Ctrl+Alt+U, Quit is Ctrl+Shift+Q (D-067). Left out because there is nothing to attach them to: sections other than mail, tags, quick actions and text snippets, forward as attachment, resend, find in message, spell check, signatures by number, translate.
**Reason:** the user asked for the standard keys to match the established ones one to one, so that people who change programs keep their habits.

## D-069 The Windows taskbar icon: an application id and an .ico file
*2026-10-11*

**Decision:** on Windows the program gives its process the application id `chrono-mail.ChronoMail` before the first window (`SetCurrentProcessExplicitAppUserModelID`), and the window icon is `ui/resources/chrono-mail.ico` (the envelope, PNG entries of 16, 24, 32, 48, 64, 128 and 256 pixels, made from the SVG). Elsewhere the SVG is used as before.
**Reason:** without its own application id the Windows taskbar treats a Python program as python.exe and shows the icon of Python, whatever icon the window sets. A shortcut or an installer needs an .ico file. Not seen on a real Windows desktop yet.

## D-070 Menu colour, Refresh, and no Add an account in the File menu
*2026-10-11*

**Decision:** dropdown menus have the colour of the menu bar. The Fusion style draws a menu in the 'base' colour of its palette (the colour of the lists) made 8% lighter, so a menu palette is set for the class QMenu with the window colour made 8% darker as its base; a test measures the result against the menu bar. Other styles draw their menus themselves and are left alone. "Get mail" is called Refresh (toolbar, File menu and tray menu), with the circular-arrows icon (`view-refresh`, bundled as Lucide `refresh-cw`) and the key F5. The File menu no longer has "Add an account…": accounts are added in Settings > Accounts, and the first-run dialog still appears when there is no account.
**Reason:** the user asked for all three: the dropdowns ran into the folder list, and Refresh is the usual name and symbol for what the button does.

## D-071 Calendars, tasks and contacts: the standard text formats, local first, then servers
*2026-10-11*

**Decision:** items are kept as iCalendar and vCard texts (ARCHITECTURE.md section 9), encrypted like mail, with a rebuildable index. A calendar object resource (an event with its changed occurrences, or a task) and a vCard are the units. Order of work: the local model and the four-section interface first, then a generic CalDAV and CardDAV client tested against the user's Kerio Connect 8.5.2 server (its calendars, contacts and tasks use CalDAV and CardDAV), then Google, then Microsoft Graph, then Exchange ActiveSync or EWS only when it is needed and can be tested (the Kerio server has the ActiveSync licence). Libraries: `icalendar` (BSD-2) for iCalendar, `recurring-ical-events` (LGPL-3.0-or-later, compatible with the AGPL) for repeating events, `tzdata` (Apache-2.0) for time zones where the system has none; vCard is parsed by our own code because no maintained typed library exists. Tags are the CATEGORIES of items with colours kept locally; they work for mail, tasks and contacts. Online meetings, weather, notes and chat are not part of this. The interface imitates the layout and behaviour of the common desktop mail programs; no artwork or names are taken from any of them.
**Reason:** the user asked for Gmail and Exchange accounts with calendars, contacts and tasks, and for the interface of a full desktop mail program. The standard formats are what servers exchange, so most of the work is shared by every kind of account.

## D-072 The store for calendars, tasks and contacts
*2026-10-11*

**Decision:** `pimstore` sits one step above `store` in the layers (it uses the atomic file writing of `store`). One directory per account holds `pim.json` (format, key id), `collections/<id>/` with `collection.json` and the encrypted items (one file per item, written to `tmp` and renamed), `.deleted/` and the SQLite index `pim.sqlite`. Collection ids and item ids are random 32-digit hex; server addresses and uids are data inside the collection or the item, never names of files. An item is replaced in one step; deleting moves the file to `.deleted` (erased only by `purge_deleted`, after 30 days by default); moving an item to another collection of the same kind stores and checks the copy first, and a repeated move after an interruption finds the copy and finishes the job. The index (kind, uid, title, sort key without accents, start, end, due, repeats and the end of the rule, status, tags, full text with prefix search) is rebuilt from the items whenever it is missing, damaged or of another version, and is reconciled with the files every time the store is opened. Wall-clock times (dates and times without a zone) are indexed as if they were UTC and ranges are widened by a day, the exact answer comes from expanding the item. Damaged items are reported by `verify`, left out of the index, and stay on disk.
**Reason:** the same guarantees as for mail (never lose data, the index is never the truth, atomic writes, authenticated encryption), with the data in the formats that servers exchange.

## D-073 The commands for calendars, tasks, contacts and tags
*2026-10-11*

**Decision:** `pim.enable` makes a calendar, a task list and an address book in the local pseudo-account (Local Folders); `collection.*` manages calendars, task lists and address books; `contact.*`, `event.*`, `task.*` list, show, add, edit, delete, move, import (vCard or iCalendar text; the same uid replaces) and export; `tag.list`, `tag.colour` and `tag.rename` work on all kinds. Item ids are the random ids of the store, found in whichever account holds them. Times are given as `2026-10-12` (a date: all day) or `2026-10-12T09:00` (in the zone given, else this computer's, with `tzlocal`), or with an offset; results give exact times with an offset and dates as text. For all-day events the end is the day after the last day, as in iCalendar. A repeating event is changed or deleted as a whole, or one occurrence at a time: the `occurrence` of `event.list` is the original start of that occurrence, which is turned into a changed occurrence or an exception, written in the event's own zone. A repeating task goes on to its next time when it is completed. Tags: names live in the items (CATEGORIES, so they reach servers), shown as first written, matched without regard to case; their colours are in `tags.json`; renaming a tag changes every item and keeps the colour. Deleting is always a move to `.deleted`.
**Reason:** every user action is a command (AGENTS.md 4.3), so the interface, the command line and any automation use the same, tested code; the CLI check showed the commands work from the command line as well.

## D-074 The four section buttons
*2026-10-11*

**Decision:** four square buttons at the bottom of the left side switch the whole window between Mail, Calendar, Tasks and Contacts (Ctrl+1 to Ctrl+4, in the View menu too). Each section has a sidebar (in a stack above the buttons), a main area (a stack beside it) and a toolbar of its own; the mail toolbar is hidden while another section is shown. New, Refresh, Delete and Search do what the section that is shown needs; the mail-only actions (reply, flags, move, archive ...) are switched off outside Mail and come back as they were, so that Delete in Tasks can never reach a hidden message. The sidebar of calendars, task lists and address books shows the accounts with their collections, each with its colour; calendars and task lists have ticks (what is not ticked is remembered), address books are chosen one at a time; a menu makes, renames, colours and deletes them. The local calendar, task list and address book are made when a section is first shown (`pim.enable`). The last section is remembered. The icons are drawn by the program's own line icons.
**Reason:** the user asked for this arrangement, as in the full desktop mail programs; one window with a section at a time keeps the sidebar short with many accounts.

## D-075 The contacts section
*2026-10-11*

**Decision:** the Contacts section has the address books of all accounts on the left with the tags in use below them (a tag filters the list), the contacts of the chosen address book in the middle (an avatar with the initials or the photo, the name, the organisation or the first address, and the tags as coloured labels; sorted by family name without regard to accents), and one contact on the right with the tabs Detail and History. History is a search of the mail of every mail place (accounts that share Local Folders count once) for the addresses of the contact, newest first. The toolbar has New contact, Refresh, Edit, Tag (a menu with a check for each tag, "New tag…" and "Remove all tags", for one or several contacts), Move to address book (inside one account), Delete, More (import and export of vCard files) and a search field. Clicking an address writes a new message to it. The list keeps the selection when it is reloaded, so that several contacts can be tagged one after the other. The editor has the name parts, e-mail addresses, phone numbers, web addresses and addresses with their kinds, company, job title, birthday, note and tags; only the lists that were emptied are cleared. Tags without a colour of their own get one made from their name. Writes to the index of one account wait for each other (an immediate write lock), so that commands that run at the same time cannot fail with "database is locked".
**Reason:** the user asked for the contacts of the full desktop mail programs; the interface only calls the commands of 0.0.60.

## D-076 The tasks section
*2026-10-11*

**Decision:** the Tasks section has the task lists of all accounts on the left (with ticks; what is not ticked is hidden), the tasks in the middle and one task on the right. The list has the tabs All, Upcoming (open tasks) and Completed, a field to add a task by typing its title and pressing Enter, and the tasks in groups by due date (Overdue, Today, Tomorrow, This week, This month, Later, No due date, Completed), each with a round mark: a ring (amber: to do, a part filled: in progress, red with a bar: overdue, grey: cancelled) or a green disc with a tick (done); a done task is struck through; the tags are coloured labels. The toolbar has New task, Refresh, Mark as completed (which becomes Mark as open again when all that are selected are done), Edit, Tag, Move to list, Delete and a search field. The detail pane shows the status, the tags, the dates, priority, progress, location and note. The editor has summary, list, start and due (each may be left out, each as a date or a time), priority, status, progress, repeat, reminder, location, note and tags. `task.edit` and `event.edit` can add and remove tags (`add_tag`, `remove_tag`), as `contact.edit` can; tags are added and taken away without regard to case. A repeating task goes on to its next time when it is completed (D-073).
**Reason:** the user asked for the tasks of the full desktop mail programs; the interface only calls commands.

## D-077 The calendar section
*2026-10-11*

**Decision:** the Calendar section has the calendars of all accounts on the left (with ticks) and a small month under them (the days that are shown are tinted; a click goes to that day). The toolbar has New event, Refresh, the views Day, Work Week, Week, Upcoming (seven days from today), Month and Agenda, Edit, Delete and a search field; above the views are previous and next, the title and Today. Day and week views are columns of hours with the timed events as blocks in the colour of their calendar (events that overlap in time stand side by side, the line of the present moment moves every minute, Ctrl and the wheel change the height of the hours and the choice is remembered), and above them the names of the days and the all-day and multi-day events as bars in rows. The month is six weeks of days with the events listed in each, "+n more" when they do not fit (a click goes to that day). The agenda lists the days that have events (the next sixty days) and can hide what is over. All times are shown in the time zone of this computer. The layout of events in days, columns, bars and cells is plain code (`ui/calendarlayout.py`), tested without a screen. A double click on an event opens it; on a free place it makes an event starting there (all day in the all-day area and in the month). For a repeating event the program asks whether a change or a deletion is for this occurrence or for all of them (`event.edit` and `event.delete` with `occurrence`). The editor has title, calendar, place, all day, start and end (the end follows the start), repeat, reminder, busy or free, attendees, tags and description. Not done: dragging events to move or resize them, tasks in the calendar, invitations.
**Reason:** the user asked for the calendar of the full desktop mail programs; the interface only calls commands.

## D-078 The CalDAV and CardDAV client
*2026-10-11*

**Decision:** `protocols/dav.py` is a generic client (no provider names in it): Basic authentication over HTTPS with the certificate rules of mail (a self-signed certificate is accepted only by its approved fingerprint, D-058; plain http only to this computer, for tests), one connection for each request, redirects only to the same server, answers capped in size, XML with a DOCTYPE or entities refused. Discovery starts at the address given and follows `current-user-principal`, `calendar-home-set` and `addressbook-home-set`, then `/.well-known/caldav` and `/.well-known/carddav`. A collection is a calendar, a task list or an address book only when its resource type says so (Kerio Connect lists its mail folders as collections too: they are left out); a calendar that holds both events and tasks becomes two collections, one for each component. Changes are asked with a sync token (RFC 6578) when the server has one and found by comparing the list of version tags when it has not; items are fetched with multiget, one by one when the server has none; a write carries `If-None-Match: *` for a new item and `If-Match` for a changed one, so that a change made by somebody else in between is a conflict, never overwritten; a server that does not send the version tag after a write is asked for it. The Kerio Connect 8.5.2 server of the user (CalDAV at `/caldav/`, calendars under `/full-calendars/<domain>/<user>/`, address books under `/contacts/<domain>/<user>/addressbook/`, sync tokens for calendars only) is the live test server; its settings are in the untracked `tests/live/dav.local.toml`.
**Reason:** Kerio, Nextcloud, Fastmail, iCloud and Google (by CalDAV) are served by one client; the sync (task 4.2) and the interface (4.3) build on it.


## D-079 Synchronisation with a CalDAV and CardDAV server
*2026-10-11*

**Decision:** an account may have a calendar and contacts server (`dav_url`, `dav_username` in its settings; the password is the mail password unless one is stored for the role `dav`; the certificates accepted for the account apply). `sync/davsync.py` keeps the account's calendars, task lists and address books (the same store as the local ones, in the folder of the account) in step with it, both ways. A collection is tied to the server by its address and component; a collection that disappears from the server stays here, untied. Per collection a state file (`dav-state.json`) remembers the sync token or ctag and, per item, the version tag and a digest of the text as it was when both sides agreed. News on the server is found by sync token (or by comparing ctag and the list of version tags), changes here by comparing digests. A change on both sides is settled for the server; the local version is kept as a copy with a new UID and the title prefix "[Conflict]", and goes to the server as an item of its own. Writes use If-Match and If-None-Match; a refused write is left for the next round. Something is deleted on one side only when the other side deleted it (here: the version on the server is still the one we knew). Read-only collections are only pulled. The state file can be lost without harm: items are then matched by UID. Commands: `account.set-dav`, `dav.discover`, `dav.sync`. The window synchronises when mail is checked (Refresh, timer, start) and after the settings of an account changed.
**Reason:** the server must never lose a change and a local change must never be lost silently; everything else follows from that. One mechanism serves Kerio, Nextcloud, Fastmail, iCloud and others.

## D-080 Messages that are sent again and again
*2026-10-11*

**Decision:** "Send later" can repeat: a start time, an interval of months, weeks and/or days (one interval, for example 1 month and 2 weeks), and an optional end. Without an end the message repeats until it is paused or stopped; there is no limit on the number of sends. The message stays in the Outbox as the pattern (`X-Chrono-Mail-Kind: recurring`); the schedule is kept in `recurring.json` next to the store. The time of send number k is the start plus k intervals, counted from the start, so nothing drifts and a month that is too short gives its last day. Sends that were missed (the program was off) are not made up: one message is sent at the next check, and the schedule moves to the first time after now. The schedule moves on before the server is asked, so a crash can lose one send but never makes two; a send that failed for certain is tried again at the next checks, three times in all, and then the schedule goes on to the next send. A series that is over removes its pattern; what was sent is in the Sent folder. Commands: `mail.send` (with `repeat_months`, `repeat_weeks`, `repeat_days`, `repeat_until`), `mail.repeat-pause`, `mail.repeat-resume` (skips what was missed), `mail.repeat-change`, `mail.repeat-cancel`, and `mail.unschedule` (back to a draft). The program has to be running at the time of a send.
**Reason:** the user asked for it. Counting from the start, at most once, and no catch-up flood are what a person expects from a mail that goes to real people.
