# DECISIONS.md

The design decisions of Chrono Mail, and how they are kept ([[D-162]]):

1. Every decision is one file, `decisions/D-nnn.md`: its title, its topics, and its short version: what is decided and why. "D-nnn" anywhere, in any document or code, means that file.
2. Its full wording, the reasons, cases, measurements and every older wording are in `decision-details/D-nnn-details.md`. That folder is on this computer only, never on GitHub.
3. A topic file, `decisions/<topic>.md`, lists the decisions of one subject by number and title, and may point to other topics. A decision can be in several topics; its text lives only in its own file.
4. This file holds the core decisions that bind all work, and the list of topics with when to read each.

**Read only what your task needs.** Read this file in full; then open only the topics and decisions that concern your task, never the whole folder. Open a decision's details file only when its short version is not enough.

**A new decision** gets the next number (next: **D-339**). Write `decisions/D-330.md` (title, topics, short version) and `decision-details/D-330-details.md` (the full wording), add it to its topic files, and to the core decisions below only when it binds all work. **The short version** says what is decided and why, in two or three sentences: enough to apply the decision without opening its file. Everything else (how it is built, cases, history, measurements, names of functions) goes into the details. Most decisions fit in about three lines; a longer one is the exception and needs a reason. **When a decision changes,** first copy its old wording, dated, to the end of its details file, then rewrite both. A decision that no longer holds becomes "→" and the number of the decision that replaced it. In the decision and topic files a reference to a decision or topic is written inside double square brackets, so Obsidian can draw the graph. The numbers run from D-001 to D-311; D-277 was never used.

## Core decisions (they bind all work)

