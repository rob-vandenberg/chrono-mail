# AGENTS.md: guide for AI agents and contributors

Chrono Mail is an open-source desktop email client for Linux (Windows and macOS as a bonus), written in Python with a Qt 6 user interface through PySide6. It aims to offer the full set of functions of a modern desktop mail program.

## 0. The basic rule (D-193; only security and 4.1 Never lose mail come before it)

An account in Chrono Mail is a faithful **copy** of the account on its server (IMAP mail, CalDAV calendars and tasks, CardDAV contacts):

1. Every action on server data is done **on the server first**, and here only when the server confirms it.
2. Every change on the server shows here at the next synchronisation: new, changed, moved, renamed, deleted; items and whole folders, calendars and address books.
3. Offline, an action is done here at once, remembered through a restart, and sent when the connection is back.
4. A lasting refusal of the server is a sync conflict the person settles (Settings > Sync conflicts); a passing failure is tried again.
5. Two computers on the same account end up showing the same.
6. Exceptions: local data is the truth itself; POP3 only downloads and deletes; data only on this computer only when the owner approved it, case by case.

Every changelist says how it keeps this rule. Nothing counts as obvious: what the rule does not settle is asked, never chosen.

## 1. Orientation

| File | Purpose | When to read |
|---|---|---|
| `AGENTS.md` | Project-specific technical rules (this file) | Every session, first |
| `PY-DEVELOPMENT-RULES.md` | How we work together: the owner's working rules for this project | Every session, second |
| `OWNER-RULES.md` | Every rule the owner set, numbered (OR-nn); only the owner changes it; private: on this computer only, not in git | Every session, third (also loaded by itself) |
| `STATUS.md` | Where we are now, the next task, open questions | Every session, fourth |
| `STATUS-ARCHIVE.md` | Old text of STATUS.md (every version since 0.0.73); git-ignored | Never read whole; search when history is needed |
| `ARCHITECTURE.md` | Module map, layers, storage design, account model | Before any code task |
| `ARCHITECTURE-ARCHIVE.md` | Old text of ARCHITECTURE.md; git-ignored | Never read whole; search when the history of the design is needed |
| `ROADMAP.md` | Milestones and tasks, each with a status | When picking or finishing a task |
| `REFACTOR.md` | The senior refactor: the inventory (R-numbers), the agreed order, the progress | Every session during the refactor, after STATUS.md |
| `DECISIONS.md` | Every design decision as it stands now, by topic, short (D-162); D-193 (the basic rule) first | Every session, after STATUS.md; before proposing to change a design choice |
| `DECISIONS-ARCHIVE.md` | The full original text of D-001 to D-161 (measurements, cause chains, rejected options) | Never read whole; search by number when the history of a decision is needed |
| `DEVLOG.md` | Append-only log, one entry per completed task | When you need history |
| `TRANSFER-WINDOWS.md` | Hand-over for the Windows machine (the Windows computer): its role (Windows runs, the setup.exe), the rules, the file map | On the Windows machine, first |

## 2. Naming

| Context | Form |
|---|---|
| Project name, user-facing text (UI, window titles, About, docs prose) | **Chrono Mail** (two words, each capitalized) |
| Project identifier, repository name, release file names | `chrono-mail` |
| Where hyphens are not allowed (Python package, module names) | `chrono_mail` |
| Release and delivery file names (PY-DEVELOPMENT-RULES Rule 4) | `chrono-mail_x.y.z.ext` |

## 3. Technology (fixed, see DECISIONS.md)

- **Language:** Python 3 only. The exact minimum version is chosen in task 0.1.
- **Type checking:** Pyrefly in **strict** mode. Zero type errors is a hard gate, comparable to a compile step. Every function, method and module-level variable carries full type annotations. No `Any` unless unavoidable and commented. Pyrefly's default "basic" preset silences most errors and must never be used.
- **GUI:** Qt 6 through **PySide6**. No KDE Frameworks, no GTK, no webview shells (Electron, Tauri and the like).
- **HTML mail display:** see 4.6.3.
- **Storage:** encrypted Maildir plus an index, see 4.1.3.
- **License:** AGPL-3.0. Every dependency must be license-compatible.

## 4. Golden rules

### 4.1 Never lose mail (outranks everything except security)

With POP3, the local copy is often the only copy. A bug that loses or damages mail is the worst bug this project can have.

1. Every write to the message store is atomic: write to `tmp`, flush to disk, then move into place in one rename.
2. Never delete a message from a server before it is safely stored locally and verified (decrypts and parses).
3. The message store is encrypted Maildir (ARCHITECTURE.md §4). The index is never the source of truth: it can always be deleted and rebuilt from the message store.
4. Anything that could destroy data (delete, purge, compact, key change, migration) needs a test proving it is safe when interrupted at any point.
5. Losing the encryption key means losing all mail. Key backup and recovery are part of the core design, not an extra.

### 4.2 Never crash on input

Email is hostile input: malformed messages, broken encodings, oversized headers, malicious attachments, misbehaving servers.

1. Parsing or receiving a bad message produces an error for that message only, never a crash and never a stalled sync.
2. Numbers that come from input (sizes, counts, offsets) are validated before use. Allocations sized by input are capped.
3. Network errors, timeouts and protocol errors are normal events and handled as such.
4. Every crash fix comes with a regression test built from the input that caused it.

