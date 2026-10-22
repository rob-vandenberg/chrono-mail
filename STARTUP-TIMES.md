# STARTUP-TIMES.md

*How long the owner's real Chrono Mail takes to start, per version, so later (and older) versions can be compared. Newest measurement first.*

## How it is measured

- The owner's own copy: the dev folder on the Linux computer, the owner's own profile (`~/.config/chrono-mail`, the real mail), never a test copy.
- The script `scratchpad/start_timing.py` (private, git-ignored) starts the program exactly as the desktop does (`chrono_mail.ui.app.main`) and only watches; it changes nothing in the program. It writes `scratchpad/start_timing.txt`: when each step finished, every engine command with how long it waited and ran, every folder list lay-out, and every moment the window's thread was busy for more than 0.2 s. When nothing has run for 10 seconds it quits the normal way (Menu > Exit).
- Run it with Chrono Mail closed: `.venv/bin/python scratchpad/start_timing.py` (from the project folder).
- "Fully loaded": the window, the folder list and every row of the view that opens at start are in.
- Seconds are counted from the moment the script starts (the Python start before it, about 0.1 s, is not in it).
- **Compiled code matters.** Python saves the compiled form of every file of the program (`__pycache__`); the first start after a new version compiles every changed file again (about 0.3 s more on 2026-10-21) and later starts use the saved form. That is why the first start after a new version is slower. Claude's shell has `PYTHONDONTWRITEBYTECODE=1`, so a run started by Claude never saves compiled code: measure "first start after a new version" with the `__pycache__` folders of `src/chrono_mail` removed, and "a normal start" after `.venv/bin/python -m compileall -q src/chrono_mail`. The owner's desktop has no such setting (checked), so the owner's own starts do save it.
- What the view that opens at start holds counts as much: write down how many messages it has, and its sort (each heading is a row: by date there are a few, by subject or sender one per subject or sender). The script counts rows, headings included.
- An older version cannot simply be measured on the real profile: a version from before 1.1.250 does not know index format 10 and rebuilds the search indexes. Measure older versions on a copy of the profile only.

## The owner's start, for comparing

- Settings that shape the start: Show on startup = Favorites > Unread (603 unread messages on 2026-10-21; 611 rows sorted by date, 1,069 sorted by subject) until 1.5.257; found set to Local Folders > Inbox at the 1.6.260 runs (2 rows); no mail check at start (`check_on_start=false`); mail checks every 20 minutes.
- 4 entries at the top of the folder list, 27 folder lists asked at start (accounts and Local Folders; written as 24 on 2026-10-21 by mistake, every run had 27).
- The Linux computer, over XRDP (`DISPLAY=:10.0`).

## Measurements

### 1.6.261 (2026-10-21, not pushed yet), two runs, with compiled code (a normal start), Unread: 571

