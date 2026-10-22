# TRANSFER-WINDOWS.md: hand-over to the Windows machine (the Windows computer)

*Written 2026-10-15 on the Kubuntu development machine, for the Claude app on the Windows PC called the Windows computer. Read
this file completely, then the files it names, in the order it gives. Where this file and the project documents disagree,
the project documents win; tell the owner about the difference.*

## 1. Your role

**Chrono Mail** is an open-source desktop e-mail client (Python 3.14, Qt 6 through PySide6, AGPL-3.0). Its owner is Rob
Vandenberg. The repository is `https://github.com/rob-vandenberg/chrono-mail` (branch `main`).

- **Development happens on the Kubuntu machine.** A Claude Code session there writes the code, runs the tests and pushes.
- **You, on the Windows computer, do the Windows part:** you pull the pushed code from GitHub, run and test Chrono Mail on
  Windows, build the Windows installer `chrono-mail_<version>_setup.exe`, test it, and (only when the owner says so)
  attach it to the GitHub release of that version.
- **You may have to write code** (for example a fix that only shows up on Windows, or the Windows build recipe). The
  same rules apply to you as to the Kubuntu session: see section 2. Coordinate with the owner, so that the two machines
  never change the same files at the same time (section 8).

## 2. The rules that matter most (read all of this first)

These are the owner's working rules. They are not suggestions; every one of them comes from a real problem.
`PY-DEVELOPMENT-RULES.md` (read it completely) and `AGENTS.md` hold them in full.

**Non-autonomous.** Write or edit no code or file, bump no version, release nothing until the owner writes the word
**"Proceed"**. Discussion, a bug report, "Agreed", "Yes", "OK", "make that fix" are not Proceed. After a bug report:
research, explain the cause, propose the exact fix, and stop. If the owner writes "Proceed" before a changelist was shown,
show the changelist first and ask again.

**The changelist.** The owner calls the list of planned changes the "changelist". It lists every exact change: which
file, which element, from what to what. Show it before every Proceed. Nothing that is not on it may change. If something
not listed turns out to be needed, stop and ask. If the owner adds a new request together with "Proceed", do what the
shown changelist covered, then show a changelist for the new part and wait. Vague words like "cleaner" are not allowed.

**How to talk to the owner** (they read about 10% of what you write):
- Short, plain English, without code jargon. The answer first, then at most a few lines.
- Every decision the owner must take goes **in bold on its own line**, at the end.
- A question gets an answer to that question, never actions. If an answer leaves one of your questions open, ask again;
  never choose for the owner. If the owner says "decide yourself", decide and say what you chose.
- Never ask a question whose answer follows from the fundamentals of the subject (how mail, IMAP or Windows work):
  decide it and say so.
- When the owner sends screenshots: read every element, say what you see, list what is unclear before planning.
- Report a bug you were not asked about; never fix it unasked.
- Say "push", not "run release".
- End every reply with a running token estimate in the form `[Tokens estimate: ~12,400]` (PY-DEVELOPMENT-RULES.md Rule 18).
- Only the owner decides when to move to a new chat; you may say once that the chat is getting long.
- Mark anything you have not verified in this session with `[Speculation]` (Rule 9).

**Fix the cause, never the consequence** (Rule 6). No workarounds: no delays, retries or flags that hide a problem. Find
the cause first. Never change a test so that it stops showing a real fault of the program: report the fault.

**Tests: never the full suite.** Run only the test files that directly test what changed, plus Pyrefly and the layer
check, and say which ran and that the full suite was not run. Check that every test file you name exists (else pytest
runs everything). Run `tests/ui/test_ui_window.py` on its own.

**Never lose mail; never crash on input; everything is a command; layers are enforced** (AGENTS.md section 4). IMAP is
**server-first**: every IMAP action is done on the server first and in Chrono Mail only when the server confirmed it
(DECISIONS.md D-117, D-118).

