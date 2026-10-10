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