Measured by Claude on the owner's own copy and profile, Chrono Mail closed, after `.venv/bin/python -m compileall -q src/chrono_mail`; Show on startup Favorites > Unread, **Unread: 571**, sorted by date: **579 rows**. What changed (D-219): the start asks the folders of each place where mail is kept once (2 questions: Local Folders and the IMAP account; before 27, one per account) and lays the folder list out once (before: an early lay-out of the Favorites part, the rest about 0.3 s later). The script now notes the one lay-out as "final".

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.577 s | 0.635 s |
| Main window built | 0.989 s (from 0.648 s: 0.341 s) | 1.098 s (from 0.721 s: 0.377 s) |
| Window shown (the window's thread busy) | 1.12 s (0.489 s busy) | 1.23 s (0.542 s busy) |
| The 2 folder lists in | 1.391 s and 1.457 s | 1.444 s and 1.526 s |
| Folder list laid out, the only time (complete, in one step) | 1.457 s (took 0.047 s) | 1.526 s (took 0.038 s) |
| First answer: all 579 rows counted, the first 100 with their rows | 1.567 s | 1.610 s |
| The whole order of the view | 1.719 s | 1.753 s |
| **Fully loaded** | **1.72 s** | **1.75 s** |

Engine answers (waited and ran, seconds), run 1 / run 2: account.list 0.046 / 0.041, sidebar.show 0.087 / 0.057, key.status 0.152 / 0.126, tag.list 0.133 / 0.118, sync.waiting 0.274 / 0.187, conflict.list 0.265 / 0.257, contact.addresses 0.152 / 0.126, the 2 folder.list 0.087 and 0.106 / 0.060 and 0.104, the first message.combined-order 0.126 / 0.093 (1.6.260: 0.403 / 0.424: it no longer waits behind 25 folder lists), the second 0.143 / 0.136. A separate run of `scratchpad/start_paint_timing.py`: the folder list is empty in its first paints and complete in the next (31 rows, 21 ms), never a part of it.

Compared with 1.6.260 (Unread: 571): the folder list complete at 1.46 and 1.53 s instead of 1.68 and 1.64 s, and in one step; the first rows at 1.57 and 1.61 s instead of 1.73 and 1.72 s; fully loaded 1.72 and 1.75 s instead of 1.87 and 1.88 s (on average −0.14 s); with 1.5.257 (2.14 and 2.22 s): −0.44 s. Building the window and the busy moment while it is shown were a little longer in these two runs (0.34 to 0.38 s and 0.49 to 0.54 s against 0.30 to 0.32 s and 0.42 to 0.44 s); nothing before the start was changed, so that is the spread of two runs.

### 1.6.260 (2026-10-21, R-15, pushed), two runs, with compiled code (a normal start), Unread: 571

Measured by Claude on the owner's own copy and profile, Chrono Mail closed, after `.venv/bin/python -m compileall -q src/chrono_mail`. The owner set Show on startup back to Favorites > Unread for these runs: **Unread: 571**, sorted by date, newest first: **579 rows** (571 messages and 8 headings), the same number of rows as the 1.5.257 runs.

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.588 s | 0.625 s |
| Main window built | 0.975 s (from 0.658 s: 0.317 s) | 0.993 s (from 0.696 s: 0.297 s) |
| Window shown (the window's thread busy) | 1.08 s (0.444 s busy) | 1.10 s (0.421 s busy) |
| Folder list, early lay-out | 1.325 s (took 0.022 s) | 1.288 s (took 0.013 s) |
| Folder list, final lay-out | 1.675 s (took 0.017 s) | 1.635 s (took 0.021 s) |
| First answer: all 579 rows counted and laid out, the first 100 with their rows (the screen shows fewer) | 1.733 s | 1.721 s |
| The whole order of the view (the second message.combined-order) | 1.866 s | 1.882 s |
| **Fully loaded** | **1.87 s** | **1.88 s** |

"Fully loaded" now means: every row of the view counted and in its place, and the rows on screen in. The rows further down are asked only when they come into view (R-15); in these runs no row was asked after the first answer (no message.rows). Before (1.5.257) every row was loaded at start in 6 batches of about 100.

Engine answers (waited and ran, seconds), run 1 / run 2: account.list 0.027 / 0.030, sidebar.show 0.053 / 0.049, key.status 0.149 / 0.105, tag.list 0.150 / 0.095, sync.waiting 0.312 / 0.284, conflict.list 0.245 / 0.211, contact.addresses 0.149 / 0.106, the 27 folder.list 0.121 to 0.401 / 0.094 to 0.382, the first message.combined-order 0.403 / 0.424, the second (the whole order) 0.126 / 0.152. The window's own work on each answer at most 0.022 s (the order of 579 messages: 0.010 / 0.014 s, then 0.006 / 0.009 s).

Compared with 1.5.257 (the same 579 rows): fully loaded on average 1.87 s instead of 2.18 s (−0.31 s), because the 5 further batches of rows are no longer loaded at start. The first rows are on screen at about the same moment (1.73 and 1.72 s against 1.68 and 1.76 s): the first answer (0.40 to 0.42 s) does the work of 1.5.257's first message.combined and message.combined-groups together (0.35 to 0.47 s, side by side). Everything before the list is unchanged. Two runs per version are few: treat it as "not slower before the list, and the list complete sooner".

### 1.6.260 (2026-10-21, R-15, pushed), two runs, with compiled code (a normal start; Show on startup is now Local Folders > Inbox, 2 rows)

Measured by Claude on the owner's own copy and profile (the dev folder, `~/.config/chrono-mail`), Chrono Mail closed, after `.venv/bin/python -m compileall -q src/chrono_mail`. **The start view is not the one of the earlier measurements:** Show on startup is now Local Folders > Inbox (`startup_view`, the owner's own setting, left as it is), whose list has **2 rows**, sorted by date; the earlier runs opened Favorites > Unread (579 to 1,069 rows). So "fully loaded" cannot be compared with them; the steps up to the folder list can.

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.575 s | 0.640 s |
| Main window built | 0.956 s (from 0.644 s: 0.312 s) | 1.050 s (from 0.712 s: 0.338 s) |
| Window shown (the window's thread busy) | 1.07 s (0.447 s busy) | 1.16 s (0.469 s busy) |
| Folder list, early lay-out | 1.611 s (took 0.098 s) | none: laid out once, at the end |
| Folder list, final lay-out | 1.678 s (took 0.011 s) | 1.767 s (took 0.025 s) |
| The list's order, with its rows (one message.order) | 1.679 s (answer after 0.099 s) | 1.808 s (answer after 0.049 s) |
| **Fully loaded** (2 rows) | **1.68 s** | **1.81 s** |

Engine answers (waited and ran, seconds), run 1 / run 2: account.list 0.048 / 0.033, sidebar.show 0.052 / 0.068, key.status 0.113 / 0.106, tag.list 0.112 / 0.181, sync.waiting 0.276 / 0.364, conflict.list 0.223 / 0.209, contact.addresses 0.115 / 0.108, the 27 folder.list 0.077 to 0.433 / 0.076 to 0.454, message.order 0.099 / 0.049 (one question: a view of 2 rows fits in the first answer). The window's own work on each answer at most 0.006 s, except the early folder list lay-out of run 1 (0.098 s; 0.018 to 0.030 s in every earlier run, and run 2 had none: not explained, one run).

Compared with 1.5.257 up to the final folder list lay-out (1.64 and 1.72 s): 1.68 and 1.77 s, the same within the spread of two runs. Program code, building the window and the busy moment while it is shown are unchanged (0.31 to 0.34 s and 0.45 to 0.47 s). R-15 adds nothing to the start before the list; the list itself cannot be compared with these runs (another view). Favorites > Unread with 1.6.260 is still to be measured: only with the owner's start setting set to it (the owner's choice).

### 1.5.257 (2026-10-21, pushed), two runs, with compiled code (a normal start; 579 rows in Favorites > Unread)

Measured by Claude on the owner's own copy and profile, Chrono Mail closed, after `.venv/bin/python -m compileall -q src/chrono_mail`. The list is sorted by date (the remembered sort), newest first: **579 rows**, headings included (fewer unread messages than the 603 of the morning).

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.541 s | 0.618 s |
| Main window built | 0.914 s (from 0.610 s: 0.304 s) | 1.017 s (from 0.690 s: 0.327 s) |
| Window shown (the window's thread busy) | 1.03 s (0.440 s busy) | 1.11 s (0.445 s busy) |
| Folder list, early lay-out | 1.209 s (took 0.018 s) | 1.343 s (took 0.022 s) |
| Folder list, final lay-out | 1.636 s (took 0.021 s) | 1.721 s (took 0.020 s) |
| First rows visible (100 / 104) | 1.680 s | 1.762 s |
| All 579 rows in (6 batches of about 100) | 2.136 s | 2.220 s |
| **Fully loaded** | **2.14 s** | **2.22 s** |

Engine answers (waited and ran, seconds), run 1 / run 2: account.list 0.038 / 0.035, sidebar.show 0.021 / 0.061, key.status 0.119 / 0.117, tag.list 0.140 / 0.159, sync.waiting 0.261 / 0.340, conflict.list 0.218 / 0.207, the 27 folder.list 0.134 to 0.441 / 0.092 to 0.437, message.combined-groups 0.473 / 0.361, the first message.combined 0.415 / 0.346, the next ones 0.079 to 0.106 / 0.070 to 0.129. The window's own work on each answer at most 0.022 s in both runs.

Compared with 1.2.253 with compiled code (2.56 s and 2.49 s, 611 rows): fully loaded on average 2.18 s instead of 2.52 s (−0.34 s). Part of that is the shorter list (32 rows fewer is about 0.03 s at about 0.1 s per batch of 100); the rest is spread over the program code loading (0.54 to 0.62 s instead of 0.63 to 0.68 s) and the moment the final folder list lay-out is reached (1.64 to 1.72 s instead of 1.75 to 1.88 s). The steps the refactor touched since (R-13 Settings OK, R-14 the current view, the link line) show no cost: building the window is still about 0.3 s, both folder list lay-outs stay under 0.025 s, and the window's work per answer under 0.025 s. Two runs per version are few: treat the difference as "not slower", not as a proven gain.

### 1.2.253 (2026-10-21), the first start after a new version: no compiled code, two runs

Both runs without any saved compiled code (Claude's shell saves none), so both are "the first start after a new version". The owner, watching: "This looked now like it looks for me." The list had **1,069 rows** now (611 earlier the same day): the same 603 unread messages, but the owner had sorted the list by Subject, which gives one heading per distinct subject (466) instead of the 8 date headings; so the list had almost twice as many rows to load. (First written as 1,069 messages by mistake: the script counts rows, headings included.)

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports, compiled first) | 0.969 s | 0.864 s |
| Main window built | 1.353 s (from 1.045 s: 0.308 s) | 1.277 s (from 0.933 s: 0.344 s) |
| Window shown (the window's thread busy) | 1.47 s (0.453 s busy) | 1.38 s (0.468 s busy) |
| Folder list, early lay-out | 1.727 s (took 0.029 s) | 1.600 s (took 0.030 s) |
| Folder list, final lay-out | 2.068 s (took 0.010 s) | 2.034 s (took 0.018 s) |
| First rows visible (175) | 2.357 s | 2.385 s |
| All 1,069 rows in (603 messages, 466 headings) | 3.484 s | 3.639 s |
| **Fully loaded** | **3.48 s** | **3.64 s** |

Compared with the runs with compiled code (the section below): the program code takes about 0.3 s longer to load (0.86 to 0.97 s instead of 0.63 to 0.68 s), and loading 1,069 rows instead of 611 (the Subject sort's headings) takes about 0.5 s longer. Everything between (the window, the folder list, the engine answers) is the same.

### 1.2.253 (2026-10-21, before it was pushed), two runs, with compiled code (a normal start; 611 messages in Favorites > Unread)

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.634 s | 0.676 s |
| Main window built | 1.141 s (from 0.816 s: 0.325 s) | 1.050 s (from 0.750 s: 0.300 s) |
| Window shown (the window's thread busy) | 1.24 s (0.561 s busy) | 1.16 s (0.429 s busy) |
| Folder list, early lay-out | 1.469 s (took 0.017 s) | 1.385 s (took 0.023 s) |
| Folder list, final lay-out | 1.876 s (took 0.016 s) | 1.753 s (took 0.022 s) |
| First 100 messages visible | 1.888 s | 1.819 s |
| All 611 rows in (603 messages, 8 headings; 7 batches of about 100) | 2.555 s | 2.493 s |
| **Fully loaded** | **2.56 s** | **2.49 s** |

Engine answers (waited and ran, seconds), run 1 / run 2: account.list 0.042 / 0.038, sidebar.show 0.074 / 0.075, key.status 0.103 / 0.104, tag.list 0.137 / 0.043, sync.waiting 0.261 / 0.223, conflict.list 0.214 / 0.216, the 27 folder.list 0.130 to 0.473 / 0.140 to 0.439, message.combined-groups 0.392 / 0.374, message.combined 0.083 to 0.374 / 0.086 to 0.386. The window's own work on each answer at most 0.019 / 0.023 s.

Compared with 1.1.252: fully loaded on average 2.52 s instead of 2.35 s (+0.17 s), the same as the spread between the two runs of 1.1.252 (0.17 s). The steps that take longer lie mostly before the window is shown (program code loaded +0.08 s, the moment until the window is made, the busy moment while it is shown); the folder list (both lay-outs under 0.025 s), the engine answers and the message batches are the same. Two runs per version are too few to call +0.17 s a real difference.

### 1.1.252 (2026-10-21), two runs

| Step | Run 1 | Run 2 |
|---|---|---|
| Program code loaded (imports) | 0.614 s | 0.525 s |
| Main window built | 0.983 s | 0.883 s |
| Window shown (the window's thread busy) | 1.10 s (0.445 s busy) | 0.98 s (0.411 s busy) |
| Folder list, early lay-out | 1.326 s (took 0.010 s) | 1.203 s (took 0.013 s) |
| Folder list, final lay-out | 1.714 s (took 0.027 s) | 1.564 s (took 0.015 s) |
| First 100 messages visible | 1.789 s | 1.631 s |
| All 611 rows in (603 messages, 8 headings; 7 batches of about 100, about 0.1 s each) | 2.437 s | 2.269 s |
| **Fully loaded** | **2.44 s** | **2.27 s** |

Engine answers in run 1 (waited and ran, seconds): account.list 0.027, sidebar.show 0.072, key.status 0.117, tag.list 0.150, the 27 folder.list 0.128 to 0.439 (four at a time), message.combined-groups 0.389, the first message.combined 0.378, the next ones 0.08 to 0.14. The window's own work on each answer stayed under 0.03 s.
