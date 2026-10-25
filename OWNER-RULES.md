# OWNER-RULES.md

The owner's rules, each with a number. **Only the owner adds, changes or removes a rule; Claude may only propose one.** An owner rule outranks everything Claude wrote: code, tests, documents, decisions, memory. When anything conflicts with an owner rule, the rule wins and the conflict is reported as Claude's fault; it is never settled by bending the program, a test or the rule (OR-41).

The full wording of many rules is in `PY-DEVELOPMENT-RULES.md`, `AGENTS.md` and `DECISIONS.md`; this list makes every rule findable by number. A test that checks the project itself must name the rule it enforces, and every number it compares against must be in that rule (`tests/test_owner_rules.py`, OR-42).

## The program

- **OR-1** The basic rule (D-193, AGENTS.md §0): an account is a faithful copy of its server; server first, here when it confirms; every server change shows here; offline done here and sent later; a lasting refusal is a sync conflict; two computers end up the same.
- **OR-2** Never lose mail (AGENTS.md 4.1): atomic writes, delete on the server only after the local copy is verified, the index is never the truth, every destructive step tested when interrupted, key backup is part of the design.
- **OR-3** Never crash on input (AGENTS.md 4.2): a bad message fails only itself, numbers from input are checked, network errors are normal, every crash fix comes with a test.
- **OR-4** Everything is a command (AGENTS.md 4.3): every user action is an engine command; the window holds no business logic.
- **OR-5** Layering (AGENTS.md 4.4, D-018): a module imports only from lower layers; only `ui` imports PySide6; the layer check enforces it.
- **OR-6** Security and privacy (AGENTS.md 4.6): bodies and attachments encrypted on disk, passwords only in the credential store, no JavaScript or remote content in HTML mail unless allowed, TLS verification always on, never log contents, passwords or keys.
- **OR-7** Technology (AGENTS.md §3): Python 3, Pyrefly strict with zero errors on the program, PySide6, AGPL-3.0.
- **OR-8** A code file may have up to 2,000 lines (the owner, R-18, 2026-10-22). No other size limit on files exists.
- **OR-9** Every colour is in `src/chrono_mail/core/colours.py` only; nothing from the platform; colours a person chooses are theirs, never themed (D-158).
- **OR-10** No other mail program is named in the repository or the program, except in the importer (D-046); it never reaches GitHub (the push guard, OR-43).
- **OR-11** No personal detail in the project folder or on GitHub; only the owner's name, robvandenberg64@gmail.com and the GitHub address (D-230).
- **OR-12** Private, never in git: `themes/`, `chrono-mail-import-samples/`, `TRANSFER.md`, `PROMPT-NEW-SESSION.md`, `chrono-mail-factory-reset.py`, `build-release.sh`, `gitpush.sh`, `scratchpad/`. The import samples are secret: structure only in the chat, tests with made-up data.
- **OR-13** The end-user experience is holy: any cost is ours, never the user's; an action changes what it changes and nothing else (D-146); between A and B, what is best for the user.
- **OR-14** Fix the cause, never the symptom (PY-DEVELOPMENT-RULES Rule 6).
- **OR-15** Unusable buttons are greyed, never hidden; a button names the one action that applies; one action, one name; buttons "New" and "Edit" without dots.
- **OR-16** A key acts on the part that has the keyboard focus (D-217).
- **OR-17** The UI follows what an unknowing user would guess; marks have hover texts.
- **OR-18** User texts reassure, never alarm.
- **OR-19** A stored password is never asked for again.
- **OR-20** The ticks in Edit account > Folders are this computer's view only, never server subscriptions.
- **OR-21** What the server supports is synced; what it does not support stays on this computer only (D-201).
- **OR-22** The importer imports everything as it is; only the impossible is reported.
- **OR-23** End-user tasks have a graphical way only; the terminal is never the way for a user.
- **OR-24** The demo data looks like real use; never "Dummy" or "Test" texts.
- **OR-25** The look follows the reference client, never identical; the Inboxes section never comes back; one Settings window (D-083).
- **OR-26** A standard folder is known by its role, never by its name, and is never deleted (D-269).

## The way of working

- **OR-27** No code, no version bump, no push until the owner writes "Proceed"; a bug report, "yes" or "do it" is not Proceed (PY-DEVELOPMENT-RULES Rules 1, 2).
- **OR-28** A changelist (every exact change) before every Proceed, unless the owner writes "no changelist"; Proceed covers only the changelist shown; one Proceed covers a whole job cut into steps; a changelist approves changes, not files.
- **OR-29** Change nothing that was not asked for (Rule 1).
- **OR-30** Versions (Rule 4): `x.y.z`; the owner decides x and y; z grows with every code change and never resets; the version lives in `pyproject.toml`; per-file version lines; a document-only change keeps the version; the bump comes last, right before the push line.
- **OR-31** Push only with `./gitpush.sh` and only when the owner says "push"; the push line in its own block, the line "Chrono Mail x.y.z is ready" above it; never credit Claude in git.
- **OR-32** Run only the tests related to a change, never the full suite; old tests stay as they are, every new or changed test is Pyrefly-clean; every test is unambiguous (AGENTS.md 4.5.5).
- **OR-33** Fresh reviewers check every fix before a push line.
- **OR-34** Replies: short plain English; every decision in bold on its own line; every list with unique markers (Rule 19); open problems last in their own block, ⚠️ only for real problems (Rule 20); the token estimate ends every reply (Rule 18).
- **OR-35** Implementation and standard-behaviour choices are Claude's, said in one line; the owner is asked only about what a user sees that has no standard answer (taste, scope).
- **OR-36** Only the owner decides when the work moves to a new chat.
- **OR-37** Ask before storing a memory; a casual remark is not a rule.
- **OR-38** Installers only for milestone versions, after the owner tested them; never automatic.
- **OR-39** `STATUS.md` is updated before every push.
- **OR-40** Every design decision in `DECISIONS.md` was approved by the owner through his Proceed; a test may enforce one by naming this rule and the decision's number, but never with a number of its own (OR-42).
- **OR-41** An owner rule outranks everything Claude wrote. Every changelist ends with "Your rules this touches: OR-…". A conflict with an owner rule is reported as Claude's fault and never settled by bending the program, a test or the rule.
- **OR-42** A test that checks the project itself (its files, sizes, names, colours, layers, calls) names the owner rule it enforces (OR-nn), and every number it compares against appears in that rule. No test enforces a rule the owner did not make (`tests/test_owner_rules.py`).
- **OR-43** No push from this computer reaches GitHub when a commit in it names the reference client outside the importer (`.git/hooks/pre-push` running `~/.claude/chrono-mail/name_guard.py`; `./gitpush.sh` refuses to push without it).
