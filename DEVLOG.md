# DEVLOG.md

Append-only. One entry per completed task or session. Newest at the bottom.

---

## 2026-10-08 Design session (chat 1)

**What happened**
- Researched the ArtCraft apps (PhotoCraft and siblings) for how they were built with AI agents: clean-room approach, AGENTS.md rulebook, everything-is-a-command, oracle tests, enforced layering, dev log for handover.
- Decided to build an eM Client-like mail client for Linux: Chrono Mail.
- Made decisions D-001 to D-014 (see DECISIONS.md): Python + strict Pyrefly, Qt 6 via PySide6, encrypted Maildir plus rebuildable index, IMAP-shaped internals, engine/UI separation, AGPL, KDEV as development machine, project memory in the repository.
- Created the GitHub repository and this document set. Cloned the repository on DEVELOPMENT (`C:\Python\chrono-mail`).

**Numbers**
- Design target: 500,000 messages (the user's current mailbox size).

**Still open**
- Linking a session to KDEV did not work from this chat (it stayed linked to DEVELOPMENT). The next session is started from the Claude app on KDEV.
- Everything listed under "Open questions" in STATUS.md.

## 2026-10-08 Task 0.1 Development environment on KDEV

**What landed**
- Python 3.14.4 (system), virtual environment `.venv` with PySide6 6.12.0, Pyrefly 1.3.2 and pytest 9.1.1. Installed with pip; `python3.14-venv` and `python3-pip` added through apt.
- Verified before installing: PySide6 6.12.0 wheel is `cp310-abi3` with `Requires-Python <3.16,>=3.10`; Pyrefly ships a binary wheel (`>=3.8`); pytest requires `>=3.10`.
- `pyproject.toml` created with version 0.0.1 and a `dev` extra. `.gitignore` covers `.venv/`, `.vscode/`, `__pycache__/`, `.pytest_cache/`.
- VS Code: Pyrefly extension `meta.pyrefly` 1.3.2 installed; local, ignored `.vscode/settings.json` selects the `.venv` interpreter and turns Pylance type checking off.
- `~/.local/bin/release`: Linux port of `release.bat` (outside the repository). Tested against a local bare remote: RELEASE mode, UPDATE mode (amend plus moved tag) and the missing-message error. Not yet run against GitHub.

**Numbers**
- A headless PySide6 start (offscreen platform) works; Qt WebEngine reports 6.140.0 (Chromium).

**Still open**
- Pyrefly without a config uses the `basic` preset and reports 0 errors for `x: int = "a"`. Strict mode must be configured in task 0.2.
- `~/.local/bin` is not on `PATH` until the next login (`~/.profile` adds it when the directory exists).
- The first `release "Initial release"` is run by the user.

## 2026-10-08 Task 0.2 Project skeleton

**What landed**
- `src/chrono_mail/` with the 11 modules from ARCHITECTURE.md §2 (empty packages with version block), plus `py.typed`.
- `pyproject.toml` (project version 0.0.2): setuptools build backend with src layout, `[tool.pyrefly]` strict preset, pytest config, import-linter contracts. `import-linter` added to the `dev` extra. Editable install (`pip install -e ".[dev]"`) works.
- Tests: `test_smoke.py` (imports every module), `test_layers.py` (runs `lint-imports`), `test_type_strictness.py` (project has zero type errors; strict mode reports errors on the deliberately bad `tests/data/bad_types.py`).

**Numbers**
- 15 tests pass; Pyrefly strict: 0 errors; layer contracts: 2 kept, 0 broken.
- Negative check in a scratch copy: a lower-to-higher import (`core` -> `ui`), a sibling import (`ui` -> `cli`) and `store` importing PySide6 were all reported as broken contracts.
- Strict preset reports `bad-assignment` for `x: int = "a"` and `implicit-any-parameter` for an unannotated parameter; the basic preset reported nothing.

**Still open**
- Open questions 1, 3 to 6 in STATUS.md (question 2, the layer-check tool, is answered: import-linter).
- The first `release "Initial release"` is still to be run by the user; the 0.2 changes will be part of that commit, so the version is 0.0.2.

## 2026-10-08 Task 0.3 Live test setup

**What landed**
- No mail server is installed on KDEV (user decision). Protocol tests run against `mail.technova.us` (Dovecot DA and Exim on the user's DirectAdmin server) with a disposable account, as opt-in tests: `pytest -m live`. `pytest` alone excludes them (`addopts = "-m 'not live'"`, marker `live` registered in `pyproject.toml`).
- `tests/live/`: `account.py` (loads host, user, password, ports and wrong-password attempts from environment variables, then from `account.local.toml`), `account.example.toml`, `helpers.py`, `test_live_smtp.py`, `test_live_pop3.py`, `test_live_roundtrip.py`. The real account file `account.local.toml` is listed in `.gitignore`, so `release` never stages it.
- `tests/` became a package and `pyproject.toml` got `"."` in the Pyrefly `search-path`, so `tests.live...` imports resolve.
- `AGENTS.md` §4.5.3 now describes the live-test approach.

**Server facts (probed 2026-10-08, read-only)**
- `mail.technova.us` (92.66.200.109) has ports 25, 110, 143, 465, 587, 993 and 995 open from KDEV. POP3: `Dovecot DA ready.`; plain port 110 offers STLS and no plaintext login before it; 995 is implicit TLS. SMTP: AUTH PLAIN and LOGIN on 25, 587 (STARTTLS) and 465 (implicit TLS). Certificate: Let's Encrypt wildcard for `*.technova.us`, valid until 2026-12-02; Python's default verification accepts it. `bccbox.com` and `mail.bccbox.com` resolve to the same address.

**Numbers**
- 21 live tests pass in about 9.5 seconds: SMTP capability, login, certificate rejection and wrong-password rejection on all three SMTP modes; POP3 login and mailbox commands over 995 and over 110 with STLS; refusal of plaintext login on 110; certificate rejection; wrong-password rejection; three send-to-self round trips (SMTP mode by SMTP mode, delivery polled over POP3, content checked, message deleted and confirmed gone).
- Offline gate: 15 tests pass; Pyrefly strict: 0 errors and 0 warnings; layer contracts: 2 kept.
- The mailbox was empty (0 messages) before and after the runs.

**Still open**
- Nothing is pushed to GitHub until the user says so (the test account password changes first). The `release` command is not run in the meantime.
- Not covered: IMAP (milestone 2), misbehaving servers, offline `aiosmtpd` SMTP tests (a later task if wanted), live tests on the user's other mailboxes.
- The live run needs the LAN/internet path to `mail.technova.us`; wrong-password attempts default to 1 per protocol per mode and per run (configurable, 0 disables).

## 2026-10-08 Task 1.1 core data model

**What landed**
- `chrono_mail.core`: `errors.py` (ChronoMailError with ConfigurationError, ProtocolError, StorageError, MessageFormatError, CryptoError), `flags.py` (MessageFlag with Maildir letters D F R S T and IMAP names; suffix conversion in both directions, unknown letters ignored), `account.py` (AccountKind, ConnectionSecurity, ServerSettings with host and port validation, Account), `folder.py` (FolderRole, Folder with tuple path), `message.py` (MessageRef). Frozen dataclasses with slots; no I/O, no PySide6.
- Project version 0.0.4.

**Numbers**
- 51 offline tests pass (36 new); 21 live tests pass; Pyrefly strict: 0 errors, 0 warnings, 0 suppressions; layer contracts: 2 kept.
- The flag round trip is tested for all 32 flag combinations.

**Still open**
- IMAP fields (UIDVALIDITY, server UIDs), POP3 options, and message headers and attachments come with their tasks (milestone 2, 1.7, 1.2).
- Nothing pushed to GitHub (waiting for the test account password change).

## 2026-10-08 Task 1.2 mime

**What landed**
- `chrono_mail.mime`: `model.py` (Address, Attachment, ParsedMessage, OutgoingMessage), `limits.py` (caps), `parser.py` (`parse_message(bytes) -> ParsedMessage`, never raises), `builder.py` (`build_message(OutgoingMessage) -> bytes`, `envelope_recipients`).
- Parser behaviour: iterative, depth- and part-limited walk of the MIME tree; `RecursionError` falls back to a headers-only parse; unknown or non-text charsets and invalid byte sequences fall back to UTF-8 or replacement characters and are reported; undecodable headers fall back to their raw text; the original text of address headers is kept in `raw_address_headers`; a date without a time zone is taken as UTC; text bodies are normalised to `\n`; `message/*` parts become attachments. Defects are reported as kebab-case strings.
- Builder behaviour: CRLF output, RFC 2047 headers, multipart/alternative and multipart/mixed, Bcc never written into the message, header injection and non-ASCII addresses rejected with `MessageFormatError` (the error never contains message text).
- Project version 0.0.5.

**Numbers**
- 124 offline tests pass (73 new); 21 live tests unaffected; Pyrefly strict: 0 errors, 0 warnings, 0 suppressions; layer contracts: 2 kept.
- Corpus: 18 malformed messages in `tests/data/mime/`, plus generated stress cases (nesting 5000 levels, 1,050 parts, 3 MiB header section, 200,000 header lines, a 5 MiB line).
- Fuzz test: about 2,900 seeded mutations plus 500 random-byte inputs per run (under 2 seconds). One-off hunt with another seed for 120 seconds: 528,733 mutated messages, 0 exceptions, slowest case 68 ms.
- The first corpus assertions were too loose and hid three gaps, fixed in this task: an undecodable Subject became an empty string, a wrongly declared charset produced replacement characters unreported, and an address header lost its original text.

**Still open**
- Python decodes invalid base64 leniently and reports nothing, so a damaged attachment arrives as wrong bytes without a defect. Detecting this needs strict decoding of attachments; to decide when the store verifies messages (task 1.4).
- The caps in `limits.py` are proposals, not measured. Memory use with 100+ MiB messages is untested.
- No fuzz test of the builder with hostile input beyond the injection cases.
- Nothing pushed to GitHub (waiting for the test account password change).

## 2026-10-08 Task 1.3 crypto

**What landed**
- `chrono_mail.crypto`: `keys.py` (DataKey: 32 bytes, id derived from the key, no key bytes in repr, str, errors; not picklable), `cipher.py` (`encrypt_message`, `decrypt_message`, `read_key_id`; file format CMF1: magic, version, algorithm, key id, salt, nonce, ciphertext and tag; AES-256-GCM with a per-file HKDF-SHA256 key; header authenticated), `keystore.py` (`KeyStore` protocol, `InMemoryKeyStore`, `KeyringKeyStore` over the OS credential store, `CredentialBackend` protocol), `recovery.py` (passphrase-protected recovery file with scrypt N=2^17, r=8, p=1; printable recovery code of 58 Base32 characters with a 32-bit checksum).
- First runtime dependencies in `pyproject.toml`: `cryptography>=50`, `keyring>=25.7`. New opt-in marker `oskeyring` (default run: `-m 'not live and not oskeyring'`). Project version 0.0.6.
- `ARCHITECTURE.md` section 4.3 updated to the decided design.

**Numbers**
- 198 offline tests pass (74 new, about 5.4 seconds); 21 live tests and the 1 `oskeyring` test pass; Pyrefly strict: 0 errors, 0 warnings, 0 suppressions; layer contracts: 2 kept.
- Verified on KDEV: `keyring` reaches KDE's Secret Service (`ksecretd`); scrypt N=2^17 takes 0.36 s, N=2^16 0.19 s, N=2^15 0.10 s, N=2^14 0.05 s.
- Tests: every single byte and every single bit of an encrypted file is flipped (all detected); truncation at every length; a known-answer test against HKDF and AES-GCM written out independently; every single-character error and every neighbour swap in the recovery code is detected; 3,000 random blobs raise only CryptoError.
- Overhead of an encrypted file: 58 bytes.

**Problems found on the way**
- `keyring` registers every `KeyringBackend` subclass globally. My test doubles leaked into the real credential store lookup and made the real-store test fail, and had made one `pytest.raises(CryptoError)` pass for the wrong reason. Fixed with a `CredentialBackend` protocol (test doubles are plain classes), and the missing-key tests now check the message.
- The recovery tests took 31 seconds because each derivation uses 128 MiB. `seal_key_with_passphrase` has a `log2_n` work factor parameter (accepted range 14 to 18); tests use 14, one test uses the default.

**Still open**
- The recovery file and code are primitives. The tested end-to-end procedure (user-facing export, restore after a lost credential store) is task 1.12.
- Master-password mode (unlock with a passphrase at each start), key rotation and key change (needs an interruption test, AGENTS.md section 4.1.4) are not built.
- Whether encryption can be switched off per store or per account is still open (task 1.4).
- Nothing pushed to GitHub (waiting for the test account password change).

## 2026-10-10 First push to GitHub

**What happened**
- All work of tasks 0.1 to 0.3 and 1.1 to 1.3 (version 0.0.6) was committed and pushed with `release "Initial release"`.
- Before the push: the staged file list was checked, no password or key was found in it, and `*.egg-info/` was added to `.gitignore` (the editable install had created `src/chrono_mail.egg-info/`).

**Still open**
- The test account password is to be changed once POP3 and SMTP work (it was never pushed; it is only in the ignored `tests/live/account.local.toml`).

## 2026-10-10 Task 1.4 store

**What landed**
- `chrono_mail.store`: `layout.py` (store ids, file names with the `!` separator, flag letters), `atomic.py` (exclusive create plus fsync, atomic replace, directory fsync), `store.py` (`Store`: create and open with key checks, folders, deliver with verification, read, verify, list, set_flags, move, copy, delete, recover, all_store_ids).
- On-disk layout: `store.json`, `folders/f-<12 hex>/{folder.json,tmp,new,cur}`, `.deleted/`. Message files `new/<32 hex>` or `cur/<32 hex>!2,<letters>`.
- Crash points (`checkpoint` callback) at every step of deliver, copy, flag change, move, message delete, folder create, rename and delete, and purge.
- Project version 0.0.7.

**Numbers**
- 285 offline tests pass (87 new); Pyrefly strict: 0 errors, 0 warnings; layer contracts kept.
- Interruption tests: crash simulated at every named step of every store-modifying operation. Invariants checked after a restart: the bystander message is intact, exactly one file per message, no half-written message ever visible, repeating the operation finishes it.
- Measured on KDEV (4 KB messages, encrypted, verified): 802 messages per second with fsync (1.25 ms each), 8,163 per second without; listing 300 messages takes about 2 ms.

**Found on the way**
- Repeating a rename that had in fact finished raised an error; a caller cannot tell where an interruption happened. Now a harmless no-op.
- A wrong test count and two vacuous assertions (`or True`) in my first interruption tests; fixed.

**Still open**
- Locating a message by id scans the `cur` directory unless the caller passes the path hint from the index; to be measured with 500,000 messages in task 1.5.
- The Windows `fsync` of directories is skipped (not possible there); the Windows run is task 1.14.
- No cross-process locking: Maildir is designed for concurrent access, and a lost flag race is retried. Two writers renaming folders at the same time are not protected.

## 2026-10-10 Task 1.5 index

**What landed**
- `chrono_mail.index`: `schema.py`, `model.py`, `text.py` (HTML to text, body cap), `entry.py` (index entries from raw messages, placeholder for damaged ones), `index.py` (`Index`, `IndexBuilder`, `match_expression`). In `sync`: `rebuild.py` (`rebuild_index`, `reconcile_index`). `Index.snapshot`, `Index.remove_folder` came with later tasks.
- Project version 0.0.8.

**Numbers: a generated mailbox of 500,000 messages** (`pytest -m perf -s`, KDEV, encrypted store, 4.3 KB average message, 10 percent with attachments, 15 percent HTML; the folder with 300,000 messages is the Inbox)
- Generating and storing all messages, no fsync: 97 s. Store on disk 2.18 GB. Index on disk 0.65 GB.
- Full index rebuild from the store: **3,554 s (59 minutes)**, 500,000 indexed, 0 unreadable. About half of this ran while other tests were using the machine (the rate fell from about 22,000 to about 4,000 messages per minute as the index grew), so it is an upper bound, not a clean benchmark. For comparison D-005 names eM Client's 1.5 to 2.5 hours.
- Folder lists (best of 3): count 15.8 ms; count unread 454 ms; first page of 100 by date, subject, sender or size 0.35 ms; page at offset 150,000 7.3 ms; unread only 0.4 ms; thread members and get by id under 0.05 ms.
- Search: rare word 0.5 ms; common word (296,548 hits) 113 ms; two words 105 ms; prefix 112 ms; phrase 14 ms; `from:` 88 ms; filter only (has attachments) 66 ms; **common word with folder and date filter 3,595 ms before the fix, 106 ms after** (see below).
- Changes: update flags of 1,000 messages in the index 15 ms; deliver one message with fsync and verify 21 ms; list all 300,000 messages of a folder 1.9 s; read one message with the path hint 0.04 ms, without the hint (search the directory) 42 ms; set flags with hint 0.13 ms; ids of all 500,000 files 0.56 s; reconcile index with store (nothing to change) 5.7 s.
- Tests: 80 new offline tests (text 5, entry 7, index 59, rebuild 9). The perf test now asserts responsiveness limits for the interactive operations (far above the measurements).

**Found on the way**
- The search with a folder filter was slow with a very common word: SQLite walked the folder's date index and probed the full-text table once per row. Fixed with a unary plus on the folder and date conditions when there is a text query (106 ms on the 500,000 index); a regression test checks the query plan and fails without the fix.
- My first interruption and reconcile tests had wrong expectations (a test that was not a generator, a message that was both new and the flag target); fixed.

**Still open**
- Count unread takes 454 ms in a folder of 300,000 messages: acceptable once, too slow to run for every folder on every refresh. A counter or a partial index is the likely fix when the UI needs it.
- The rebuild time under no load was not measured separately.
- Threading is the simplified thread key (D-025).

## 2026-10-10 Task 1.6 config

**What landed**
- `chrono_mail.config`: `model.py` (Pop3DeletePolicy, Pop3Options, AccountSettings, AppConfig), `paths.py` (AppDirectories, platformdirs), `files.py` (load, save, parse), `passwords.py` (PasswordStore, KeyringPasswordStore, InMemoryPasswordStore). New runtime dependency: `platformdirs`. `CredentialBackend` moved to `core`.
- Project version 0.0.9.

**Numbers**
- 56 new offline tests: round trips, 23 kinds of wrong settings, unsafe account ids (path traversal), duplicates, damaged and huge files, binary garbage, platform directories, password stores including an unavailable store.

**Still open**
- Nothing from this task. The settings file is JSON, not TOML (D-026).

## 2026-10-10 Task 1.7 POP3 client and sync

**What landed**
- `chrono_mail.protocols.pop3`: own POP3 client (`Pop3Client`, `Pop3Limits`, `Pop3Refused`). `chrono_mail.sync`: `pop3_state.py` (append-only state log), `pop3_fetch.py` (`fetch_pop3`, delete policies). `core`: `AuthenticationError`. `Store.locate_all` and `Store.locate`.
- Test infrastructure: `tests/fakeserver.py` (scripted TCP and TLS server), `tests/fakepop3.py` (stateful fake POP3 server), `tests/certs.py` (test certificates).
- Project version 0.0.10.

**Numbers**
- 64 client tests and 42 sync tests. Hostile server cases: bad greetings, silent server, stall mid-message, connection closed mid-message, endless line, endless response, invalid STAT, LIST and UIDL lines, too many messages, lone line feeds and NUL bytes, garbage error text, refused connection, unknown host, a closed client. TLS: trusted and untrusted certificates, STLS not offered, data injected before the handshake. Login: wrong password (no password in the error), commands before login, line breaks in the user name or password.
- Sync: a crash simulated at each of the 6 steps of the download and delete sequence, three occurrences each, for the download steps; all delete policies; wiped store, empty store and damaged folder block deletion; per-message failures; a connection lost part-way resumes without duplicates.
- Live: 5 tests with `Pop3Client` against the real Dovecot (TLS 995, STLS 110, wrong password, IP address certificate refused).

**Found on the way**
- `poplib` rejects any line over 2,048 bytes; confirmed with a 5,000-byte body line. This is why the client is our own.
- My first `_login` turned every protocol error into "wrong password"; a timeout would have been reported as an authentication failure. Now only a server `-ERR` is.
- The fake server renumbered messages after a DELE, which real servers never do (RFC 1939); fixed in the fake.
- A listening socket that is closed does not stop the thread blocked in `accept()` on Linux; the scripted server now shuts it down first.

**Still open**
- Only USER and PASS (no APOP, no SASL); no certificate exception for self-signed servers; the state log has no compaction.

## 2026-10-10 Task 1.8 SMTP client and sending

**What landed**
- `chrono_mail.protocols.smtp`: `send_message`, `SmtpLimits`, `SendResult`, `Refusal`. `chrono_mail.sync.send`: `send_and_file`, `SendOutcome`. Test servers: `tests/fakesmtp.py`.
- Built under project version 0.0.10 (the same as task 1.7).

**Numbers**
- 47 SMTP client tests and 11 send tests. Hostile server cases include an endless `250-` reply (cut off after 200 lines), a reply line over the limit, a silent server, a connection dropped inside DATA, error answers at three steps. Crash simulated after each of the 3 steps of send and file; exactly one copy of the message exists in every case.
- Live: 2 tests with our SMTP client (STARTTLS 587 and TLS 465, sent to the account itself and received with our POP3 client) and 1 wrong-password test.

**Found on the way**
- My own reimplementation of the reply reader looked at the wrong position for the continuation marker, so every multi-line reply was cut after its first line; the tests against the fake server found it immediately.
- `smtplib` itself would read an endless `250-` reply for ever; hence the capped reader.

**Still open**
- Only AUTH PLAIN and LOGIN (smtplib); no OAuth; non-ASCII addresses are refused (no SMTPUTF8).

## 2026-10-10 Task 1.9 engine and command line

**What landed**
- `chrono_mail.engine`: `registry.py` (typed parameter dataclasses to command specifications, `to_data`), `session.py` (`Engine`), `mailbox.py` (store and index changed together), `commands_accounts.py`, `commands_folders.py`, `commands_mail.py`, `catalog.py`. `chrono_mail.cli`: `main.py`, `render.py`. Entry point `chrono-mail-cli`. 26 commands.
- Project version 0.0.12 (0.0.11 was not used).

**Numbers**
- 92 new offline tests: registry 25, commands 39, CLI 28. Three live tests with the CLI and engine against the real server: an empty installation to account creation, send, fetch (delete after download), search, show, store verification, and a clean mailbox afterwards.
- Offline total: 681 tests (44 s, mostly the timeouts of the hostile-server tests); live 30; credential store 1; perf 1 (opt-in). Pyrefly strict: 0 errors, 0 warnings, 0 suppressions. Layer contracts: 2 kept.

**Found on the way**
- A message builder fuzz test (3,000 hostile messages): only `MessageFormatError` is ever raised, headers are always ASCII, Bcc never appears. Added to `test_mime_fuzz.py`.
- A line break in a POP3 user name or password could have injected commands; refused now. The CLI reported an unexpected `OSError` as a traceback; it is now a storage error with exit code 5.

**Still open**
- No command to save an attachment, to create or send drafts, or to reply and forward; the composer (task 1.11) needs them.
- An Engine is for one thread; the UI must use worker threads with their own index connection.

## 2026-10-10 Second push to GitHub

**What happened**
- Tasks 1.4 to 1.9 (version 0.0.12) were committed and pushed with `release "Store index config POP3 SMTP engine and CLI"`.
- Before the push: full gate (681 offline, 30 live, 1 credential-store tests; Pyrefly strict 0 errors, 0 warnings, 0 suppressions; layer contracts kept), a scan of the 72 files to be staged for secrets and private files (none), and the 500,000-message performance run passed.
- The generated performance data (3.8 GB in `~/chrono-mail-perf`) was deleted after the numbers were recorded; the test recreates it.

**Still open**
- The test account password is only in the ignored `tests/live/account.local.toml` and has never been pushed; changing it remains the user's decision.

## 2026-10-10 First testable user interface (tasks 1.10 and 1.11)

**What landed**
- `chrono_mail.ui`: `backend.py`, `models.py` (message list and folder tree), `delegate.py` (row painting), `reader.py` (reading pane with the locked-down web view), `mainwindow.py`, `accountdialog.py`, `recoverydialog.py`, `composer.py`, `formatting.py`, `app.py`. Start command `chrono-mail` (also `python -m chrono_mail.ui`); about 2,500 lines.
- Engine additions for it (D-031): index schema 2 (preview, partial unread index), `message.attachment.save`, `message.compose`, `account.check`, inline pictures in `message.show`, extended `mail.send`, `folder.delete` with subfolders, key cache; `smtp.check_login`.
- Dependencies: PySide6 is now a runtime dependency, pytest-qt a development dependency. Project version 0.0.13.

**Numbers**
- 894 offline tests (157 of them for the interface: window 29, composer 46, reader 17, dialogs 15, models and painting 13, formatting 27, backend 9, smoke 1); 31 live tests (the new one drives the window against the real server: compose and send to the account itself, fetch, read in the reading pane, move to Trash, delete for good, mailbox clean afterwards) and 1 credential-store test. Pyrefly strict: 0 errors, 0 warnings, 0 suppressions. Layer contracts kept. The whole offline run takes about 46 seconds.
- Reading pane safety tests (a local web server counts what really arrives): JavaScript does not run; images, stylesheets, fonts, frames, redirects, forms and links cannot reach the network; remote content appears only after the person allows it, for that message only; a clicked link is reported and never followed; a link that wants a new window is reported and no window opens.
- Fetching 250 messages: 0.57 s (the test servers, see below).

**Bugs found on the way (most by the interface tests)**
1. **Raw UTF-8 in headers crashed indexing** (and so the download): Python's parser returns such bytes as lone surrogates, which SQLite cannot store. Fixed in the parser (D-032), with fuzz and regression tests. This was invisible to every earlier test because all test mail was ASCII.
2. **`getaddresses()` of Python 3.14 silently drops a whole address field** when one part is malformed (`a@x.org,,` gave no recipient, `a@b@c` was not rejected). The composer now parses address by address.
3. The unread count above the list stayed stale after a message was marked as read.
4. Clicking a message and pressing Delete at once could show "Could not open the message: Message not found" instead of the confirmation (a race between two commands).
5. The subject label would have interpreted markup in a mail subject (rich text auto-detection). Now plain text.
6. A field named `sender` hid `QObject.sender()` (found by the type checker).
7. Deleting a folder with subfolders left the subfolders under a "virtual parent": the folder still showed in the tree. The interface now deletes the folder with everything below it.
8. The web page and its profile were destroyed in the wrong order (Qt warning); the page now owns the profile.
9. Test servers wrote tiny packets and ran into Nagle plus delayed ACK (40 ms per message): `TCP_NODELAY` on the test connections; the UI model tests went from 78 s to 4 s. It was the test servers, not the product.

**Still open**
- Not in this version: drafts, HTML composing, drag and drop, conversation view, a window icon, notifications, settings dialog, Archive and Snooze, Flatpak packaging. The first real use against the test account will show what else is missing.
- The index schema change means the 500,000-message numbers of task 1.5 should be measured again some time (the index is a little larger by the previews).
- The web view cannot be captured on screenshots in headless mode; I only reviewed its rendering through PDF export.
- Nothing pushed to GitHub.

## 2026-10-10 Third push to GitHub

**What happened**
- The first testable user interface (tasks 1.10 and 1.11, version 0.0.13) was committed and pushed with `release "First testable user interface"`.
- Before the push: full gate (894 offline, 31 live, 1 credential-store tests; Pyrefly strict 0 errors, 0 warnings, 0 suppressions; layer contracts kept) and a scan of the files to be staged for secrets and private files (none).

## 2026-10-10 Polish after the first real use (version 0.0.14)

**What the user saw** (a screenshot of the program with the real mailbox: 24 messages, a real HTML newsletter, dark KDE theme, and two messages in the terminal)
- Previews showed link addresses and rows of dashes; the window had no icon and the tree no icons; the preview on the selected row was faint; two senders had the same avatar colour; dates were Dutch while the menus were English.

**What changed**
- Cleaned previews (`clean_preview`, index schema 3, automatic rebuild: tested by tampering with the schema of a real index); window and application icon; theme icons in the folder tree (also for parent folders that exist only in a path); avatar colours from 72 hues with a tested contrast; secondary text on the selected row at full strength; `packaging/chrono-mail.desktop` (validated with `desktop-file-validate`) and the desktop file name constant.
- Version 0.0.14.

**The two terminal messages**
- `qt.qpa.services: Failed to register with host portal ... App info not found for 'chrono-mail'`: the program announced the name `chrono-mail` but no launcher file with that name existed. Fixed with the launcher file (it takes effect once the file is installed in the user's `applications` directory).
- `GLX: No DRI3 Extension.`: printed by the graphics libraries when the X display lacks that extension; the program ran and the web view rendered. Not changed; if it is wanted quiet, software rendering of the web view can be tried with `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu"` (not verified).

**Found on the way**
- White on Breeze's highlight blue is only 2.2:1 whatever is done to the text: my first idea (more opaque text) could not reach my first test limit, which showed the limit is the theme's own colours.

**Still open**
- The language of the interface (item above); the launcher file is not installed on this machine yet.

## 2026-10-10 Fourth push to GitHub

**What happened**
- The polish after the first real use (version 0.0.14) was committed and pushed with `release "Polish after first real use"`.
- Before the push: full gate (915 offline, 31 live, 1 credential-store tests; Pyrefly strict 0 errors, 0 warnings, 0 suppressions; layer contracts kept) and a scan of the files to be staged for secrets and private files (none).

## 2026-10-10 Step 1 of the autonomous work: key recovery (task 1.12, version 0.0.15)

**What landed**
- `ui/restorekeydialog.py` and the start-up check in the main window (D-035); File menu entry; 11 end-to-end scenarios in `tests/ui/test_ui_key_recovery.py` (no dialog when the key is there, restore by code, by file, wrong and mistyped code, wrong passphrase, key of another installation, locked wallet that is unlocked later, "not now" then later from the menu, no account, unencrypted account, the menu action).
- Tests: 926 offline (+11), Pyrefly strict 0 errors, layers kept.

**Still open**
- A backup nudge for users who have not saved a recovery file (the code is shown at account creation, but nothing checks that it was kept).

**Push**
- Pushed as version 0.0.15 with `release "Key recovery in the interface (task 1.12)"` after the full gate and the secret scan.