- [[D-193]] (the owner) **An account is a faithful copy of the account on its server** (IMAP, CalDAV, CardDAV). The full rule is AGENTS.md §0: server first, here only when it confirms; offline done here, remembered, sent later; a lasting refusal is a sync conflict, a passing failure is retried; two computers end up the same; exceptions: local data, POP3, and local-only data the owner approved ([[D-172]], [[D-191]], [[D-201]]). Every changelist says how it keeps the rule.
- [[D-201]] (the owner) **What the server supports is synced; what it does not support stays on this computer only**, with no extra folders or hidden records on the server. Given once for all: never asked per feature.
- [[D-001]] A full desktop mail client, built in steps: POP3 and SMTP, IMAP, calendars, contacts, tasks, later Gmail and Exchange.
- [[D-002]] Python with Pyrefly **strict** as a hard gate. Rejected: Rust, C++, C#, Electron.
- [[D-003]] Qt 6 through PySide6, no KDE Frameworks. Rejected: GTK4, PyQt6.
- [[D-008]] (the owner) Linux first (KDE primary, GNOME supported), Windows second. macOS is not supported (may change later).
- [[D-009]] AGPL-3.0; every dependency compatible.
- [[D-010]] The project memory is the repository's documents, kept current after every task.
- [[D-011]] "Proceed" approves exactly the changelist shown (AGENTS.md §6).
- [[D-012]] Names: Chrono Mail, `chrono-mail`, `chrono_mail`.
- [[D-013]] The version lives only in `pyproject.toml`; per-file version blocks per Rule 4.
- [[D-014]] Development on the Linux computer; the Windows computer tests and builds for Windows.
- [[D-015]] Python 3.14, venv in `.venv`, pip. Rejected: uv.
- [[D-016]] `.vscode/` ignored; dev dependencies in `[dev]`.
- [[D-017]] Pushes go through the private `./gitpush.sh`, which wraps `~/.local/bin/release` (commit, tag, push; amend when the tag exists).
- [[D-034]] One night of autonomous work once; non-autonomous is the default.
- [[D-046]] No other mail program is named in the repository or the program, except in the import and export functions, where the person needs the name ([[D-124]]); never in the documents (OR-10).
- [[D-047]] About shows the version from `pyproject.toml`, else the package metadata, else "unknown".
- [[D-161]] → [[D-233]].
- [[D-233]] The tests are in git again; they hold no personal detail ([[D-230]]). The live-test logins stay outside the project.
- [[D-230]] (the owner) **No personal detail in the project folder or on GitHub**: no account address, server or computer name, network address, password or mail detail; only the owner's name, email and the GitHub address. Neutral words in the documents, made-up data in tests; `gitpush.sh` scans every file and the message and refuses on a match.
- [[D-242]] (the owner) **Documentation in three layers, each for one reader**: `README.md` for the people who use the program, a user guide in `docs/` with one page per thing a person wants to do, and `CONTRIBUTING.md` for developers. Why: a big program needs a front door that answers "what is it, how do I get it" in 30 seconds.
- [[D-243]] (the owner) **The documentation's screenshots come from a demo profile**: a made-up person's five years of use, invented names, only example domains; no server is ever contacted. Why: the owner's own data must never appear in screenshots.
- [[D-270]] (the owner) **The owner's rules are numbered in OWNER-RULES.md and outrank everything Claude wrote**; every check of the project names its rule and uses only its numbers; no push carries the reference client's name outside the importer. Why: Claude's own size guards overruled the owner's 2,000-line rule, and the name reached GitHub unnoticed.
- [[D-162]] **Every decision is one short file in `decisions/`, its full wording in `decision-details/`, and DECISIONS.md is the entry point** with the core decisions and the topics. The short version says what is decided and why, in two or three sentences: enough to apply the decision without opening its file; most fit in about three lines, a longer one is the exception and needs a reason. Why: every chat reads DECISIONS.md at the start, so it must stay small, and nothing may be lost.
- [[D-163]] The same for STATUS.md and ARCHITECTURE.md (old text in their git-ignored archives). The token estimate counts every document read.
- [[D-018]] Layer check with import-linter (`tests/test_layers.py`); siblings in a layer do not import each other; only `ui` imports PySide6.
- [[D-019]] setuptools, src layout; a test proves the strict preset is active.
- [[D-205]] No checks on GitHub; Pyrefly (Linux and win32), the layer check and the related tests run here before every push. SonarQube Cloud reads the code after a push: [[D-237]].
- [[D-237]] (the owner) **SonarQube Cloud's automatic analysis is an outside review**, read now and then; never a gate, a push never waits for it. Only real findings are fixed (through a changelist). Why: it sees security and bug patterns Pyrefly does not.
- [[D-223]] (R-45, the owner) **Every test is unambiguous**: its result never depends on order or timing (AGENTS.md 4.5.5). An unanswered message box, window or picker fails the test at once; a test waits for what it checks; 20 s limit per test.
- [[D-020]] No mail server on the development machine: protocol tests run live (`-m live`) against the DirectAdmin and Kerio test servers; the default run is offline.
- [[D-004]] Messages are an encrypted Maildir: one file per message, authenticated encryption, random names with the flag suffix (a flag change is an atomic rename). Rejected: SQLite as the store, PST, zip, mbox.
- [[D-005]] The index is SQLite, never the truth, rebuildable at any time, not encrypted.
- [[D-030]] (security) HTML mail without JavaScript, plug-ins or storage; no outside request until allowed per message; links only after a question; subjects and headers never markup. (engine) `Backend` runs commands on a thread pool and delivers answers on the GUI thread; every job answers exactly once however it ends. (mail window) Three panes; Delete moves to Trash (in Trash it asks). Refresh is Send and Receive (per account too, [[D-059]]); Settings > General chooses what runs at start and on the timer (the owner). Nothing else sends or receives by itself ([[D-300]]).
- [[D-281]] (the owner) **A right-click on a link of a message offers Copy link address**, in the reading pane and the message window, HTML and plain text: the link goes to the clipboard fully encoded. Nothing is opened or fetched.
- [[D-215]] (the owner) **Where a link leads is shown before it is clicked**: the full address in the bottom bar while hovering; an orange triangle when it leads to another main domain than the sender's (Public Suffix List, never fetched).
- [[D-042]] (privacy) A new-mail notification gives only a count. Tray icon with the unread count; close to tray only while the icon is really visible; Quit always quits.
- [[D-058]] → [[D-082]].
- [[D-082]] An accepted certificate is kept by its exact SHA-256 fingerprint, per server; that server then connects with exactly that certificate, or with another one only when the usual checks accept it (a renewal by a known authority); any other asks again ([[D-299]]). The calendar server counts as its own server.
- [[D-299]] (UR-05, the owner) **A certificate that cannot be verified asks at once**: the Verify certificate window opens by itself, one at a time, each server once. Connect trusts exactly this certificate until Chrono Mail closes, or for good with Don't ask me again; a changed certificate asks again. TLS without a certificate check is the person's own choice, never the program's (OR-6). Why: a click on a button first was not what anyone would guess.
- [[D-180]] (the owner) **Never accept a certificate the person did not accept.** The calendar server is accepted without asking only when it shows exactly a certificate the person accepted for that account; any other failing one is skipped and told.
- [[D-007]] Everything is a command in the engine's registry; the UI and the CLI call the same commands.
- [[D-029]] A command is a typed parameter dataclass plus a handler; the CLI is generated from the registry; secrets only through `--<name>-stdin` or a prompt; deleting commands need `--yes`; fixed exit codes. `account.remove` keeps the mail. Standard folders can't be deleted: → [[D-269]].
- [[D-038]] Archive goes to the archive-role folder (made when needed); Empty only for Trash and Junk. Drag data holds ids only.
- [[D-206]] (R-08) **Actions on many messages are one command per account and folder**; on IMAP the server work goes in groups of 500, then each message's own step runs and tells its fact. One error line for all failures. A refusal holds for its group.
- [[D-122]] Commands that change one message run in the order given; different messages run in parallel; a many-message command waits for earlier ones of its account.
- [[D-137]] One copy per user, found by the process name (Linux `/proc`, Windows process list); a second start asks the first to show its window. The CLI refuses while the program runs.
- [[D-140]] Windows skips its own process and its ancestors.
- [[D-143]] No checks or sends while an import runs, the calendar sync included; none after it, except the first fill of the IMAP and calendar accounts it made ([[D-300]]).
- [[D-175]] (R-02, R-19) **Typed calls**: every command the window uses is a `TypedCommand` (parameters and answer as the engine's dataclasses), called with `Backend.ask`; no window code calls a command by its text name, and answers are read by field, never by getattr (except the reasons listed with `# getattr:`).
- [[D-146]] **An action changes what it changes, and nothing else.** The engine tells every change as a fact after success (for IMAP after the server confirms); the list, the folder list and the other sections apply exactly that change; never a full reload after a local change. The window works by identity: an action takes the messages selected at the key press, a message under way is greyed and gets no second action, a refusal brings it back with the error. A message that changed where it is moves to the place the sort now gives it, its selection kept.
- [[D-208]] (R-10) **The folder numbers are the engine's**: the index keeps them (triggers in the same transaction), every fact carries the new numbers of its folders, the window counts nothing.
- [[D-209]] (R-11) **The folder list is built once and never again**: it works out what should be there and adds, removes, moves or changes only the entries that differ, each known by its key, so the selection, open branches and scroll stay by themselves. When the selected entry goes, its account's Inbox is selected. No guard flags, nothing put back by hand.
- [[D-219]] The start asks the folders of each place where mail is kept once (not per account) and lays the folder list out once.
- [[D-210]] (R-41) A renamed folder (in every way it can be renamed) takes its Favorites pins along, before the fact is told.
- [[D-133]] → [[D-146]].
- [[D-113]] → [[D-146]].
- [[D-098]] After a delete, move, archive, junk or snooze the list keeps its scroll and selects the message after the ones that left (at the end: the one before). F2 renames a folder.
- [[D-144]] A new sort keeps the selected message selected and in view ([[D-147]]).
- [[D-147]] A new sort, or the same view fetched again, rearranges the list in place; Qt's selection moves with its messages. No hand-made reselect.
- [[D-218]] (R-15) **The message list holds every kind of view one way**: the order of every message of the view (one answer of the engine) and the rows loaded so far, asked by the message, never by place. The count line and scrollbar count the whole view at once. An action takes every selected message, loaded or not (its place comes from the order). One queue runs actions, drops, the Delete icon and Undo presses in the order made.
- [[D-148]] The cost follows the change (only new headings get their bar; a message's marks are read from its file name).
- [[D-266]] (the owner) **The list paints exactly what Qt asks it to paint; it never replaces Qt's request with its own.** Every action that takes messages out of the list selects the next message below; after the last message of the folder nothing is selected, and a row is never selected before it is loaded and painted. The automatic repeats of a held key that piled up during an action are thrown away; a separate press is never thrown away. An action on many messages not done after 1 second says what it does in the status line; the busy bar moves only for work that has run for 1 second; an idle program uses next to no processor. Why: Delete must be instant, also with the key held.
- [[D-267]] (the owner) **An action that needs no server's word (POP3, Local Folders) takes its rows out at the key press**; the action waits in turn behind the earlier ones and runs in the background. A failure brings its messages back at their places and says so in the status line. IMAP waits for the server ([[D-193]]). Why: Delete must be instant, also with the key held.
- [[D-268]] **The engine keeps one connection to each index open between commands**, let go before the file is replaced or the account's files are moved (rebuild, restore, setting Local Folders aside). Why: SQLite writes its whole change log back when the last connection closes; every command paid that.
- [[D-149]] In a view filtered by marks or a tag, a message that stops matching stays until the view is left. Exception: [[D-282]].
- [[D-282]] (the owner) **Settings > Message list: "Remove messages from Unread when I mark them as read"**, off by default. On, the person's own Mark as read in a list of only unread mail takes the messages out as Junk does ([[D-266]], [[D-267]]). A message read by showing it stays (it would vanish while being read). Kept on this computer only.
- [[D-150]] A mail check tells each change as a fact; "mailbox changed" only for real bulk jobs. More than 200 arrivals at once are one rearrange.
- [[D-151]] One lock per mailbox is held from writing the index until the fact is told, and questions read under it, so every change an answer counts was told before it.
- [[D-152]] Every fact is numbered per mailbox; a places question says how far the list has heard.
- [[D-273]] (the owner) **An answer that gives places with their headings reads the order and the headings of each mailbox in one go, under its lock**, so no change falls between them. Why: an Undo between two reads (a held Ctrl+Z) made every heading one off.
- [[D-274]] (the owner) **A profile keeps its passwords and key in the wallet under a name of its own**, "chrono-mail (<folder name>, <code of its place>)", never with a "/"; the person's own keep "chrono-mail". Why: the demo's made-up passwords were mixed with the person's real ones; KDE's wallet garbles an entry of a name with "/" when its label changes (B-23).
- [[D-275]] (B-23, the owner) **Every entry of Chrono Mail in the wallet carries a text a person can read**: "Chrono Mail: <account name>, <address> (incoming mail server)" (outgoing, calendar and contacts server), "Chrono Mail: the key that encrypts your mail". A text that cannot be written fails nothing. Why: the lookup names told the person nothing.

## Topics

| Topic | Read before work on |
|---|---|
| [[basic-rule]] | anything: it comes before every other decision |
| [[way-of-working]] | how the project is run, documents, versions |
| [[checks-and-tests]] | tests, type checks, the push checks |
| [[storage]] | the message store, the index, keys, backup and restore |
| [[security]] | passwords, certificates, encryption, privacy |
| [[pop3-smtp-sending]] | POP3, SMTP, the outbox, sending |
| [[imap]] | IMAP folders, flags, sync with the server |
| [[engine]] | the engine, commands, the CLI |
| [[changes-reach-the-screen]] | any UI work |
| [[mail-window]] | the message list, search, rules, settings |
| [[look]] | colours, fonts, icons, layout |
| [[calendars-contacts-tasks]] | appointments, tasks, contacts, reminders |
| [[tags]] | tags |
| [[importer]] | importing from other programs |
| [[packaging]] | installers, updates, Windows builds |
| [[sync-conflicts]] | anything that can differ here and on the server |