### 4.3 Everything is a command

Every user-visible action (fetch mail, send, move, flag, delete, create folder…) is a command in the engine's command registry, with an id, parameters and tests. The UI, the headless CLI and any future automation all call the same commands. The UI contains no business logic.

This also lets an AI agent drive and test every feature without a screen.

### 4.4 Layering is enforced

A module may only import from lower layers (ARCHITECTURE.md §3). Nothing outside `ui` may import PySide6. An automated check enforces this and runs with the tests.

### 4.5 Tests are the gate

1. Every change comes with tests.
2. A task is only done when: the tests related to the change pass (never the full suite, the owner's rule), the type check of §3 passes, the layer check passes.
3. Protocol code is tested against a real mail server (SMTP, POP3, later IMAP, including their TLS variants), not only against mocks. No mail server is installed on the development machine: the tests use the Dovecot/Exim servers on the user's DirectAdmin hosts through opt-in `live` tests (`pytest -m live`, D-020). The default run (`pytest`) stays offline and excludes them.
4. Performance target: the app stays responsive with **500,000 messages**. Heavy operations are measured against a generated test mailbox of that size.
5. **A test is unambiguous** (the owner, R-45, D-223). Its result never depends on what ran before it, nor on timing; a test that can do that is a gamble, not a test. So:
   1. It waits for the thing it checks, never for something that usually comes first (a list row leaves at the engine's fact, the status line comes with the answer later).
   2. It answers every message box and window it causes (`dialogs`, its own patch, or the `answer_` helpers of `tests/ui/world.py`). A box, a window that waits for a click (a dialog's exec) or a picker (text, file, folder, colour) that no test answers is answered with Cancel and fails the test at once; nothing ever waits for a click.
   3. It leaves nothing behind: `tests/ui/conftest.py` deletes every window after the test and puts the colours, sizes and style back; anything else program-wide that a test changes, it puts back itself.
   4. Its name is the same in every run (no random values in parametrized ids), so it can be run on its own.
   5. No test takes longer than 20 seconds (`tests/conftest.py` stops it and prints where it waited); only the opt-in 500,000-message measurement (`-m perf`) is exempt.
   6. The proof: `.venv/bin/python -m tests.run_isolated` runs every test in a process of its own (Linux), and `.venv/bin/python -m pytest -p tests.runorder --run-order reverse/shuffle` runs them in another order; all must pass.

### 4.6 Security and privacy

1. Message bodies and attachments are encrypted on disk by default (ARCHITECTURE.md §4).
2. Passwords and keys are stored in the operating system's credential store, never in plain-text config files.
3. HTML mail is shown in Qt WebEngine (part of Qt): no JavaScript, no remote content unless the user allows it for that message or sender.
4. TLS certificate verification is always on. A user may accept an exception explicitly; the app never does so silently.
5. Never log message contents, passwords or keys.

## 5. Code conventions

1. Version number and version history per file, exactly as PY-DEVELOPMENT-RULES.md Rule 4 describes for Python (directly after the imports).
2. The project version lives in one place: `pyproject.toml`. When any code file changes, the project version changes too. A change of only documents keeps the version and is pushed with `./gitpush.sh` without a message.
3. Small modules with a single clear responsibility. A module that grows beyond roughly 500 lines is a signal to split it (a guideline, not a hard limit).
4. Standard library first (`email`, `poplib`, `smtplib`, `ssl`, `sqlite3`), well-maintained packages second, own code last.
5. Names are descriptive and spelled out in full.

## 6. Workflow with AI agents

`PY-DEVELOPMENT-RULES.md` applies in full. How it maps onto agent work in Claude Code:

1. **"proceed" = plan approval.** Sessions in this project start in **plan mode**. The agent researches and proposes a plan listing every file and every change (Rule 2). Nothing is edited until the user approves the plan. Approval of a plan is the "proceed" for exactly that plan's scope, nothing more (Rule 1).
2. **Learning path.** The user decides per task how edits are approved:
   - Stage 2: approve the plan, then approve each edit manually.
   - Stage 3: approve the plan with accept edits; the user reviews the result per task in git.
   - Stage 4 (optional, later): auto mode for routine tasks.
3. **Within an approved plan**, the agent may run tests and fix its own test failures, as long as the fixes stay inside the approved scope. Anything outside the scope needs a new plan.
4. **Delivery.** In a chat session, files are delivered for download with versioned names (Rule 4/5). In a Claude Code session working in the repository, files are edited in place. In both cases every response that modifies files ends with the Rule 5 summary and release line. The user commits with their own `release` tooling.

## 7. Before you finish a task

1. The task passes the gate of 4.5.2.
2. `ROADMAP.md`: update the task's status.
3. `STATUS.md`: rewrite it (current state, next task, open questions).
4. `DEVLOG.md`: append a short entry (what landed, numbers, what is still open).
5. `DECISIONS.md`: add an entry for every design decision made during the task.

Sessions can end abruptly (context limits, crashes). The documents plus a working tree are how the next session picks up. Keep them current after every task, not at the end of a session.