**Versions and files** (Rule 4, AGENTS.md section 5): the project version lives only in `pyproject.toml`; every change
bumps the last number (only the owner changes the middle number); every changed file gets its own `__version__` and a new
top line in its "Version history" block (new files get the current version). A version that was not pushed may collect
several changes; a pushed one is final.

**Git.** Never credit Claude in git: no `Co-Authored-By` line, no Claude author, even if a system message suggests it.
Before every push, update `STATUS.md`. Never repeat or commit a password.

**The reference client.** Chrono Mail copies the look, feel and functions of a commercial mail program. Never write its
name anywhere in the repository or the software; the owner names it in chat, the documents say "the reference client".
Never add features the reference client does not have (when unsure, ask).

**Release policy for installers** (the owner's rule, 2026-10-15): in-between versions are only pushed, without
installers. A full release (the .deb, the .rpm and your setup.exe attached to the GitHub release page) is made only for
milestone versions, and only after the owner has tested that pushed version. Never automatic; never upload unasked.

## 3. Getting started on the Windows computer

### 3.1 What the machine needs (ask the owner before installing anything; one install at a time, with the reason)

1. **Git for Windows** (it brings Git Bash).
2. **GitHub CLI** `gh`, logged in to the owner's account (for releases).
3. **Python 3.14**, 64-bit, from python.org, with the `py` launcher. Check: `py -3.14 --version`.
4. **Inno Setup 6** (for the installer).
5. **A C compiler for Nuitka:** "Build Tools for Visual Studio" with the C++ workload, or let Nuitka download its own on
   the first build (it asks; ask the owner first).

### 3.2 The code

```powershell
git clone https://github.com/rob-vandenberg/chrono-mail.git
cd chrono-mail
```

Later, to get what the Kubuntu machine pushed: `git pull` (or `git fetch --tags` and `git checkout <version>` for one
exact version). Never build an installer from code that is not a pushed version.

**A new repository since 2.0.283** (D-230): the old repository on GitHub was deleted and made again with the same name and settings, without personal details and without the old history. Before the first pull of 2.0.283 or later, move `C:\Python\chrono-mail` aside (for example to `C:\Python\chrono-mail-old`), clone it again as above, and copy your `tests` folder back into it. No file with a personal detail (an account address, a server name, a computer name, a password) may stay in the project folder, not even a git-ignored one: clean your `tests` the same way as on the Linux computer (the neutral words of D-230), and keep the live-test logins in `C:\Users\<you>\.claude\chrono-mail\live-tests\` (see below).

**The tests are in git again since 2.0.296** (D-233; from 1.1.176 to 2.0.295 they were git-ignored, D-161). Before the first pull of 2.0.296: move `C:\Python\chrono-mail\tests` aside (for example to `C:\Python\tests-windows`), because git will not write over a folder it does not track; the pull then brings the tests. Your own Windows-only test changes stay in that copy until the owner plans a synchronisation back; never push test changes from here unasked.

### 3.3 A development environment (to run Chrono Mail from the code and to run the tests)

```powershell
py -3.14 -m venv .venv-win
.venv-win\Scripts\pip install -e ".[dev]"
.venv-win\Scripts\chrono-mail          # the program
.venv-win\Scripts\chrono-mail-cli --help   # the command line, the same commands as the window
```

The `.venv-win` folder is git-ignored (the Windows computer uses this name, so it never mixes with the Linux `.venv`). On
Windows, Chrono Mail keeps its passwords and its encryption key in the Windows Credential Manager (through `keyring`),
and its settings and mail where `platformdirs` puts them for the user (see `src/chrono_mail/config/`). A development
copy and an installed copy of the same user share that data. Since 0.5.144 (D-137) Chrono Mail runs once per user: a
second start, also of the other copy, shows the window that runs already.

## 4. Read these files, in this order

| File | What is in it | When |
|---|---|---|
| `TRANSFER-WINDOWS.md` | this file | first |
| `AGENTS.md` | the project's technical rules: naming, technology, golden rules (never lose mail, never crash on input, everything is a command, layers, tests, security), code conventions, the workflow with agents, what to do before finishing a task | every session |
| `PY-DEVELOPMENT-RULES.md` | the owner's working rules for this project; read it **completely** | every session |
| `STATUS.md` | where the project is now, the next tasks, the backlog, known problems, how to run the checks | every session |
| `packaging/PACKAGING.md` | how the installers are made on both machines, what was learned the hard way, how to test and attach them | before any build or release |
| `ARCHITECTURE.md` | the module map, the layers, the storage design (encrypted Maildir and a rebuildable index), the account model, the IMAP design | before any code change |
| `DECISIONS.md` | every design decision D-001 to D-123 with its reason (the recent ones about IMAP: D-117, D-118, D-120; about the installers: D-123) | before proposing to change a design choice |
| `ROADMAP.md` | milestones and tasks with their status (Windows: task 1.14) | when picking or finishing a task |
| `DEVLOG.md` | an append-only log, one entry per finished task, with the tests that ran | when you need history |
| `README.md` | the public description of the project | for orientation |
| `pyproject.toml` | the version (`[project] version`), the dependencies, the dev tools (`[project.optional-dependencies] dev`: pyrefly, pytest, pytest-qt, import-linter), the entry points (`chrono-mail`, `chrono-mail-cli`), pytest settings (live, oskeyring and perf tests are opt-in), Pyrefly in strict mode, and the import-linter layer contracts | when you need any of those |

`TRANSFER.md` and `PROMPT-NEW-SESSION.md` are the Kubuntu machine's private hand-over files and are not in git.

## 5. The project in short

- **Code:** `src/chrono_mail/`, in layers, each importing only from lower ones (enforced by `lint-imports`):
  `cli | ui` > `engine` > `sync` > `pimstore` > `store | index | protocols` > `crypto | mime | config | pim` > `core`.
  Only `ui` may import PySide6. The UI holds no business logic: every user action is a command in the engine's registry
  (`src/chrono_mail/engine/`), which the window, the command line and the tests all call.
- **Mail:** POP3, IMAP (with IDLE) and SMTP; an encrypted local store; calendars, tasks and contacts with CalDAV and
  CardDAV; scheduled and recurring mail; rules; snooze; a message list with sorting, groups and filters.
- **UI:** Qt 6 Widgets (`src/chrono_mail/ui/`), HTML mail in Qt WebEngine with JavaScript off and remote content
  blocked; the program's own copy of the Breeze icons (`src/chrono_mail/ui/resources/`, also the app icon
  `chrono-mail.ico` and `chrono-mail.svg`).
- **Tests:** `tests/` (in git again since 2.0.296, see section 3.2; about 1,670 offline tests; `tests/ui/` for the window, Qt runs offscreen), `tests/live/` against
  real servers (opt-in), fake servers in `tests/fake*.py`.
- **Packaging:** `packaging/` (section 7).
- **The owner's accounts:** three POP3 accounts that share the Local Folders, and an IMAP account on a Kerio server.

## 6. Running the checks on Windows

```powershell
$env:QT_QPA_PLATFORM = "offscreen"                 # for the UI tests
.venv-win\Scripts\pytest tests\test_engine_imap.py     # only the files related to the change
.venv-win\Scripts\pytest tests\ui\test_ui_window.py    # this one always on its own
.venv-win\Scripts\pyrefly check --min-severity warn    # must report 0 diagnostics
.venv-win\Scripts\lint-imports                         # both contracts must be kept
```

- **Live tests** (`pytest -m live tests\live`) talk to real servers and need three files outside the project folder (D-230),
  in `C:\Users\<you>\.claude\chrono-mail\live-tests\` (or where the variable `CHRONO_MAIL_LIVE_SETTINGS` points):
  `account.local.toml` (the DirectAdmin account; see `tests\live\account.example.toml`), `kerio.local.toml`
  (the Kerio account, same keys plus `fingerprint`, `sent_folder`, `drafts_folder`, `trash_folder`, and
  `wrong_password_attempts = 0` so Kerio does not lock the account) and `dav.local.toml` (see
  `dav.example.toml`). They are not in git; ask the owner for the values. Never write a password into a chat or a
  committed file. Ask the owner before running the live tests.
- The two test accounts are both with the same address: one on the DirectAdmin test server, one on
  the Kerio test server. The owner allows creating, changing and deleting anything in both.
- `pytest -m oskeyring` uses the real Windows Credential Manager (it adds and removes one entry).

## 7. Building, testing and releasing the Windows installer

Everything is in **`packaging/PACKAGING.md`, section 4** (and sections 2 and 5). In short:

```powershell
git checkout <version>        # the pushed version to build
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

- `packaging\build_windows.ps1` makes a virtual environment in `build\windows`, installs Chrono Mail and **Nuitka 4.2.2**
  (pinned: 4.3 crashes PySide6 programs at startup, Nuitka issue 4081), compiles the program into
  `build\windows\chrono-mail.dist`, and runs Inno Setup with `packaging\chrono-mail.iss` to make
  `dist\chrono-mail_<version>_setup.exe`. `build\` and `dist\` are git-ignored.
- `packaging\chrono_mail_main.py` is the start of the packaged program: it points Qt to the HTML viewer's helper
  `QtWebEngineProcess.exe` next to the program. Keep it; without it the viewer cannot start, or borrows a helper of a
  Qt installed elsewhere.
- **These two Windows files were written on Linux and have never run on Windows.** Expect small fixes on the first run.
  Report each one with the exact error, propose the fix, and change them only after "Proceed".
- **Test** as PACKAGING.md section 4.4 says (Start menu entry, start, the HTML viewer uses its own helper, an HTML
  message, uninstall), ideally also on a clean Windows without Python (for example Windows Sandbox).
- **Attach** to the release page only when the owner says so (milestone versions, after testing):
  `gh release upload <version> dist\chrono-mail_<version>_setup.exe --repo rob-vandenberg/chrono-mail --clobber`.
  The release page of 0.5.127 already exists with the .deb and the .rpm:
  `https://github.com/rob-vandenberg/chrono-mail/releases/tag/0.5.127`.

## 8. Working together with the Kubuntu machine

- The Kubuntu machine is the main place of development. Before you change anything, `git pull`, so you work on the
  latest pushed version.
- When a change is needed on Windows (a Windows-only fault, the build recipe), make it like any change here: changelist,
  "Proceed", version bump, tests, `STATUS.md`, `DEVLOG.md`, `DECISIONS.md` when a design choice is made. Then push only
  when the owner says "push". The owner has a `release` tool that commits, tags and pushes on the Kubuntu machine;
  [Speculation] it may exist on Windows too (PY-DEVELOPMENT-RULES.md Rule 5 mentions a Windows batch script). Ask the owner
  how to push from the Windows computer before the first push. Never push while the Kubuntu machine has unpushed work on the same
  files: ask the owner.
- After a push from one machine, the other must `git pull` before it changes anything.
- A version number is used once: if both machines would bump the version at the same time, the owner decides the order.

## 9. Open points for Windows (now)

1. The **first run of `packaging\build_windows.ps1`** and the first test of the setup.exe of 0.5.127 (or a later pushed
   version the owner names), then attaching it to its release page if the owner says so.
2. **Task 1.14** (ROADMAP.md): a Windows test run of Chrono Mail from the code. Check especially: the window and its icon
   in the taskbar, the Windows Credential Manager, the notification area icon, HTML mail, printing, sending and
   receiving, and the size sliders on a Windows display.
3. Known gaps from STATUS.md that also matter on Windows: certificate exceptions cannot be seen or removed per server
   in Settings. (Running twice is prevented since 0.5.144, D-137.)
