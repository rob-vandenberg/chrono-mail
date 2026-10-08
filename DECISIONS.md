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
