# DEVLOG.md

Append-only. One entry per completed task or session. Newest at the bottom.

---

## 2026-10-08 Design session (chat 1)

**What happened**
- Researched the ArtCraft apps (PhotoCraft and siblings) for how they were built with AI agents: clean-room approach, AGENTS.md rulebook, everything-is-a-command, oracle tests, enforced layering, dev log for handover.
- Decided to build a full-featured mail client for Linux: Chrono Mail.
- Made decisions D-001 to D-014 (see DECISIONS.md): Python + strict Pyrefly, Qt 6 via PySide6, encrypted Maildir plus rebuildable index, IMAP-shaped internals, engine/UI separation, AGPL, the Linux computer as development machine, project memory in the repository.
- Created the GitHub repository and this document set. Cloned the repository on the Windows computer (`C:\Python\chrono-mail`).

**Numbers**
- Design target: 500,000 messages (the user's current mailbox size).

**Still open**
- Linking a session to the Linux computer did not work from this chat (it stayed linked to the Windows computer). The next session is started from the Claude app on the Linux computer.
- Everything listed under "Open questions" in STATUS.md.

## 2026-10-08 Task 0.1 Development environment on the Linux computer

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
- No mail server is installed on the Linux computer (user decision). Protocol tests run against the DirectAdmin test server (Dovecot DA and Exim on the user's DirectAdmin server) with a disposable account, as opt-in tests: `pytest -m live`. `pytest` alone excludes them (`addopts = "-m 'not live'"`, marker `live` registered in `pyproject.toml`).
- `tests/live/`: `account.py` (loads host, user, password, ports and wrong-password attempts from environment variables, then from `account.local.toml`), `account.example.toml`, `helpers.py`, `test_live_smtp.py`, `test_live_pop3.py`, `test_live_roundtrip.py`. The real account file `account.local.toml` is listed in `.gitignore`, so `release` never stages it.
- `tests/` became a package and `pyproject.toml` got `"."` in the Pyrefly `search-path`, so `tests.live...` imports resolve.
- `AGENTS.md` §4.5.3 now describes the live-test approach.

**Server facts (probed 2026-10-08, read-only)**
- The DirectAdmin test server has ports 25, 110, 143, 465, 587, 993 and 995 open from the Linux computer. POP3: `Dovecot DA ready.`; plain port 110 offers STLS and no plaintext login before it; 995 is implicit TLS. SMTP: AUTH PLAIN and LOGIN on 25, 587 (STARTTLS) and 465 (implicit TLS). Certificate: Let's Encrypt wildcard for the test servers' domain, valid until 2026-12-02; Python's default verification accepts it. the test domain and its mail server resolve to the same address.

**Numbers**
- 21 live tests pass in about 9.5 seconds: SMTP capability, login, certificate rejection and wrong-password rejection on all three SMTP modes; POP3 login and mailbox commands over 995 and over 110 with STLS; refusal of plaintext login on 110; certificate rejection; wrong-password rejection; three send-to-self round trips (SMTP mode by SMTP mode, delivery polled over POP3, content checked, message deleted and confirmed gone).
- Offline gate: 15 tests pass; Pyrefly strict: 0 errors and 0 warnings; layer contracts: 2 kept.
- The mailbox was empty (0 messages) before and after the runs.

**Still open**
- Nothing is pushed to GitHub until the user says so (the test account password changes first). The `release` command is not run in the meantime.
- Not covered: IMAP (milestone 2), misbehaving servers, offline `aiosmtpd` SMTP tests (a later task if wanted), live tests on the user's other mailboxes.
- The live run needs the LAN/internet path to the DirectAdmin test server; wrong-password attempts default to 1 per protocol per mode and per run (configurable, 0 disables).

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
- Verified on the Linux computer: `keyring` reaches KDE's Secret Service (`ksecretd`); scrypt N=2^17 takes 0.36 s, N=2^16 0.19 s, N=2^15 0.10 s, N=2^14 0.05 s.
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
- Measured on the Linux computer (4 KB messages, encrypted, verified): 802 messages per second with fsync (1.25 ms each), 8,163 per second without; listing 300 messages takes about 2 ms.

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

**Numbers: a generated mailbox of 500,000 messages** (`pytest -m perf -s`, the Linux computer, encrypted store, 4.3 KB average message, 10 percent with attachments, 15 percent HTML; the folder with 300,000 messages is the Inbox)
- Generating and storing all messages, no fsync: 97 s. Store on disk 2.18 GB. Index on disk 0.65 GB.
- Full index rebuild from the store: **3,554 s (59 minutes)**, 500,000 indexed, 0 unreadable. About half of this ran while other tests were using the machine (the rate fell from about 22,000 to about 4,000 messages per minute as the index grew), so it is an upper bound, not a clean benchmark. D-005 accepts an hour or two as a reference.
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

## 2026-10-10 Step 2 of the autonomous work: drafts (version 0.0.16)

**What landed**
- Engine: `draft.save` and `draft.open` (`engine/commands_drafts.py`), `Mailbox.deliver`, `mail.send` can replace a draft or Outbox message and keep its stored attachments; the parser reads Bcc and `X-Chrono-Mail-*` headers, the builder writes them for drafts only (D-037).
- Interface: Save draft (button, Ctrl+S), autosave every 30 seconds, Save / Discard / Cancel on close, editing from Drafts and Outbox (double-click, Return, context menu). The account dialog says "Leave all mail on the server (default)" (D-036).
- Tests: engine (12), composer (5 new, close prompt changed), main window (2 new).

**Still open**
- Drafts of HTML messages come with HTML composing (step 10).

## 2026-10-10 Step 3 of the autonomous work: message actions (version 0.0.17)

**What landed**
- Engine: `message.archive`, `folder.mark-read`, `folder.empty` (`engine/commands_actions.py`, D-038).
- Interface: Archive (Ctrl+E; Message menu and list context menu), Mark all as read and Empty folder (Folder menu and folder context menu; Empty only enabled in Trash and Junk, asks first), drag messages from the list to a folder (`ui/foldertree.py`, `ui/dragdata.py`).
- Tests: engine (6), window (2), drag and drop (3).

**Still open**
- Drag and drop is tested through the drop events, not with a real mouse drag.

## 2026-10-10 Step 4 of the autonomous work: conversations (version 0.0.18)

**What landed**
- Index: `thread_sizes`, and the `newest_per_thread` filter. Engine: `message.thread`, `thread_size` on every row, `conversations` option of `message.search` (D-039).
- Interface: count box in the list, View > Show conversations (remembered), conversation bar above the reading pane (`ui/conversationbar.py`).
- Tests: engine (6), window (4). Measured on 200,000 generated messages: grouped first page 1.5 ms, full scan 275 ms (not the 500,000 mailbox).

**Still open**
- No subject-based threading fallback; the grouped list is sorted by date only.

## 2026-10-10 Step 5 of the autonomous work: advanced search (version 0.0.19)

**What landed**
- Index: `parse_query` (OR, NOT, minus) and the exclusion condition. Engine: date and size options of `message.search` (D-040).
- Interface: Advanced search dialog (Ctrl+Shift+F), filter chip for dates and sizes (`ui/advancedsearch.py`).
- Tests: index (3), engine (4), interface (5).

**Still open**
- Saved searches are not built.

## 2026-10-10 Step 6 of the autonomous work: settings and accounts (version 0.0.20)

**What landed**
- Config: a signature per account; `with_account` keeps an account's place. Engine: `account.edit`; the account summary tells every setting (D-041).
- Interface: Settings window (`ui/settingsdialog.py`), account editor (`ui/accountedit.py`), signatures in the composer.
- Tests: engine (6), config (4), composer (3), settings window (9).

**Still open**
- The interface language and a choice of appearance are not in the settings.

## 2026-10-10 Step 7 of the autonomous work: tray and notifications (version 0.0.21)

**What landed**
- `ui/tray.py` (icon, menu, notifications), unread count in the window title and tooltip, notification of new mail (count only), close-to-tray option, three settings in the settings window (D-042).
- Tests: tray and notification tests (10) with a pretended tray; the settings tests changed.

**Still open**
- Not tried on a real desktop with a tray (only with a pretended one in the offscreen test platform). Under XRDP the tray may not exist.

## 2026-10-10 Step 8 of the autonomous work: save as, source, print (version 0.0.22)

**What landed**
- Engine: `message.export`, `message.source`, `message.text` (`engine/commands_export.py`, D-043). Interface: Save as, Print, View source (`ui/sourcedialog.py`, `ui/printing.py`).
- Tests: engine (8), interface (8, including a real PDF produced through the print path).

**Still open**
- Printing the HTML layout of a message (only the text is printed); saving several messages at once.

## 2026-10-10 Step 9 of the autonomous work: address completion (version 0.0.23)

**What landed**
- Index schema 4 with the `addresses` table; `Index.suggest`; engine `contact.suggest` (D-044). Interface: `ui/addresscompleter.py` on To, Cc and Bcc.
- Tests: index (6), engine (6), interface (13). Measured: 100,000 messages with addresses insert in 4.4 s against 3.3 s without; a suggestion takes about 2 ms.

**Still open**
- Not measured on the 500,000-message mailbox. A real address book (contacts) is a later milestone.

## 2026-10-10 Step 10 of the autonomous work: rich text composing (version 0.0.24)

**What landed**
- `ui/richeditor.py`, rich mode in the composer, `html` in `draft.save` and `draft.open` (D-045), a setting for the default.
- Tests: engine (3), interface (11 in `test_ui_richtext.py`).

**Still open**
- No inline pictures, colours or tables when writing.

## 2026-10-10 Legal wording (version 0.0.25)

**What landed**
- Every mention of another mail program removed from the About text, README, package description and documents; a test keeps it that way (D-046).

**Still open**
- Old commits still contain the old wording (history rewrite is the user's decision).

## 2026-10-10 Version in the About dialog (version 0.0.26)

**What landed**
- `core/appinfo.py` with `program_version()`; the About dialog shows it (D-047). Tests: 4.

## 2026-10-10 Step 11, part 0: IMAP design (version 0.0.27)

**What landed**
- ARCHITECTURE.md section 8 and D-048; ROADMAP tasks 2.1 to 2.5. Documents only.

## 2026-10-10 Task 2.1: the IMAP client (version 0.0.28)

**What landed**
- `protocols/imap.py` (own client, caps on lines, literals, tokens and nesting; modified UTF-7 folder names; LOGIN and AUTHENTICATE PLAIN; TLS and STARTTLS; list, select, search, fetch, store, copy, move, expunge, append, folder create, rename, delete, subscribe).
- `tests/fakeimap.py` (stateful fake server) and 60 client tests; 5 live tests against the DirectAdmin test server (all pass, folders of their own are created and removed).

**Still open**
- IDLE, CONDSTORE (CHANGEDSINCE is supported in the call but not yet used), and the sync itself (tasks 2.2 to 2.5).

## 2026-10-10 Task 2.2: IMAP sync, server to store (version 0.0.29)

**What landed**
- `sync/imap_state.py` (append-only state), `sync/imap_sync.py` (folder plan, removals, flags, downloads, renumbered folders; D-049).
- Tests: 22 offline against the fake server (including crash repair, a dropped connection and the vanishing-folder guard), 1 live test against the DirectAdmin test server (download, flag change, removal).

**Still open**
- Nothing is sent to the server yet (task 2.3); the engine and the window do not know IMAP accounts yet (task 2.4).

## 2026-10-10 Task 2.3: IMAP sync, store to server (version 0.0.30)

**What landed**
- Flags, moves, deletions and uploads from the store to the server, found by comparison (D-050); matching of local and server messages by Message-ID; `uid_move` tells the new UID; uploaded marks in the state.
- Tests: 13 new offline sync tests (35 in all) including the guards that refuse to delete on the server, crash repair and a refused upload; 1 new live test (flags, move, upload, deletion on the real server).

**Still open**
- Creating, renaming and deleting folders on the server from here (task 2.4); folders created here that the server does not have are not created there yet.

## 2026-10-10 Task 2.4: IMAP accounts in the engine and the interface (version 0.0.31)

**What landed**
- Account kind `imap`, `mail.fetch` runs the sync, account dialog with the account type, settings list shows it, folders made here are made on the server, folders deleted here are hidden (D-051).
- Tests: 5 new sync tests (40), 10 engine tests with a fake IMAP server, 2 interface tests.

**Still open**
- Not tried by hand in the window against the real server (the live tests drive the sync itself); IDLE (task 2.5); renaming a folder is not a server rename.

## 2026-10-10 Task 2.5: IMAP IDLE (version 0.0.32)

**What landed**
- `ImapClient.idle`, `mail.wait` (`engine/commands_watch.py`), `Engine.stop_event`, a pool of its own for waits, watchers in the window (D-052).
- Tests: client (4), engine (4), window (5), live (1: a message appended by a second connection ends the wait).

**Milestone 2 is complete** (tasks 2.1 to 2.5). Still open for IMAP: CONDSTORE (a cheaper flag sync for large folders), watching folders other than the Inbox, a real server-side folder rename, and trying it by hand in the window.

## 2026-10-10 Step 12 of the autonomous work: rules (version 0.0.33)

**What landed**
- `config/rules.py` (model and file), `engine/rule_engine.py` (matching and actions), `engine/commands_rules.py` (rule.list, add, edit, remove, enable, move, run), rules applied to new Inbox mail in both kinds of fetch, new messages listed in the fetch reports (D-053).
- Interface: Tools > Rules (`ui/rulesdialog.py`, `ui/ruleedit.py`).
- Tests: file (43), engine (19), interface (10).

**Still open**
- Rules for outgoing mail, rules on other folders as mail arrives there, and "reply with a template" actions.

## 2026-10-10 Step 13 of the autonomous work: junk and unread navigation (version 0.0.34)

**What landed**
- `message.junk`, `message.not-junk`; Mark as junk / Not junk; next and previous unread message (D-054). Tests: engine (1), window (3).

## 2026-10-10 Step 14 of the autonomous work: send later and snooze (version 0.0.35)

**What landed**
- `mail.send --send-at`, `mail.scheduled`, `mail.send-due`, `mail.unschedule`, `message.snooze`, `message.snoozed`, `message.wake` (D-055); Send later… in the composer, Snooze…, a once-a-minute check in the window, a window for scheduled and snoozed messages.
- Tests: engine (11), interface (8).

**Still open**
- A scheduled message is only sent while the program is running.


## 2026-10-10 Task 0.4: GitHub Actions (version 0.0.36)

**What landed**
- `.github/workflows/ci.yml`: Pyrefly strict, the layer check and the offline tests on every push, with Qt running without a screen. Written without being able to run it here; the first run on GitHub shows whether the runner needs more libraries.

## 2026-10-10 CI made green (versions 0.0.37 to 0.0.39)

- The first run found a YAML error (a colon in a step name), a test that wrote into a path that exists only on this machine, and a crash of Qt WebEngine while the process shuts down after all tests passed; the last is avoided by ending the process right after the results when `CHRONO_MAIL_FAST_EXIT` is set (the workflow sets it).

## 2026-10-10 Message list as columns (version 0.0.41)

**What landed**
- `ui/columns.py`, `ui/columnsdialog.py`, `ui/messagewindow.py`; the list model is a table model; the list view a tree view without decoration; sortable, movable, resizable columns that are remembered; reading pane right, below or off; Open (Enter, double click) in a window of its own (D-056). Tests: 11 new.

**Still open**
- Column widths are saved per column id, not per folder; no per-folder layouts.

## 2026-10-10 Settings pages (version 0.0.42)

**What landed**
- `ui/prefs.py`, a settings window with seven pages (D-057), `apply_preferences`, remote content per sender, plain text first, zoom, mark-as-read modes, link confirmation, default account, reply quote styles, autosave interval, subject warning, font sizes, key checks and rebuild from the window, editable keyboard shortcuts. `message.compose --quote`. Tests: 16 new.

**Still open**
- Certificate exceptions, per-account check intervals, which IMAP folders are shown, folder mapping.

## 2026-10-10 Certificate exceptions (version 0.0.43)

**What landed**
- `protocols/tls.py` (pinned context, certificate inspection for POP3, IMAP and SMTP), accepted certificates per account in the settings, `certificate.inspect`, `account.trust-certificate`, `account.forget-certificates`, windows to look at and accept a certificate in the account dialog and the account editor (D-058). Tests: engine (15), interface (5), live (1: the real server's certificate passes the usual checks).

**Still open**
- A connection that fails in the main window because of a certificate only shows the error; accepting is done in Settings > Accounts > Change > Servers.

## 2026-10-10 Folder choices, roles, check interval (version 0.0.44)

**What landed**
- `commands_imap.py` (`account.folders`, `account.set-folders`), folder choices and roles in the IMAP state and the planning of folders, `check_minutes` per account with per-account timers, a Folders tab and the interval in the account editor, links to Rules and scheduled messages in the settings (D-059). Tests: sync (4), engine (8), interface (4).

## 2026-10-10 Local Folders (version 0.0.45)

**What landed**
- `storage` per account (own or local, POP3 only), the Local Folders pseudo-account, `local.enable`, `account.set-storage` (copy, verify, commit, remove; rerunnable), the choice on the setup dialog and in the account editor, reply account rule (`reply_account`), scheduled messages sent from the From account, contacts and checks counted once per storage place, Local Folders left out of check, watch and composer lists.
- 15 new tests (interrupted moves at copy, commit and remove; corrupted copy keeps the original); 1,391 offline tests, Pyrefly 0 errors.

**Still open**
- The sidebar still shows Local Folders as a plain node; account groups, collapsible accounts, unified views and the Favorites and Inboxes sections follow in 0.0.46 and later.

## 2026-10-10 Favorites (version 0.0.46)

**What landed**
- `message.combined` (eight views over all mailboxes, shared Local Folders counted once), `account` on message rows, the Favorites and Inboxes headings and the Local Folders node in the folder list, accounts that are opened only when in use, three switches in the settings, message actions that use the account of each row.
- New tests in test_engine_local.py, test_ui_models.py and test_ui_window.py.

**Still open**
- Pinning other folders, renaming and reordering favorites, account groups, search inside a combined view.

## 2026-10-10 Pinned favorites and groups (version 0.0.47)

**What landed**
- `config/sidebar.py` and `sidebar.json`, `commands_sidebar.py` (nine commands), pins and groups in the folder list with a context menu, query and account list for `message.combined`.
- 6 engine tests and 2 window tests; 1,400 offline tests, Pyrefly 0 errors.

**Still open**
- Dragging to reorder favorites (Move up and Move down exist), dragging accounts into groups.

## 2026-10-10 Drag badge (version 0.0.48)

**What landed**
- `ui/messagelistview.py`: the message list draws a small badge while dragging; test added.

## 2026-10-10 Rules for an account alone (version 0.0.49)

**What landed**
- Rules without conditions for one account, in the engine (`rule.add`, `rule.edit --no-conditions`) and in the editor; a test with two accounts that share Local Folders and a rule that files one account's mail in a folder and stops.

## 2026-10-10 Rules and scheduled messages in Settings (version 0.0.50)

**What landed**
- `ui/rulespanel.py` and `ui/scheduledpanel.py` (the former dialogs as pages), the Tools menu removed, the two buttons on the General page removed; tests changed.


## 2026-10-11 Colour scheme and style (version 0.0.51)

**What landed**
- `ui/appearance.py` (scheme and style, live), `ui/colours.py` (marks, palettes, contrast), Appearance page in Settings, bundled Lucide icons with a palette-tinted icon engine, web pages kept light, error and success colours that suit both schemes; 9 new tests.

**Still open**
- The real `windowsvista` look has to be seen on the Windows machine. A change of the system scheme while the program runs re-applies the style and palette, but the status line only follows at its next message.

## 2026-10-11 Shortcut for Mark as read (version 0.0.52)

**What landed**
- Mark as read has Ctrl+Q; Quit has Ctrl+Shift+Q; a test that no key is used twice.

## 2026-10-11 Keyboard shortcuts (version 0.0.53)

**What landed**
- The standard keys changed to the usual set (D-068); new actions: next and previous message, toggle read and flag, delete permanently, move and copy menus, go to folder, folder list on and off; keys in the message window and the composer; letter keys only in the lists; 6 new tests.

**Still open**
- Forward as attachment, resend, find in message, spell check, tags.

## 2026-10-11 Redraw after a flag change (version 0.0.54)

**What landed**
- After marking messages as read, unread or flagged, the list announced a change of the first column only, so in the table layout the other cells kept their old (bold) look until the selection moved. The change now covers every column of the row, also when a page of rows arrives; test added.

## 2026-10-11 Windows icon (version 0.0.55)

**What landed**
- Application id at start-up on Windows, `chrono-mail.ico` (7 sizes) and its use as the window icon on Windows, 3 tests.

**Still open**
- To be checked on the Windows machine: the taskbar shows the envelope. A pip-made `chrono-mail.exe` launcher keeps a generic icon of its own; a shortcut with the .ico, or an installer, is the way to a proper launcher.

## 2026-10-11 Menu colour and Refresh (version 0.0.56)

**What landed**
- Dropdown menus in the colour of the menu bar (Fusion), Refresh with its icon in the toolbar, the File menu and the tray, no Add an account in the File menu; 2 tests.

**Still open**
- Menus with "Follow the system" on Windows are drawn by the system: to be checked there.

## 2026-10-11 Calendars, tasks, contacts: design and vCard (version 0.0.57)

**What landed**
- ARCHITECTURE.md section 9, D-071, milestones 3 to 7 in ROADMAP.md; dependencies `icalendar`, `recurring-ical-events`, `tzdata`, `python-dateutil`; packages `pim` (L1) and `pimstore` (L2) in the layer check.
- `pim/vcard.py` (content lines for vCard 2.1, 3.0 and 4.0, folding, escapes, quoted-printable, inline photos) and `pim/contact.py` (the typed contact; properties we do not understand are kept); 20 tests including hostile input.
- The Kerio Connect 8.5.2 server of the user was probed without a login: TLS 1.2, a self-signed certificate, CalDAV at the Kerio test server's CalDAV address (Basic authentication). A test mailbox exists; its credentials go only into the untracked local test settings.

**Still open**
- 3.2 events and tasks (iCalendar).

## 2026-10-11 Events and tasks from iCalendar (version 0.0.58)

**What landed**
- `pim/ical.py`: typed `Event`, `EventSeries` (an event and its changed occurrences), `Task`, `Reminder`, `Attendee`; reading with the library and the Windows-style and custom time zones that servers send; writing that changes only what was edited (properties and alarms that were not touched, and anything unknown, stay as they were; VTIMEZONE is made for zone-aware times); `occurrences()` expands repeating events over a range with exceptions, moved occurrences and clock changes, with a cap against endless rules; completing and reopening tasks; tolerant reading of damaged alarms and tasks. 19 tests.

**Still open**
- 3.3 the encrypted item store and the index.

## 2026-10-11 The store and index for calendars, tasks and contacts (version 0.0.59)

**What landed**
- `core/pim.py` (collection kinds), `pimstore` (collections, encrypted items, the SQLite index with search, tags, range and due queries, reconcile, rebuild, verify, purge); crash tests at every step of put, delete and move. 24 tests, with and without encryption.

**Still open**
- 3.4 engine commands.

## 2026-10-11 Commands for calendars, tasks and contacts (version 0.0.60)

**What landed**
- `commands_pim.py` (pim.enable, collection.*, tag.*), `commands_addressbook.py` (contact.*), `commands_calendar.py` (event.*), `commands_tasklist.py` (task.*), `pimaccess.py`, `pim/times.py`, `pim/edit.py`, `config/tags.py`; `advance_task` for repeating tasks; the index keeps the tags as written and a lower-case copy; 13 new engine tests (contacts, events with repeats and exceptions, tasks, tags).
- A throw-away run of the command line used the real credential store and left a test key in it; it was removed again. Command-line runs for tests must use an in-memory key store.

**Still open**
- 3.5 the four-button shell, then the three sections.

## 2026-10-11 The four-button shell (version 0.0.61)

**What landed**
- `ui/sections.py` (the buttons, the base of a section), `ui/collectionpanel.py` (the sidebar of calendars, task lists, address books with ticks, colours and a menu), `ui/pimpages.py` (three pages), the stacks, toolbars and the switching in the main window, three new icons; 5 tests.
- The main areas of the three sections show only a line of text for now: 3.6 to 3.8 fill them.

## 2026-10-11 The contacts section (version 0.0.62)

**What landed**
- `ui/contactsview.py`, `contacteditor.py`, `contactdetail.py`, `tagchips.py`; the sort key in `contact.list`; the index takes its write lock at once (found by a test with two edits at the same time; a store test with four threads was added); 6 UI tests.

**Still open**
- Business cards and list views of the contacts; photos can be shown but not yet chosen in the editor; the History tab lists the mail but does not open it.

## 2026-10-11 The tasks section (version 0.0.63)

**What landed**
- `ui/tasksview.py`, `ui/taskeditor.py`; `add_tag` and `remove_tag` for tasks and events (`pim.edit.changed_tags`); 8 UI tests and 1 engine test.

**Still open**
- Tasks are not yet shown in the calendar (3.8); no sub-tasks view; the editor has no choice of the zone of a time.

## 2026-10-11 The calendar section (version 0.0.64)

**What landed**
- `ui/calendarlayout.py` (layout), `ui/calendarwidgets.py` (time grid, all-day header, month, agenda), `ui/calendarview.py` (the page, the mini calendar), `ui/eventeditor.py`; 8 layout tests and 7 UI tests; three painting mistakes found by looking at screenshots (a brush that stayed set filled the month cells; short blocks cut their title; the all-day editor ended a day late).

**Still open**
- Dragging events, tasks in the calendar, invitations and replies, a setting for the first weekday and the work days, an event of another time zone shown in its own zone.
- 3.9 tags for mail and reminders with notifications.

## 2026-10-11 The CalDAV and CardDAV client (version 0.0.65)

**What landed**
- `protocols/dav.py`, `tests/fakedav.py` (a fake server that can also behave awkwardly: no change report, no multiget, no ETag after a write, TLS with a self-signed certificate, read-only collections), 12 offline tests and 3 live tests against the Kerio Connect test account; the test account was added to the program as an IMAP account (D-058: its certificate approved by its fingerprint); dummy contacts, events and tasks were put on the server for the next steps.

**Still open**
- 4.2 the sync; 4.3 the account settings and the interface.
- A race found in the full runs: the three sections can each ask for the local calendars at the same moment on first use, and the first-time setup of Local Folders was not protected; it is serialised now (test with six threads).

## 2026-10-11 Synchronisation with a CalDAV and CardDAV server (version 0.0.66)

**What landed**
- `sync/davsync.py` (pull by sync token or ctag, push with If-Match, conflict copies, state file), the commands `account.set-dav`, `dav.discover`, `dav.sync`, the DAV settings in the account, the tab "Calendars and contacts" in the account dialog and the synchronisation whenever mail is checked.
- Tests: `tests/test_sync_dav.py` (13, with fake servers of every kind), `tests/test_engine_dav.py`, `tests/ui/test_ui_dav.py`, one live test against the Kerio test server (the data is pulled; a test contact is pushed and removed again).

**Still open**
- 4.4 invitations and replies (iTIP). Making calendars and address books on the server from here (MKCALENDAR). A timer of its own for the calendar sync.

## 2026-10-11 Messages that are sent again and again (version 0.0.67)

**What landed**
- `engine/recurring.py` (the schedule and its file), the repeat options of `mail.send`, the commands `mail.repeat-pause`, `-resume`, `-change`, `-cancel`; `mail.scheduled` and `mail.send-due` know repeating messages.
- The "Send later" dialog has "Send it again and again, every ..." and "Stop after"; the scheduled-messages page shows the interval and the number of sends, and pauses, resumes and stops.
- Tests: `tests/test_engine_recurring.py` (18), additions in `tests/ui/test_ui_schedule.py`.

**Still open**
- Google accounts (next, on the user's word), 4.4 invitations, Microsoft.

## 2026-10-11 Address suggestions entered twice (version 0.0.68)

**What landed**
- A suggestion chosen with the arrow keys and Enter was in the address field twice: highlighting already puts the address in the field, and Enter added it again to what was there. The completer now builds the result from what the person typed, not from the field. Regression test added.

## 2026-10-11 Accounts told apart by login, server and protocol (version 0.0.69)

**What landed**
- Adding or editing an account is refused only when another account has the same user name on the same incoming server with the same protocol (POP3 or IMAP); the e-mail address and the port do not matter. The same address on different servers, or one mailbox through POP3 and through IMAP, are separate accounts.
- Accounts are named "address - server (PROTOCOL)" where they must be told apart: the rule editor, the From box, the account list and default account in Settings. The folder tree, the Inboxes list, notifications and the calendar, task and contact panels show the plain address (or name) and add the server and protocol only when another account has the same address (or name). The engine makes the labels (`label`, `server_label`, `shown_name` in `account.list`).

**Still open**
- A warning in the account dialog when another account uses the same mailbox with a delete-after-download policy.

## 2026-10-11 The Inboxes section is removed (version 0.0.70)

**What landed**
- The Inboxes heading in the folder tree (one inbox per account, shown with two or more own-mailbox accounts) is removed completely, with its settings switch, its default and its tree item kind. It was never requested. Favorites (with All Inboxes), accounts, account groups and Local Folders are unchanged. An old `show_inboxes` value in a saved config is ignored.
- D-061 corrected. Tests adjusted.

## 2026-10-11 Stale accepted certificate in the Add account dialog (version 0.0.71)

**What landed**
- In the Add account dialog a certificate accepted for one server stayed in the list after the server address was corrected. Any entry in the list switches both servers to pinned-only checking, so the valid certificate of the new address was refused ("not the certificate you accepted") and the account could not be added. The dialog now remembers for which host, port and security each certificate was accepted and sends only those that still match the servers as they are now. Regression test added.

**Still open**
- The accepted certificates are one list per account (and per dialog) that applies to both servers, and one entry makes the usual checks stop for both. Pins per server would need a change to D-058 and the saved config; not done, to be decided.

## 2026-10-11 Accepted certificates per server (version 0.0.72)

**What landed**
- An accepted certificate is stored as host:port=fingerprint and applies only to that server. A server without one keeps the usual checks, whatever the other server of the account has (D-082, changes D-058). Used by fetch, watch, send, the connection test and calendar sync (the calendar server counts as a server of its own).
- `account.trust-certificate` now needs `host` and `port`. Entries saved before (a bare fingerprint) keep working and apply to every server of the account, as they did.
- Tests: one server's certificate does not touch the other, other port, older form, the pin format and its checks. The 0.0.71 open item is closed.

## 2026-10-11 New look of the main window, step 1 (version 0.0.73)

**What landed**
- A shared stylesheet (`ui/theme.py`) for the main window: the menus sit in one Menu button, the toolbar is flat with an orange New button and the main actions in the middle, the search field is a pill with the folder/all-folders choice inside, the folder list has a heading and rounded rows, the section buttons share the width and show the open section with a line under it. It takes its colours from the palette, so light and dark both work. The rows/columns and cards choice for the message list is unchanged.
- The stylesheet is set on the main window only: on the application it would hide the style from `appearance.py`. It is set again when the colours change.

**Still open**
- Steps 2 to 5: message list and reading pane, composer, settings and dialogs, calendar, tasks and contacts.

## 2026-10-11 Always for this sender in a message window (version 0.0.74)

**What landed**
- In a message window "Always for this sender" was not saved: the window's reader was not connected to the saving code of the main window. It now is.
- A message window received the allowed senders only after it had shown the message, so a remembered sender still got the banner. The window now gets the privacy settings first. Two regression tests.

## 2026-10-11 The folder list remembers what is open (version 0.0.75)

**What landed**
- Which headings (Favorites ...), account groups, accounts and folders with subfolders the person opened or closed is remembered, also after a restart (setting `tree_state`, a damaged value is ignored). Before, only accounts were remembered, and only until the next restart; subfolders closed at every refresh.
- Never opened or closed: headings open, accounts open (with more than three only the one with the selection), folders closed. A node the person closed stays closed even when the selected folder is inside it.

## 2026-10-11 Menu root, blue accent and section buttons (version 0.0.76)

**What landed**
- The accent is the blue of the menus (the highlight colour of the palette), not orange: the New button, the lines under the open section, hover. It follows the light and the dark scheme.
- The root of the Menu: Settings…, Accounts…, Rules…, a separator, File, Message, Folder, View, Help, a separator, Exit. Accounts… and Rules… open the Settings window on their page. Settings is no longer in the File menu. Quit is now Exit and has no keyboard shortcut.
- The four section buttons use the program's own plain icons (not the desktop's icon theme): mail, calendar, person, check mark; the open section is blue. The order is Mail, Calendar, Contacts, Tasks, with Ctrl+1 to Ctrl+4 following that order.

## 2026-10-11 New look, step 2: message list and reading pane (version 0.0.77)

**What landed**
- The column and the sort choice that said Date now say Received (the saved settings keep working: the ids did not change).
- Unread: a blue dot in the status column and in the cards (instead of the bar); read: a faint empty circle in the status column.
- The list header is flat with a line under it; the header of the reading pane and the remote content banner have the window colour with a line under them, the banner buttons are grey, and the lines "to ..." and "cc ..." are in a quieter colour. A message window gets the same look as the main window.
- The rows/columns and cards choice is unchanged.

**Still open**
- Group headers in the list (the reference groups by the sort column): the list is loaded in pages and has no group rows; needs a plan of its own.
- Steps 3 to 5: composer, settings and dialogs, calendar, tasks and contacts.

## 2026-10-11 New look, step 3: the composer (version 0.0.78)

**What landed**
- The composer: Send (blue), Send later… and Save draft on top with the From box beside them, then the fields with rounded borders and a blue outline when focused, a flat formatting toolbar and the text; the Rich text toggle moved to the bottom row. The composer window gets the same look as the main window.
- Rich text is now the default for new messages (setting `compose_html`, on in Settings > Writing; a person who switched it off keeps it off). The tests start in plain text.

**Still open**
- Steps 4 and 5: settings and dialogs, calendar, tasks and contacts. The composer has no attachment chips, no emoji and no font colour: not built.

## 2026-10-11 Lines between the column headers (version 0.0.79)

**What landed**
- The thin vertical line between the column headers of the message list is back (one property, border-right, on the header section rule). Version 0.0.77 had removed it by mistake.

## 2026-10-11 The test run no longer crashes at exit (version 0.0.80)

**What landed**
- Every run of the ui tests ended with a segmentation fault (415 crash reports since 10 October): a web view that was still waiting to be deleted when the process ended crashed it. A fixture in `tests/ui/conftest.py` now finishes the pending deletions after each test. The full suite exits cleanly and made no new crash report. Only tests changed.

**Still open**
- Not checked: whether the program itself leaves a web view waiting when it quits; a few small probes exited cleanly.
- Not tried: closing the windows in that fixture (closing a composer asks a question and the run waits for the answer).

## 2026-10-11 The Menu button in the toolbar (version 0.0.81)

**What landed**
- The Menu button is the first button of the toolbar in every section, with the blue background. The row of the menu bar is not shown any more (the menu bar still holds the menus; their actions are also given to the window so that their keys work). New is a plain toolbar button, like Refresh. Tests for each toolbar.

**Still open**
- `test_mark_all_as_read_and_empty_folder` (tests/ui/test_ui_window.py) fails now and then: after "as read" the list rows are not always loaded yet when the test reads them (a race in the test). Not changed.

## 2026-10-11 Text size of the interface (version 0.0.82)

**What landed**
- Settings > Appearance has "Text size of the interface" (8 to 48 pt) and a button "Use the system size". The first time there is no saved size, the size of the font the platform gave is saved, so the setting is never 0. The size is applied at once on OK. The list, message and writing sizes keep their own settings; the heading "Mail" and the icons have fixed sizes and do not scale.
- The Menu button shows the character U+2630 (three bars), which is drawn larger than the one used before.

**Still open**
- Whether U+2630 is large enough in the owner's font: to be seen. Option if not: draw the symbol separately.
- Tests run: appearance, theme and preferences only; the full suite was not run (owner's instruction).

## 2026-10-11 Remembered window size and position (version 0.0.83)

**What landed**
- `ui/windowstate.py`: saves and restores the size and position of a window (keys `geometry`, `message_window_geometry`, `composer_geometry`, `settings_geometry`). After restoring, the top edge of the window must be at least 100 by 20 pixels on a screen that is connected now (a screen that is gone, another resolution); if not, the window keeps its own size and position. Used by the main window (which restored without a check before), the message windows, the composers and the Settings window (saved with OK and with Cancel). All message windows share one saved geometry, all composers another.
- Found: Qt itself already moves a window that was saved off-screen onto a screen when restoring; the check is a second guard.
- Tests run: the new window state tests and the files for composer, columns (message window), preferences and window; not the full suite.

## 2026-10-12 STATUS.md rewritten, repository recreated (version 0.0.84)

**What landed**
- `STATUS.md` rewritten: current state, next task, backlog, problems, open questions; the old work queue and old counts removed. It is now updated before every push.
- The GitHub repository was recreated; the author of the first commit was changed to the owner (history rewritten, all 70 tags kept, files unchanged).
- Test count (collected, not run): 1621 offline, 48 opt-in.

## 2026-10-12 Main screen restyle step 1: toolbar look (version 0.0.85)

**What landed**
- Window title in the mail section: the open folder and its unread messages, then the program ("Inbox (3) - Chrono Mail"); folder items keep their plain name for this (`NAME_ROLE`).
- Mark has the crosshairs icon and Move to the go-parent-folder icon; Archive is in the toolbar after Mark.
- All icons are now the program's own copy of the Breeze icons (51 files, LGPL, D-087); the desktop's icon theme is not asked any more and the Lucide icons are removed. The text colour of an icon follows the palette; other colours (red Delete) stay.
- The arrow of a toolbar button with a menu sits beside its text, vertically centred, instead of in the corner.
- Tests run: `tests/ui/test_ui_theme.py`, `test_ui_sections.py`, `test_ui_tray.py`, `test_ui_icons.py`, `test_ui_appearance.py`, `tests/test_packaging.py`, `tests/test_appinfo.py` and two tests of `test_ui_window.py`; not the full suite.

**Still open**
- Running some ui test files together with others (`test_ui_window.py` by node id, `test_ui_appearance.py`) gives "fixture 'world' not found" (a test-collection quirk, not new); run alone they pass.
- Steps 2 to 8 (D-086).

## 2026-10-13 Main screen restyle step 2: Refresh, Snooze and Forward menus, Send All (version 0.0.86)

**What landed**
- Refresh is a split button: a click sends what waits in the Outbox and then checks all accounts (Send and Receive All, as the reference does); its arrow opens Send and Receive All, Receive All, Send All and one entry per account ("<account>" only).
- Send All: new command `mail.send-outbox` (`engine/commands_outbox.py`, `send_stored` in `sync/send.py`). The copy in the Outbox and in Sent now keeps the Bcc header; `without_bcc` takes it out of what goes to the server (D-088). Tests for a crash after the server accepted and after the move: one copy, never zero, never two.
- Forward is a split button with Forward as attachment: the original goes along unchanged as `<subject>.eml` (message/rfc822, not base64); the composer shows a checked box "Attach the original message (...)"; such a draft can be saved and taken up again.
- A Snooze split button (clock icon) after Archive: a click opens the time dialog; its arrow offers Tomorrow, Later this week, This weekend and Next week (each at 09:00, with the weekday) and Custom date and time. The times come from `snooze_presets` in `engine/schedule.py`: later this week is two days ahead and left out when that is Saturday or later; this weekend is the coming Saturday and left out on Saturday and Sunday.
- The Snooze button opens its menu wherever it is clicked (Custom date and time is its last entry). While no message is selected (after a fresh start) the message buttons and actions are greyed out: Reply, Reply all, Forward, Mark, Archive, Snooze, Move to, Delete and the matching menu items; Delete is on again in the other sections.
- Tests run: `tests/test_sync_send.py`, `tests/test_mime_build.py`, `tests/test_engine_outbox.py` (new), `tests/test_engine_drafts.py`, `tests/test_engine_schedule.py`, `tests/test_engine_compose.py`, the send tests of `tests/test_engine_commands.py`, `tests/ui/test_ui_schedule.py`, `tests/ui/test_ui_composer.py`, `tests/ui/test_ui_window.py`, `tests/ui/test_ui_export.py`, `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_shortcuts.py` (the known flaky test failed once), `tests/ui/test_ui_sections.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_tray.py`, `tests/test_appinfo.py`; not the full suite.

**Still open**
- Messages already in the Outbox from before 0.0.86 have no Bcc header. "Forward as Original" and "Resend" of the reference are not built. The tray menu's Refresh still only receives.

## 2026-10-13 Main screen restyle step 3: unread counts and folder icons (version 0.0.87)

**What landed**
- The folder list shows the name only, never bold; the number of unread messages is drawn in bold at the right edge (8 px from it) by `FolderItemDelegate` (`ui/foldertree.py`), and not at all when nothing is unread. A long name is shortened (…) before it reaches the number. The count is kept in the new `COUNT_ROLE` (`ui/models.py`); `NAME_ROLE` and `UNREAD_ROLE` are unchanged.
- Icons: other folders use `folder-open-symbolic`, the Local Folders root uses `user-home-symbolic` (both copied from breeze-dark `places/22`; the owner's choice). All other icons are unchanged.
- Side effect: Rename… of a pinned favorite now offers the plain name (it offered "Inbox  (5)").
- Tests run: `tests/ui/test_ui_models.py` (two new tests: the count at the right edge and no count without unread; the two new icons), `tests/ui/test_ui_window.py` (alone), `tests/ui/test_ui_icons.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- Step 4 of the restyle (selection colours and separator lines), after the owner's go-ahead.

## 2026-10-13 Main screen restyle step 4: selection colours, separator lines, Sorted by (version 0.0.88)

**What landed**
- The selection in the folder list and the message list is blue only while that list has the focus, grey otherwise (`#d6d6d6` light, `#4a4a4a` dark, `selection_grey` in `ui/colours.py`), with text, icon and unread number in the colour of text. The lists set a `focused` property on focus in and out (`show_focus` in `ui/theme.py`); a context menu (popup focus) leaves the selection blue. The card layout (`MessageDelegate`) paints the same grey, with the unread dot staying blue.
- 1 px black lines between the panes (the two splitters, `mainSplitter` and `contentSplitter`, replace the dotted grip) and between and under the column headers (`SEPARATOR` = `#000000`, both schemes).
- "Sorted by" is bold in the colour of text (`sortButton`).
- Tests run: `tests/ui/test_ui_theme.py` (two new tests), `tests/ui/test_ui_models.py` (one new test), `tests/ui/test_ui_window.py` (alone: one test failed once and passed on the second run; the failing test was not recorded, the known flaky tests are in STATUS); Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- Step 5 (message list columns and row height, together with the column-fit item), after the owner's go-ahead.

## 2026-10-13 Main screen restyle step 5: message list columns, row height, Contact and Delete columns, column fit (version 0.1.89)

**What landed**
- The owner moved the minor version: 0.1.89.
- Column fit (`ui/columnfit.py`, pure arithmetic): the columns always fill exactly the width of the list, never a horizontal scrollbar. Text columns are saved as shares of the width left after the icon columns, icon columns in pixels (settings key `column_layout`; the old `column_widths` pixels are taken over once). A line next to an icon column changes that icon column (between two icon columns the one farther from the text columns), and all text columns give or take in proportion while other icon columns keep their width; a line between two text columns changes the left one and the text columns to its right give or take; a drag stops at 50 px (text) or 24 px (icon). A resize changes the text columns in proportion. The last column has no drag line at its right edge (fixed resize mode).
- Icon columns are 24 px and never narrower; the column headers have 4 px padding left and right (was 8) so the symbols fit.
- Fixed row heights in the column layout (`ColumnDelegate` in `ui/delegate.py`): compact 22, normal 29, roomy 36 px (was text height plus padding).
- New Contact column: the `user` icon when the sender (the recipient in Sent, Drafts and Outbox) is a contact; a click opens the Contacts section with that contact selected; no icon, no action. New command `contact.addresses` (all addresses of all contacts, lower case), loaded at start, at every Refresh and when going back from Contacts to Mail.
- The headers of Contact and Delete are grey icons (`grey_icon` in `ui/icons.py`, every colour grey, also the red of the bin), drawn in the middle of the column by `ListHeader` (`ui/messagelistview.py`; Qt puts a header icon at the left).
- `ListHeader` drags column lines itself: it keeps the widths and the pointer position of the press and reports the whole distance, and the window works out all widths from the start of the drag. Qt reports the left column at its width at the press plus the whole distance, which made the line run ahead of the pointer (twice, four times) when the right-hand column was the one that changed, and never below its smallest width.
- New Delete column: a bin on the row under the pointer; a click deletes that one message (as Delete) without changing the selection.
- Contact and Delete are in the default columns, at the right; they are added once to a saved set of columns (setting `columns_contact_and_delete_added`).
- Tests run: `tests/ui/test_ui_columnfit.py` (new, 9), `tests/ui/test_ui_columns.py` (7 new, one drags a line with the mouse pixel by pixel), `tests/ui/test_ui_icons.py`, `tests/test_engine_pim.py` (1 new), `tests/ui/test_ui_models.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_contacts.py`, `tests/ui/test_ui_window.py` (alone), `tests/test_appinfo.py`, `tests/test_legal_names.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- On a grey (unfocused) selection the unread dot of the column layout is drawn in the text colour instead of blue (since 0.0.88).
- New contacts that arrive by the calendar and contacts sync show their icon only after the next Refresh.

## 2026-10-13 Main screen restyle step 6: Filter button with a menu (version 0.1.90)

**What landed**
- The three toggle buttons Unread, Flagged and Attachments above the list are replaced by a Filter button: the funnel `view-filter`, the word "Filter" and a ▾. A click on either opens the menu: "Filter by" (greyed out), Flagged (`flag`), Unread (`mail-unread`), With attachments (`mail-attachment`, newly copied from breeze-dark `actions/22`). Several can be on at once, as before.
- While at least one filter is on, the funnel, "Filter" and the ▾ are blue and "Filter" is bold (`_show_filter_state`, the button is checked; the icon is drawn blue in the checked state like the section buttons). The ▾ is part of the button, placed as on the toolbar buttons (Snooze, Move to).
- The entries keep the names `filter_unread`, `filter_flagged`, `filter_attachments`; the filter logic is unchanged.
- Qt's own menu arrow is hidden on the Sorted by button; it showed there as a second small arrow under its ▾.
- Tests run: `tests/ui/test_ui_window.py` (alone, one new test; after the last change one test failed once and passed on the second run, the failing test was not recorded), `tests/ui/test_ui_icons.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- Step 7 (search help), after the owner's go-ahead. "Select" next to the Filter button later.

## 2026-10-13 Filter menu: Clear filter and bold filters (version 0.1.91)

**What landed**
- The filters that are on are bold in the Filter menu (with an icon on an entry, Qt showed no tick, so there was no way to see which were on).
- "Clear filter" at the bottom of the menu, below a line: greyed out while no filter is on; a click turns all three off and the list reloads once. It does not remove the advanced filters.
- 0.1.90 was not pushed; it goes out together with this version.
- Tests run: `tests/ui/test_ui_window.py` (alone, the filter test extended); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Main screen restyle step 7: search help (version 0.1.92)

**What landed**
- The search box hint is "Search (type '?' for help)".
- When the search box holds only "?", a help list opens under it (`ui/searchhelp.py`): per line an icon (32 px), the name, and a grey explanation. Lines: words (magnifier `edit-find`), subject, body, from, to, "exact phrase", -word (`dialog-question`, newly copied from breeze-dark `status/22`). The first line is highlighted.
- Arrows move, Enter or a click picks a line, Escape or a click elsewhere closes it; any other key closes it and goes to the search box. Picking puts the search word in place of the "?" (`from:`, `""` with the cursor between the quotes, `-`; words leaves the box empty) without starting a search.
- OR still works but has no line (the owner's choice).
- Tests run: `tests/ui/test_ui_searchhelp.py` (new, 5), `tests/ui/test_ui_window.py` (alone), `tests/ui/test_ui_icons.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- Step 8 (group headers), after the owner's go-ahead.

## 2026-10-13 Search field looks and search history (version 0.1.93)

**What landed**
- The search field is its own widget (`ui/searchfield.py`, object name `searchPill` kept) with three looks, set by the property `look` for the stylesheet: not in use (grey border, magnifying glass at the start, "Search (type '?' for help)" in italics); clicked in (blue border, hint "Start search", magnifying glass at the end); search active after Enter until it is cleared (orange border `#f0a030`, red ✕ `edit-delete-remove` at the end, newly copied from breeze-dark `actions/22`; a click clears the search and shows the whole folder). A thin line stands before "This folder / All folders", which stays. Qt's own clear button is gone.
- The field counts as clicked in while the search text or one of its own lists (history, help) has the keyboard (`focusChanged`); the lists take the keyboard when they open, which first made the field fall back to "not in use".
- Search history (`ui/searchhistory.py`): the last 10 searches, newest first, each once, one list for all accounts, kept in the settings (`search_history`). A click in the field shows it; typing narrows it to the searches that contain the text and closes it when none do; a click on a line runs that search, its grey ✕ removes it. "?" shows the help instead.
- Tests run: `tests/ui/test_ui_searchfield.py` (new, 7), `tests/ui/test_ui_searchhelp.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_advancedsearch.py`, `tests/ui/test_ui_window.py` (alone), `tests/ui/test_ui_icons.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

**Still open**
- The field has square corners (was so before; under Problems in STATUS).
- Step 8 (group headers), after the owner's go-ahead.

## 2026-10-13 Main screen restyle step 8: group headers, sorting by To, searches follow the sort (version 0.2.94)

**What landed**
- The owner moved the minor version: 0.2.94.
- Index: sort field `to` (recipients, case ignored as the other texts); `search` takes a sort and a direction (newest first stays the default); `groups` counts the groups of a folder or a search in list order with one GROUP BY query: date groups by local midnights (Today, Yesterday, Earlier this week from Monday, Last week, Earlier this month, Last month, Older, No date), size groups (under 10 KB, 100 KB, 1 MB, 10 MB, above), or one group per sender, recipients or subject (case ignored).
- Engine: `message.list`, `message.search` and `message.combined` accept every sort and oldest first; new `message.groups` and `message.combined-groups` (combined views merge the groups of all mailboxes); `date_bounds` makes the date boundaries in local time, each day at its own midnight.
- List model: a group layer. The groups are asked for first, then the messages (still 100 at a time); headers sit before their group's messages; positions in the list and among the messages are told apart (`message_position`, `display_row`, `row_at` gives None on a header); a header is not enabled, so the arrow keys pass it and it cannot be selected; folding removes and inserts the rows of one group; folded groups are kept per folder (or combined view) and sort order until the program closes. A search adds rows (and headers) at the end as it is scrolled.
- Window: View > Show groups (setting `show_groups`, on by default); "To" in the Sorted by menu and on the To column header; the headers are spanned over all columns (again after every reset of the list); a click on a header folds or unfolds it; next / previous message passes headers. Headers are drawn as a bar with a chevron and the name in bold, in the colours of text, in both layouts.
- The search field has round ends: rounding 15 px (16 px was more than half its 30 px height, which Qt drew square).
- The tests of the window and the list start with Show groups off (`tests/ui/world.py`, the owner's permission); the new tests switch it on.
- Tests run: `tests/test_index_index.py` (5 new), `tests/test_engine_search.py` (3 new), `tests/test_engine_local.py` (1 new), `tests/test_engine_commands.py`, `tests/test_cli.py`, `tests/test_engine_conversations.py`, `tests/ui/test_ui_models.py` (4 new), `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_searchfield.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_window.py` (alone, 2 new); Pyrefly 0 diagnostics, layer check kept; not the full suite and not the 500,000-message test.

**Still open**
- In the card layout the headers are as tall as a card (uniform row heights).
- Switching to the card layout while the program runs leaves its column 24 px wide (also in 0.1.93).
- The speed of the group query on 500,000 messages is not measured.

## 2026-10-13 Group headers darker in the dark scheme (version 0.2.95)

**What landed**
- The bar of a group header has a fixed colour per scheme (`group_header_colour` in `ui/colours.py`): #2a2a2a dark (was #343434, a veil of the text colour over the list), #e6e6e6 light (unchanged).
- 0.2.94 was not pushed; it goes out together with this version.
- Tests run: `tests/ui/test_ui_models.py`, `tests/ui/test_ui_theme.py`, a dark screenshot (header pixel #2a2a2a); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The card layout fills the list again (version 0.2.96)

**What landed**
- Since 0.1.89 the card layout's one column kept the width of the first column of the column layout (24 px or so): the column fit stopped Qt from stretching the last column and did nothing itself in the card layout. `_fit_columns` now gives the card column the width of the list (fixed, no drag line), also when switching to the card layout and at every change of width.
- In the card layout a group header is 36 px high (was 74, as high as a card): the card layout no longer has one row height (the column layout keeps it). Measured with 500,000 rows in one list and the real card delegate: 2.71 s to show instead of 0.51 s, at every rebuild of the list; scrolling stays smooth (the owner's choice; one row at a time would have kept 0.5 s). The model keeps the rows of the headers in a list for quick lookups.
- View > Message list: Columns or Cards (ticked), above Reading pane; switches at once and is kept in the settings (`list_layout`), the same choice as in the Message list window.
- Tests run: `tests/ui/test_ui_window.py` (alone, one new test), `tests/ui/test_ui_models.py` (one new), `tests/ui/test_ui_columns.py`, a card screenshot; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Message list… is no longer in the View menu; it stays at the bottom of the menu of the column headers. Settings > Message list shows the columns right on the page (ColumnPicker in `ui/columnsdialog.py`: tick boxes, Move up, Move down, Standard columns; greyed out while Layout is Cards); the Columns line and the Choose the columns… button are gone. The Message list window uses the same part.
- Found, not fixed: `test_the_look_of_the_list_follows_the_settings` (tests/ui/test_ui_preferences.py) fails since 0.1.89 (checked on 0.0.87: passes; 0.1.89 and 0.2.95: fails); it expects the old row padding.
- Tests run as well: `tests/ui/test_ui_preferences.py` (the columns test rewritten), `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_window.py` (alone; View has no Message list…, the header menu has it), a screenshot of the Settings page.

## 2026-10-13 Row height in the Message list window (version 0.2.97)

**What landed**
- The Message list window (menu of the column headers) has Row height (Compact, Normal, Roomy) under Dates, the same setting (`list_density`) as Settings > Message list; OK saves it and the list takes it at once. The three choices live once, in `ui/columnsdialog.py`.
- Tests run: `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_preferences.py`, `tests/ui/test_ui_window.py` (alone); Pyrefly 0 diagnostics, layer check kept; not the full suite. The only failure is the known old one (`test_the_look_of_the_list_follows_the_settings`, padding since 0.1.89).
- The search field's ends are fully round at every text size: its corners are rounded by half its height, set by the field at every change of height (was a fixed 15 px in `ui/theme.py`, round only at 30 px high; at a larger interface text size the field is about 40 px). Tests run: `tests/ui/test_ui_searchfield.py` (one new), screenshots at 40 px (grey and blue looks); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The old failing preferences test is fixed (version 0.2.98)

**What landed**
- `test_the_look_of_the_list_follows_the_settings` checked for the row padding of before 0.1.89 ("padding-top: 6px"); since the fixed row heights of D-091 it checks that Roomy gives rows of 36 px. No program code changed.
- Tests run: `tests/ui/test_ui_preferences.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The place in the list after a delete; F2 renames a folder (version 0.2.99)

**What landed**
- Delete, Delete permanently and the bin of the Delete column remember the scroll position and the message to select before they run; when the list is back it scrolls there and selects that message: the selected one when it was not deleted, else the one right after the deleted ones, else the one just before them. A message whose page is not loaded yet is fetched first; one in a folded group is not selected. `ListModel.message_count`.
- Rename folder… has the key F2, active only while the folder list has the focus (changeable in Settings > Keyboard).
- The owner: the unread dot on a grey selected row stays as it is (taken off the open items).
- Tests run: `tests/ui/test_ui_window.py` (alone, four new), `tests/ui/test_ui_models.py`, `tests/ui/test_ui_shortcuts.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Move (button, menu, M, Ctrl+Shift+V, dragging to a folder), Archive, Junk, Not junk and Snooze keep the place the same way (the owner's instruction). Tests run again: `tests/ui/test_ui_window.py` (alone, 59 passed, five more); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The Snoozed until column (version 0.2.100)

**What landed**
- Engine: rows carry `snoozed_until` in the Snoozed folder; sort order `snoozed` for message.list, message.search and message.groups (only in the Snoozed folder, elsewhere it is date), groups by `snooze_group` (Today, Tomorrow, This week, Next week, Later, on the engine clock). SNOOZED_FOLDER and SNOOZED_FILE moved to `engine/schedule.py`.
- Window: the column Snoozed until (`ui/columns.py`, added once after Received, setting `columns_snoozed_added`), only in the Snoozed folder; kept in place when columns are chosen in Settings or the Message list window (`keep_snoozed_column`); Sorted by menu entry in Snoozed only; cards show "Until …" (`coming_date` in `ui/formatting.py`; weekdays follow the system language).
- Tests run: `tests/test_engine_schedule.py` (two new), `tests/ui/test_ui_schedule.py` (four new), `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_models.py`, `tests/ui/test_ui_preferences.py` (one expectation: Snoozed until keeps its place), `tests/ui/test_ui_window.py` (alone: 58 passed; the known flaky `test_mark_all_as_read_and_empty_folder` failed once, not rerun), screenshots of both layouts; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Seen: in a narrow list the header "Snoozed until" is cut off at its default share.

## 2026-10-13 Cancel snooze; a folder is never made twice (version 0.2.101)

**What landed**
- Cancel snooze in the Snoozed folder (Snooze button menu and right-click menu); the list keeps its place.
- Bug found while testing it: three snoozes at once with no Snoozed folder yet made three Snoozed folders on disk (verified: three `folder.json` with path Snoozed, one message each); the store used the first and set the others aside as damaged, the wake forgot their records without moving them. Fixed in `Store.create_folder` (refresh under a per-store lock) and `ensure_folder`, used by snooze, wake, archive, rules, IMAP sync and Local Folders; `SNOOZED_LOCK` for `snoozed.json`; `merge_duplicate_folders` at store open repairs existing stores; a woken message without a record goes to the Inbox.
- Tests run: `tests/test_store_interruption.py` (five new: six folders made at once, the merge, crashes at three points), `tests/test_store_store.py`, `tests/test_engine_schedule.py` (one new), `tests/test_engine_actions.py`, `tests/test_engine_rules.py`, `tests/test_engine_local.py`, `tests/test_engine_imap.py`, `tests/test_engine_imap_folders.py`, `tests/test_sync_imap.py`, `tests/ui/test_ui_schedule.py` (one new: three snoozes at once give one folder, then Cancel snooze), `tests/ui/test_ui_window.py` (alone), `tests/ui/test_ui_shortcuts.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Unarchive; clearer Junk items (version 0.2.102)

**What landed**
- `message.archive` appends the folder of origin to `archived.log`; new command `message.unarchive`. In the Archive folder Archive becomes Unarchive (toolbar, A, menus). Icon `archive-extract` copied from breeze-dark.
- "Move to Junk / back to Inbox" is now "Move to Junk"; "Not junk" is "Not junk (move to Inbox)".
- Tests run: `tests/test_engine_actions.py` (two new), `tests/ui/test_ui_window.py` (alone: one new, the old archive test now expects Unarchive in the Archive folder; the known flaky `test_mark_all_as_read_and_empty_folder` failed once, not rerun), `tests/ui/test_ui_shortcuts.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Drag and drop: drop bar, own cursors; selection from the icon (version 0.2.103)

**What landed**
- `FolderTree.drawBranches` paints the indent in the list colour (the selection and hover colour came from Qt filling the indent; a stylesheet `show-decoration-selected: 0` did not help, checked by pixels); `drawRow` draws the drop bar across the whole row; `drop_row` follows `dragMoveEvent`, cleared on leave and drop.
- `ui/dragcursors.py` (new): `move_cursor`, `no_drop_cursor`, drawn on a 24 grid at the cursor size; `MessageListView.make_drag` sets them for MoveAction and IgnoreAction. The owner's desktop: X11, cursor size 42. Not seen on a real screen here.
- Tests run: `tests/ui/test_ui_window.py` (alone, three new: indent colour by pixel, drop bar by pixel and its clearing, drag cursors set), `tests/ui/test_ui_dragdrop.py`, `tests/ui/test_ui_icons.py`, `tests/ui/test_ui_models.py`, `tests/ui/test_ui_theme.py`, screenshots; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The desktop's own drag cursors (version 0.2.104)

**What landed**
- The owner saw the drawn cursors black and tiny. The theme (Breeze_Light, size 42) has dnd-copy (arrow with a green plus) and no-drop (arrow with a red sign); dnd-move, move and closedhand are the hand. The drag is now offered as the copy kind and the folder list takes it as such and moves the messages; `ui/dragcursors.py` is removed.
- Not known: why the hand looked huge on the owner's screen (at size 42 it is normal in the theme file); the owner checks the new cursors.
- Tests run: `tests/ui/test_ui_dragdrop.py` (the drag is of the copy kind; drops still move), `tests/ui/test_ui_window.py` (alone; the cursor test removed); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The drag badge clears the cursor (version 0.2.105)

**What landed**
- `drag_badge` leaves the cursor's square free (`cursor_size()`: XCURSOR_SIZE, else 32) instead of 22 pixels; the owner found the cursor over the first digit at size 42.
- Tests run: `tests/ui/test_ui_dragdrop.py` (the badge test checks the free square at 42 and the 32 fallback); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 The interface size slider, step 1a (version 0.3.106)

**What landed**
- New `ui/scale.py`: the factor, `px`, `scale_css`, slider positions, `saved_factor` (converts `ui_font_size`), `ScaledStyle` (icon and indicator pixel metrics at the factor). `appearance.apply` takes an optional factor to try out. `theme.stylesheet()` is scaled; section buttons, search field, search help and history, reader circle, folder list width and the Settings page list follow `px`.
- Settings > Appearance > Sizes: the interface slider (Small, Large, Standard) and the three text sizes moved from Message list, Reading and Writing. Live while dragging; Cancel restores.
- Screenshots at 0.75, 1.00 and 1.50 and of the Appearance page checked. Seen: at 1.50 a list at the system size has its text cut (row heights fixed until step 2); the toolbar no longer fits 1300 px at 1.50 (its overflow arrow appears). The Menu button colour in tests is the same before and after.
- Tests run: `tests/ui/test_ui_appearance.py` (two old text size tests replaced by four), `tests/ui/test_ui_preferences.py`, `tests/ui/test_ui_window.py` (alone), `tests/ui/test_ui_sections.py`, `tests/ui/test_ui_searchhelp.py`, `tests/ui/test_ui_searchfield.py`, `tests/ui/test_ui_reader.py`, `tests/ui/test_ui_theme.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- The owner found the size jumping while the handle was dragged (the Settings window grew with it and moved the slider). The slider no longer tracks a drag: the size is applied when the handle is let go; keys and clicks apply each step at once. One more test (keys, drag).
- The owner found the slider squeezed to its handle at larger sizes (the Settings window kept its size). The Settings window now grows and shrinks with the factor (from its size when the slider first moved, so nothing creeps), its smallest size (760 x 520 at 1.00) follows, never beyond the screen; the slider keeps a smallest width of 200 px at 1.00; Cancel gives the window its size back before it is remembered. One more test (shrink and grow back, Large within the screen, Cancel). On the 800 px test screen the window is capped at 1.15 and the slider touches "Large"; on a real screen there is room.
- "Standard" is "Reset"; the slider draws ticks below its track at 0.75, 1.25, 1.50, 1.75, 2.00 and a taller one at 1.00 (`SizeSlider`), checked by a picture and a test.

## 2026-10-13 Ticks above the size slider (version 0.3.107)

- The ticks are drawn first, above the track and touching it (they end on the top of the track's area); the track and the handle are drawn over them. The slider is only as high as the tall tick needs. Tests run: `tests/ui/test_ui_appearance.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- The owner found "Large" over the end of the slider at Small: the window shrank to 0.75 of its size (570 px) while the Appearance page needed 438 px of the 400 it got. The window now never shrinks below what the page that is shown needs. One more test.

## 2026-10-13 Size sliders for messages and writing (version 0.3.108)

**What landed**
- Settings > Appearance > Sizes: "Text of messages" and "Text when writing" are size sliders like the interface one (one part, `_size_row`; Reset and ticks). `scale.saved_reading` and `scale.saved_writing` convert `reader_zoom` and `compose_font_size`; the settings are `reading_scale` and `writing_scale`. `MainWindow.try_reading_scale` and `try_writing_scale` show them at once in the reading pane, message windows and open composers (`ComposeWindow.set_text_size`, points with a fraction); Cancel puts them back.
- The first labels ("Messages (text of a message)", "Writing (text of the composer)") were cut off in a picture; they are "Text of messages" and "Text when writing".
- Tests run: `tests/ui/test_ui_appearance.py` (two new), `tests/ui/test_ui_preferences.py` (two tests use the sliders), `tests/ui/test_ui_composer.py`, `tests/ui/test_ui_reader.py`, `tests/ui/test_ui_schedule.py`, `tests/ui/test_ui_window.py` (alone), pictures of the Sizes section and of a message at 0.75 and 2.00; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- The owner: one size for reading and writing. The Writing slider is gone; the composer takes the system's size × `reading_scale`; `saved_writing` and `writing_scale` removed.
- The composer's way of writing is a drop-down button at the bottom left (`rich_action`, `plain_action`; D-105). Found while checking: the owner's settings file has `compose_html=false` saved, so new messages start in plain text; the program's default is rich text (D-085) but a saved value wins. Asked the owner whether to change it.
- Tests run again: `tests/ui/test_ui_appearance.py`, `tests/ui/test_ui_preferences.py`, `tests/ui/test_ui_richtext.py` (the button is switched through its menu), `tests/ui/test_ui_composer.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_window.py` (alone: the known flaky `test_mark_all_as_read_and_empty_folder` failed once, not rerun), a picture of the composer; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Settings > Writing: "New messages are written in" Rich text or Plain text (a list instead of the tick box; the owner chooses Rich text there). A blank line (18 px at 1.00) above Sizes. Tests run: `tests/ui/test_ui_preferences.py` (two new), `tests/ui/test_ui_appearance.py` (one new), `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_richtext.py`; Pyrefly 0 diagnostics, layer check kept.

## 2026-10-13 The message list size slider (step 2, version 0.3.108)

**What landed**
- `scale.lpx`, `saved_list`, `list_scale`; the delegates, the group header bar, the column fit (`columnfit.py`: icon widths at 1.00, minimums at the factor), the header icons and the header's smallest section follow the list factor. `MainWindow.try_list_scale`; Settings > Appearance > Sizes > "Message list" slider.
- Found in pictures and fixed: the column header text kept its own size (fixed by a font-size rule on the header); at 0.75 the columns were 30 px too wide (the header's smallest section stayed 24 px).
- A mistake: a test command named a file that does not exist, so pytest ran the whole suite once (186 s): 3 failed, 1738 passed; the names of the 3 were not kept. Afterwards the related files were run: `tests/ui/test_ui_appearance.py` (two new), `tests/ui/test_ui_preferences.py`, `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_columnfit.py`, `tests/ui/test_ui_models.py`, `tests/ui/test_ui_dragdrop.py`, `tests/ui/test_ui_theme.py`, `tests/ui/test_ui_schedule.py`: all passed; `tests/ui/test_ui_window.py` alone: once 1 failed (name not kept), on a second run all passed. Pyrefly 0 diagnostics, layer check kept.
- The owner found the list size falling back when the interface slider moved: trying out the interface size redrew the list at its saved size, not the one being tried. It keeps the size it shows now. One more test (the owner's three steps, and the size of messages).

## 2026-10-13 Every column sorts except Delete (version 0.3.109)

**What landed**
- The owner's bug: a click on an icon column showed the sort arrow but did not sort. Cause: those columns had no sort order (D-056), the click was ignored, and Qt moved the arrow by itself.
- New sort orders unread, flagged, attachment, replied, contact, folder, conversation and preview in the index (`SortField`, `YES_NO_SORTS`, `_order`: newest first within the same value), the engine (`message.list`, `message.search`, `message.groups`, `message.combined`, `message.combined-groups`; contact sorted in memory with `from_contacts`), the columns, the Sorted by menu and the arrow. No group headers for the icon columns; groups per folder, per number of messages in the conversation and per preview. A click on Delete puts the arrow back (D-106).
- Index schema 5 with five new indexes. Found while testing: the unread count took the new Unread index instead of the partial index; the Unread index is now on `(flags & 1)` so the count seeks it (folder and read bit). The test of the partial index accepts either index; the partial index is now unused (not removed: not in the changelist).
- Tests run: `tests/test_index_index.py` (three new tests, two changed), `tests/test_engine_search.py` (one new), `tests/test_engine_local.py` (combined views in the new orders), `tests/ui/test_ui_columns.py` (one new, with a real click on Delete; checked that it fails without the fix), `tests/ui/test_ui_models.py`, `tests/ui/test_ui_schedule.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Not measured: the speed of the Contact and Messages in conversation sorts with 500,000 messages.

## 2026-10-13 Index changes wait for each other; deleting a message without a file (version 0.3.110)

**What landed**
- The owner's bug: 17 messages in the Trash of the IMAP test account could not be deleted ("Could not delete: Message not found"). Found by comparing the owner's index with the store (read only, with the owner's permission): the account has no message files at all, the index 17 rows. Cause: parallel deletes; the index update of some failed with "database is locked" (deferred BEGIN, no waiting) after the file was removed, and the failure was ignored (D-107).
- `Index._transaction` uses BEGIN IMMEDIATE; `MessageNotFoundError` in the store; `Mailbox.delete` removes the index line of a message whose file is gone and reports "deleted".
- Tests run: `tests/test_index_index.py` (one new: 60 removals at once; checked that it fails with a deferred BEGIN), `tests/test_engine_commands.py` (one new: delete to Trash and for good of messages whose files are gone), `tests/test_store_store.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Seen, not changed: the IMAP test account now has no stored messages, so the safety rule blocks every deletion on its server ("most messages are missing from the store"); about 60 deleted messages stay on the server.

## 2026-10-13 Maintenance once per place where mail is kept (version 0.3.111)

**What landed**
- The owner's rebuild showed three red errors. Cause: one rebuild per account, all at once, while three POP3 accounts share the Local Folders index (D-108). The owner's indexes were checked read-only afterwards: both match their stores, the shared one passes SQLite's quick check; the 17 messages without a file are gone.
- Settings > Security and storage: Maintenance skips accounts whose mail is in Local Folders; the report is selectable. `index.rebuild` locks per index file.
- Tests run: `tests/test_engine_commands.py` (one new: three rebuilds of the shared index at once; checked that it fails without the lock), `tests/test_index_rebuild.py`, `tests/ui/test_ui_preferences.py` (one new: one line for Local Folders, selectable report); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Messages whose file is gone: warning and Repair (version 0.3.112)

**What landed**
- `Mailbox.delete` answers "gone" for a message whose file is gone; `Mailbox.forget`; `message.junk` removes such a message from the index (`JunkResult.gone`). Other actions unchanged in the engine (D-109).
- The window: one warning per action (window and red line) for every "Message not found" error and every "gone" answer (`MISSING_WARNING`, `_missing_timer`). Settings: Repair the search index.
- Tests run: `tests/test_engine_commands.py` (the 0.3.110 test now expects "gone" and covers Move to Junk), `tests/test_engine_actions.py`, `tests/ui/test_ui_preferences.py` (two new: Repair removes a message without a file; one warning for a delete of two such messages, one for a flag, the flagged message stays), `tests/ui/test_ui_window.py` (alone); Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Seen, not changed: in the window, a failed delete or move to Junk does not count down, so the list is not refreshed after an error.

## 2026-10-13 The page list of Settings is 2 points larger (version 0.3.113)

- `SettingsDialog._size_sidebar_text`: (desktop size + 2) × interface factor, also while the slider is tried out (D-110).
- Tests run: `tests/ui/test_ui_appearance.py` (one new), `tests/ui/test_ui_preferences.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 IMAP: messages gone on both sides are forgotten (version 0.3.114)

- `_remove_gone` unmaps messages that are neither on the server nor in the store before the safety rule counts (D-111).
- Tests run: `tests/test_sync_imap.py` (one new), `tests/test_engine_imap.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- For the owner's test account: empty the Trash on the server once (webmail); the next check forgets the messages and the red line stops.

## 2026-10-13 The rows of the Settings page list are higher (version 0.3.115)

- `TallerRows` delegate; `_size_sidebar_text` sets its extra height with the font, also while the slider is tried out (D-112).
- Tests run: `tests/ui/test_ui_appearance.py` (the page list test checks the row height at 1.0 and 1.5), `tests/ui/test_ui_preferences.py`; Pyrefly 0 diagnostics; not the full suite.

## 2026-10-13 The list is shown again after a failed message (version 0.3.116)

- `MainWindow._answers` (done and failed handlers for an action on several messages) and `_after_change(keep_status=...)`; used by Delete, Delete for good, Move to Junk, Move (menu and dragging), Copy; Archive counts its failures the same way (D-113).
- Tests run: `tests/ui/test_ui_preferences.py` (one new: a delete with one failing message and a move with a missing file; checked that it fails without the change), `tests/ui/test_ui_dragdrop.py`, `tests/ui/test_ui_window.py` (alone); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Small items (version 0.4.117)

- AGENTS.md sections 4.5 and 7: related tests, never the full suite. `act_open_menu` (Alt+M) and `MainWindow.open_menu`; `_load_contact_addresses` after `dav.sync`; schema 6 without `messages_unread` (D-114).
- Tests run: `tests/test_index_index.py`, `tests/test_index_rebuild.py`, `tests/ui/test_ui_shortcuts.py` (one new; the key table has Alt+M), `tests/ui/test_ui_dav.py` (one new; checked that it fails without the change), `tests/ui/test_ui_preferences.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.
- `tests/ui/test_ui_shortcuts.py` was run several times while looking for a problem: once the test process crashed (no test name kept) and once one test failed (name not kept; the file holds the known flaky `test_marking_as_read_redraws_every_cell_of_the_selected_rows`); the other runs passed. Not investigated further.

## 2026-10-13 One rebuild at a time when accounts are opened (version 0.4.118)

- The owner's start after 0.4.117: "Rebuilding the index", then the program quit once; another start showed "Folders: Unexpected error: FileNotFoundError ... index.sqlite.new". Cause: parallel automatic rebuilds of the shared Local Folders index (D-115). The owner's indexes were checked read-only afterwards: both format 6, complete, quick check ok.
- `session.index_lock` used by `open_index` and `index.rebuild`.
- Tests run: `tests/test_engine_commands.py` (one new: six commands open an old index at once; checked that it fails without the change), `tests/test_engine_local.py`; Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 A folder held back by a safety rule is a question (version 0.4.119)

- `ImapSyncOptions.confirmed`, `FolderReport.blocked`; `FetchResult.blocked` (`BlockedFolder`); `mail.settle`; the window's question (`ask_choice`, `BLOCKED_QUESTIONS`, `_ask_about_blocked`) (D-116).
- Tests run: `tests/test_sync_imap.py` (three new: reported, delete on the server, bring back, remove here, keep here), `tests/test_engine_imap.py` (one new), `tests/test_engine_commands.py`, `tests/test_cli.py`, `tests/test_engine_registry.py` (the list of destructive commands has `mail.settle`), `tests/ui/test_ui_preferences.py` (one new), `tests/ui/test_ui_window.py` (alone: once 1 failed, name not kept; the next run passed; the file holds the known flaky `test_mark_all_as_read_and_empty_folder`); Pyrefly 0 diagnostics, layer check kept; not the full suite.
- For the owner: at the next mail check the question comes for Trash of the test account.

## 2026-10-13 IMAP actions on the server first (version 0.4.120)

**What landed**
- `engine/imap_online.py` (`ServerSession`, `server_session`): the server step of every action; `Engine.imap_link`, `close_imap_links`, `notice`; `Mailbox.set_flags`, `move`, `copy`, `deliver`, `delete` do the server step first for IMAP accounts, then the same here, then record it; `Mailbox.put_on_server` for the Sent copy (after `mail.send`, `mail.send-due`, repeating messages and `mail.send-outbox`); the IMAP sync holds the account's lock; `ImapState.note_deleted` ("k") and `_push_expunges` never hold back the person's own deletes; the window shows the engine's notices and logs out of the connections when it closes (D-117).
- Found while testing: a folder never synchronised (or made just now) had no known numbering, so a message moved or saved there could not be recorded and a following action (Cancel snooze, replacing a draft) did not reach the server; the numbering is now learned first, and the folder of the message is opened last, right before the step.
- Tests run: `tests/test_engine_imap_online.py` (new, 7 tests: every action on the server before any sync; deleting 29 of 30 and emptying Trash without a question; drafts and the Sent copy; a refusal changes nothing here; offline done here and sent at the next check; a crash between the server and here; a server without MOVE and UIDPLUS loses nothing), `tests/test_engine_imap.py` (two tests now expect the server at once; the held-back test loses files instead of deleting), `tests/test_engine_imap_folders.py`, `tests/test_sync_imap.py`, `tests/test_engine_commands.py`, `tests/test_engine_actions.py`, `tests/test_engine_drafts.py`, `tests/test_engine_outbox.py`, `tests/test_engine_schedule.py`, `tests/test_engine_recurring.py`, `tests/test_engine_rules.py`, `tests/test_engine_local.py`, `tests/test_engine_watch.py`, `tests/test_sync_send.py`, `tests/test_cli.py`, `tests/test_engine_registry.py` (311 passed), `tests/ui/test_ui_preferences.py`, `tests/ui/test_ui_backend.py`, `tests/ui/test_ui_window.py` (alone); Pyrefly 0 diagnostics, layer check kept; not the full suite. Not run: the live tests against the real server (they need the owner's go-ahead).

## 2026-10-13 More tests of the IMAP refactor (version 0.4.121)

- `tests/test_engine_imap_online.py`: four more tests (30 deletes from several threads at once; a mail check while 20 messages are deleted; a connection the server closed is made again; a folder the server renumbered: nothing is touched by number, the delete made then is not done and the message is shown again, nothing is lost). The file ran 10 times in a row: 11 passed each time.
- `tests/live/test_live_imap_online.py` (new, against the owner's real server, test folders of their own, cleaned afterwards): marks, move, copy, a folder made in Chrono Mail with a move into it and out of it, 9 of 11 deleted for good (removed at once, not only marked, never held back), drafts saved and replaced, the Sent copy of a real sent message; every step checked on the server over a connection of its own before any sync; the sync afterwards finds nothing to do and both sides agree. 2 passed.
- The whole live suite (`pytest -m live tests/live`): 47 passed, 1 failed: `tests/live/test_live_ui.py::test_send_receive_read_and_delete_through_the_window` (a POP3 account, not IMAP): the test reads a row of the Trash list without allowing for the group header rows that exist since 0.2.94 (`row_of` gives None for a header). The test is out of date, not the program; not changed (not in a changelist).

## 2026-10-13 The live window test allows for group headers (version 0.4.122)

- `tests/live/test_live_ui.py`: the Trash list is searched without the group header rows. Run live: 1 passed. The whole live suite is now expected to pass.

## 2026-10-13 IMAP folders on the server first (version 0.5.123)

- The owner asked for every IMAP fault left. Found by reading every mail and folder command: folder create, rename and delete were done here only (a rename hid the old folder at the next check and uploaded all its mail again; a delete only hid it), Empty folder and Mark all as read skipped mail not downloaded yet, the Archive, Snoozed and rule folders were made here first, and an action on a message deleted on the server by another program was done here anyway. All fixed (D-118). The owner moved the minor version to 0.5.
- `engine/imap_online.py`: `make_folder`, `rename_folder`, `delete_folder`, `empty_folder`, `mark_folder_read`, `GoneFromServer`; `engine/mailbox.py`: the same as `Mailbox` methods; `sync/imap_state.py`: events "w" and "y"; the commands use them; `tests/fakeimap.py`: RENAME takes the folders below along.
- Tests run: `tests/test_engine_imap_folder_actions.py` (new, 9 tests), `tests/test_mailbox_guard.py` (new, 3 tests), `tests/test_engine_imap_online.py`, `tests/test_engine_imap.py`, `tests/test_sync_imap.py`, `tests/test_engine_actions.py`, `tests/test_engine_imap_folders.py` (96 passed), `tests/test_engine_rules.py`, `tests/test_engine_schedule.py` (38 passed); the two IMAP action files 5 times in a row: 20 passed each time. Live (`tests/live/test_live_imap_online.py`, two new: folders made, renamed with a folder below, marked read, deleted; Trash emptied): 4 passed; the whole live suite: 50 passed. Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Account and group rows show their Inbox (version 0.5.123, with D-118)

- The owner's report: a click on the Local Folders row kept the list of the IMAP account's Trash. `MainWindow._tree_changed` ignored account, group and heading rows; now an account or group row shows its Inbox (`_inbox_under`), a mouse click moves the highlight there (`_tree_clicked`), the title follows (`_mail_title`), and the Favorites heading is not selectable (`models.py`) and folds on a click (D-119).
- Found while answering: the 4 messages in that Trash are calendar and contact items Kerio put in Deleted Items when `tests/live/test_live_dav.py` deleted them; the owner's IMAP account is on the Kerio test server, while the IMAP live tests use the DirectAdmin test server. TRANSFER.md said they were the same account.
- Tests run: `tests/ui/test_ui_tree_rows.py` (new, 4 tests), `tests/ui/test_ui_treestate.py`, `tests/ui/test_ui_models.py` (31 passed), `tests/ui/test_ui_window.py` alone (62 passed); Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Gaps closed: unsynchronised folders, unsubscribe, groups at start, Kerio live tests (version 0.5.123)

- `engine/commands_imap.py` (`account.set-folders`): a folder left out is taken away here (`Mailbox.drop_unsynchronised_folder`, `ServerSession.synchronised_folders`), under the account's lock; refused offline, for standard folders and with mail not yet on the server (D-120). Found by the UI test: a folder never brought here must be skipped.
- `protocols/imap.py`: `unsubscribe`; `imap_online.rename_folder` unsubscribes the old names; `tests/fakeimap.py` keeps subscriptions on the old name after RENAME and knows UNSUBSCRIBE.
- `ui/mainwindow.py` `_first_inbox` looks inside groups; `tests/ui/test_ui_tree_rows.py` waits for the 3 messages again.
- Live: `tests/live/conftest.py` `IMAP_SERVERS`; `test_live_imap.py` and `test_live_imap_online.py` run against DirectAdmin and Kerio (`tests/live/kerio.local.toml`, git-ignored, written from the owner's credential store; accepted certificate; Kerio's folder names); `test_live_dav.py` removes its items from Kerio's Deleted Items. The 12 leftovers of today's test runs were removed from Kerio's Deleted Items.
- Tests run: `tests/test_engine_imap_folder_actions.py` (2 new), `tests/test_engine_imap_folders.py` (one expectation changed: a folder left out is no longer shown), `tests/test_mailbox_guard.py`, `tests/test_engine_imap_online.py`, `tests/test_engine_imap.py`, `tests/test_sync_imap.py`, `tests/test_engine_actions.py`, `tests/test_protocols_imap.py`, `tests/test_cli.py`, `tests/test_engine_commands.py` (255 passed); `tests/ui/test_ui_tree_rows.py`, `tests/ui/test_ui_treestate.py`, `tests/ui/test_ui_folderchoices.py` (11 passed); `tests/ui/test_ui_window.py` alone: 61 passed, 1 failed (`test_mark_all_as_read_and_empty_folder`, the known flaky test; POP3 path, unchanged steps; passed when run alone to read the error, which was therefore not seen). Live: `test_live_imap.py` + `test_live_imap_online.py` on both servers 27 passed, 3 skipped (Kerio: wrong password off, own certificate, Trash not empty); `test_live_dav.py` 4 passed. Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-13 Account rows stay selected; the empty list says so (version 0.5.123)

- The owner's correction of D-119: `_tree_clicked` only folds headings; an account or group row stays selected and shows its Inbox; an account without an Inbox shows an empty list (`_inbox_under` has no fallback); `MessageListView.paintEvent` draws "There are no messages to show in this view." when the list is empty and `MessageListModel.settled` (a new view starts unsettled, a reload of the same view stays settled).
- Tests run: `tests/ui/test_ui_tree_rows.py` (6: account row and group row stay selected, no Inbox, the empty text drawn only once loaded; checked that the drawing test fails without the text), `tests/ui/test_ui_treestate.py`, `tests/ui/test_ui_models.py`, `tests/ui/test_ui_folderchoices.py` (36 passed); `tests/ui/test_ui_window.py` alone: 61 passed, 1 failed (`test_deleting_right_after_clicking_does_not_report_a_missing_message`, a timing test; passed when run alone to read the error, which was therefore not seen; it passed in the two earlier runs today). Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-14 Show on startup (version 0.5.124)

- `ui/settingsdialog.py`: `startup_box` on the General page (`startup_choices`, headings disabled), saved as `startup_view`; `ui/mainwindow.py`: `_startup_choices`, `_restore_state` opens the chosen section (or Mail for a chosen folder), `_populate_tree` tries the chosen folder first, then the last selected one, then the first Inbox (D-121).
- Tests run: `tests/ui/test_ui_startup_view.py` (new, 5: the order of the list, a chosen folder after closing in Calendar, a chosen section, a folder that is gone, Last selected folder), `tests/ui/test_ui_preferences.py` (one new: the list, a heading cannot be picked, the choice saved and shown again) (32 passed), `tests/ui/test_ui_treestate.py`, `tests/ui/test_ui_tree_rows.py` (10 passed), `tests/ui/test_ui_window.py` alone: 61 passed, 1 failed (`test_mark_all_as_read_and_empty_folder`, the known flaky test, not touched by this change). Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-14 The three flaky tests: two program faults and two test faults (version 0.5.125)

- Caught by running each test repeatedly. `test_deleting_right_after_clicking_does_not_report_a_missing_message` (failed 6 of 15): a program fault: a message deleted or moved right after it was clicked made its still-running opening fail, and the window reported missing messages (status line and dialog). `MainWindow._keep_place_after_delete` now cancels the opening of a message that is moved away (request counter, mark-read timer).
- `test_mark_all_as_read_and_empty_folder` (failed 3 of 15): a test fault (it looked at the rows while the list was reloading); it now waits until the list is shown again. Found with it, a fault of 0.5.123: a list with messages that was reloaded could say "There are no messages to show in this view." for a moment; `MessageListModel.show` keeps a reload settled only when the list was empty.
- `test_marking_as_read_redraws_every_cell_of_the_selected_rows` (failed 3 of 25): a test fault (the messages were unread already, so it did not wait for Mark as unread to finish before Mark as read; both ran at once and sometimes "unread" finished last); it now waits for the first change. It shows a weakness of the program: two changes of the same message given quickly one after the other run at the same time and may finish in the other order (not fixed; proposed to the owner).
- Tests run: the four tests (three fixed, one new in `tests/ui/test_ui_tree_rows.py`) 20 times in a row: 80 passed; `tests/ui/test_ui_shortcuts.py`, `tests/ui/test_ui_tree_rows.py`, `tests/ui/test_ui_models.py`, `tests/ui/test_ui_startup_view.py` (43 passed); `tests/ui/test_ui_window.py` alone 3 times: 62 passed each time. Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-14 Changes of the same message in order (version 0.5.126)

- `ui/backend.py`: `call(..., in_order=True)` with a waiting list per message, `_deliver` starts the next one; `wait_for_done` lets the waiting ones run before the end. `ui/mainwindow.py`: the 11 calls that change messages pass `in_order=True` (D-122).
- Tests run: `tests/ui/test_ui_backend.py` (three new: unread then read 30 times always ends read, different messages start at once, waiting changes are done before the end; checked that the first fails without the ordering: 2 of 5 runs) 10 times: 12 passed each time; `tests/ui/test_ui_shortcuts.py`, `tests/ui/test_ui_tree_rows.py`, `tests/ui/test_ui_startup_view.py` (20 passed); `tests/ui/test_ui_window.py` alone twice: 62 passed each time. Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-14 Installers for Linux, and the Windows recipe (version 0.5.127)

- New `packaging/`: `build_linux.sh` and `build_in_container.sh` (Nuitka 4.2.2 in an Ubuntu 22.04 podman container), `chrono_mail_main.py` (the packaged start; points Qt to the HTML viewer's helper), `nfpm.yaml` and `package_linux.sh` (.deb and .rpm), `upload_release.sh` (attach to the GitHub release), `build_windows.ps1` and `chrono-mail.iss` (setup.exe; not yet run on Windows), `PACKAGING.md` (the instructions for both machines). `.gitignore`: `build/` and `dist/`. Installed on the owner's machine (with permission): podman 5.7.0, nfpm 2.48.0 (checksum checked). D-123.
- Problems found and fixed: Nuitka 4.3 crashed the program at startup (Nuitka issue 4081; traced with an unstripped build and gdb to `Nuitka_Function_New` for the inner function `changed` in `appearance.start`; pinned 4.2.2); the HTML viewer's helper was not found on bare systems and the system's own helper was borrowed on the owner's machine (fixed in `chrono_mail_main.py`, checked with `ps`); missing requirements found on bare containers (`libxkbfile`, `libglib`, `libXfixes`, Wayland and CUPS libraries); the dateutil time-zone data file.
- Tests: on this machine 10 starts with an empty environment and with a window (all kept running); bare Ubuntu 22.04 and 26.04 (.deb) and Fedora 43 (.rpm) as a normal user, 3 starts each, no warnings; a Nuitka test program with the same settings showed an HTML page with its own helper on bare Fedora (31 colours drawn) and stored, read and removed a password in KDE Wallet; the .deb installed and removed cleanly here (menu entry, command, icon). No Python tests changed; not the full suite.

## 2026-10-15 First release page; hand-over for the Windows machine (version 0.5.128)

- 0.5.127 pushed; its GitHub release page made with `packaging/upload_release.sh`: the .deb and the .rpm (the first public release, for the owner's real-world test on a second Ubuntu installation). The owner's release policy: in-between versions only pushed; installers only for milestone versions, after testing the pushed version (also in `packaging/PACKAGING.md` section 5).
- New `TRANSFER-WINDOWS.md` for the Claude app on the Windows computer (the Windows machine builds, tests and releases the setup.exe and may write code): its role, the rules, getting started, the file map, the checks on Windows, the installer, working together with the Kubuntu machine, open points. `AGENTS.md` lists it.
- Found while answering the owner: Chrono Mail has no protection against running twice (two copies would share the same mail); reported, not fixed.
- No code changed; no tests run.

## 2026-10-15 The first Windows build; its three fixes (version 0.5.129)

- The Windows machine (the Windows computer) built `chrono-mail_0.5.128_setup.exe`: it worked after three fixes in `packaging/build_windows.ps1`, made there without a version and applied here: `${env:ProgramFiles(x86)}` (PowerShell read `$env:ProgramFiles(x86)` as `C:\Program Files(x86)`); `--include-package-data=tzdata:zones` (the time-zone files were included twice, 604 warnings); `win32ctypes.core` and `win32ctypes.pywin32` instead of all of `win32ctypes` (its tests were included). Build facts: Nuitka 4.2.2, Visual Studio Build Tools 18 (cl 14.5), Inno Setup 6.7.3, program folder 485 MB, setup.exe 130 MB. `packaging/PACKAGING.md` section 4.3 says so.
- The Linux build had the same time-zone fault: `packaging/build_in_container.sh` got the same change. Rebuilt: no duplicate warnings, 605 of 605 tzdata files, three starts with an empty environment kept running.
- Reported from Windows (not fixed): with two copies of Chrono Mail running, the index rebuild after a format change fails with WinError 32 on `index.sqlite-wal` (`index/index.py` lines 510-513): Windows cannot delete a file the other copy has open. It is the known missing protection against running twice; on Linux the delete succeeds silently.
- Still to test on Windows: the password store in the installed program.

## 2026-10-15 A failed index open closes its connection: the format upgrade works on Windows (version 0.5.130)

- Reported by the Windows machine and confirmed here: `Index.__init__` (`index/index.py`) and `PimIndex.__init__` (`pimstore/index.py`) opened the connection, switched it to WAL, then raised for an unknown format or a damaged file without closing it. The rebuild that follows deletes the `-wal` file; Windows refuses that while the file is open (WinError 32), so every index format upgrade failed on Windows, also with one copy running. Now both close the connection before the error goes on (`_open` split off); an unreadable format number counts as an unknown format (it raised an uncaught ValueError and leaked too); a failure while making a new index closes it as well.
- Tests run: `tests/test_index_index.py` (four new: old and unreadable format, damaged file, and the rebuild after a failed open), `tests/test_pimstore.py` (three new), `tests/test_index_rebuild.py`, `tests/test_engine_commands.py` (198 passed); the new ones fail without the fix (6 of 7; the rebuild test passes on Linux either way). The connection is checked with a recording connection class, because Linux cannot show the Windows file lock. Pyrefly 0 diagnostics, layer check kept; not the full suite. To test on Windows: the upgrade of an old index with the rebuilt setup.exe.

## 2026-10-15 The packaged program knows its version; the build copies only what it needs (version 0.5.131)

- The About window of the installed Windows program said "version unknown": `core/appinfo.py` reads `pyproject.toml` in the source tree and otherwise the package information, which Nuitka leaves out unless told. Both build scripts now pass `--include-distribution-metadata=chrono-mail` (also proposed by the Windows computer). Checked with a small Nuitka test build of `program_version()`: 0.5.131 with the option, "unknown" without it.
- Found while checking: `packaging/build_in_container.sh` copied the whole project folder into the build container, since today including `chrono-mail-import-samples/` (the owner's private mail export, git-ignored). The container is temporary and deleted after the build; nothing left the machine. It now copies only `pyproject.toml`, `README.md`, `LICENSE`, `src` and `packaging`. Full Linux rebuild: the folder is not touched (0 mentions in the build log), three starts kept running.
- `.gitignore`: `chrono-mail-import-samples/` (the owner's exports from the reference client for the importer; never in git). STATUS.md backlog: a friendly message when Chrono Mail may not connect (a firewall or no internet), proposed by the Windows computer; later.
- `packaging/PACKAGING.md` section 2: both points. No Python code changed; no tests run besides the build checks.

## 2026-10-15 Import from the reference client (version 0.5.132)

- The owner's three exports were examined, structure and counts only: settings .xml (25 POP3 accounts, 30 rules, 34 categories, passwords present and scrambled), mail export (140,269 .eml files in 94 folders, 23 GB, complete messages with all headers but no marks), backup .zip (51 SQLite files, 20 GB; marks for every message, 59 contacts, no events or tasks; mail stored in parts; passwords properly encrypted). 140,167 of 140,269 messages matched to the backup by folder and Message-ID or by date.
- The password scrambling is a fixed XOR key: found from one password the owner gave; all four different passwords in the file come out as readable text. First 11 characters of the key were known (21 of 25 accounts share a 12-character password); the owner then gave that password too: 12 characters known, all 25 passwords decode (checked on his export, counts only).
- New `engine/<name>_source.py` (reading the three exports, hostile-input safe), `engine/commands_<name>.py` (`import.<name>-scan`, `import.<name>`, journal, fingerprints), `ui/importdialog.py` (one window), Menu > File > Import… (the owner: just "Import…"; the program is chosen in the window's list, more will follow). `config/model.py` and `config/files.py`: account setting `skip_known_mail`; `sync/pop3_fetch.py`: `already_here`; `engine/commands_mail.py`: uses it on the first check of an imported account. `tests/test_legal_names.py`: the name allowed only in the importer's files. D-124.
- Read-only scan of the owner's real exports with a throwaway profile (deleted): 25 accounts, 21 passwords unreadable (the 12-character one), 26 rules importable and 4 not at first; after the owner's instruction (every rule as it is) the blocked-senders rule and the script rule are imported too (both move to the special Spam folder, which is Junk): 28 importable, 2 not (an empty rule, a rule for an account that is gone), 81 folders, 140,269 messages, 140,272 marks, 59 contacts, 2 seconds.
- Tests (made-up data only): new `tests/test_<name>_source.py` (25; the 12 for the rules fail without the rule fix, 16 failures with the importer tests), `tests/test_import_<name>.py` (8), `tests/test_pop3_skip_known.py` (4), `tests/ui/test_ui_import.py` (5); related `tests/test_config.py`, `test_sync_pop3.py`, `test_engine_commands.py`, `test_engine_registry.py`, `test_engine_local.py`, `test_mailbox_guard.py`, `test_layers.py`, `test_cli.py`, `test_engine_rules.py` (259 passed), `tests/ui/test_ui_window.py` alone (62 passed). Pyrefly 0 diagnostics, layer check kept; not the full suite. `tests/test_legal_names.py` fails on this machine only, because of the two private, git-ignored hand-over files in the project folder (it did before this change).
- The owner's screenshot: the summary fell off the bottom of the window (measured: it needed 102 of 69 pixels); it now scrolls and the window opens 560 high. The right edge cut off in the screenshot could not be reproduced (the window's own title bar was cut off too).
- Still open: the owner imports his mail himself and checks it; task I.2.

## 2026-10-15 The import twice as fast (version 0.5.133)

- The owner: the import takes much longer than the export did. Measured (1,000 made-up messages of 165 KB, real disk, a throwaway profile): 38 messages per second; half of the time two fsyncs per message, almost half reading each message twice (the duplicate check parsed the whole message, the index again); between batches the file list, the backup (170 MB unpacked) and the fingerprints of all mail here were read again, 140 times for the owner's export.
- Now (D-125): `Store.written_together` makes a batch safe at once; the journal gets the batch only after that; the fingerprint comes from the header only; the file list, the backup's marks and the known mail stay in memory for the whole import (also the marks are now used once across batches); a duplicate counts only when its copy here can be read back. Measured again: 76 messages per second; the pauses between batches are gone on top of that.
- Tests: new in `tests/test_import_emclient.py` (4: header fingerprint equals the whole-message one, the backup read once across batches, a duplicate whose copy is gone is imported again, one sync per batch before the journal) and `tests/test_store_store.py` (3: one sync at the end, each file and folder without a sync of everything, safe also after an error); the crash test now also clears the importer's memory, as a restart does. Without the fixes 7 of them fail. Run: the importer tests, `test_store_store.py`, `test_store_interruption.py`, `test_mailbox_guard.py`, `test_sync_pop3.py`, `test_engine_local.py`, `test_pop3_skip_known.py`, `tests/ui/test_ui_import.py` (190 passed). Pyrefly 0 diagnostics, layer check kept; not the full suite.
- Also in 0.5.133: after the owner's first real import the list on screen (Unread) still showed the messages from before, while the counts were new. The import reloaded the folder list, and the message list is only loaded again when the selection changes; it did not. Now the list on screen is loaded again after the folder list (`ui/mainwindow.py`, `_after_import`). New test in `tests/ui/test_ui_import.py` (fails without the fix); `tests/ui/test_ui_window.py` alone 62 passed.
- Also in 0.5.133: `chrono-mail-factory-reset.py` (the owner's private helper, git-ignored): deletes the settings, the data and the wallet entries of the accounts in the settings, after a warning and only when Proceed is typed; refuses while Chrono Mail runs. Tested on a throwaway profile with a dummy password store.
- Also in 0.5.133: after the import the owner's delete seemed to do nothing. Cause: the window started the first mail check of all accounts at once (25 new ones), the program runs at most 4 commands at the same time, and the delete waited in line; the first check of an imported account downloaded every message on the server to compare it. Now (D-126) that check reads the header first (POP3 TOP n 0) and downloads only what is not here (a server without TOP: the whole message, as before), and mail checks have a lane of their own in the window (`Backend.call(..., checking=True)`, 4 places), so the person's actions never wait behind them. Tests: `tests/test_pop3_skip_known.py` (2 new; the two older stand-ins now compare headers, because the check gets the header first), `tests/ui/test_ui_backend.py` (1 new); without the fixes 2 of the 3 fail (the one for a server without TOP passes either way: it guards the old way). Run: these, `tests/test_sync_pop3.py`, `tests/test_protocols_pop3.py`, `tests/test_engine_local.py` (143 passed), `tests/ui/test_ui_window.py` alone (62 passed).
- Also in 0.5.133 (D-127): during the owner's second import KDE Wallet's Secret Service answered "Can't find session …" (a known, unsolved KDE bug, 523878); Chrono Mail showed "Unexpected error" and stopped the whole import, because the password and key stores only caught keyring's own errors. Now every failure of the credential store is a normal error with its reason; the importer reports the account, goes on, and makes it at the next run. Checked on the owner's machine: kwallet6 6.24.0; a fresh process read the wallet fine (also 120 reads by 8 threads at once, harmless reads of an entry that does not exist). Tests: `tests/doubles.py` (SessionLostBackend), `tests/test_config.py`, `tests/test_crypto_keystore.py`, `tests/test_import_emclient.py` (3 new, all 3 fail without the fix); related run 155 passed.

## 2026-10-15 Tags for mail, step 1: the engine (version 0.5.134)

- The reference client's manual (its "Working with Tags" page) was read for the design: a Tags window (Mails, Events, Contacts, Tasks, Notes; Add, Edit, Delete, Up, Down; colour, name, shortcut), a tag editor (name, colour, shortcut, use for), a Tag column with coloured icons and a drop-down of tags with coloured ticks, several tags per item. Five steps agreed: engine, window, rules, importer, IMAP keywords.
- New `store/tags.py` (the tags file of a mailbox), `engine/commands_tags.py` (`message.tag`, `tag.create`, `tag.delete`, mail counts and renaming); the store gives a copy the tags of the original and forgets the tags of a message deleted for good; the index keeps tags (format 7, format 6 upgraded in place), finds them by filter and by `tag:` in searches, and a rebuild or repair takes them from the store; the mailbox sets tags and keeps them on copies; `tag.list` counts mail, `tag.rename` renames in mail; `message.list` and `message.search` take a tag; listed messages carry their tags. `tests/test_mailbox_guard.py`: changes of tags must go through the mailbox too. D-128.
- Tests: new `tests/test_store_tags.py` (6), `tests/test_index_tags.py` (6), `tests/test_engine_tags.py` (7); related existing tests 495 passed (store, index, rebuild, engine commands, pim, actions, search, IMAP, local, contacts, CLI, guard, sync, importer, the contacts, tasks and calendar windows), `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 diagnostics, layer check kept; not the full suite. Without the new code the new tests cannot load.

## 2026-10-15 Tags for mail, step 2: the window (version 0.5.135)

- D-129. New `ui/tagging.py` (tag icon, Tag menu, tags drawn in the list), `ui/tagsdialog.py` (Tags window and editor). `config/tags.py`: the order of the tags, a shortcut and what each tag is for (old files still load); `engine/commands_tags.py`: `tag.edit` (shortcut, use for, up/down, place); `engine/commands_pim.py`: `tag.list` in the person's order with shortcut and kinds; `engine/commands_combined.py`: the view `tagged`. Window: `ui/mainwindow.py` (Menu > Tags, Tag button, right-click Tag, Filter > Tagged with, Tags in the folder list, tags in the reading pane, shortcuts, text colour), `ui/models.py` (Tag column, text colour, replace_tags, tag filter, Tags section), `ui/delegate.py` (drawing), `ui/columns.py` (Tag column), `ui/reader.py` (tags line), `ui/settingsdialog.py` and `ui/prefs.py` (two settings).
- Found while testing: a Tag column switched on for everyone made Subject too narrow on a small window (`tests/ui/test_ui_columns.py`); the column is now narrow and off by default.
- Tests: new `tests/test_config_tags.py` (5), `tests/ui/test_ui_tags.py` (8), 2 more in `tests/test_engine_tags.py`; `tests/ui/test_ui_window.py` (the Filter menu has Tagged with: its expected list and positions updated) alone 62 passed; related window and engine tests 235 passed. Without the new code the new tests cannot load. Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-15 Tags for mail, step 3: rules (version 0.5.136)

- D-130. `config/rules.py` (the field tag with is / is_not; the actions add_tag and remove_tag with a tag as value), `engine/rule_engine.py` (the tags of the message, kept up to date while the rules run; adding and removing tags; a new tag gets a colour through `give_colours`, now public in `engine/commands_tags.py`), `ui/ruleedit.py` (Tag as a condition, Add tag and Remove tag as actions, the tags to choose from).
- Found while testing: three window tests had lists that no longer held: the Menu has Tags… (0.5.135), the toolbar has the Tag button after Mark (0.5.135), and File has Import… (0.5.132, already pushed: `tests/ui/test_ui_theme.py` was not run then and failed since). Their expected lists were updated.
- Tests: `tests/test_engine_rules.py` (3 new), `tests/ui/test_ui_rules.py` (1 new); 4 of them fail without the change. Run: rules, rule editor, theme, tags, importer, IMAP, local, guard, layers (157 + 27 passed). Pyrefly 0 diagnostics, layer check kept; not the full suite.

## 2026-10-15 Tags for mail, step 4: the importer (version 0.5.137)

- D-131. The owner's exports (structure only): 34 categories, colours as #rrggbb, "used for" as text (None 14, All 4, Mail 3, Contact 3, Event 2, Task 2, combinations), no shortcuts; the backup's MailCategoryNames: 9 tagged messages (all with a Message-ID), 3 names, of which 1 is a category.
- `engine/emclient_source.py`: categories, the tags of each message in the backup (`BackupData.take` gives marks and tags, `tagged` lists them), the category parts of rules. `engine/commands_emclient.py`: the phase tags (categories made into tags, the mail that is here tagged once), tags set when a message is stored, `tags` parameter, scan counts. `ui/importdialog.py`: Tags type and summaries. Read-only scan of the owner's exports: 34 categories, 9 tagged messages, rules 28 importable, 2 not.
- Tests: `tests/test_emclient_source.py` (3 new; the builders write categories and message tags), `tests/test_import_emclient.py` (3 new), `tests/ui/test_ui_import.py` (summaries); 6 fail without the change. Related run: 90 passed (importer, tags, rules, guard); `tests/test_legal_names.py` still fails only for the owner's private hand-over files. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-15 Tags for mail, step 5: IMAP keywords (version 0.5.138)

- D-132. `protocols/imap.py`: `tag_to_keyword`, `keyword_to_tag`, PERMANENTFLAGS (`SelectInfo.keeps_keywords`), keywords allowed in STORE and APPEND. `sync/imap_sync.py`: tags compared, sent, taken over, downloaded and uploaded with the flags; a folder that keeps no keywords keeps its tags here (a note says so). `engine/imap_online.py`: `set_tags` on the server first; a change of flags keeps the keywords in the state. `engine/session.py`: `ImapLink.keywords`. `engine/mailbox.py`: `set_tags` and `rename_tag` on the server first in an IMAP account. `tests/fakeimap.py`: PERMANENTFLAGS, a server without keywords.
- Tests: new `tests/test_imap_tags.py` (9: the keyword form, PERMANENTFLAGS, server first, tags of another program in and out, an offline change sent later, a flag change keeps tags, renaming and deleting a tag on the server, a server without keywords); they cannot load without the change. Related run 242 passed (IMAP client, sync, engine, folders, online, guard, tags, rules, importer, local, store, index). Pyrefly 0, layer check kept; not the full suite.
- Live, with the owner's permission: DirectAdmin (Dovecot) keeps keywords (PERMANENTFLAGS with \\*; `Work` and `Family&ACA-matters` stored, removed in another letter case); Kerio does not (PERMANENTFLAGS without \\*, a keyword STORE is refused with "Invalid flag list"), so on the owner's Kerio account tags stay on his computer; the test folders were removed. Live suite: 62 passed, 3 skipped by their own settings (wrong-password attempts off, Kerio's own certificate, the test Trash not empty).

## 2026-10-16 The list no longer goes blank after a delete (version 0.5.139)

- D-133. Reported by the owner, analysed on the Windows computer and confirmed here from the code: `MessageListModel.show` emptied the list at once and then waited for the group headers and the first rows, one after the other (half a second in Unread). `ui/models.py`: `reload()` fetches both in the background and `_swap_in` replaces the rows in one reset; every caller of reload gets this (delete, move, archive, after sending, after a mail check, tags, import).
- Tests: `tests/ui/test_ui_models.py` (2 new: a delete in a folder and in Unread never shows an empty list; both fail with the old reload); `tests/ui/test_ui_window.py`: the unread test waited for "3 rows" right after a reload, which held only because the list used to empty first; it now waits for the reloaded rows. Run: `tests/ui/test_ui_window.py` alone 62 passed; models, drag and drop, tags, import, columns, schedule, search, shortcuts passed.
- Found, not fixed (not asked): three tests of `tests/ui/test_ui_push.py` (new mail shows up without asking, waiting is not busy, no watching when automatic checking is off) time out waiting for the window's watch connection; they fail the same way on the pushed 0.5.132 code, so they are older than this work. Cause not looked into yet.

## 2026-10-16 The first column of the message list can be moved (version 0.5.140)

- The owner could not drag the Unread column any more: it had become the first column, and a tree view keeps its first column in place (meant for a real tree; the message list is flat). The window allowed moving the columns but never the first one; `ui/mainwindow.py` now also sets `setFirstSectionMovable(True)`.
- Test: `tests/ui/test_ui_columns.py` (the first column moves and the new order is saved; fails without the line). Run: `tests/ui/test_ui_window.py` alone, columns, column fit, models, tags. Pyrefly 0, layer check kept; not the full suite.
- Also in 0.5.140: with the first column movable, a group row started at the place of the model's first column (Qt spans a row from there to the right edge), so after dragging Unread to second place the group bars began at the Unread column (the owner's screenshot). `ui/messagelistview.py` now draws a group row itself from the left edge to the right edge (`drawRow`). Test in `tests/ui/test_ui_columns.py` (the drawn list: the bar is at the left edge with Unread in second place; fails with the old drawing). Run: `tests/ui/test_ui_window.py` alone 62 passed; columns, column fit, models, drag and drop, theme, tags 81 passed. Pushed: 0.5.133 to 0.5.140 together.

## 2026-10-16 The push tests count messages (version 0.5.141)

- The three failing tests of `tests/ui/test_ui_push.py` (also failing on 0.5.132) were a test fault: they waited for a number of rows, and since group headings (0.2.94) a heading is a row too (2 rows for 1 message). The window watched the account and showed the mail as it should. The tests now count messages. Run: `tests/ui/test_ui_push.py` 5 passed. Pyrefly 0. No program code changed.
- Also in 0.5.141: every mail check failed with "[AUTH] Authentication failed" for the imported accounts. Checked (lengths and comparisons only): all 25 had a stored password equal to what the importer decoded, so the key was wrong. The owner has two passwords: the newer one with an underscore and "!", the older one with a hyphen; the key's 9th character had been derived from the older one written with an underscore. With the corrected character, 49 stored values decode to the newer password and 2 to the older one exactly. `engine/emclient_source.py`: the corrected value; `engine/commands_emclient.py`: `import.emclient-passwords` (stores the export's passwords for accounts that are here already; never shows them). Run on the owner's profile with his permission: KDE Wallet refused once ("Can't find session", the known passing fault), the second run updated all 25; checked: all 25 stored passwords equal the corrected decoding. Test: `tests/test_import_emclient.py` (1 new); importer tests 52 passed. Pyrefly 0, layer check kept.

## 2026-10-16 The release page text names only the Linux installers (version 0.5.142)

- `packaging/upload_release.sh` wrote "and setup.exe (Windows)" on every new release page; the owner removed it by hand from all releases. The text now names only the .deb and .rpm. No tests (a shell script's text); `bash -n` checks the script. Pushed with 0.5.141 earlier today: full release with the .deb and .rpm.

## 2026-10-16 The Import window waits for the reading of the exports (version 0.5.143)

- Reported from the Windows computer: Import clicked while the exports were still being read showed 100% at once (the bar's size was set once, from a count of 0) and the late reading answer replaced "Importing…" and changed the counts; the import itself ran fine. `ui/importdialog.py`: Import off until the reading answered; late and older answers ignored; the bar follows the count (D-134).
- Tests: `tests/ui/test_ui_import.py`, 3 new (each failed on the old code, checked); the stop test now waits for the reading before Import (a consequence of the change). Run: `tests/ui/test_ui_import.py` 10 passed. Pyrefly 0, layer check kept; not the full suite.
- Also in 0.5.143 (the owner's screenshot): the status line counted each batch of 1,000 from 1 again ("Importing mail… 225 of 1000"). `engine/commands_emclient.py` now reports the progress over the whole import (earlier batches and imports counted in); `ui/importdialog.py` takes that number as it is (it added its own count before); `ui/mainwindow.py` writes progress numbers with thousands separators ("45,225 of 140,269"). Tests: 1 in `tests/test_import_emclient.py`, 2 in `tests/ui/test_ui_import.py` (each failed on the old code, checked). Run: `tests/test_import_emclient.py` and `tests/ui/test_ui_import.py` 30 passed, `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 Four size sliders (version 0.5.144)

- Settings > Appearance > Sizes: Main window, Message list, Other windows and dialogs (new), Text of messages (D-135). Changed: `ui/scale.py` (a third factor, icons by the window they are in, the style gives menus and popups of the main window its text size), `ui/appearance.py` (the program's text at the other windows' size, `main_font`), `ui/theme.py` (the stylesheet at a given factor), `ui/mainwindow.py` (its own text size, tries out both sliders, message windows and composers at the other windows' size), `ui/settingsdialog.py` (the four rows; this window follows Other windows and dialogs), `ui/prefs.py` (`windows_scale`), `ui/reader.py` (the sender's circle by the window it is in), `ui/messagewindow.py` and `ui/composer.py` (their look at the other windows' size). Not needed after all: `ui/searchhelp.py` and `ui/searchhistory.py` (their sizes already follow the main window; their text comes through the style).
- A first try with a watcher of every event of the program (for the menus) crashed the appearance tests; it was replaced by a hook in the style.
- Tests: `tests/ui/test_ui_appearance.py`, 3 new (an older settings file; the main window and a dialog each follow their own slider, also the icons; a menu follows the window it opens from), each failing on the old code (checked); 4 existing tests changed to the new meaning (the program's text, the page list of Settings and the growing of Settings follow Other windows and dialogs; the names on the page). Run: appearance, preferences, settings, theme, composer, reader, search field, search help, sections, rules, columns 206 tests (1 failed: `test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons`, which also fails on the old code in that same run and passes alone: it depends on what earlier tests leave behind; not changed); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.
- Also in 0.5.144: STATUS.md's test count corrected (1,941), two Import window items on the Backlog.

## 2026-10-16 Settings changes one at a time; Chrono Mail runs once (version 0.5.144)

- Reported from the Windows computer (0.5.143): after an import the first mail check said "The settings could not be saved: Access is denied". Cause (checked in the code): the accounts of a check ran at the same time and each read config.json, changed its own account and saved the whole file, with nothing making them take turns. On Windows replacing the file failed while another thread read it; on every system the last save undid the other changes. 12 places changed the settings that way. `engine/session.py`: one lock for the settings file (every read and save) and `change_config`, which applies a change to a fresh read under the lock; the long work (copying mail, making stores) stays outside it (measured: a read and save takes about 1 to 2 ms on the owner's disk). All 12 places use it: `engine/commands_mail.py`, `commands_emclient.py`, `commands_accounts.py` (add, remove, policy, edit, trust and forget certificates), `commands_local.py` (2), `local.py`, `commands_dav.py`. In `mail.fetch` the rules run first, and a failed save of the imported-account flag is a line in the check's report instead of ending the check (D-136).
- Chrono Mail runs once per user: the program looks for its own process (Linux: the process name `chrono-mail` in /proc, of the same user, which the program also sets on itself; Windows: `chrono-mail.exe` of the same user, from `WTSEnumerateProcessesW`); a second start asks the running program to show its window, or says "Chrono Mail is already running."; the command line tool refuses every command while the program runs (`--help` still works). New: `config/running.py`, `ui/singleinstance.py`; changed: `ui/app.py`, `ui/mainwindow.py` (`bring_forward`), `cli/main.py` (D-137).
- Tests: `tests/test_config_changes.py` (2: eight accounts changed at the same moment all keep their change; the rules run when the flag cannot be saved), `tests/test_running.py` (6, one only on Windows), `tests/ui/test_ui_singleinstance.py` (2). On the old code 7 of them failed (checked); the two that check that nothing is found wrongly (a killed program, a process with another name) pass there by their nature. Changed as a consequence: `tests/test_cli.py` and `tests/live/test_live_clients.py` give the command line tool their own check (so they run while the program is open); `tests/test_engine_local.py` makes its crash at the commit point in the file write of the settings. Run: settings, running, skip-known, importer, account edit, local, dav, certificates, engine commands, config and command line tests 228 passed, 1 skipped (Windows); single instance and tray 12 passed (the window tests run apart from the others, as their fixtures need). Pyrefly 0, layer check kept; not the full suite. The Windows check is not tested yet: The Windows computer runs `tests/test_running.py` and a second start of the installed program.
- Found, not fixed: two accounts added at the very same moment on a new installation (no key yet) could each make an encryption key; the settings keep the first.

## 2026-10-16 The Operations window (version 0.5.145)

- The owner's request, with three screenshots of the reference client's Operations window and its Settings > General > Operations Window section (D-138). New: `ui/operations.py` (the window: tabs Operations, Errors and Log; a row per account with the account icon and a blue badge, the name in bold, its step and a blue bar; Ignore All, Copy Log, Clear Log; the tick box "Hide when all operations are complete"; the clickable busy bar of the status line). Changed: `engine/session.py` (`Engine.activity`, the steps per account), `sync/pop3_fetch.py` and `sync/imap_sync.py` (a `step` report), `engine/commands_mail.py` (`mail.fetch` reports Connecting, Connected, Listing messages or folders, Downloading n of m, Deleting messages on the server, Synchronising a folder, Disconnected, Applying rules), `engine/commands_outbox.py` (Send All reports Sending n of m and Disconnected per sending account, only for accounts with mail waiting; `failed_accounts` names the account of each failure), `ui/backend.py` (the `activity` signal), `ui/mainwindow.py` (rows, errors and the rules for opening and closing; Menu > View > Operations…; a click on the busy bar), `ui/settingsdialog.py` (Settings > General > Operations window, three tick boxes), `ui/prefs.py` (the three settings, all on by default).
- Tests: `tests/test_engine_activity.py` (6) and `tests/ui/test_ui_operations.py` (11), new. Changed as a consequence: `tests/ui/test_ui_schedule.py` (its stand-in for the mail check takes the new `manual` choice). Run: engine activity, engine commands, outbox, IMAP engine, IMAP and POP3 sync, skip-known, layers and mailbox guard 181 passed, 1 failed (the mailbox guard: a variable in the Send All call was renamed; the original wording was put back in the program), then activity, outbox, mailbox guard and schedule 31 passed; Operations, theme, rules, columns, settings, tray, schedule, backend, push and preferences 144 passed, 1 failed (`test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons`, the known one that fails only in long combined runs; passes alone); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 Every send and receive opens the Operations window (version 0.5.146)

- The owner's report: no Operations window at startup. Cause: D-138 opened it only for Refresh (the agent's own proposal, wrongly accepted as what the owner meant); the check at startup, the timers and the push never opened it. `ui/mainwindow.py`: every send and receive opens it while "Show window on send and receive" is on; an automatic check and an error open it without taking the keyboard focus, Refresh brings it to the front with the focus (D-139). The `manual` choice of 0.5.145 stays, now only for the focus.
- Tests: `tests/ui/test_ui_operations.py`, 2 new or changed (an automatic check, the check at startup: both open it without the focus; both fail on 0.5.145, checked); the Refresh test also checks the focus; the test without "Show window when an error occurs" switches the opening on send and receive off for its automatic check. Run: operations 12 passed; push, tray, schedule, settings, startup view 50 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The Windows source run starts again (version 0.5.147)

- Reported from the Windows computer (0.5.145): the dev copy on Windows always said "Chrono Mail is already running." Cause (confirmed in `config/running.py`): only the own process and its parent were skipped, and a source run on Windows is launcher -> venv redirector -> program, so the launcher (the grandparent, named chrono-mail.exe) counted as another copy. Fix: `config/running.py` skips every ancestor (`windows_parents` from a Tool Help snapshot, `ancestors`, the selection as `windows_matches`) (D-140).
- Tests: `tests/test_running.py`, 4 new (the launcher chain is not counted; a real other copy, and not another user's, still is; a loop or an unknown parent ends the walk; Windows only: the snapshot holds this process with its parent). On the old code the tests cannot load (the functions are new); the old rule applied to the same made-up chain finds the launcher (checked). Run: `tests/test_running.py`, `tests/test_cli.py`, `tests/ui/test_ui_singleinstance.py` 38 passed, 2 skipped (Windows). Pyrefly 0, layer check kept; not the full suite. To do on the Windows computer: `tests/test_running.py`, start the dev copy, and the installed program twice.

## 2026-10-16 The bars of the Operations window are drawn by the program (version 0.5.148)

- The owner's report: on Windows the bars of the Operations window stay grey (blue on Linux). On Linux both available styles, Fusion and Windows (also wrapped in the program's ScaledStyle and inside the main window), draw them blue (checked with pictures); Windows' own style is not available here, so the fault could not be reproduced. `ui/operations.py`: `OperationBar`, drawn by the program (grey track, blue part, a moving blue piece while the end is not known), replaces the progress bar of a row (D-141). The busy bar of the status line is unchanged.
- Tests: `tests/ui/test_ui_operations.py`, 1 new (the bar's blue part, grey track, full, empty and moving piece, drawn under Fusion and Windows); 2 tests read the new bar. The new test cannot load on the old code (the bar is new). Run: operations 13 passed; operations, appearance and theme together 58 passed (the test switches styles and puts the old one back); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. To check on the Windows computer: the bars are blue.

## 2026-10-16 The program's own blue everywhere (version 0.5.149)

- The owner's screenshot from Windows (0.5.148): the Menu button, the tab lines, the badges and the bars were grey. My first guess (windows that are not active) was wrong; the Windows computer measured the cause: the colour scheme was System (the factory reset had removed the earlier Dark), and Qt uses the Windows accent colour as the highlight, on the owner's Windows dark grey (#4c4a48 active, #1e1e1e inactive). `ui/colours.py`: the program's blue in one place and `with_program_blue`; `ui/appearance.py`: every scheme gets it for every window state (D-142).
- Tests: `tests/ui/test_ui_appearance.py`, 5 new (System with a made-up grey accent gives the program's blue in all three window states, which fails on the old code with #4c4a48, checked; the function for both shades; Dark and Light keep their blue). Run: appearance 36 passed; theme, operations, preferences, settings, sections, icons 77 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. Qt without a screen cannot report a dark system, so System with a dark system is covered by the function test only. To check on the Windows computer: with System, everything is blue.

## 2026-10-16 Standard column order; no mail checks during an import (versions 0.5.150 and 0.5.151)

- 0.5.150 (the owner's instruction: only this change, no tests): the standard order of the message list starts with Flag, Unread, Attachment (`ui/columns.py`); saved column orders are not changed.
- 0.5.151: the owner asked what a scheduled check does while an import runs. Found: the checks ran at the same time; the import's list of the mail already in Local Folders is made once at its start, so a message a check stored meanwhile could be stored again by the import. `ui/importdialog.py` tells when an import starts and ends (`on_running`); `ui/mainwindow.py` checks and sends nothing meanwhile (timers, push, tray, Refresh), Refresh is off, and one check runs when the import is done (D-143).
- Tests: `tests/ui/test_ui_import.py`, 2 new (the window tells start and end; no check during the import, Refresh off, one check afterwards); both fail on the old code (checked). Run: import 14 passed; operations, push, tray, schedule 47 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 A Favorites view sorts by the chosen column (version 0.5.152)

- Reported from the Windows computer (0.5.151): in Favorites > Unread a click on a column header changed nothing; the groups stayed those of Received. Cause (confirmed): `MainWindow.view_spec` built the request of a Favorites view (every combined view: inboxes, unread, flagged, outbox, sent, drafts, junk, trash, tagged; also the Sorted by menu) without the sort order, so it was always Received, newest first. Fix: `ui/mainwindow.py` passes the chosen order and direction, as for a folder (D-095 already says that combined views follow the sort order).
- Tests: `tests/ui/test_ui_columns.py`, 1 new (a real mouse click on the Subject header in Favorites > Unread sorts A to Z with the groups, a second click Z to A; fails on the old code, checked). Also corrected: `test_the_list_is_a_table_by_default_with_the_standard_columns` still expected Unread as the first column; made wrong by 0.5.150 (pushed without tests at the owner's instruction), a test fault, not a program fault. Run: columns 22 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 A new sort order keeps the selected message (version 0.5.153)

- Reported from the Windows computer (0.5.152): select a message, click a column header: the message was no longer selected. Cause (confirmed): `_set_sort` reloads the list (a model reset) and nothing selected the message again. Fix (D-144): `ui/mainwindow.py` remembers the selected message before the new order and selects it again, scrolled into view; when it is on a page not loaded yet, `message.position` (new, `engine/commands_mail.py`, with `Index.position` in `index/index.py`) gives its place and `MessageListModel.load_position` (`ui/models.py`) loads that page; searches and Favorites views load their next batches; choosing another message meanwhile wins.
- Tests: `tests/test_engine_commands.py` (1 new: the position equals the place in message.list for five orders both ways, None outside the folder); `tests/ui/test_ui_columns.py` (4 new: a folder and Favorites > Unread keep the message and the reading pane matches; a message on a page not loaded yet, with one message per page; choosing another message meanwhile). All 5 fail on 0.5.152 (checked). Run: engine commands, command line, mailbox guard, index 233 passed; columns, models, operations, import 78 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The index is opened only inside its lock (version 0.5.154)

- Reported from the Windows computer (0.5.153; 87 of 89 engine command tests pass on Windows). 1: `test_key_status_export_and_restore_cycle` checks Unix permission bits, which Windows does not have: a test fault; the check now runs only where the system has them. 2: `test_commands_that_open_an_old_index_at_once_all_succeed` failed with a Windows sharing error: a real fault (confirmed in the code): `Engine.open_index` tried the index once outside its lock, so it could have the files open while another command replaced them during a rebuild. Fix: `engine/session.py` opens only inside the lock (D-145).
- Tests: `tests/test_engine_commands.py`, 1 new (every open of an index holds its lock; fails on 0.5.153, checked; the Windows refusal itself cannot be reproduced on Linux). Run: engine commands, settings changes, Local Folders, index rebuild, IMAP engine 104 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. To check on the Windows computer: `tests/test_engine_commands.py`.

## 2026-10-16 The engine reports every change of mail (version 0.5.155, step 1 of D-146)

- The principle and the design in six steps are in DECISIONS.md (D-146). Step 1, nothing visible changes yet: new `engine/changes.py` (the facts, `Change`; `MessageRow` moved here from `commands_mail.py` so the mailbox can describe a message), `engine/session.py` (`Engine.changed`, `report`, `mailbox_changed`, `quietly`, `quiet_command`), `engine/mailbox.py` (every operation tells its fact after it succeeded; Empty folder and Mark all as read one fact each), `engine/commands_mail.py` (a mail check and mail.settle are quiet and tell "mailbox changed" only when the sync changed something here), `engine/commands_folders.py` (a rebuilt or repaired index), `engine/commands_emclient.py` (the import, per batch), `engine/commands_local.py` (moving mail to or from Local Folders; the changelist said `engine/local.py`, the command lives here), `ui/backend.py` (`changed`).
- Tests: `tests/test_engine_changes.py`, 8 new (a check is one fact and nothing when nothing is new; marks; four quick deletes are four facts in order, each about its own message; copy and tags; folder actions one fact each; a rebuilt index; an import per batch; IMAP tells only after the server confirmed and nothing on a refusal). On the old code they cannot load (the facts are new). Run: every engine test file that goes through the mailbox, plus import, guard, settings, command line, skip-known (33 files): 414 passed, 1 failed (`tests/test_engine_registry.py::test_destructive_commands_are_marked`: its list lacks tag.delete, destructive since 0.5.134; fails the same on 0.5.154, a test fault, not changed); backend, operations, import window, columns, tags 74 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The message list applies the facts (version 0.5.156, step 2 of D-146)

- `ui/models.py`: `MessageListModel.apply(change)` applies each fact to its own rows: marks and tags change one row in place; a removed or moved-away message takes out its row (its heading follows; an emptied heading goes) with a row removal, not a reset, so the selection stays; a message moved or added into the view is put at its place under its heading, asked from the engine (`message.locate` in `engine/commands_mail.py`, `message.combined-locate` in `engine/commands_combined.py`; two commands where the changelist named one, because Favorites have their own parameters); folder read, emptied and renamed. A Favorites view decides by the folder role in the fact (`Change.role`, new, `engine/changes.py` and `engine/mailbox.py`) whether a moved message belongs in it, so a delete in All Inboxes or Unread asks the engine nothing. Folders keep their rows as one ordered list with empty places instead of fixed pages, so a removal or insertion shifts what follows; an answer asked before a change that moved places is asked again. A message that goes while it was not loaded: only the parts after the first part not loaded are let go, and the heading counts are asked again (counts only). Not yet wired into the window (step 3): nothing visible changes.
- A: Run rules on a folder, Rename a tag everywhere and Delete a tag everywhere are big jobs: one "mailbox changed" at their end (`commands_rules.py`, `commands_pim.py`, `commands_tags.py`); `tests/test_engine_registry.py` lists tag.delete as destructive (missing since 0.5.134).
- B: `engine/mailbox.py` refuses an action that names a message in a folder where it no longer is ("This message is no longer in Inbox; it is in Trash"), also when reading its marks first; before, a second delete of a message already moved to Trash removed its index line and hid it (the Windows computer's finding a), and a mark gave "Message not found" with the false "Messages missing" warning (finding d).
- Tests: `tests/ui/test_ui_list_changes.py` (10 new: a delete takes out one row and asks nothing; a mark; a message moved into the folder shown comes in at its place; headings follow and an emptied one goes; Favorites > Unread does not take a deleted message back; a search keeps a moved message at its new place; folder read, emptied, renamed; a late answer is asked again; 3,000 messages: a delete asks nothing; a message that was not loaded goes without a reset); `tests/test_engine_changes.py` (2 new: the bulk commands, the outdated folder; the second fails on 0.5.155, checked: the delete went through and hid the message). Run: 34 engine files through the mailbox 417 passed; list changes, models, columns, operations, import, tags, backend, drag and drop, schedule 132 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The window works by identity (version 0.5.157, step 3 of D-146)

- `ui/mainwindow.py`: the facts of the engine drive the list (`Backend.changed` to `MessageListModel.apply`); after delete, delete permanently, move (also dragging), copy, archive, unarchive, junk, not junk, snooze, cancel snooze, marks and tags the list is no longer fetched again. An action takes the messages selected at the key press; `_take_away` greys them (`MessageListModel.pending`, `PENDING_ROLE`, `ui/delegate.py` for the cards) and moves the selection and the reading pane at once to the next message without an action under way (after them, else before); `_each` lets a message's grey go when its action ended (the fact took the row out before) or failed (the error shows, the selection stays). The position-based `_keep_place_after_delete` and `_select_after_delete` are gone. `_after_change` fetches again only for big jobs (rules run on a folder, scheduled messages changed, a held-back folder settled). A new sort order finds the selected message by asking where it stands once (`message.position` for a folder, `message.locate` and `message.combined-locate` for a search or Favorites view, then `load_through`), instead of loading batch after batch. A message that loses its tag stays in a tag's list until the view is left, as a read message in Unread.
- Not yet: the Sent copy after sending is stored past the mailbox's operations (`sync/send.py`), so no fact reports it; after sending the Sent and Drafts views are still fetched again (`_after_sent`). The folder list's counts and the refetch after a mail check or import follow in step 4.
- Tests: `tests/ui/test_ui_rapid_actions.py` (new, the Windows computer's test as a project test: Favorites > Unread sorted by Subject with headings, four Delete key presses with pauses of 0, 200 and 1,500 ms on 16, 600 and 3,000 messages: each press acts on the message selected at that moment, the selection never empties, the four go to Trash in the order pressed, nothing else moves, the list is never reset, no "Messages missing"); all 9 fail on 0.5.156 (checked; 4 on the faults themselves, nothing selected or another message deleted). `tests/ui/test_ui_models.py`: two tests that expected a reset now require none. Run: rapid actions 9 passed (45 s); columns, list changes, models, drag and drop, schedule, tags, operations, import, shortcuts, push, reader, composer 203 passed; `tests/ui/test_ui_window.py` alone 62 passed; the 34 engine files 417 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The folder list follows the facts; outside changes once, one at a time (version 0.5.158, step 4 of D-146)

- `ui/mainwindow.py`: the folder list's numbers follow the facts (`_folder_fact`: per folder, read or unread ±1, removed, moved, added, folder read or emptied), and the Favorites, group and account numbers are worked out again from them (`FolderTreeModel.recount` in `ui/models.py`, `favorite_unread`), without asking the engine and without rebuilding the list. A folder made, renamed or deleted changes that one entry (`add_folder`, `rename_folder`, `remove_folder`; laid out again from what is known here only when a pin shows it); the folder on screen follows a rename, and a deleted one gives way to its account's Inbox. A "mailbox changed" (a mail check, an import, rules run, a tag renamed or deleted, a rebuilt or repaired index) counts only that mailbox's folders again (one `folder.list`, not one per account) and fetches the list again only when it shows that mailbox, keeping the selected message; one at a time, and once more afterwards for what came meanwhile. The separate reloads after a mail check, an import, sending, tags, rules and a settled folder are gone.
- Engine: `Mailbox.report_added` and `report_moved` (`engine/mailbox.py`): sending stores its copies past the mailbox's operations and reports them (`commands_mail.py` mail.send, `commands_outbox.py` Send All, `commands_schedule.py` scheduled and repeating messages). A made folder's fact carries its role (`changes.py`), so a new Archive folder is an archive at once. An IMAP check that made folders reports its mailbox as changed.
- Tests: `tests/ui/test_ui_folder_counts.py` (4 new: the numbers after a mark, a delete, mark all read and empty folder without counting again; a folder made, renamed and deleted as single entries; three outside changes give two fetches one after the other and keep the selection; sending moves the message from the Outbox to Sent without a reload); all 4 fail on 0.5.157 (checked). `tests/ui/test_ui_rapid_actions.py`: the Unread number afterwards equals the engine's. Run: the 34 engine files 417 passed; 21 window test files 274 passed, 2 failed: `test_ui_tree_rows.py::test_a_list_with_messages_that_is_reloaded_never_says_it_is_empty` (fails the same on 0.5.157: it expects the list to empty during a reload, which 0.5.139 changed; a test fault, not changed) and `test_ui_columns.py::test_a_click_on_a_column_header_sorts_a_favorites_view_with_its_groups` (passes alone three times and with its file: fails only in the long combined run, as the known grey-icon test; not changed); `tests/ui/test_ui_window.py` alone 62 passed; rapid actions and folder counts 13 passed. Pyrefly 0, layer check kept; not the full suite.
- Not changed (not in the changelist): pinning, renaming or moving a favorite and group changes still load the folder list again (`_sidebar_call`); the Settings page of scheduled messages still fetches the list again.

## 2026-10-17 Calendar, contacts and tasks follow the facts (version 0.5.159, step 5 of D-146)

- Engine: `pimstore/store.py` reports every change of an item or a collection right after it succeeded (a reporting function it is given; a move is one report); `engine/pimaccess.py` turns the reports into facts (`engine/changes.py`: item added, changed, removed, moved; collection made, edited, removed; the kind of collection in `role`, its name and colour in `name` and `colour`); a quiet big job tells "collection_changed" per account for calendars, task lists and address books and "mailbox_changed" for mail (`engine/session.py`). Big jobs: the sync with the server (`commands_dav.py`), importing contacts, events and tasks from a file (`commands_addressbook.py`, `commands_calendar.py`, `commands_tasklist.py`). `contact.list`, `task.list` and `event.list` can be asked for one item (`item`; `task.list` also gives its place, `TaskList.position`), so a section asks only for the item that changed (the changelist named this for `event.list`; contacts and tasks needed it too).
- Window: `ui/pimpages.py` routes the facts (by kind) to the panel and the section; a "collection_changed" fetches again what the section shows, once, one at a time. `ui/collectionpanel.py`: one entry made, renamed, recoloured or removed, the numbers of items follow, no reload after its own actions (its own updates no longer count as a tick, which reloaded the calendar). `ui/contactsview.py`: one contact in, out, changed or moved to its new place (a changed contact keeps its row and its selection); the selection moves at the key press of a delete or move; greyed while under way. `ui/tasksview.py`: one task in, out or changed (grouped again here, the selection, the current task and the scroll position kept); the same at the key press; greyed; the right side says "There are no tasks here" when the last one goes. `ui/calendarview.py`: only the blocks of the event that changed are replaced. The separate reloads after save, delete, move, tag, complete and collection changes are gone. Still fetched after a save: the tags list of Contacts (its numbers).
- Tests: `tests/ui/test_ui_pim_changes.py` (5 new: contacts, tasks, calendar, the panel, a sync once one at a time); all 5 fail on 0.5.158 (checked). `tests/ui/test_ui_window.py`: the delete test waits for its status text (since step 3 the row goes with the fact, a moment before the answer sets the text; it failed about one run in three). Run: engine files through the mailbox and the PIM tests 543 passed; contacts, tasks, calendar, sections, dav, the new tests, folder counts, list changes, tags, import, shortcuts 78 passed; `tests/ui/test_ui_window.py` alone 62 passed (the delete test 5 of 5 alone). Pyrefly 0, layer check kept; not the full suite.

## 2026-10-17 A new sort keeps the selection: the list is rearranged, never thrown away (version 0.5.160, D-147)

- Found by the owner: after a click on a column header the selected message was dark blue with a bright piece at the start of the row. Cause: a new sort reset the list (selection gone) and the window selected the message again by hand, one cell instead of the row; every action works on whole rows, so Delete did nothing after a sort until the message was clicked again. The same reset and reselect followed every fetch of the open folder after a change from outside.
- `ui/models.py`: `rearrange` (used for a new sort and by `reload`): the rows stay on screen until the headings, the first rows and the places of the selected messages are there, then one layout change moves Qt's selection with each message (`changePersistentIndexList`); a fact or another selection meanwhile asks again; a search or Favorites view loads down to the deepest selected message in one request; a selected message already known needs no page. `ui/mainwindow.py`: `_set_sort` rearranges; the reselect (`_reselect`, `_find_unloaded`, `_restore_row_selection`) is gone; the current message is scrolled into view after a rearrange.
- Engine: `message.places` and `message.combined-places` (several messages, the order worked out once); `Index.positions` (one pass over a folder); `message.position` and `message.places` share `_ordered_ids` for the snoozed and contact orders.
- Tests: `tests/ui/test_ui_columns.py` 5 new (whole row selected after a sort and Delete deletes it, folder and Favorites; several selected stay selected; a message chosen meanwhile wins; a refetch keeps the whole row and never resets) and the page-not-loaded test checks the whole row; all 6 fail on 0.5.159 (checked: the old `src/` put back, run, restored). Engine: `tests/test_engine_commands.py` and `tests/test_engine_local.py` one each; `tests/test_index_index.py` 12 (every sort order). Run: columns, list changes, rapid actions, tree rows, index, engine commands, engine local, registry, mailbox guard 278 passed, 1 failed (`test_ui_tree_rows.py::test_a_list_with_messages_that_is_reloaded_never_says_it_is_empty`, the known test fault since 0.5.139: it expects the list to empty during a reload); `tests/ui/test_ui_window.py` alone 62 passed; folder counts, reader, models, sections 51 passed. Pyrefly 0, layer check kept; not the full suite.
- Still open (D-147): the list does not know which message sits in a place it has not loaded; to be rebuilt in the code review.

## 2026-10-17 the Windows computer's review, quick items: one heading, one file (version 0.5.161, D-148)

- From the Windows computer's review of D-146 steps 1 to 5 (its rapid-Delete test: every Delete acted on the message selected, 3,000 messages in 0.7-0.8 s).
- `ui/models.py`: `header_rows_added(first, last)` for an inserted heading and a batch of more rows; a removal, a fold and a reset no longer announce the headings (a reset is handled by `modelReset`). `ui/mainwindow.py`: `_span_new_headers` spans only those; `_span_group_headers` (every heading) stays for a reset and a rearrange.
- `store/store.py`: `Store.flags(ref, hint)`, from the file name; `engine/mailbox.py`: `current_flags` uses it.
- Windows faults: `tests/test_engine_commands.py` closes the connection that makes an index old (`contextlib.closing`); `tests/test_engine_export.py` asks for the user id only off Windows; `config/running.py` tells the type check that the Linux part is not for Windows. `TRANSFER-WINDOWS.md`: `.venv-win`, running twice is prevented since 0.5.144 (3.3 and 9.3); `.gitignore`: `.venv-win/`. `STATUS.md`'s first line says it is pushed with its version.
- Tests: 3 new (a delete spans no heading again and the headings keep their bar; an open, a mark and a move never list the folder; `Store.flags`); all 3 fail on 0.5.160 (checked). Run: columns, list changes, rapid actions, tree rows, store, engine commands, export, running, mailbox guard, IMAP engine 198 passed, 2 skipped, 1 failed (the known `test_ui_tree_rows.py` test fault); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-17 What changes in a view stays in it (version 0.5.162, D-149)

- Point 5 of the Windows computer's review: the waiting lane holds only `mail.wait` (the IMAP push); not the cause. The cause: a message read while Favorites > Unread showed it vanished at the next sort or fetch, because only the window kept it.
- `index/model.py` `SearchFilter.stay`; `index/index.py` the conditions on unread, flagged and the filter's tag become one group, ORed with the messages that stay. `engine/commands_mail.py` `stay` (search, groups, locate, places); `engine/commands_combined.py` `stay_messages` and `stay_mailboxes` (Favorites, its groups, locate, places). `ui/models.py` `_stay` (a flag or tag fact on a shown row of such a view), sent in `_values`, emptied when the view is left.
- Tests: 4 new (the window: read in Favorites > Unread, it stays after a sort and a fetch, keeps its whole-row selection, and is gone when the view is opened again; the engine for a search and for Favorites; the index); all 4 fail without the change (checked with the code before 0.5.161). The run is in the 0.5.163 entry.

## 2026-10-17 A mail check tells what it changed (version 0.5.163, D-150)

- Point 2 of the Windows computer's review. `sync/imap_sync.py`: the report names each message removed here, each change of flags (with whether it was unread) or tags taken over, each message kept in Recovered. `engine/mailbox.py`: `report_removed`, `report_flags`, `report_tags`, `report_folder_made`, `report_folder_deleted`. `engine/commands_mail.py`: `mail_fetch` (POP3) tells each new message; `_report_sync` (IMAP) tells all of it, folders made first and gone last; `mail.fetch` and `mail.settle` are no longer quiet commands; `message.places` with `headings`; `engine/commands_combined.py` the same for `message.combined-places`.
- `ui/models.py`: `_bring_in` collects the facts of messages that came in and `_take_in` asks once (`message.places`, `headings`) and inserts them, the first place first; more than `BURST` (200) at once is one rearrange. `_take_out_unseen`: a message gone from a part not loaded, with headings or with loaded rows after it, plans one rearrange for the burst (`_plan_rearrange`) instead of a reset; found while writing it and fixed (not in the changelist, told to the owner): when every message of the folder is loaded and the gone message is not among them, the list no longer gets one row shorter. `_refresh_group_counts` and `_relayout` went (nothing used them any more).
- Tests: 4 new (a POP3 check and an IMAP check tell facts, never "mailbox changed"; three messages that come in at once are placed with one question; a message gone from a part not loaded under headings: one rearrange, no reset); all 4 fail without the change (checked with the code before 0.5.161). `test_ui_list_changes.py`: a message that moved into a search is placed with `message.places` (was `message.locate`).
- Run for 0.5.161 to 0.5.163 together: 21 engine, sync, index and store files 496 passed, 2 skipped; columns, list changes, rapid actions, tree rows 59 passed, 1 failed (the known `test_ui_tree_rows.py` test fault since 0.5.139); folder counts, reader, models, sections, push, operations 69 passed; import, backend 27 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-17 An answer describes exactly what the list knows (version 0.5.164, D-151)

- The owner's bug: the list was out of order after overlapping checks (from 0.5.163). The Windows computer's second review: 91 rapid-Delete runs all correct; 4 Deletes on 3,000 messages settle in 127-136 ms at gap 0 (700 ms in 0.5.159); points 3 to 6 below.
- Engine: `session.py` `place_lock`, `is_quiet`; `mailbox.py` the place lock from `_index` to `_tell`, `let_go`, `reading`, `changing`; `sync/announce.py` (new) `Announcer` and `SILENT`; `sync/pop3_fetch.py` and `sync/imap_sync.py` tell each change inside `changing()` with its index write (the per-message lists of 0.5.163 in the IMAP report are gone); `commands_mail.py` `MailboxAnnouncer`, `_report_sync` tells only the folders gone, list/position/places/search/groups read under the lock; `commands_combined.py` the same for Favorites and its groups.
- List (`ui/models.py`): `_mark` (rows that came or went, facts heard) replaces the count of rows in the checks of a page, a batch of arrivals and a rearrange; `_unplaced` (arrivals waiting or being placed): a page asked meanwhile waits in `_waiting_pages` and is asked again once they are placed. Every row returns `ItemNeverHasChildren`; the `index` override is gone (its one-argument callers pass the column: 6 in the window, the rest in the tests).
- Store: `_MESSAGE_LOCKS` (256 shared re-entrant locks) around finding and using a message file in read, flags, copy, set_flags, move and delete.
- Windows: `config/running.py` the Linux part in a block for "not Windows"; `tests/test_engine_export.py` the file mode only where there are mode bits, the read-only folder test skipped on Windows, the root check in a function; `tests/test_store_store.py` `raising=False` for `os.sync`. Pyrefly for Windows (`--python-platform win32`) 0, as for Linux.
- Tests: 8 new: the owner's case end to end (a check paused between two messages while another comes in; fails on 0.5.163: Message 3 above Message 35); a question waits while a change is written but not told; a POP3 check and an IMAP sync tell each change in one step with its index write; a read never finds a name a change of flags took away; message rows never have children (these 6 fail on 0.5.163, checked); an answer and a page asked before a fact are asked again (these 2 pass on 0.5.163 too: they guard the list's side). `tests/test_engine_changes.py`: the mail check test still expected one "mailbox changed" (it failed since 0.5.163, which did not run that file); now it expects one fact per message.
- Run: 21 engine, sync, index and store files 500 passed, 2 skipped; changes, actions, activity, conversations, drafts, search, watch, compose, send, store interruption and tags 138 passed (after the test above was brought up to date); columns, list changes, rapid actions, tree rows 63 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); folder counts, reader, models, sections, push, operations, preferences 96 passed; shortcuts, export, smoke, tags, drag and drop, schedule, import, backend 75 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- For the big review: does the list need Qt's tree view (it lays out every row after a change, in C++), or can a flat view draw the headings?

## 2026-10-17 An answer counts only what the list has heard (version 0.5.165, D-152)

- The Windows computer's report on 0.5.164: rapid Delete correct (3,000 messages, gap 0: 100-316 ms); the lock per message file holds (722 opens during 703 mark changes, no errors); live Kerio IMAP 9 passed. Found: the list empty while checks bring mail (point 2), snoozing several at once "Access is denied" on Windows (3), answers that never come in a window test on Windows (4), the reading pane showing the wrong message (5.1), two test faults (5.4, 5.5).
- Engine: `changes.py` `Change.sequence`; `session.py` `_tell` numbers and tells (one at a time), `arrived_after`, `REMEMBERED_ARRIVALS`; `commands_mail.py` `not_heard`, `leave_out`, `message.places` with `heard` and `known`; `commands_combined.py` the same for Favorites. `schedule.py` `SNOOZED_FILE_LOCK` around reading and replacing snoozed.json (only the file: nothing else is waited for while it is held, so it cannot deadlock with a mailbox lock).
- List (`ui/models.py`): `_seen` (the number of the last fact per mailbox, from every fact), sent as `heard`; `_placing`: one question at a time, what came meanwhile goes into the next; an arrival no longer counts in `_mark`; a rearrange or a new order does not drop arrivals on their way (they are placed in it or found there); `known` false gives one rearrange. Window: going back to the message on screen calls off the one still opening.
- Point 4 not found: three full runs of `tests/ui/test_ui_window.py` here pass; queued tasks are not collected by Python (5,000 queued, all ran). The Windows computer checks with a probe whether the two commands started at all.
- Sending to a server whose certificate is not trusted (point 5.5): from the code, the composer shows "The message was not sent" with the reason and stays open; the live test waits for it to close, so it waited its 30 s. [Speculation] the send failed at once on the certificate; not run here.
- Tests: 4 new (the Windows computer's case: 300 messages from two checks at the same moment are placed while they come and all are there, in the engine's order, at once after, with fewer than 100 questions; places count only what was heard and facts are numbered one after the other; reading the snooze file waits while it is replaced; going back to the message on screen); all 4 fail on 0.5.164 (checked; the snooze test file does not load there). `tests/test_engine_changes.py` compares facts without their numbers; `tests/test_store_store.py` the fsync count on Windows; `tests/live/test_live_ui.py` the certificate fingerprint.
- Run: 21 engine, sync, index and store files 517 passed (after the fact comparisons above); PIM, calendar, contacts, dav, recurring, search, conversations, activity, compose, store interruption and tags 140 passed; columns, list changes, rapid actions, tree rows 65 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); folder counts, PIM changes, reader, models, sections, push, operations, schedule, tags 101 passed; preferences, shortcuts, backend, import 62 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 Sort and group by what the column shows (version 0.5.166, D-153)

- `core/names.py` (new) `shown_name`; `ui/formatting.py` `display_name` uses it. `index/schema.py` format 8: `sender_name` and its index (`messages_by_sender` dropped), `UPGRADE_7_TO_8`; `index/index.py` stores it, sorts and groups by it, gives the connection the function `shown_name` for the upgrade, upgrades 6 to 7 to 8. `engine/commands_combined.py` sorts a Favorites view by sender with `shown_name`.
- Tests: 3 new (the index groups and sorts by the shown name, a quoted name under its name; format 7 upgraded in place, both messages kept under one heading, the old index gone; the list: one heading for one name from two addresses); all 3 fail on 0.5.165 (checked). Brought up to date: `tests/test_index_index.py` (groups by sender compared by the shown name), `tests/test_engine_search.py` (group names), `tests/test_index_tags.py` (format 6 goes on to 8).
- Run: index, engine local, search, commands, tags, changes, IMAP and POP3 sync, conversations, rules 391 passed; columns, list changes, models, tree rows 82 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); reader, folder counts, rapid actions, contacts 36 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- Not measured: how long the upgrade to format 8 takes on the owner's 140,000 messages (one UPDATE over all rows at the first start).

## 2026-10-17 Every toolbar folds into More (version 0.5.167, D-154)

- `ui/toolbar.py` (new): `FoldingToolbar`, `ToolbarRow` (units, states, `arrange`, More and its menu, `minimumSizeHint`), `view_button`. `ui/searchfield.py`: `ScopeButton` (with its two measured widths), `SearchField` with `hint`, `focused_hint`, `scope`, `preferred_width`, `minimum_width`, `set_compact`. `ui/mainwindow.py`: the mail toolbar on `FoldingToolbar`, `_toolbars`, `button_for` instead of `widgetForAction`, the scope's `changed`, `current_data`, `set_current_index`. `ui/sections.py`, `ui/pimpages.py`: a section's toolbar is a `FoldingToolbar` made with the window as parent. `ui/calendarview.py`, `ui/contactsview.py`, `ui/tasksview.py`: views with icons and the merged button, Tag and Move to with icons, Import and Export in More, Mail's search field. `ui/theme.py`: `QToolBar[folding="true"]` rules, the view line, the scope button. Seven Breeze icons copied.
- Tests: `tests/ui/test_ui_toolbar.py` 11 new (More only when needed; This folder first, then right-most first, until all are in More; the search field's width; the same state whichever way the window is resized; More after the last button shown; More's entries greyed out as they are, with their menus; Contacts' entries that live in More; the mail window at its narrowest keeps Menu and the search field and the folded actions keep their keys; the calendar views merge before Delete folds and the merged button shows the chosen view; icons in Contacts and Tasks; a section's search field). On 0.5.166 the test file cannot load (no toolbar module); the behaviour itself was checked there: at the narrowest window the search field was hidden (now it is shown). Brought up to date: `test_ui_theme.py`, `test_ui_schedule.py`, `test_ui_appearance.py`, `test_ui_window.py`.
- Run: theme, schedule, appearance, search field, search help, sections, calendar, contacts, tasks, shortcuts, PIM changes, advanced search, smoke, toolbar, layers 138 passed, 1 failed (`test_ui_theme.py::test_the_selection_is_blue_with_the_focus_and_grey_without`: it fails on an untouched 0.5.166 too, cause not looked into); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- Seen while checking: the main window cannot be made narrower than 794 px (its other panes), so in Mail the search field never needs to shrink below its preferred width at the standard size.

## 2026-10-17 Narrower: the sections' search field and the sidebar (version 0.5.168, D-155)

- `ui/searchfield.py`: a field without This folder is Mail's width minus that part; `ScopeButton.measure` polishes first, so a hidden button measures with the window's style. `ui/calendarview.py`: the month calendar's minimum width is 240.
- Measured at the standard size in the test setup: the window's minimum went from 794 to 768 px (sidebar 266 to 240); the sections' search field is 256 px (Mail's 360 minus 104).
- Tests: 2 new in `tests/ui/test_ui_toolbar.py`; both fail without the change (checked by taking the two changes out). Run: toolbar, search field, calendar, contacts, tasks, sections, theme, appearance, layers 97 passed, 1 failed (the known `test_ui_theme.py` selection test, failing on 0.5.166 too); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 The month calendar shows all seven days at 240 px (version 0.5.169, D-155)

- The owner's report on 0.5.168: the small month calendar cut off its right-hand days. Cause: its day columns kept Qt's minimum for a column heading (38 px at 9 points, measured on the owner's display), so 7 columns needed 266 px; 0.5.168 only made the outside 240 px. The 0.5.168 test checked the outside width only.
- `ui/calendarview.py`: `RangeCalendar._fit_columns` (minimum of a day column: the width of "00" plus `DAY_MARGIN`, 6 px), again at a change of font or style.
- Tests: 1 new (at 240 px, at 9 and 11 points, all seven columns fit and nothing scrolls sideways); it fails without the fix (checked). The 0.5.168 test no longer expects the calendar's own wish to be above 240 (it is 166 now). Run: toolbar, calendar, sections, layers 27 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 One toolbar for the window (version 0.5.170, D-156)

- The owner's report on 0.5.169: Calendar at its narrowest, then Mail: the window grew from 768 to 794 px. Reproduced in a test (768, then 794 at the switch); 794 = 2 × 397, two toolbars side by side for the moment between showing Mail's and hiding Calendar's.
- `ui/toolbar.py`: `WindowToolbar` (a stack of rows, `add_row`, `show_row`, icon size and size factor for every row) replaces `FoldingToolbar`; `ToolbarRow` keeps the toolbar's icon size for buttons added later and does its own scaling. `ui/mainwindow.py`: `window_toolbar`; `toolbar` is Mail's row; `switch_section` shows the row in one step. `ui/sections.py`: a section's `toolbar` is a `ToolbarRow`.
- Tests: 1 new (from Calendar at its narrowest, Mail, Contacts, Tasks, Calendar and Mail keep the width); the same steps gave 768 then 794 on 0.5.169 in the reproduction above. Brought up to date: `test_ui_theme.py` (one toolbar, a row per section), `test_ui_sections.py`, `test_ui_toolbar.py`.
- Run: toolbar, sections, theme 33 passed, 1 failed (the known selection test); schedule, appearance, search field, search help, calendar, contacts, tasks, shortcuts, PIM changes, smoke, layers 103 passed, 1 failed in the first run (`test_ui_schedule.py::test_the_snooze_button_offers_ready_made_times`; it passed alone three times, with its file, and in a second run of the same set; cause not known, it waits for a snooze through the engine); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 The first public release, and rule actions that move (versions 1.0.170 and 1.0.171, D-157)

- 1.0.170: the owner moved the version to 1.0 (only the version changed) and made it the first public release: the .deb (193 MB) and .rpm (198 MB) built in the Ubuntu 22.04 container, installed and started as a normal user on bare Ubuntu 22.04 and Fedora 43 (still running after 8 s), both say 1.0.170, attached to the GitHub release (latest, not a pre-release). The Windows setup.exe is still to come from the Windows computer.
- 1.0.171: `ui/ruleedit.py` `ActionRow.up_button`, `down_button`; `RuleEditDialog.move_action`, `_update_moves`. Breeze's `arrow-up` and `arrow-down` copied.
- Tests: 2 new. `test_ui_rules.py` (moving up and down, the saved order, the first and last buttons greyed out, also after a remove) fails on 1.0.170 (checked); `test_engine_rules.py` (actions after Stop are skipped) passes on 1.0.170 too: it guards the behaviour the editor now relies on. Run: rules editor, rule engine, rules file 80 passed; `tests/ui/test_ui_window.py` and the icon tests 69 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 Every colour our own, in one file (version 1.1.172, D-158)

- `core/colours.py` (new): `Theme`, `DARK`, `LIGHT`, `THEMES`, `ROLES`, and the colours outside a theme. `ui/colours.py`: `palette_of`, `menu_palette_of`, `theme`, `css`; no colour of its own. `ui/appearance.py`: the theme's palette whatever the choice, `system_is_dark`, Fusion always, no style setting (`style_to_use`, `style_given` gone; `start` no longer takes the command line). Colours moved out of `theme.py`, `delegate.py` (and the new line under a heading), `calendarwidgets.py`, `tasksview.py`, `contactsview.py`, `contactdetail.py`, `messagelistview.py`, `operations.py`, `tagchips.py`, `tagsdialog.py`, `collectionpanel.py`, `reader.py`, `formatting.py`, `mainwindow.py`, `engine/commands_tags.py`, `engine/emclient_source.py`, `engine/pimaccess.py`. Settings > Appearance: the Style choice is gone.
- The dark values were read from the program's palette on the owner's KDE (Breeze Dark) and the light ones measured in the owner's screenshot.
- Tests: 9 new in `test_ui_appearance.py` (both themes complete; the heading stands out and has its line, dark and light; the dark theme is what Linux showed; Fusion and the whole theme for System, Light and Dark; System follows a switch; no colour outside the colour file); the 5 that test the program fail on 1.0.171 (checked; the other 4 check the new file itself). Brought up to date: the old style and blue tests, `test_ui_theme.py`.
- Run: appearance, theme 49 passed, 1 failed (the known selection test; its cause found: since 0.5.150 the first columns are icons, so the spot it reads is the white unread mark, a test fault); PIM, tags, layers, calendar, columns, contacts, drag and drop, models, operations, preferences, reader, settings, smoke, tags, tasks, toolbar, rules 206 passed, 1 failed (see below); importer 46 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- Found, not changed (the same on 1.0.171): `test_ui_toolbar.py::test_showing_another_section_keeps_the_width_of_the_narrowest_window` fails when the window is shown in Calendar before Mail was ever laid out: Calendar's narrowest is then 652 px and Mail makes it 768. The main area's minimum is stale while Mail is not shown (Qt does not pass on size changes of a hidden part); cause still to be pinned down.

## 2026-10-17 One minimum width for the window (version 1.1.173, D-159)

- `ui/mainwindow.py`: `MINIMUM_WIDTH` (768), `_fix_minimum_width` (768 or what the freshly measured contents need), run once the size factors are applied. Measured: 768 px at 0.75 and 1.00, 817 at 1.50, 926 at 2.00, back to 768 at 1.00; the same in all four sections.
- Tests: 2 new in `test_ui_toolbar.py` (768 px in Calendar and Mail; one minimum for every section at 2.00 and back at 1.00). Run after the Settings tests, the first one and the 0.5.170 test fail on 1.1.172 (twice, checked) and pass now (twice); the second passes on 1.1.172 too (it guards the new rule). Run: toolbar, settings, appearance, sections 69 passed; `tests/ui/test_ui_window.py` with the toolbar tests 79 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.


## 2026-10-17 Actions below Stop greyed out; Change… is Edit… (version 1.1.174, D-160)

- `ui/ruleedit.py`: `ActionRow.set_skipped` (the palette's Disabled text colours on the action's choice, folder and tag), `RuleEditDialog._update_skipped` after every add, move, remove and change of an action. `ui/rulespanel.py` and `ui/settingsdialog.py`: the button text Edit….
- Tests: 1 new in `test_ui_rules.py` (greyed below Stop, still editable, follows add, move, a change to Stop and a remove), two checks of the button text in `test_ui_rules.py` and `test_ui_settings.py`; all three fail on 1.1.173 and pass now. Run: `test_ui_rules.py`, `test_ui_settings.py` 26 passed, the colour-file test passed; Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 The tests leave git; .bat files ignored (versions 1.1.175 and 1.1.176, D-161)

- 1.1.175: `.gitignore` has `*.bat` (no such file existed).
- 1.1.176: `.gitignore` has `tests/`; `git rm -r --cached tests` (179 files no longer tracked, still on disk). `TRANSFER-WINDOWS.md`: the tests are not in git, and how the Windows computer keeps its copy through the first pull.
- Checked in throwaway repositories: a pull deletes files the new version stops tracking, also when they are ignored there; neither a local untrack nor skip-worktree prevents it. No program code changed, so no tests were run.

## 2026-10-17 A private script for the Linux installers (version 1.1.177)

- `build-release.sh` (project folder, git-ignored): refuses unless the folder is exactly on the pushed tag of the version, runs `packaging/package_linux.sh --build`, starts the .deb in Ubuntu 22.04 and the .rpm in Fedora 43 (must still run after 8 s), then asks before `packaging/upload_release.sh`. `.gitignore` lists it. No program code changed, so no tests were run.

## 2026-10-17 A private push script (version 1.1.178)

- `gitpush.sh` (project folder, git-ignored): refuses a message with `( ) & | < > ^ %`, a version that already has a tag, or a private path that is not git-ignored; shows the changes and asks; runs `release`; then checks on GitHub the branch, the tag, the author, no Claude in the message and no private path. `.gitignore` lists it. No program code changed, so no tests were run.

## 2026-10-17 gitpush updates the last version without a new version (version 1.1.179)

- `gitpush.sh`: without a message and with the version already tagged it runs `release` in its update mode (amend, tag moved, force-push), after saying so and asking; with a message and the version tagged it refuses; a new version without a message is refused; nothing changed is refused. The release tool was read to confirm it ignores the message in update mode. No program code changed, so no tests were run.

## 2026-10-18 The composer's From row (version 1.1.180)

- `ui/composer.py`: the account choice is the first row of the fields, "From", above To; the top row keeps Send, Send later… and Save draft at the left. STATUS.md backlog: opening .eml files.
- Tests: `test_ui_composer.py` checks the From row above To; `test_ui_theme.py`'s composer layout test follows the new place (both fail on 1.1.179, pass now). Run: `test_ui_composer.py`, `test_ui_theme.py`, `test_ui_preferences.py`: 94 passed, 1 failed (the known selection-colour test fault since 0.5.150). Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-18 DECISIONS.md cut to the current rules (documents only, update of 1.1.180)

- `DECISIONS.md` rewritten by topic (D-162): every number D-001 to D-162 kept (code and tests cite them), each the rule as it stands now with its reason in a line; replaced decisions are one line pointing to their successor. 203 KB → 41 KB (80% less, about 65,000 → 10,000 tokens at every session start).
- `DECISIONS-ARCHIVE.md`: the full old text, unchanged, with a header; searched by number, never read whole.
- `AGENTS.md` file table and the private start prompt `PROMPT-NEW-SESSION.md` point to both files. `STATUS.md` updated. No program code, no tests run (none touched).

## 2026-10-18 The start-up documents cut down (documents only, update of 1.1.180)

- `STATUS.md` holds only the current state (38 KB → 12 KB): "Where we are" in eight lines instead of a paragraph per version since 0.0.73; "Next task" without the finished steps; two problems that were history and the old `release` line removed. The old text, unchanged, is in `STATUS-ARCHIVE.md` (git-ignored, search only).
- `ARCHITECTURE.md` describes the design as it stands (D-163): the "to be decided" notes replaced by what was decided (import-linter, the `!` separator, the live tests), the IMAP section server-first (D-117) with the comparison as the offline fallback, EXAMINE, the item store as D-072, tags as IMAP keywords done, `pim` in the layer table, a section on the window (D-146, D-158). The whole old file is in `ARCHITECTURE-ARCHIVE.md` (git-ignored).
- `PY-DEVELOPMENT-RULES.md`: `DEVELOPMENT-RULES.md` without JavaScript, Home Assistant YAML, other languages and chat downloads; Rule 5 gives `./gitpush.sh`; the token estimate counts every document read, from the first reply. `DEVELOPMENT-RULES.md` unchanged. `AGENTS.md` and the private start prompt point to it and to the archives; `.gitignore` has both archives; D-163.
- About 11,000 tokens less at every session start. No program code, no tests run (none touched).

## 2026-10-19 F1 opens About; Tags after Favorites (1.1.181)

- `ui/mainwindow.py`: About Chrono Mail has F1 as its standard key (changeable in Settings > Keyboard; Standard keys sets it back).
- `ui/models.py`: the folder list shows Tags right after Favorites, before the account groups, the accounts and Local Folders (it came last).
- Tests: `test_ui_shortcuts.py` has About = F1 in the standard keys; `test_ui_tags.py` has `test_the_tags_section_comes_right_after_favorites`. Not run yet, on the owner's instruction: the tests, Pyrefly and the layer check run once after the next changes.

## 2026-10-19 The Settings window: Favorites, Tags, Operations window, accent colour, startup tree (1.1.182)

- Page order: General, Appearance, Message list, Favorites, Reading, Writing, Accounts, Rules, Tags, Scheduled messages, Operations window, Security and storage, Keyboard (D-166).
- Operations window: the three tick boxes moved off General to their own page; the settings keys are unchanged.
- Appearance > Accent colour (D-164): a swatch (colour picker), a #RRGGBB field (a wrong value gets a red note, the colour stays) and Blue (the theme's own blue). Each theme has its own accent (`accent_dark`, `accent_light`); the page edits the one in use. `core/colours.py` `with_accent` puts it in place of the blue (the blue, every role holding it, the Accent role, white or black text by contrast); `ui/colours.py` `set_accents`/`theme_of`; `ui/appearance.py` applies them.
- Favorites (`ui/favoritespage.py`): a tick per standard entry (off = hidden, `favorite.show`) and per pinned folder (off = removed, `favorite.unpin`); only changed ticks are sent after OK, then the folder list loads once.
- Tags (D-165): `TagsDialog` became `TagsPanel`, the Tags page; no Save & Close. OK sends the changes only when something changed and closes after they are saved; a failure keeps the window on Tags with the error and reloads the tags as saved, so OK never sends a step twice. Menu > Tags opens Settings on Tags.
- Show on startup (`ui/startupchooser.py`): a drop-down whose list is a folded tree, at most 15 rows; headings (Favorites, Tags, accounts, groups) only unfold; it opens down to the current choice. Favorites and Tags are now headings with their entries below them (the Tags views were listed as "Favorites <tag>" before). The saved value is unchanged.
- Tests written, not run (the owner's choice: one run after the next changes): `test_ui_operations.py` (page order, boxes on their own page), `test_ui_appearance.py` (with_accent, an accent per theme, the field), `test_ui_preferences.py` (startup tree values, the Favorites page), `test_ui_startup_view.py` (Favorites as a heading, the folded tree), `test_ui_tags.py` (TagsPanel, Menu > Tags opens the page and OK saves).

## 2026-10-19 Tags page and accent row fixed after the owner's test (1.1.183)

- Tags page: Add, Edit, Delete, Up and Down in a column at the right of the list, as on the Accounts and Rules pages; the tabs above the list; the page keeps the normal margins (1.1.182 copied the window's top button bar).
- Accent colour: below the size sliders (after a blank line), no explanation line; a swatch 72 x 24 px (1.5 times as wide) with square corners, and Reset (was Blue); the #RRGGBB field is only the colour picker's own.
- Found in the owner's test of 1.1.182, not fixed yet: the Show on startup picker blocks the whole desktop, does not close on a click outside it or on the field, and has no border. A new design is proposed.
- Tests updated, not run (the owner's choice): `test_ui_appearance.py` (swatch, Reset).

## 2026-10-19 The Show on startup picker rebuilt; the accent swatch square (1.1.184)

- Cause of the 1.1.182 picker faults: it was a popup window of our own (the tree view itself as the popup). While open, all input of the desktop went to it, and a click outside it never closed it, so it held the whole desktop until a choice was made; it had no frame. [Speculation, not checked in Qt's source] a view handles mouse presses itself and skips the step by which a popup closes on a click outside.
- Now the tree is the list of the drop-down's own popup (`QComboBox.setView`): Qt closes it on a click outside, on the field and on Escape, and the old choice stays. `combobox-popup: 0` keeps it a list under the field with at most 15 rows; a 1 px border; it is as high as the whole tree unfolded (at most 15 rows), so unfolding a heading does not make it scroll sooner than needed. A heading is not selectable, so the popup stays open when it is clicked; a click on it or its arrow unfolds it. The shadow is the desktop's (KDE draws one for drop-down lists), not the program's.
- Accent swatch: square, 72 x 72 px (1.5 times the old width), with the buttons' 4 px corners.
- Tests updated, not run (the owner's choice): `test_ui_startup_view.py` (the popup is the drop-down's own; heading unfolds; Escape and an outside click keep the choice), `test_ui_appearance.py` (square swatch).

## 2026-10-19 The accent row as the owner's mockup (1.1.185)

- Settings > Appearance > Accent colour: a button "Sample" filled with the accent, its text white or black by contrast (`text_on_accent`, as everywhere on the accent), the buttons' 4 px corners, as wide as Small and the slider of the size rows (an unseen "Large" keeps the room), so Reset lines up under the other Reset buttons. A click opens the colour picker. Replaces the square swatch of 1.1.184.
- Test updated, not run (the owner's choice): `test_ui_appearance.py`.

## 2026-10-19 Tags page: the tabs as wide as the list (1.1.186)

- `ui/tagsdialog.py`: the page is a grid; the tab bar sits above the list in the same column, and its four tabs share that width (`setExpanding`); the buttons are in the second column beside the list. Before, Tasks reached over the buttons.
- Test added, not run (the owner's choice): `test_ui_tags.py::test_the_tabs_of_the_tags_page_are_as_wide_as_the_list`.

## 2026-10-19 Tags page: Name and Shortcut always fit (1.1.187)

- `ui/tagsdialog.py`: the list has no horizontal scroll bar; Shortcut fills what Name leaves. Name starts at half the width and keeps its share when the list gets wider or narrower; a drag of the line sets a new share, remembered in the settings (`tags_name_share`, between 5% and 95%). Neither column gets smaller than 50 px (times the size of the other windows); a drag past that is brought back to the most that fits. Before, Name was fixed at 380 px and hid Shortcut.
- `ui/settingsdialog.py` gives the page the settings.
- Test added, not run (the owner's choice): `test_ui_tags.py::test_name_and_shortcut_always_fit_and_the_dragged_width_is_remembered`.

## 2026-10-19 Standard folders together; Empty Junk folder and Empty Trash (1.1.188)

- `ui/models.py`: in every account and Local Folders the standard folders stay together under Unread and Flagged, in the order Inbox, Drafts, Sent, Outbox, Archive, Junk, Trash; the person's own folders follow by name. Before, Junk and Trash came after the own folders and Outbox sorted among them by name. The same order is used when one folder is added (`_place_in`).
- `ui/mainwindow.py`: the Empty item (folder right-click menu and the Folder menu) says "Empty Junk folder…" or "Empty Trash…" for the folder selected; elsewhere it stays "Empty folder…" (greyed).
- Tests added, not run (the owner's choice): `test_ui_tree_rows.py`.

## 2026-10-19 New folder only where it can work (1.1.189)

- Cause: the folder list's right-click menu offered New folder on every row; on a row that belongs to no single account (a Favorites entry, a tag view, the Favorites or Tags heading, a group) the program had no account to make it in and did nothing, silently.
- `ui/mainwindow.py`: `_can_make_folder` (an account row, one of its folders, its Unread or Flagged, a pinned folder); the menu offers New folder only there, and the Folder menu greys it elsewhere.
- No test written or run (the owner: implementation only).

## 2026-10-19 A group always holds an account (1.1.190)

- Cause: "New group…" in the menu of Favorites, the headings and the empty area made a group without accounts; the folder list shows a group only when it holds an account, so the group was saved but never seen. Taking the last account out of a group left the same invisible group.
- `ui/mainwindow.py`: New group… only in an account's Put in group menu (the group starts with that account); the folder list shows no menu where nothing is offered.
- `config/sidebar.py`: a group whose last account leaves (moved, taken out, account removed) is removed; an empty group in `sidebar.json` is dropped when it is read (the owner's "Test" goes at the next start). `engine/commands_sidebar.py`: `group.add` needs at least one account.
- Tests written, not run (the owner's choice): `test_engine_sidebar.py`.

## 2026-10-20 Settings > Sidebar; three new Favorites; one blue (1.1.191)

- One blue: the light theme uses the dark theme's #2a6fc4 (the owner's choice). Text on the accent is white unless white's contrast on it is below 3:1 (`WHITE_TEXT_LEAST_CONTRAST`); before, the larger contrast won and the light blue got black text.
- Engine: `message.combined` views snoozed (every Snoozed folder), unreplied (Inbox mail without the Replied mark; `SearchFilter.replied`; a message replied while shown stays until the view is left, as with unread) and archive (every Archive folder).
- Folder list: Favorites in the owner's order (All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Snoozed, Unreplied, Archive, Junk, Trash); Snoozed and Archive only once a mailbox has that folder (`USED_ONLY`, `favorite_in_use`). Icons: clock, reply.
- Settings > Sidebar (`ui/sidebarpage.py`, replaces `ui/favoritespage.py` and the three Show switches of the Message list page): Favorites with its entries (those in use) and pinned folders, Tags, Local Folders, one tick per account with its own tree (`sidebar.json` `hidden_trees`, `sidebar.show-tree`). What Show on startup chose is ticked and greyed (`startup_forces`; it follows the startup choice while Settings is open) and always shown in the folder list (`forced`, `force_favorites`, `force_tags`, `forced_trees`); the person's own ticks are what is saved. The last thing still shown is greyed. A group whose accounts are all hidden is hidden. At start, when nothing chosen can be opened: the first Inbox shown, else the first entry (`_first_visible`).
- Limit: Show on startup lists only what the folder list shows, so a hidden entry must be shown before it can be chosen.
- Tests written, not run (the owner's choice): `test_engine_local.py`, `test_engine_sidebar.py`, `test_ui_models.py`, `test_ui_window.py`, `test_ui_startup_view.py`, `test_ui_operations.py`, `test_ui_preferences.py`, `test_ui_appearance.py`.

## 2026-10-20 Show on startup works again: at most two builds at start (1.1.192)

- Cause: the folder list is rebuilt for every answer that comes in. At start the tags answered before the folders; that build had no Inbox to select, and the 1.1.191 fallback (the first entry) then selected Favorites > All Inboxes. When the folders came, the selection was kept and the startup choice ignored. The fault of building per answer is old; the fallback made it visible.
- Stopgap until the refactor (the folder list must take in each answer where it belongs): `ui/mainwindow.py` builds the folder list at start at most twice. Early, when the sidebar file and what the folder to open needs are in (that account's folders, or the tags for a tag view; nothing more for a Favorites entry): the folder to open is selected and its messages load; accounts not loaded yet are left out (`pending_trees`, `ui/models.py`); no fallback. Finally, when every answer is in: the selection kept, the fallback (last selected folder, first Inbox, first entry) only here. A tags answer during that round builds nothing of its own (`_tags_arrived`, `_folder_round`). Every build keeps the scroll position.
- Tests written, not run (the owner's choice): `test_ui_startup_view.py`.

## 2026-10-20 Test run of 1.1.181 to 1.1.192, and the faults it found (1.1.193)

- Run (related files only, never the full suite): `test_ui_shortcuts.py`, `test_ui_tags.py`, `test_ui_operations.py`, `test_ui_appearance.py`, `test_ui_preferences.py`, `test_ui_startup_view.py`, `test_ui_tree_rows.py`, `test_ui_models.py`, `test_ui_window.py`, `test_ui_settings.py`, `test_ui_folder_counts.py`, `test_ui_treestate.py`, `test_engine_sidebar.py`, `test_engine_local.py`. All pass except one old test fault (STATUS.md, Problems). Pyrefly 0 (Linux and Windows), layer check kept. The new tests of 1.1.191 to 1.1.193 fail on 1.1.190 and pass now.
- Faults found and fixed: (1) Tags page: a drag past the right edge left Shortcut at 100 px and a scroll range, because Qt never shrinks a stretched last column below its default size; both widths are now set by the program (Shortcut fixed, only the line between is dragged). (2) Show on startup list: a click on a heading's name did nothing, because the code asked the system where the pointer was; the tree handles its arrow itself, so every press on a heading now folds it. (3) The Settings window saved only the accent of the theme in use; it now keeps both settings. (4) `sidebar.show` forgot only the pins of a removed account; now also its hidden tree and group place. (5) Two callbacks typed for Pyrefly.
- Test faults fixed: the page list and the Favorites positions after 1.1.182 and 1.1.191, no New group on a favorite or the heading (1.1.190), the Outbox that exists already, the engine's fixed clock for a snooze. Added: the Sidebar page follows Show on startup while Settings is open, and Cancel changes nothing.
- Removed (the owner's decision): `test_ui_tree_rows.py::test_a_list_with_messages_that_is_reloaded_never_says_it_is_empty`. It expected the list to empty during a reload; since D-146 the list never empties for a reload, so the "no messages" text it guarded against cannot appear while messages exist.

## 2026-10-20 Settings: Sidebar page above Message list (1.1.194)

- `ui/settingsdialog.py`: the Sidebar page is added right after Appearance, so the order is General, Appearance, Sidebar, Message list, Reading, … (D-166). Pages are found by name, so nothing else depends on the order.
- Tests: the page order in `test_ui_preferences.py` and `test_ui_operations.py` updated; not run, Pyrefly and the layer check not run (the owner's choice).

## 2026-10-20 Show on startup lists hidden entries greyed (1.1.195)

- Cause: `_startup_choices` in `ui/mainwindow.py` walked the folder list as shown, so whatever Settings > Sidebar hides (Favorites entries, the Favorites or Tags heading, Local Folders, account trees) was missing.
- Now it builds the folder list once more with nothing hidden (a temporary `FolderTreeModel`, same pins, groups, tags) and walks that; an entry the shown list lacks is greyed (the palette's Disabled text) and cannot be chosen; a greyed heading still unfolds. A choice is now (text, value, can be chosen, depth, shown) (`ui/startupchooser.py`, `ui/settingsdialog.py`). D-121 changed.
- Tests: only the tuple unpacking in `test_ui_startup_view.py` adapted; nothing run, no Pyrefly or layer check (the owner's choice).
- Not changed: the list is made when Settings opens, so ticks changed on the Sidebar page show in it only the next time Settings opens.

## 2026-10-20 Senior refactor: audit round 1, the inventory (no version)

- `REFACTOR.md` made: 14 items (R-01 to R-14). Sweep of all 172 files for the signs of junior code; in depth: the window's folder list, view and counting, the message list model, the Tasks and Contacts lists, the error handling of the lower layers.
- Main findings: the engine–window border is untyped (about 590 reads of fields by name); `MainWindow` is 3,481 lines with 266 methods; the folder list rebuilds itself and the window counts folders itself; the message list holds rows in two ways; Tasks and Contacts still reset and reselect by hand (D-146 step 5 says done; to verify); a silent 5,000-item limit in Contacts, Tasks and Calendar; one command per message for bulk actions.
- The lower layers look sound. Round 2 still to do. No code changed.

## 2026-10-20 Senior refactor: audit round 2 (no version)

- `REFACTOR.md` grows to 18 items. New: R-15 Settings OK applies everything again (also rebuilds the folder list; a column change resets the list), R-16 every IMAP check fetches every folder's marks and scans the whole store once per folder, R-17 two account dialogs with the same fields, R-18 scheduled messages found by decrypting the whole Outbox every minute (an unreadable one silently never sent). Extended: R-03 (sidebar changes and Settings OK reload every folder list), R-07 (the Calendar is fine), R-10 (one error report per failed message).
- Sound: composer, reader, importer, CLI, calendar fact handling. No code changed.

## 2026-10-20 Senior refactor: items renumbered in the agreed order (no version)

- Order (the owner and the agent): bugs and quick checks, then what the user notices small to big, then invisible tidying; a dependency wins over size. The four phases are gone; the order is the numbers.
- Old to new: R-18→R-01, R-09→R-02, R-12→R-03, new R-04 (the D-146 step 5 check, split off from R-07), R-17→R-05, R-07→R-06, R-08→R-07, R-10→R-08, R-16→R-09, R-04→R-10, R-03→R-11, R-11→R-12, R-15→R-13, R-06→R-14, R-05→R-15, R-13→R-16, R-02→R-17, R-14→R-18, R-01→R-19. Each heading in REFACTOR.md keeps "(was R-xx)".

## 2026-10-20 R-01: scheduled messages found in the index (1.1.196)

- Cause: `mail.send-due` (every minute) decrypted and parsed every Outbox message of every mailbox twice (`_scheduled` and `_patterns`); a scheduled message that could not be read was skipped without a word (never sent, nobody told); a repeating message whose pattern could not be read had its schedule deleted from `recurring.json` as if it had been removed by hand.
- Now (D-169): index format 9 keeps `send_at` (from the X-Chrono-Mail-Send-At header; 0 for a message that could not be read), with a partial index. Format 8 is upgraded in place; `Engine.open_index` then fills in the Outbox once (`sync.fill_send_times`, meta key `send_times_pending`). The minute check reads `Index.scheduled()` and opens only due messages; patterns are opened by their id; a read failure is told once per run (`Engine.first_time`) and never ends a schedule; only a pattern gone from the Outbox (MessageNotFoundError) ends it. `mime.send_time` is the one reader of the header.
- Tests: new `test_engine_schedule.py` (unreadable told once and never sent; only due messages opened; an index from before send times finds them), `test_engine_recurring.py` (unreadable pattern keeps its schedule; deleted by hand ends it), `test_index_index.py` (scheduled order; format 8 upgrade), `test_index_entry.py` (send time in the entry). The format 6 and 7 upgrade tests now build their old schema without the new column and expect 9. Five new tests fail on 1.1.195 (the sixth guards behaviour that was already right).
- Run: `test_engine_schedule.py`, `test_engine_recurring.py`, `test_engine_outbox.py`, `test_engine_local.py`, `test_engine_drafts.py`, `test_engine_commands.py`, `test_index_index.py`, `test_index_rebuild.py`, `test_index_entry.py`, `test_index_tags.py`, `test_index_text.py`, `test_sync_pop3.py`, `test_sync_send.py`, `test_sync_imap.py`, `ui/test_ui_schedule.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 R-20: the key dialog names the cause (1.1.197)

- Seen: at the owner's start the dialog "The encryption key is missing" came up; Try again worked at once. Cause of that not known: the check threw the credential store's answer away (any CryptoError meant "missing"). The store is Secret Service (keyring 25.7.0): a locked collection raises KeyringLocked; a store not ready may answer nothing.
- Now (D-170): `crypto.KeyNotFoundError` (the store answered: no key) and `crypto.CredentialStoreError` (it could not be asked), each with the store's reason; `key.status` has `problem` and `store_answered`; the dialog shows "The wallet could not be opened" or "The encryption key is missing" with "The wallet said: …"; Try again shows the newest reason; opened from the menu it keeps the old text.
- Tests: `test_crypto_keystore.py`, `test_engine_commands.py` (key status with its reason), `ui/test_ui_key_recovery.py` (the wallet named with its reason; Try again shows the newest reason). The engine and window tests fail on 1.1.196. Run: those files plus `test_cli.py`, `test_engine_local.py`, `ui/test_ui_dialogs.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 New… on the Scheduled messages page (1.1.198)

- The owner looked for a way to make a scheduled (test) message on Settings > Scheduled messages and found none. Now (D-171): New… opens the normal message window in schedule-only mode (`ComposeWindow(schedule_only=True)`: Send is "Schedule…" and asks for the time, Send later hidden, Ctrl+Return asks too; signal `scheduled` with the new message's id). It is application-modal over the modal Settings window (`MainWindow._open_composer(modal=True)`, `_new_scheduled_message`). The page adds the new message at its place in time and selects it (`ScheduledPanel.add_scheduled`), keeping every other row as it is.
- Found: the page still rebuilds its list after its other actions (R-21, place to be agreed).
- Tests: `ui/test_ui_schedule.py` (New… makes a scheduled message that joins the list in place; a scheduling window never sends at once); both fail on 1.1.197. Run: `ui/test_ui_schedule.py`, `ui/test_ui_composer.py`, `ui/test_ui_preferences.py`: 104 passed, 1 failed: `test_the_snooze_button_offers_ready_made_times`, the known intermittent one (STATUS.md, Problems); it passed in the next run of the same set. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 No autosave under the send confirmation (1.1.199)

- Seen by the owner: after Schedule…, "The draft could not be saved: Message not found" over the "Scheduled" box. Cause: `ComposeWindow._sent` set `_finished` only after the confirmation box; the box runs its own event loop, so the 30-second autosave ran under it, while the window still counted as being written. With a draft saved before (and typed on after), the autosave replaced the draft the schedule had removed ("Message not found"); without one, it made a stray draft of a message already in the Outbox. The same order after a send "with remarks". An old fault, now easy to meet through New… on the Scheduled page.
- Now: `_handed_over()` marks the window finished and stops the autosave timer as soon as the engine has the message, before any box; the signals go out then too. A failed send is unchanged (the window stays and keeps saving).
- Test: `ui/test_ui_schedule.py::test_no_draft_is_saved_while_the_confirmation_waits` (with and without a draft saved before); both fail on 1.1.198, one with exactly "Message not found". Run: `ui/test_ui_schedule.py`, `ui/test_ui_composer.py`: 77 passed. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 The Scheduled folder (1.1.200)

- The owner: in the Outbox a scheduled message looks like one that failed; if you forget you scheduled it you wonder why it is not sent. Now (D-172) scheduled and repeating messages wait in their own folder, Scheduled, made when first used, local only.
- Engine: `engine.schedule.SCHEDULED_FOLDER` (= `sync.imap_sync.SCHEDULED`); `mail.send` with send_at delivers there (`ensure_folder`); `mail.send-due` and `mail.scheduled` first move what still waits in the Outbox (`_into_scheduled`: index rows with a send time, and repeat patterns; `Mailbox.move`, one rename, same id); `_waiting_place` finds a message in Scheduled or the Outbox by asking the store, and only "in neither" ends a schedule; `mail.unschedule` replaces the draft from the folder it is in (it named the Outbox). `Mailbox.timed` gives the leave times of Snoozed and Scheduled (repeats: next send); list, search, places and groups use it, so Scheduled sorts and groups like Snoozed. A move between two local-only folders never asks the server. Combined views and the Contact column treat Scheduled like the Outbox. Index upgrade fills send times for both folders.
- IMAP: `LOCAL_ONLY` (sync, now also used by `imap_online`) has Scheduled; `plan_folders` no longer synchronises a server folder whose name is a local-only folder (Outbox, Recovered, Scheduled): it would mix server mail into it and its messages, Bcc included, onto the server. A note says so. This changes the behaviour for a server with a folder named Outbox (it was mixed into the local Outbox before).
- Window: Scheduled right after the Outbox (`LOCAL_PLACES`, icon appointment-new); the Snoozed until column, its sort and the sort menu are called Send at there; cards say "Send …"; it opens in the editor like the Outbox and is never a move or copy target.
- Tests: new `test_engine_schedule.py` (made when first used, listed by send time; old Outbox messages move once, same id, then sent), `test_sync_imap.py` (server folders named like local-only folders not synchronised), `ui/test_ui_schedule.py` (after the Outbox; Send at); all four fail on 1.1.199. Updated for the new place: `test_engine_schedule.py`, `test_engine_recurring.py`, `test_engine_outbox.py`. Fixed a test fault: `ui/test_ui_rules.py` found the pages by position (wrong since pages were added; it failed on 1.1.199 too). Run: the engine, sync, index and 15 window test files listed in the session: all pass. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 "Move back to drafts" (1.1.201)

- Settings > Scheduled messages: the button "Take back as a draft" is "Move back to drafts" (the owner's words); what it does is unchanged. `ui/test_ui_schedule.py` checks the text.
- Run: `ui/test_ui_schedule.py`: 23 passed, 1 failed: `test_the_snooze_button_offers_ready_made_times`, the known intermittent one; this time its output showed that the message was snoozed but the status line was empty (R-22). Not run again. Pyrefly 0 (Linux and Windows), layer check kept.

## 2026-10-20 Favorites > Scheduled, and used-only Favorites appear at once (1.1.202)

- The owner: what applies to Snoozed and Archive (made when first used) applies to Scheduled. Checked every place: the folder, Show on startup and the views it is kept out of were already the same; missing were the Favorites entry, its in-use check, the engine's combined view and the rule that keeps a Favorites list current. Now: `message.combined` view `scheduled`; `FAVORITES` has Scheduled after Outbox; `USED_ONLY`, `favorite_in_use`, `favorite_unread` and the list's fact rule know it; Favorites > Scheduled shows recipients like Favorites > Outbox. Settings > Sidebar and Show on startup follow from that.
- Found and fixed (older, for Snoozed and Archive too): a "shown once used" Favorites entry only appeared after a restart, because a made folder was added to its account's tree but Favorites was not looked at. Now `FolderTreeModel.follow_used_favorites` adds or removes the entry in place at its place in the order (`_folder_structure`). (PySide6: `insertRow(row, item)` with one item inserts an empty row; it takes a list.)
- Tests: `test_engine_local.py` (the view scheduled over two accounts), `ui/test_ui_models.py` (the entry only once a Scheduled folder exists, after Outbox), `ui/test_ui_schedule.py` (on the Sidebar page and in Show on startup, a message moved back to drafts leaves the list at once; Archive and Snoozed appear the moment their folder is made). All four fail on 1.1.201. Run: `test_engine_local.py`, `test_engine_sidebar.py`, `test_engine_schedule.py`; `ui/test_ui_schedule.py`, `ui/test_ui_models.py`, `ui/test_ui_tree_rows.py`, `ui/test_ui_preferences.py`, `ui/test_ui_startup_view.py`, `ui/test_ui_folder_counts.py`, `ui/test_ui_window.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept. Full suite not run.

## 2026-10-20 Clearer scheduling words (1.1.203)

- Settings > Scheduled messages: the button "Send or bring back now" says the one action that applies to the selected line: "Send now" (scheduled or repeating) or "Cancel snooze" (snoozed; tooltip: back to <folder> now, unread); nothing selected: "Send now", greyed. The other buttons stay greyed when they do not apply (the owner: grey out, never hide; D-171).
- The message window: "Send later…" is "Schedule…", its time picker is titled "Schedule"; the confirmation says the message waits in the Scheduled folder (it said Outbox). The help of `mail.send --send-at` and of `mail.unschedule` follow; three comments too. The texts about a failed send keep the Outbox (correct).
- Tests: `ui/test_ui_schedule.py` (the button for each kind of line and none; Schedule… and the Scheduled folder in the confirmation); both fail on 1.1.202. Run: `test_engine_schedule.py`, `test_engine_recurring.py`, `test_cli.py`, `test_engine_commands.py`, `ui/test_ui_schedule.py`, `ui/test_ui_composer.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept.


## 2026-10-20 Settings > Sidebar: ticks for the standard folders of Local Folders (1.1.204)

- The owner's backlog item, with Snoozed, Archive and Scheduled when they exist (D-173, changes D-167). Under the Local Folders tick: one indented tick per standard folder Local Folders has (Inbox, Drafts, Sent, Outbox, Scheduled, Archive, Junk, Trash, Snoozed, in the folder list's order), greyed while Local Folders is off.
- Config: `sidebar.json` `hidden_local_folders`, checked against `LOCAL_FOLDER_KINDS`; `Sidebar.with_hidden_local_folder`. Engine: `sidebar.show-local-folder` (folder, visible); `SidebarInfo.hidden_local_folders`.
- Window: `FolderTreeModel.hidden_local`, `forced_folders`; `local_folder_kind`; a hidden folder is left out with what is inside it at the build, in `add_folder` (made later, or made inside it) and in `rename_folder` (moved into it: removed; moved out: added from the window's folder list). `StartupForces.folders`: a chosen folder keeps its standard folder shown (tick ticked and greyed). Show on startup lists the hidden folder greyed (no change needed: it compares with a full tree).
- Tests: `test_engine_sidebar.py` (the command, the file refuses another name), `ui/test_ui_models.py` (hidden with its subfolders, made later, moved in and out, kept by the startup choice), `ui/test_ui_preferences.py` (the ticks, greyed, startup forcing, only changes sent), `ui/test_ui_startup_view.py` (not in the folder list, greyed among the choices, which exist); all four fail on 1.1.203. Run: `test_engine_sidebar.py`, `ui/test_ui_models.py`, `ui/test_ui_preferences.py`, `ui/test_ui_tree_rows.py`, `ui/test_ui_startup_view.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept.
- Found: `ui/test_ui_models.py::test_local_folders_and_other_folders_use_their_icons` aborts when run on its own (also on 1.1.203); in STATUS Problems.

## 2026-10-20 R-02: no silent limit of 5,000 in Contacts, Tasks and Calendar (1.1.205)

- Found first: Tasks and Calendar asked for the first 5,000 items of every list or calendar and then threw away those of the hidden ones, so hidden lists used up the limit; the Tasks count counted only what arrived; Contacts showed 5,000 of a larger address book while its count said the true number; every Tasks reload read (decrypted) every task.
- Tasks (D-174): new command `task.places`: every task's place in the list order from the index alone (no task read, never cut): item, account, list, done, due day, tags. The order is now the engine's (open before done, due date, priority, summary); the window only groups it. `task.list` takes `lists` (several lists) and `items` (the rows of given tasks), sorts on the index and reads only the tasks of its answer. The window's `TaskListModel` holds places and asks for rows 100 at a time as they come into view (`want`, `arrive`, `forget`; a row asked again after its task changed waits for the newer answer); an action works on places, so a task whose row is not in yet can be selected, completed, tagged and moved. The count counts every place.
- Contacts: `contact.list` with `limit` 0 gives every contact. Not paged, unlike the proposal: a contact's row comes from the index alone, so its place and its row cost the same; paging would only add code. The command line keeps paging (default 200).
- Calendar: `event.list` takes `calendars`, gives every event of the days (at most 400; the window asks at most 60) and answers `EventList` with `cut`: true only when a limit against hostile calendars was reached (100,000 events in the days, or one event 5,000 times); the status line then says not every event is shown. `limit` 0 (all) is the default.
- Typed (D-175): `registry.TypedCommand` (id, parameter type, answer type), `CommandRegistry.ask`, `Backend.ask`; `TASK_LIST`, `TASK_PLACES`, `CONTACT_LIST`, `EVENT_LIST` are the first typed calls. `pimstore` `ItemQuery.items` (last field: queries are built positionally).
- Limit kept: the index has one floating mark for a task's due and start together, so a zoned due beside a start without a zone gets its day as wall-clock time (at worst a day off near midnight).
- Tests: new `test_engine_pim_lists.py` (5,100 contacts all listed; every task has a place, a hidden list takes no room, order, items, one place; 6,000 occurrences in shown calendars only; an event too frequent is said to be cut), `ui/test_ui_tasks.py` (5,104 places, rows only as they come into view, the last row comes in when scrolled to, unticking the big list), `ui/test_ui_contacts.py` (5,100 contacts in the window). All fail on 1.1.204 (contacts 1 of 5,100 or 5,000 in the window; no task.places; events cut at 1,000). Changed: `test_engine_calendar.py` (`.events`), `ui/test_ui_tasks.py` (grouping from places), `ui/test_ui_pim_changes.py` (rows of given items are not a whole list; places never asked again).
- Run: `test_engine_pim_lists.py`, `test_engine_calendar.py`, `test_engine_pim.py`, `test_cli.py`, `test_engine_commands.py`, `test_engine_dav.py`, `test_import_emclient.py`; `ui/test_ui_tasks.py`, `ui/test_ui_contacts.py`, `ui/test_ui_pim_changes.py`, `ui/test_ui_calendar.py`: all pass. Pyrefly 0 (Linux and Windows), layer check kept. The full suite was not run.

## 2026-10-20 1.1.206 A message has at most two files (D-176)

**What happened**
- The owner's report: a repeating message (re-scheduled after Move back to drafts) sat in Local Folders > Scheduled but Settings > Scheduled messages was empty. Cause found on the owner's data (structure only): the message was in Local Folders, its timetable in `recurring.json` under the sending account (info@…, which keeps its mail in Local Folders). `mail.scheduled` and `mail.send-due` read the file beside the place where the mail is, so the message was never listed and **never sent**. The same fault in `snoozed.json` and `archived.log` (written under `Mailbox.account_id`, not the store's account): Unarchive through Favorites lost the origin. Move back to drafts was not the cause; any repeating message from such an account had it.
- The owner's rule: one message, at most two files. Done that way:
  - `store/properties.py` (`MessageProperties`, `Properties`) replaces `store/tags.py`: `properties/<store id>.json` per message (tags, snoozed_until, snoozed_from, archived_from; unknown names kept), lock per message, atomic write, damaged → `.bad` for that message alone. `Store.properties`; `copy` takes the tags; `delete` removes the file; `take_from` for the storage move (`commands_local._copy`; tags were left behind there before). `tags.json` is taken over when the store opens.
  - `Store.replace` (new, checkpoints `replace:*`): a message's content replaced under the same id, written aside, read back, one rename. `Mailbox.replace_waiting` allows it only in the Scheduled folder and updates the index (no fact: the list shows the same; the Send at column follows at the next fetch, as before).
  - Repeating messages carry `X-Chrono-Mail-Repeat` (timetable and progress as short text, `recurring.repeat_text`/`repeat_from_text`; the reader undoes the encoded words the builder makes of a long header) and `X-Chrono-Mail-Send-At` (next send; when the series is over, now, so the next check removes it). The index finds them like any scheduled message; `mail.send-due` sends one-time and repeating ones in one loop (`_send_repeat`), progress written before the send. `mime.with_extra_headers` changes only those headers, every other byte kept.
  - Snooze, the snoozed list and wake work per place (the Snoozed folder's messages and their properties); `SnoozedInfo.account` is the place now. Archive and Unarchive use `archived_from` (cleared on Unarchive).
  - `engine/old_files.py`: once per mailbox and run, `snoozed.json`, `archived.log` and `recurring.json` from the mailbox's folder and its member accounts' folders move into the properties files and the messages; each old file removed only after all of it is written; what is already in the new place wins; a failure keeps the file and is tried at the next opening.
- Gone: `store/tags.py`, `load_snoozed`/`save_snoozed`/`SNOOZED_FILE_LOCK`, `load_repeats`/`save_repeats` (only `load_old_repeats` for the move), `note_archived`/`archived_from`.
- Tests: new `test_store_properties.py` (replaces `test_store_tags.py`; one file per message, only while needed; damage; unknown names; concurrency; move/copy/delete; take_from; tags.json taken over, damaged, interrupted), `test_engine_properties.py` (the owner's case listed and sent; pause/resume/change/cancel in the message; snooze, Unarchive and tags through an account in Local Folders; the old files moved, and interrupted), `test_store_interruption.py` (replace crashed at every step: old or new version, never neither; a failed verification keeps the old). The five behaviour tests of `test_engine_properties.py` fail on 1.1.205 (scheduled list empty, nothing sent, Unarchive to the Inbox instead of Projects, no properties file). Changed: `test_engine_recurring.py`, `test_engine_schedule.py` (the snoozed.json tests are gone with the file; `damage` skips properties files), `test_index_tags.py`, `test_mailbox_guard.py` (tags through properties; `properties.change` is for what exists only here).
- Run: those, plus `test_engine_actions.py`, `test_engine_changes.py`, `test_engine_commands.py`, `test_engine_drafts.py`, `test_engine_imap_online.py`, `test_engine_local.py`, `test_engine_rules.py`, `test_engine_tags.py`, `test_imap_tags.py`, `test_index_rebuild.py`, `test_store_store.py`, `test_mime_build.py`, `test_cli.py`, `test_emclient_source.py` (433 passed), `ui/test_ui_schedule.py`, `ui/test_ui_tags.py` (40 passed). Pyrefly 0 (Linux and Windows), layer check kept. The full suite and the live tests were not run.

**Still open**
- The owner's computer: at the first start of 1.1.206 the timetable in `accounts/94427…/recurring.json` moves into "Test scheduled message (2)"; it then shows on the page (next send 2026-10-21 08:00).
- The Send at column of an open Scheduled folder shows a repeating message's new next send only at the next fetch (as before; a fact for it would be new).

## 2026-10-20 1.1.207 Edit a scheduled message (D-177)

**What happened**
- The owner expected an Edit button on Settings > Scheduled messages; it had never been designed. Found while researching: a message opened from the Scheduled folder was an ordinary draft, and the 30-second autosave moved it to Drafts (no longer scheduled); scheduling again asked the time anew and a repeat lost its count and id.
- Engine: `mail.edit-scheduled` (typed `MAIL_EDIT_SCHEDULED`; `ScheduledEdit` = `SendParameters` + `message` + `keep_time`), in `commands_schedule.py`: the message rebuilt (`commands_mail.outgoing_message`, shared with `mail.send`) and replaced under the same id (`Mailbox.replace_waiting`); time and repeat kept, or new ones counted from the new time with the repeat's count, pause and last send kept; Source and Mark-Replied notes kept. `ScheduledInfo` has months, weeks, days, start, end; `_info` builds it for the list and the edit.
- The fact `changed` (`engine/changes.py`): `replace_waiting` tells it with the new row; `MessageListModel.replace_row` replaces the loaded row in place (keeping a combined view's account). This also makes the Send at column follow a repeat's next send at once (open since 1.1.206).
- Window: `ScheduledPanel` New and Edit (no dots, the owner), Edit greyed for snoozed lines, double click edits, `replace_scheduled` moves only that line to its place, selected, with no reload (and no `changed` flag, so Settings closing fetches nothing). `ComposeWindow(editing=…)`: Save (keep time), Schedule… opening on the current time, interval and end (`WhenDialog.show_current`), Send and Save draft greyed, Ctrl+S and Ctrl+Return save, no autosave, closing asks Save/Discard/Cancel. `MainWindow._edit_scheduled`; `edit_draft` opens Scheduled-folder messages that way.
- Tests: `test_engine_schedule.py` (edited in place, same id and time, sent with the new text; a repeat keeps its count, a new time and interval, back to once; errors), `ui/test_ui_schedule.py` (New and Edit, greyed for snoozed; edit from the page in place, autosave makes no draft, only the line follows; Schedule… opens on the current values and moves the line; the Scheduled folder opens in edit mode and the row follows its fact). All 7 fail on 1.1.206.
- Run: `test_engine_schedule.py`, `test_engine_recurring.py`, `test_engine_properties.py`, `test_engine_changes.py`, `test_cli.py`, `test_mailbox_guard.py`, `test_engine_commands.py`, `test_engine_drafts.py` (182 passed); `ui/test_ui_schedule.py`, `ui/test_ui_composer.py`, `ui/test_ui_models.py` (114; 12 runs: one first run with 2 failures not seen again, R-22's snooze test failed 3 times in 11). Pyrefly 0 (Linux and Windows), layer check kept. The full suite was not run.

**Still open**
- Why two window tests failed once in the first run (see STATUS Problems).
- A changed row keeps its place in the list until the next sort or fetch, also when its new time would sort it elsewhere.

## 2026-10-20 1.1.208 Scheduled messages handled where they are (D-178)

**What happened**
- The owner's test of 1.1.207: Edit worked only once. Cause: a message window was never deleted when it closed (no `WA_DeleteOnClose`), so it stayed, hidden, in `MainWindow._composers`; since an edited message keeps its id, the second Edit matched the hidden window and only "raised" it. Every closed message window had stayed in memory until quit. Now `ComposeWindow` deletes itself on close; its `destroyed` takes it out of the list.
- The owner's requests: Edit (first item) on a message and only New on the empty area in the list menu of a Scheduled view (`act_edit_scheduled`, `act_new_scheduled`, `_in_scheduled_view`); Favorites > Scheduled edits on Enter and double click (`_in_drafts`). Settings > Scheduled messages loses Move back to drafts and Stop repeating (New, Edit, Send now, Pause stay). A scheduled message dropped on Drafts or moved there goes through `mail.unschedule` (`_move_command`), which now writes the draft from the account in the From (a drop in Local Folders names Local Folders). Only Drafts and Trash take a scheduled message (`SCHEDULED_TARGET_ROLES` in the folder list and in Move to).
- Tests: `ui/test_ui_schedule.py` (Edit twice; the page's four buttons; the menus, through a QMenu subclass: patching QMenu.exec on the class did not stop the real menu, and the first run hung on it; Favorites > Scheduled edits; a drop on Drafts unschedules, Inbox refuses, Trash takes it), `test_engine_properties.py` (back to drafts through Local Folders keeps the sender). All 6 fail on 1.1.207. Changed: tests that read a message window after it closed (`world.gone`, `world.add_window`; `test_ui_composer.py`, `test_ui_schedule.py`, `test_ui_preferences.py`), the page tests without the two buttons.
- Run: engine `test_engine_schedule.py`, `test_engine_recurring.py`, `test_engine_properties.py`, `test_engine_local.py`, `test_engine_drafts.py`, `test_engine_changes.py`, `test_cli.py`, `test_mailbox_guard.py` (134 passed); window `test_ui_schedule` 37, `test_ui_composer` 54, `test_ui_preferences` 31, `test_ui_shortcuts` 8, `test_ui_completer` 14, `test_ui_appearance` 39, `test_ui_columns` 33, `test_ui_contacts` 7, `test_ui_richtext` 18, `test_ui_sections` 5, `test_ui_windowstate` 5, `test_ui_window` 62, `test_ui_dragdrop` 4, `test_ui_models` 28 passed; `test_ui_theme` 13 passed, 1 failed (the known selection test, in Problems since 0.5.150). Pyrefly 0 (Linux and Windows), layer check kept. The full suite was not run.
- A hung test run was stopped once with `pkill -f` (against the owner's rule; it matched only that run); afterwards with the task tool.

**Still open**
- Found, not changed: the folder list accepts a drop onto Scheduled (D-172 says it is never a target).

## 2026-10-20 1.1.209 A dragged column keeps its place (D-179)

**What landed**
- The owner's bug report: after dragging a column (Replied) to the left, adding a column or opening another folder jumbled the order.
- Cause: the order was kept twice. A drag left the move in Qt's header (its visual-to-logical map) and saved the new order, while the model kept its old column tuple. Every rebuild of the model (`set_columns`: a column added or removed, and every folder switch, because `list_model.columns != _list_columns()` was always true after a drag) put the model in the saved order and Qt applied its remembered move on top: the drag applied twice (in the probe Delete jumped to second place). Qt keeps header moves across a model reset (verified with a bare QTreeView).
- Fix: the model's column tuple is the one record. `_columns_moved` undoes the header's move and moves the model column (`MessageListModel.move_column`, beginMoveColumns: rows, selection and widths stay), then saves, shows the sort indicator at the column's new place and fits the widths. The header always shows the model's order (visual = logical).
- Test: `tests/ui/test_ui_columns.py::test_a_dragged_column_keeps_its_place_when_a_column_is_added_and_another_folder_opens` (fails on 1.1.208, passes now).
- Run: `tests/ui/test_ui_columns.py`, `tests/ui/test_ui_columnfit.py` (43 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- R-13: a column added or removed still resets the whole list (selection lost); only the order part is fixed.

## 2026-10-20 1.1.210 Calendars, contacts and tasks found by themselves (D-180 to D-183)

**What landed**
- The owner: adding the Kerio IMAP account gave no calendars; linking them by hand on Edit > Calendars and contacts is unfriendly.
- `dav.find` (typed, `DAV_FIND`): with an address, checks it; without, looks at `https://<incoming host>/` and then `https://<domain of the address>/` (port 443, `WEB_PORT`), each first asked without a login at `/.well-known/caldav` and `/carddav`: only a place that asks for a login (401) or answers (207) gets the password. DNS SRV records were left out (the owner: not needed; the stdlib cannot read them).
- Every folder is listed: the user's own and the public ones in `#public` next to the home (`DavClient.public_collections`, `all_collections`; Kerio, found live on the Kerio test server's name: public Calendar and Public Contacts). `RemoteCollection.public`; a public folder is made here as "<name> (public)".
- Certificates (D-180): a place whose certificate fails is retried only when it shows exactly a fingerprint already accepted for this account; the pin `host:port=fp` is returned and saved by `dav.link`. `DavCertificateError` tells a certificate failure apart. `dav.link` refuses a pin whose fingerprint the account did not accept.
- `dav.link` (typed, `DAV_LINK`, destructive): saves address, user name, `dav_left_out` (the unticked folders by `folder_key` = component + href) and the pin; a folder newly left out goes to the PIM store's `.deleted` with its sync state, refused while `unsent_changes` (an item made, changed or deleted here since the last sync). `account.set-dav` to another address clears `dav_left_out`. `sync_dav(left_out=...)` skips those folders; a folder made on the server later arrives by itself (D-183).
- Window: after Add account > OK the search runs in the background; when it finds folders, "Additional folders found:" (`ui/foundfolders.py`) lists each as "Calendar: Calendar", "Contacts: Public Contacts (public) (read only)" with a tick (all ticked for a new server), OK and Cancel; OK links the ticked ones and syncs. Nothing found: nothing is shown. Edit > Calendars and contacts: the button is "Find calendars, contacts and tasks" (checks the address typed or searches), opens the same window with the current ticks, links at once; Settings takes the change into use also when the editor is then cancelled.
- Live (temporary profile, the Kerio test account, incoming the Kerio test server (port 993)): without the IMAP certificate accepted nothing was found ("self-signed certificate"); with it, `https://kerio.example/` gave Calendar, Tasks, Contacts, public Calendar, Public Contacts; linked with the public Calendar left out, pin the Kerio test server (port 443) added, sync made 4 collections without errors.
- Tests: `tests/test_engine_dav.py` (5 new), `tests/test_sync_dav.py` (3 new), `tests/test_config.py` (1 new), `tests/ui/test_ui_dav.py` (3 new, 1 changed); `tests/fakedav.py` has public folders, a plain web mode and a login record. The new engine and sync tests fail on 1.1.209 (the functions did not exist).
- Run: `tests/test_engine_dav.py`, `tests/test_sync_dav.py`, `tests/test_protocol_dav.py`, `tests/test_config.py` (100 passed); `tests/ui/test_ui_dav.py`, `tests/ui/test_ui_settings.py`, `tests/ui/test_ui_certificates.py` (23 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- "Add account" in the Calendar, Contacts and Tasks sections (ROADMAP 4.6): the changelist is still to be shown.

## 2026-10-20 1.1.211 Account name and Your name (D-184)

**What landed**
- The owner: the folder list showed "the test account - mail…" for both test accounts (same address, the server cut off); he had used the Name field to tell them apart, which also goes into the From line.
- `AccountSettings.account_name` (config.json `account_name`, at most 200 characters, no spaces at the ends); `account.add` / `account.edit` take `account_name`; `AccountSummary.account_name`.
- `shown_name`: the account name, else the address; the server and protocol added when another account would show the same text (case ignored). `account_label` (the lists that always told accounts apart) is the account name with the same rule when one is set, else "address - server (PROTOCOL)" as before. `pimaccess.account_name` (the Calendar, Contacts and Tasks headings) now uses `shown_name` instead of the display name.
- Add account and the account editor: Account name, grey line, Your name (was Name), E-mail address, grey line (`ui/accountdialog.py` `add_name_rows`, `help_line`: the palette's PlaceholderText role, no colour of its own). Settings > Accounts: "<account name>  <address>  - server" when set. The From picker is unchanged.
- Tests: `tests/test_config.py::test_the_account_name_goes_through_the_file`, `tests/test_engine_commands.py::test_the_account_name_is_shown_instead_of_the_address_and_told_apart_by_the_server`, `tests/test_engine_dav.py::test_the_calendars_of_an_account_carry_its_account_name`, `tests/ui/test_ui_settings.py` (2 new), `tests/ui/test_ui_dialogs.py` (1 new); all fail on 1.1.210.
- Run: `tests/test_config.py`, `tests/test_engine_dav.py`, `tests/test_engine_pim.py`, `tests/test_engine_local.py` and the two naming tests of `tests/test_engine_commands.py` (106 passed); `tests/ui/test_ui_settings.py`, `test_ui_dialogs.py`, `test_ui_dav.py`, `test_ui_models.py`, `test_ui_folderchoices.py` and `test_ui_schedule.py::test_refresh_has_a_menu_to_receive_all_or_one_account` (68 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- "Add account" in the Calendar, Contacts and Tasks sections (ROADMAP 4.6): changelist to be shown.

## 2026-10-20 1.1.212 Accounts of calendars, contacts and tasks only (D-185)

**What landed**
- The owner: Nextcloud and shared calendars have a login of their own, without mail here; option B (a new account kind) over A (only linking a mail account).
- `AccountKind.DAV` ("dav"): no address, the login in `incoming` (host from the address, 443, TLS), the password stored as INCOMING. `Engine.storage_settings` refuses it ("it has no mail"), `storage_groups` leaves it out (combined views, maintenance), `mail.send` reply-from leaves it out. `server_label` "host (Calendars and contacts)" from `dav_url`; `_own_name` falls back to the user name. `account.edit` keeps its empty address; `account.set-dav` refuses an empty server for it; `account.remove` also deletes the DAV password (for every account).
- Commands (typed): `dav.find-new` (`DAV_FIND_NEW`: address, login, an accepted fingerprint → every folder, all ticked) and `account.add-dav` (`ACCOUNT_ADD_DAV`: asks the server first, `ensure_local_folders` so the data key exists, then one config change; a failure removes the stored password). `web_address`: a host name alone becomes `https://host/`.
- Window: `ui/calendaraccount.py` (`CalendarAccountDialog`): For, Account name (+ grey line), Server address, User name, Password, Find calendars, contacts and tasks, Look at the server's certificate…, Add (greyed until found), Cancel; Add → Additional folders found → `account.add-dav` or `dav.link`. `CollectionPanel.menu_at` builds the sidebar menu (Add account… first), `_menu` shows it; `add_account_requested` → `MainWindow.add_calendar_account` (reload, recovery dialog when a key was made, sync). `MainWindow._accounts` holds only accounts with mail (and Local Folders), `_all_accounts` all; the first-run dialog only when there are no accounts at all; `_sync_calendars` looks at all. Settings > Accounts: "<name>  - <server> (Calendars and contacts)", never in the default account list or the storage check. The editor of such an account: General (Account name + its grey line) and Calendars and contacts only.
- Live (temporary profile, the Kerio test login on the Kerio test server's name): without the certificate `dav.find-new` refused ("self-signed certificate"); with it, 5 folders; `account.add-dav` with one public folder left out: kind dav, named "Kerio calendars", server "kerio.example (Calendars and contacts)", the data key made; the sync made 4 collections; `message.list` refused ("it has no mail").
- Tests: `tests/test_engine_dav.py` (2 new), `tests/ui/test_ui_dav.py` (4 new); all fail on 1.1.211.
- Run: `tests/test_engine_dav.py`, `test_config.py`, `test_engine_pim.py`, `test_engine_local.py`, `test_sync_dav.py` and three account tests of `test_engine_commands.py` (125 passed); `tests/ui/test_ui_dav.py` (10 passed); `tests/ui/test_ui_settings.py`, `test_ui_dialogs.py`, `test_ui_models.py`, `test_ui_folderchoices.py`, `test_ui_certificates.py`, `test_ui_schedule.py::test_refresh_has_a_menu_to_receive_all_or_one_account` (68 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- In a first run the new menu test hung on a real menu (patching `QMenu.exec` does not reach PySide's method); the menu is now built by `menu_at` and the test reads it. The hung run was stopped by its process number.

**Still open**
- The window's own account list was empty when a test opened the dialog right after start (the test now uses the engine's list); in the program the list is loaded before the sections can be used.

## 2026-10-20 1.1.213 A login per server (D-186)

**What landed**
- The owner, with screenshots of the reference client: the grey help lines go; the user name and password belong to each server, with the choice to use the incoming login for the outgoing server.
- `AccountSettings.outgoing_same_login` (config.json `outgoing_same_login`; missing: true when both servers have the same user name). `account.add` sets it from whether an outgoing user name or password of its own was given. `account.edit` takes `outgoing_same_login`; while on, the outgoing user name is the incoming one and a new incoming password is written for the outgoing server too; switched on again, the stored incoming password is copied; an outgoing login of its own given without the choice switches it off. `AccountSummary.outgoing_same_login`.
- Add account (`ui/accountdialog.py`): Your account = Account type, Account name, Your name, E-mail address; Incoming mail: Server, Security, Port, User name, Password; Outgoing mail: Server, Security, Port, the tick, User name, Password (greyed while ticked, following the incoming user name). Without the tick its own login must be filled in. The editor's Servers tab has the same tick. `help_line` and its texts are gone, also in `ui/calendaraccount.py` and the editor of an account of calendars only.
- Found while testing: `tests/test_engine_account_edit.py` gave a separate outgoing login through `account.edit` without the choice, which the tick would have ignored; now such a login switches the tick off.
- Tests: `tests/test_engine_commands.py::test_the_outgoing_login_follows_the_incoming_one_while_it_is_the_same`, `tests/test_config.py::test_the_same_login_goes_through_the_file_and_older_files_get_it_from_the_user_names`, `tests/ui/test_ui_settings.py` (2 new, 1 changed), `tests/ui/test_ui_dialogs.py` (1 new); the new ones fail on 1.1.212.
- Run: `tests/test_config.py`, `tests/test_engine_dav.py`, `tests/test_engine_account_edit.py`, `test_engine_imap.py`, `test_engine_imap_folders.py`, `test_engine_watch.py` and the account tests of `test_engine_commands.py`; `tests/ui/test_ui_settings.py`, `test_ui_dialogs.py`, `test_ui_dav.py`, `test_ui_certificates.py`, `test_ui_folderchoices.py`, `test_ui_key_recovery.py`, `test_ui_composer.py`, `test_ui_richtext.py`, `test_ui_preferences.py`, `test_ui_window.py`: all passed; Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-20 1.1.214 The calendar folders in the account editor (D-187)

**What landed**
- The owner: after choosing in "Additional folders found" there was no visible place to change the choice; the folders belong below New password, and unticking one and saving disconnects it. Also the window's line: "Select the folders you would like to add:".
- `ui/foundfolders.py`: `FolderTicks` (a tick per folder, `show_folders`, `left_out`, `changed`), used by the window and by the editor; `CHOOSE_TEXT`.
- `ui/accountedit.py`: the Folders row under New password; `_find_dav_folders` fills it when the editor opens (stored server) and on Find; `_dav_link` gives the `dav.link` values when the shown folders belong to the address in the field and a tick or the login changed; `_save` sends it last (after edit, folders, storage); a refusal (changes not yet on the server) keeps the editor open with the reason. Nothing is saved on Cancel, so `ui/settingsdialog.py` no longer synchronises after a cancelled editor.
- Tests: `tests/ui/test_ui_dav.py` (3 new, 2 changed); they fail on 1.1.213 (no `FolderTicks`).
- Run: `tests/ui/test_ui_dav.py`, `test_ui_settings.py`, `test_ui_certificates.py`, `test_ui_folderchoices.py` (37 passed); `tests/test_engine_dav.py` (12 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-20 1.1.215 One separator between the panes (D-188)

**What landed**
- The owner: the 1 px black line between the sidebar and the rest separated too little, and Contacts and Tasks had a fat bar; one look everywhere, wider than the line, half the fat bar, with the dots in the middle. Rendered 1 to 4 px for him; Qt's dots sit against one side at narrow widths, so the program draws its own; 3 px chosen by the owner.
- `ui/splitter.py`: `PaneSplitter` (handle width 3) and `PaneHandle` (fills `pane_separator`, 7 dots 1 × 2 px with 2 px gaps in `pane_separator_dots`, centred; a row of dots when the panes are above each other). Used by `mainSplitter`, `contentSplitter`, Contacts and Tasks. The style sheet's 1 px black handle rules are gone (the column header lines keep `separator`). Colours sampled from Qt's own separator with the program's palettes: bar #202326 / #ffffff, dots #6f7173 / #c3c3c3.
- Tests: `tests/ui/test_ui_theme.py::test_every_separator_between_panes_is_the_same_with_its_dots_in_the_middle` (every splitter of the window, the dots' column and their centring, the flat one) replaces the thin black line test; the sorted-by part is its own test now.
- Run: `tests/ui/test_ui_theme.py`, `test_ui_appearance.py`, `test_ui_columns.py`, `test_ui_columnfit.py`, `test_ui_contacts.py`, `test_ui_tasks.py`, `test_ui_preferences.py` (141 passed, 3 failed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- The 3 failures: `test_ui_theme.py::test_the_selection_is_blue_with_the_focus_and_grey_without` (the known test fault since 0.5.150), `test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons` and `test_ui_tasks.py::test_every_task_of_a_large_list_has_a_place_and_its_rows_come_in_as_they_come_into_view`: both pass alone on 1.1.215 and on 1.1.214, and the same run on 1.1.214 fails them too (plus `test_a_task_is_added_quickly_completed_reopened_and_deleted`): they depend on the tests run before them, not on this change.

## 2026-10-20 1.1.216 The contact details reach the separator

**What landed**
- The owner: the contact details' 12 px border made the separator look as if it floated between the list's scrollbar and the details (the scrollbar stays: it belongs to the list, as in Mail). The calendar and the tasks have no such border.
- `ui/contactdetail.py`: the outer layout has no margins; the header (avatar, name, subtitle, tags) keeps 12, 12, 12, 8; the avatar is 50 px (halfway between 64 and the list's 36, the owner).
- Test: `tests/ui/test_ui_contacts.py::test_the_details_reach_the_separator_and_only_the_header_has_spacing` (fails on 1.1.215).
- Run: `tests/ui/test_ui_contacts.py` (8 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-20 1.1.217 Calendar sync fixed, sync with every check, the mouse wheel (D-078, D-079, D-189)

**What landed**
- The owner's report: none of the 500 test events came in. Two causes:
  1. The automatic checks never synchronised the calendars: `check_accounts` only did when all accounts were checked (Refresh, start); the 5-minute timer checks a set of accounts. Now every check syncs the calendars of the accounts it checks (`_sync_calendars(only)`, `dav.sync` `accounts`), and the general timer adds the accounts of calendars only.
  2. After Refresh, "Calendar: an item could not be fetched" 1,000 times: Kerio's change report (sync-collection) writes `@` as `%40` and lists each new item twice, its multiget answers with `@`; the hrefs were compared as text, so every item looked missing. Now every href is kept and compared decoded (`protocols/dav.py` `canonical_href`, applied in `parse_multistatus`; `_escaped` escapes once when sending, the query left alone; `#public` written decoded), the change report counts an item once (its last line), and the remembered forms are made the same on reading (`DavState` links, the collections' server href in `_matching`, the unlink loop, `matching_collection`, `canonical_key` for `dav_left_out`).
- Found live, not changed: Kerio lists 8 contacts in Public Contacts but answers 404 to GET and gives nothing in an addressbook-multiget, also asked byte for byte as it listed them; each shows as "Public Contacts: an item could not be fetched" when that book changes or syncs first.
- The mouse wheel (D-189): `EntryHits.period_step` and `step_by_wheel` (120 per notch, small movements added up, Shift's sideways delta used too); `MonthView` and `DayHeader` step on the wheel; `GridCanvas` steps on Shift+wheel and scrolls otherwise; `CalendarPage` connects `period_step` to `step`.
- Live (temporary store, the Kerio test account on the Kerio test server's name): a full sync pulled 626 items; an event with `@` in its name added on the server came in at the next (incremental) sync, and its removal too.
- Tests: `tests/test_sync_dav.py::test_a_server_that_writes_the_same_address_two_ways_loses_nothing` (fake server `like_kerio`), `tests/ui/test_ui_dav.py::test_the_timers_check_brings_the_calendars_in_step`, `tests/ui/test_ui_calendar.py` (2 wheel tests); all fail on 1.1.216 (the first with exactly the owner's message).
- Run: `tests/test_protocol_dav.py`, `test_sync_dav.py`, `test_engine_dav.py` (41 passed); `tests/ui/test_ui_dav.py`, `test_ui_calendar.py`, `test_ui_window.py` (85 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- The test data on the Kerio account (500 events, 100 tasks, made on 2026-10-20 by a script in the chat's scratch folder, not in the project): they come in with 1.1.217.

## 2026-10-20 1.1.218 Refresh in the sections synchronises; the Windows computer's four sync faults (D-079, D-174, D-190)

**What landed**
- Why the owner still saw no events with 1.1.217: his settings have check_on_start=false and a 20-minute interval, and Refresh in the Calendar, Contacts and Tasks sections only re-read this computer (`PimPage.refresh` → `panel.reload`); only Refresh in Mail synchronised. Shown by his files: no synchronisation since 18:37, the token still the old one.
- Refresh in a section: `PimPage.refresh_from_servers` → `MainWindow.refresh_calendars` (the toolbar button and File > Refresh/F5 in a section). `_sync_calendars(manual)`: the three Refresh buttons greyed while it runs; status "synchronising…", then the summary (added or changed here, removed here, sent), merged with the shown section's count once it re-read its list (`_sync_summary`), or "are up to date"; problems in one line per calendar. `_page_status`: only the shown section writes its count (a hidden one overwrote it).
- The Windows computer's report (Windows, 1.1.217), all four fixed: (1) a change report cut short (507, RFC 6578 3.6) is followed with the token it gives (`DavClient.changes`, at most 1,000 rounds); (1, 2) a complete listing that would remove more than half of at least 10 linked items removes nothing, says so and does not keep its token or ctag (`_pull`, `VANISH_MINIMUM`); (3) a single item whose GET fails (not the login) is left out in `fetch`, as its docstring promised, and `_pull` tells the missing ones in one line and keeps the token back; (4) `pim.ical.occurrences` walks the occurrences in start order (`recurring_ical_events ... .after(start)`) and stops at the end of the range or `limit`, and `event.list` gives each event only the room left of the 5,000 for the whole range.
- Checked on the owner's real data: a copy of his Kerio account's calendar files and settings, his login from the wallet, the real window off screen: Calendar > Refresh: November 2 → 66 events, status "Calendars, contacts and tasks: 600 added or changed here. 66 event(s)", Tasks 106; a second Refresh: "… are up to date". The copies were deleted. His own files were not touched (the program was running).
- Tests: `tests/test_sync_dav.py` (3 new), `tests/test_pim_ical.py` (1 new), `tests/test_engine_calendar.py` (1 new), `tests/ui/test_ui_dav.py` (1 new); `tests/fakedav.py` can cut its change report short; all six fail on 1.1.217.
- Run: `tests/test_protocol_dav.py`, `test_sync_dav.py`, `test_engine_dav.py`, `test_pim_ical.py`, `test_engine_calendar.py`, `test_engine_recurring.py`, `test_calendar_layout.py` (102 passed); `tests/ui/test_ui_dav.py`, `test_ui_calendar.py`, `test_ui_tasks.py`, `test_ui_contacts.py`, `test_ui_window.py` (103 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Found in this round, my own fault, repaired before anything ran: an edit script pasted `ui/mainwindow.py` into itself; the file was restored from 1.1.217 and the change applied again.

## 2026-10-20 1.1.219 Appointment colours from the calendar's own palette (D-191)

**What landed**
- The owner's report: changing a calendar's colour did not recolour its appointments (the view ignored `collection_edited`); and his wish after Google Calendar: one colour per appointment, the calendar's own by default, from a palette per calendar.
- Model: `core/pim.py` `PaletteColour`, `CollectionInfo.palette` (None: never changed, the suggestions) and `own_colour_name`, `palette_of`; `core/colours.py` `SUGGESTED_EVENT_COLOURS` (Google's eleven, values checked against a Google Calendar API library's colour table) and the 147 CSS colour names (`css_name_nearest`, `css_colour`). `pimstore`: the palette in `collection.json`, checked (ids of their own, never `calendar`, names 1–60 characters, at most 100), carried by every `update_collection` (it used to rebuild the description without it).
- `pim/ical.py`: `Event.colour_id` (X-CHRONO-MAIL-COLOR-ID) and `css_colour` (COLOR), read and written as single edited properties. `commands_calendar`: `colour` on event.add/edit (an id of the palette; empty: the calendar's own; an unknown id is refused), `_with_colour` writes the id and the nearest CSS name; `OccurrenceInfo`/`EventInfo` carry `colour_id` and `other_colour`, `colour` is the resolved colour (`colour_of`). `commands_pim`: `CollectionSummary.palette`, typed `collection.set-palette` (own name and colour, the others as id TAB name TAB #rrggbb).
- Window: `ui/paletteeditor.py` (`PaletteEditor`, `ColourRow`, `circle_icon`, `colour_from`); `ui/eventeditor.py` Colour row (QToolButton with the list, Other colour, Edit colours…; the colour is sent only when picked); `ui/collectionpanel.py` Edit colours… (calendars, greyed when read-only) and `take_infos` on `collection_edited` (in place, `infos_changed`); `ui/calendarview.py` `_coloured`, `_recolour`.
- Live: an appointment with both properties written to the Kerio test calendar came back with both; it was deleted again.
- Tests: `tests/test_engine_calendar.py` (4 new), `tests/ui/test_ui_calendar.py` (3 new); the 7 fail on 1.1.218.
- Run: `tests/test_engine_calendar.py`, `test_engine_pim.py`, `test_pim_ical.py`, `test_engine_dav.py`, `test_sync_dav.py`, `test_protocol_dav.py`, `test_engine_recurring.py` (106 passed); `tests/ui/test_ui_calendar.py`, `test_ui_dav.py`, `test_ui_appearance.py` (the colour file guard), `test_ui_window.py` (128 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- Task colours (their amber, grey and red): the owner decides later, perhaps with tags.
- A palette change does not rewrite the appointments' COLOR for other programs (only Chrono Mail's id follows).

## 2026-10-20 1.1.220 A real mass delete on the server settles (D-079)

**What landed**
- The Windows computer's report on 1.1.218: the guard of 1.1.218 refused every listing that lost most items and kept no ctag, so a real mass delete on the server (15 of 20 on a phone) was refused again at every sync and never done here; the same after a forgotten token.
- The owner chose A (the Windows computer's fix) over B (a question, as mail asks): `CollectionState.refused` keeps the digest of the sorted addresses of a refused listing (in dav-state.json); exactly the same listing at the next sync is trusted (removals done for the items unchanged here, counted as deletes, token or ctag kept); a different one is refused and remembered instead; a listing that passes the guard forgets it.
- Accepted risk (told to the owner): a server that always cuts its list at the same place without saying so has that cut trusted at the second sync (the items go to .deleted, 30 days).
- `tests/test_protocol_dav.py`: 5 s to refuse a closed port (Windows needs 2.0 s; the Windows computer measured it).
- Test: `tests/test_sync_dav.py::test_a_real_mass_delete_on_the_server_is_done_here_at_the_second_synchronisation` (ctag only, and tokens forgotten; fails on 1.1.219); the cut-short and empty listing tests unchanged and passing.
- Run: `tests/test_sync_dav.py`, `test_protocol_dav.py`, `test_engine_dav.py` (45 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-20 1.1.221 Calendar names and colours follow the server; one palette per account (R-27, R-26; D-191 to D-193)

**What landed**
- The Windows computer's report: unticking and re-ticking a calendar in the account window lost all its colours (the palette and the calendar's colour lived only in the copy here, which untick deletes). The chat then found the wider fault (audit round 3, R-23 to R-35) and the owner wrote the basic rule (D-193, first in DECISIONS.md and AGENTS.md).
- Palette per account (D-191): `config/model.py` `calendar_palette`, `calendar_own_colour_name`; `core/pim.py` `palette_of(own_colour, palette, own_name)`, `palette_problem`; `CollectionInfo` keeps no palette; `engine/pimaccess.py` `palette_for` and `_take_over_palettes` (once per account per run: the calendars' old palettes merged in calendar order, removed from `collection.json` only after the account is saved; what could not be kept is told); `pimstore/store.py` `old_palettes`, `forget_old_palette`. The palette editor's title names the account.
- Name and colour server first (D-192): `protocols/dav.py` `set_collection` (one PROPPATCH; a 424 part asked again alone in `send_look`), `canonical_colour` (#RRGGBBAA, #RGB to #rrggbb), `passing_failure`; `core/pim.py` `CollectionLook` with the states waiting and kept, kept in the account's `collection_looks` by folder key; `engine/collection_looks.py` `change_look` (under the account's DAV lock); `sync/davsync.py` `_follow` per collection at every sync (send what waits when the server still has the base; a clash sends nothing and is told; the same change on both sides clears), `server` values `followed` and `public` on the collection; `engine/commands_dav.py` `keep_looks` saves only looks unchanged since the sync began. Rename…, Colour… and Edit colours… greyed for read-only collections (`ui/collectionpanel.py`).
- The Windows computer's four additions to the changelist: colours compared in one form (test with #RRGGBBAA), nothing sent on a clash, the one-time merge tells what it changed, the palette as an approved exception in D-191.
- Live (Kerio, the test account): PROPPATCH of displayname and calendar-color both 200, read back as set; the original name and colour put back (`tests/live/test_live_dav.py::test_the_server_keeps_a_calendar_s_new_name_and_colour`).
- Tests: `tests/test_engine_dav.py` (11 new: server first, offline and back, refused, server changes shown, clash, the same change, the colour form, untick and re-tick, read only, a calendar from before, two computers), `tests/test_engine_calendar.py` (1 changed to the shared palette, 2 new), `tests/test_protocol_dav.py` (2 new), `tests/test_config.py` (1 new), `tests/ui/test_ui_calendar.py` (1 new); `tests/fakedav.py` answers PROPPATCH (keeps no colour, refuses names, fails with 503 on request). The new tests fail on 1.1.220 (16 of 17; the other account's own palette passed there too).
- Run: `tests/test_engine_calendar.py`, `test_engine_dav.py`, `test_sync_dav.py`, `test_pimstore.py`, `test_protocol_dav.py`, `test_engine_pim.py`, `test_engine_pim_lists.py`, `test_config.py` (178 passed, 1 failed: see below); `tests/ui/test_ui_calendar.py` (13 passed); the live test above; Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- `tests/test_engine_pim_lists.py::test_every_event_of_the_days_is_listed_only_for_the_calendars_shown` fails the same on 1.1.220: it expects 6,000 occurrences, the cap of 5,000 for a whole range (1.1.218, D-174) gives 5,000. The test is behind; not changed.
- Until R-24 brings Sync conflicts, a clash on a name or colour is told in the status line at every sync.
- A collection from before 1.1.221 whose colour differs from the server's sends its colour at the first sync; if the server's colour was changed meanwhile by another program, ours wins that once (accepted; the name is kept and told instead).

## 2026-10-20 1.1.222 The palette editor's title and Calendar row

**What landed**
- The owner tested 1.1.221 (the Kerio test calendar given a palette of Google's eleven plus Ocean, Forest, Plum, Sand and Coral, about 70% of its 508 appointments coloured, by a one-off script in the scratchpad): the colours show as intended. His remark: the palette editor's title was cut off. His design: a window's title says what it is (as "Event: Trip to Antwerp"); the data is a row inside.
- `ui/paletteeditor.py`: title "Event colours"; at the top the event editor's Calendar row (a greyed field "account — calendar", the calendar it was opened from).
- Test: `tests/ui/test_ui_calendar.py::test_the_calendars_of_an_account_share_its_palette_and_a_read_only_one_is_greyed` checks the title and the row; it fails on 1.1.221.
- Run: `tests/ui/test_ui_calendar.py` (13 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- One word for one thing: the program says "Event" in the editor and menus, "appointment" in some texts (asked the owner; the other texts unchanged until he decides).
- Whether Kerio's web client shows an appointment's colour (the owner checks on the server).

## 2026-10-20 1.1.223 One connection to a calendar server (R-36)

**What landed**
- `protocols/dav.py` `DavClient`: one connection kept open and reused (`_exchange`, under a lock); a read that finds the reused connection closed by the server is sent once more on a new one; a write is never sent twice (a new connection when the open one was idle over `IDLE_BEFORE_WRITE`, 4 s; a break is an error, a passing failure for the next sync); "Connection: close" followed; `close()` and `with`. Every engine caller closes its client (`engine/commands_dav.py`, `engine/collection_looks.py`).
- Measured on the live servers: Kerio 8.5.2 answers every request with "Connection: Close" (nothing reused there: 20 lookups 0.55 s with the old and the new client alike). The owner's slow first sync of 1.1.221 (about 450 changed appointments) had another cause: Chrono Mail's account reaches Kerio as the Kerio test server's name, and the Linux computer looks that name up in 0.25 s (LLMNR; it is not in /etc/hosts; nsswitch: files llmnr mdns4_minimal dns), once for every request.
- Tests: `tests/test_protocol_dav.py` (4 new: one connection for many requests, a read sent again on a dropped connection and a write never twice, a write after a pause on a new connection, "Connection: close"); `tests/fakedav.py` counts connections and can drop them silently or say close. They fail on 1.1.222 (mostly because the old client has no `with`).
- Run: `tests/test_protocol_dav.py` (18), `test_sync_dav.py`, `test_engine_dav.py`, `test_engine_calendar.py` (59) passed; live `tests/live/test_live_dav.py` 4 passed, 1 failed (Kerio's Public Contacts, the same with 1.1.222); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- One lookup of the server's name per client (proposed to the owner), or the name in /etc/hosts on the Linux computer (the owner's system).

## 2026-10-20 1.1.224 A calendar server's name looked up once per client

**What landed**
- The cause of the slow first sync of 1.1.221 (found with 1.1.223): every request looked up the Kerio test server's name again, 0.25 s each on the Linux computer, and Kerio closes every connection.
- `protocols/dav.py`: `DavClient._connect_to_known_address`, set as http.client's `_create_connection` of each connection: the addresses found for the name the first time are used for every later connection of the client; when none answers they are forgotten and the next connection looks the name up again. The certificate is still checked against the name, and the request names it.
- Measured through the Kerio test server's name (Kerio, read-only): 20 requests 0.31 s (before: about 0.27 s each, so about 5.4 s).
- Tests: `tests/test_protocol_dav.py` (2 new: one lookup for five connections, a failed connection looks again); both fail on 1.1.223.
- Run: `tests/test_protocol_dav.py` (20), `test_sync_dav.py`, `test_engine_dav.py` passed; live `tests/live/test_live_dav.py` 4 passed, 1 failed (Kerio's Public Contacts, as before); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-20 1.1.225 Appointments, tasks and contacts on the server first; sync conflicts in the engine (R-23, R-24 step B; D-194)

**What landed**
- `pim/merge.py`: the three-way comparison field by field of an iCalendar item or a vCard, on the text lines (nothing a program wrote is lost), with the owner's rules F1 to F5; `merge(base, local, server, choices)` gives the merged text or the clashes (key, label, both values).
- `sync/davconflicts.py`: `SyncConflict`, kept per account in the store's encrypted private file `sync-conflicts`; the agreed versions (`base-<collection>-<item>`); one conflict per item (updated, never doubled). `pimstore/store.py`: `write_private`, `read_private`, `drop_private`.
- `sync/davitems.py` `ItemGateway`: save, delete and move on the server first; offline kept with the agreed version; 412 weighed field by field; a lasting "no" raises (nothing changes here); `settle`. `sync/davsync.py`: `weigh` (shared), a change on both sides merged or a conflict (the "[Conflict]" copies are gone), changed here and deleted on the server a conflict, items with an open conflict not sent, a refusal of an offline change or delete a conflict, a passing failure ends the push.
- `engine/pim_server.py` `item_writer`: every item command (event, task and contact add, edit, delete, move, import; task complete and reopen; a tag renamed or deleted in the items) writes through it; the connection and the account's sync lock are taken at the first write of a server's collection and held to the command's end; waiting changes and conflicts are told (status line and the facts `waiting_changed`, `conflicts_changed`).
- `engine/commands_conflicts.py`: `conflict.list`, `conflict.settle` (a side, or per field), `sync.waiting` (typed).
- An accident during the work: a cut of `sync/davsync.py` went wrong (the function sought came after the one it was cut to) and the file was badly damaged; it was never run; restored from git (1.1.221) and the edit made again with checks.
- Tests: `tests/test_pim_merge.py` (11), `tests/test_engine_conflicts.py` (11: server first, offline and a restart and back, refused, merged, the same change, a clash kept once and settled with ours, settled with the server's and per field, changed here and deleted there, deleted here and changed there, an offline change refused later, two computers), `tests/live/test_live_dav.py` (Kerio: an event made, changed while the server changed it, merged, deleted, Deleted Items cleaned); changed to the new behaviour: `tests/test_sync_dav.py` (the conflict tests), `tests/test_engine_dav.py`, `tests/ui/test_ui_dav.py` (an unsent change made offline); `tests/fakedav.py` writes that fail (503) or are refused (403). The engine tests fail on 1.1.224 for the behaviour (11 of 11); the merge tests cannot load there.
- Run: `tests/test_sync_dav.py`, `test_engine_dav.py`, `test_engine_calendar.py`, `test_engine_pim.py`, `test_engine_contacts.py`, `test_engine_tags.py`, `test_pimstore.py`, `test_pim_merge.py`, `test_engine_conflicts.py`, `test_protocol_dav.py`, `test_engine_pim_lists.py` (154 passed, 1 failed: the cap test, as on 1.1.220); `tests/ui/test_ui_calendar.py`, `test_ui_contacts.py`, `test_ui_dav.py`, `test_ui_pim_changes.py`, `test_ui_tasks.py` (48 passed, 2 failed that fail on 1.1.224 too); live `tests/live/test_live_dav.py` (5 passed, 1 failed: Kerio's Public Contacts, as before); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- Step C: the window (Settings > Sync conflicts, the triangle, the sidebar entry, the waiting icon and its count).
- Step D: mail marks that clash and IMAP refusals.
- An action on a server's item waits while that account synchronises (one lock), now a few seconds at most (1.1.224).

## 2026-10-20 1.1.226 Sync conflicts in the window (R-23, R-24 step C; D-194)

**What landed**
- `ui/syncmarks.py` `SyncMarks`: the conflicts and the changes waiting for the servers, asked again (one question at a time) only on `conflicts_changed`, `waiting_changed`, `collection_changed`; `mark_icon`, `paint_mark`. Icons added to the program's Breeze copy: `emblem-warning` (the triangle) and `upload-later` (the Linux computer picked it for the owner: a clock with an arrow up, in the colour of text; the clouds do not read at 22 px).
- `ui/syncconflicts.py`: Settings > Sync conflicts (`SyncConflictsPanel`: the table, Check all, Use Chrono Mail, Use Server data, Invert selection, greyed never hidden, problems in red, rows followed in place) and the popup (`ConflictDialog`: details, a choice per field, All from Chrono Mail, All from Server, Apply). `ui/settingsdialog.py`: the page after Scheduled messages.
- The marks: `ui/calendarwidgets.py` (blocks, bars, the month's lines, the agenda), `ui/tasksview.py`, `ui/contactsview.py`; `ui/pimpages.py` `use_marks`; `ui/collectionpanel.py` the "Sync conflicts (n)" entry under the list; `ui/mainwindow.py` the window's SyncMarks, the waiting count in the status line, the entry opens the page.
- Found by the tests while building it: the month view's new mark reused the name of its row count (`room`), so from the second day on no timed event had room (`tests/ui/test_ui_calendar.py::test_the_views_navigation_the_small_calendar_and_the_search` failed; renamed `mark_room`). And a sync that found conflicts or sent what waited told the window nothing (a quiet big job turned every fact into one summary): `engine/session.py` lets `conflicts_changed` and `waiting_changed` through as they are, `engine/commands_dav.py` reports them after a sync.
- Tests: `tests/ui/test_ui_sync_conflicts.py` (4: the page and Use Chrono Mail, Use Server data and a refusal greys Use Chrono Mail, the popup field by field, the marks with the sidebar entry and the waiting count following the facts alone); the page lists in `tests/ui/test_ui_preferences.py` and `test_ui_operations.py`. The new tests cannot load on 1.1.225 (the window part does not exist there).
- Run: `tests/ui/test_ui_sync_conflicts.py`, `test_ui_settings.py`, `test_ui_preferences.py`, `test_ui_operations.py`, `test_ui_calendar.py`, `test_ui_contacts.py`, `test_ui_dav.py` (all passed), `test_ui_tasks.py` (2 failed that fail on 1.1.224 too or only in company), `test_ui_appearance.py::test_no_colour_is_written_outside_the_colour_file`; `tests/test_engine_conflicts.py`, `test_engine_dav.py`, `test_sync_dav.py` (54 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- Step D: mail marks that clash and IMAP refusals as sync conflicts; the Sync conflicts entry in Mail.

## 2026-10-21 1.1.227 Mail refusals as sync conflicts; marks never clash; waiting mail counted (R-24 step D; D-195)

**What landed**
- Found while reading the IMAP sync: marks are already compared and sent one mark at a time against the agreed marks it remembers; a mark is yes or no, so a clash on a mark cannot happen (a test proves both sides' different marks are kept, and the same mark is no conflict). The gap was refusals: an offline change the server refused was a line at every check and stayed different for ever.
- `store/store.py`: private files (encrypted). `sync/mailconflicts.py`: `MailConflict` (marks, move, message, folder refused; `kept` for Keep here only), `held`. `sync/imap_sync.py`: refusals of a move, marks, an upload and a folder made offline become conflicts, and what is held is not sent again; `marks_in_words`; `imap_waiting`.
- Engine: `engine/imap_online.py` the `imap-waiting` marker and `waiting_changed` after an action done here only; `engine/mailbox.py` `take_server_marks`, `move_back`; `engine/commands_conflicts.py` mail rows in conflict.list, settling (server, keep), sync.waiting for IMAP accounts with the marker; `engine/commands_mail.py` the facts after an IMAP check.
- Window: `ui/syncconflicts.py` Message rows, Keep here only (page and popup), Use Server data greyed where it would delete mail; `ui/mainwindow.py` Sync conflicts (n) under the mail folders; `ui/delegate.py` the waiting mark before the subject (cards and columns, the column drawn as Qt draws a cell with an icon).
- Tests: `tests/test_engine_mail_conflicts.py` (4: marks refused and settled, a move refused and moved back, a folder kept here only, marks merged per mark), `tests/test_sync_imap.py` (the refused upload is a conflict, not retried while open), `tests/ui/test_ui_sync_conflicts.py` (2 more: mail rows and Keep here only with the entry under the mail folders; the waiting mark in the message list); `tests/fakeimap.py` refuse_stores, refuse_moves, refuse_creates. The three refusal tests and the upload test fail on 1.1.226; the marks test passes there too (it proves what was already right).
- Run: every `tests/test_*imap*.py`, `test_engine_mail_conflicts.py`, `test_engine_conflicts.py`, `test_sync_dav.py` (200 passed); `tests/ui/test_ui_columns.py`, `test_ui_appearance.py`, `test_ui_preferences.py`, `test_ui_sections.py`, `test_ui_sync_conflicts.py`, `test_ui_models.py`, `test_ui_window.py` (205 passed); live `tests/live/test_live_imap.py`, `test_live_imap_online.py` (27 passed, 3 skipped); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

**Still open**
- A refusal that is not really lasting (a full mailbox): Keep here only leaves the message here for good; a "Try again" may be wanted (asked).

## 2026-10-21 1.1.228 Retry sync (the owner)

**What landed**
- The Linux computer's question after 1.1.227 (a full mailbox leaves a message "Keep here only" for good); the owner: a "Retry sync" instead of a winner, below the list with the other buttons, for the ticked rows.
- `engine/commands_conflicts.py`: `conflict.settle` side `retry` releases a refusal (calendars: refused, delete refused; mail: all) and names the accounts to synchronise (`SettleResult.retried`); refused for a clash with a reason; `ConflictSummary.retry_allowed`.
- `ui/syncconflicts.py`: Retry sync below the list (order: Use Chrono Mail, Use Server data, Retry sync, Keep here only, Invert selection) and in the popup, greyed for a clash; `sync_requested`; `ui/mainwindow.py` `_retry_sync` checks those accounts at once (mail and calendars). The window's 1.1.227 history line, missed then, added.
- Tests: `tests/test_engine_mail_conflicts.py` (retry: refused again comes back once, then taken), `tests/test_engine_conflicts.py` (retry of a refused calendar change; refused for a clash), `tests/ui/test_ui_sync_conflicts.py` (greyed for a clash, enabled for a refusal, the account synchronised at once); all three fail on 1.1.227.
- Run: `tests/test_engine_mail_conflicts.py`, `test_engine_conflicts.py`, `test_sync_dav.py`, `test_sync_imap.py` (87 passed); `tests/ui/test_ui_sync_conflicts.py`, `test_ui_preferences.py`, `test_ui_settings.py` (53 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-21 1.1.229 Sync conflicts in a window of its own (the owner)

**What landed**
- The owner tested a real conflict (made with `scratchpad/make_test_conflict.py`): it worked; his remarks: the triangle too big, the entry pinned to the bottom, the columns could not be dragged (ResizeToContents), the table too wide for the Settings window, so "it isn't really settings". His rule, saved: what a person would guess when he does not know how it works is how it should work.
- `ui/syncconflicts.py`: `SyncConflictsWindow` (non-blocking, about 1,100 px, size remembered, closing hides it); the table Fields, What, Name, Where, Why; Why as an icon (`ui/syncmarks.py` `why_icon`, four kinds; Breeze exchange-positions, process-stop, network-server and computer with emblem-remove, copied into the program's icons) with hover texts and a legend at the bottom left; columns Interactive, fitted to their contents at first, dragged widths remembered (`sync_conflicts_columns`), no stretch (a scrollbar when wider); `open_item` for a triangle's click. `ui/settingsdialog.py` without the page.
- The marks 11 px (`MARK_SIZE`); a click on a triangle opens its conflict and hovering a mark says what it is: `ui/calendarwidgets.py` (`mark_hits`, `mark_at`, `conflict_clicked`, tool tips), `ui/tasksview.py` and `ui/contactsview.py` (their row painters' `editorEvent` and `helpEvent`), `ui/delegate.py` (the waiting mark's hover text, cards and columns); `ui/pimpages.py` `conflict_requested`. `ui/mainwindow.py`: Menu > Sync conflicts…, `open_sync_conflicts`, `_open_conflict`, the entries at the top of the sidebars (also `ui/collectionpanel.py`).
- Tests: `tests/ui/test_ui_sync_conflicts.py` (9; 6 fail on 1.1.227: the columns, the window, the legend, the click on a triangle, the entries at the top); the page lists of `tests/ui/test_ui_preferences.py` and `test_ui_operations.py` as before 1.1.226.
- Run: `tests/ui/test_ui_sync_conflicts.py` (9), `test_ui_calendar.py`, `test_ui_contacts.py`, `test_ui_preferences.py`, `test_ui_operations.py`, `test_ui_settings.py`, `test_ui_sections.py`, `test_ui_columns.py`, `test_ui_window.py` (181 passed, 1 failed that passes alone, known in STATUS), `test_ui_tasks.py` (the known older failure), the colour-file guard; Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-21 1.1.230 The mark evenly placed (the owner)

**What landed**
- The owner's screenshot: the triangle almost touched the coloured dot in the month view, with a wide gap after it. The cause: the mark started at 15 px (the dot ends at 12.5) and the text 4 px after it plus the 1 px the text was set further in.
- `ui/syncmarks.py` `MARK_GAP` 3 (the text 3 px after a mark everywhere); `ui/calendarwidgets.py`: in the month's lines the mark starts at 16 px, where the text starts without one; in blocks and bars the text 3 px after the mark.
- Test: `tests/ui/test_ui_sync_conflicts.py::test_a_click_on_the_triangle_opens_its_conflict_and_the_entry_is_at_the_top` checks the place and the gap; it fails on 1.1.229.
- Run: `tests/ui/test_ui_sync_conflicts.py`, `test_ui_calendar.py`, `test_ui_contacts.py` (30 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-21 1.1.231 "Why" centred (the owner)

**What landed**
- `ui/syncconflicts.py`: the header "Why" centred; its icons drawn in the middle of the cell by `CentredIconDelegate` (Qt draws a cell's icon at its left edge whatever the alignment; the icon is taken from the item, because the option's own icon object is emptied with the option). The owner also asked why Retry sync and Keep here only were greyed: a clash, as agreed; no hover text wanted.
- Test: `tests/ui/test_ui_sync_conflicts.py::test_why_and_its_icon_are_centred_in_their_column` (the header's alignment, and the icon's pixels in the middle of a wide cell); it fails on 1.1.230.
- Run: `tests/ui/test_ui_sync_conflicts.py` (10 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-21 1.1.232 The legend in one column; Close in its place (the owner)

**What landed**
- `ui/syncconflicts.py`: the legend's four entries one under the other; the window keeps its own margin and the panel inside it none, so Close lines up with the table and the buttons (it sat in the corner: the window had no margin).
- Test: `tests/ui/test_ui_sync_conflicts.py::test_the_legend_is_one_under_the_other_and_close_lines_up`; it fails on 1.1.231.
- Run: `tests/ui/test_ui_sync_conflicts.py` (11 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.

## 2026-10-21 1.1.233 The conflicts table fills the window; line icons (the owner)

**What landed**
- The owner: like the message list, the table always fills the width; the triangle in every row says nothing; the tick box, Fields, What and Why fixed, Name and Where variable (the Linux computer advised the message list's way: shares, draggable, remembered); the small Breeze icons lost their details, so line icons like the toolbar's; for the minus a solid circle with the minus cut out; the database drum for the server.
- `ui/syncconflicts.py`: fixed sections (the tick box 28 px, Fields two names wide, What, Why) and Name (Interactive) and Where sharing the free width (`fit_columns` on every resize of the table and of a fixed column; `SHARE_KEY`, 60/40 at first); no horizontal scrollbar; no triangle in the rows; `WhyDelegate` draws the kind in the middle of the cell with the row's colours.
- `ui/syncmarks.py`: `paint_why`, `why_pixmap`: our own two arrows, Breeze dialog-cancel-symbolic, network-server-database-symbolic and computer-symbolic (copied in; the five icons of 1.1.229 removed), the solid badge drawn in the text colour with the minus in the background colour (inverted in the light theme by itself).
- Tests: `tests/ui/test_ui_sync_conflicts.py` (12; the width filled at two sizes, the fixed columns kept, the share after a drag, the lines in the text colour, no triangle, the column modes); three fail on 1.1.232.
- Run: `tests/ui/test_ui_sync_conflicts.py` (12), `test_ui_calendar.py`, `test_ui_contacts.py`, the colour-file guard (22); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.


## 2026-10-21 1.1.234 R-25: a collection deleted on the server goes here only on the server's confirmation

**What landed**
- The owner: "confirmation of deletion on the server"; no answer or an error is never that. A collection no longer listed is asked about directly (`DavClient.exists`); only 404 or 410 confirm it. Otherwise it is kept, one line in the report, asked again next time.
- Found while doing it: `DavClient.collections` skipped a home whose listing failed, so a failing server looked like "no calendars" (then only untied; with R-25 it would have deleted them). Now that is an error that stops the synchronisation.
- `sync/davsync.py` `_gone_from_list`: confirmed and unchanged here: `delete_collection` (deleted items, 30 days), `report.removed`; changed here (unsent items, or a name or colour waiting): a conflict `collection_deleted_on_server` (`sync/davconflicts.py`, the old tie kept in `tie`), the collection untied meanwhile. `engine/commands_conflicts.py` `_settle_collection`: Use Server data deletes it here; Use Chrono Mail makes it again (`DavClient.make_collection`: MKCALENDAR, extended MKCOL, Kerio's 207 answer checked per property), gives the tie back, forgets its sync state and asks for a synchronisation at once (`retried`), which sends every item as new. `dav.sync` answers `removed`; the status line says so; the Sync conflicts window shows What as Calendar, Task list or Address book.
- Checked live on Kerio first: MKCALENDAR 201, DELETE 204, PROPFIND after the delete 404; MKCOL of an address book 207 with 200.
- Tests: `tests/test_sync_dav.py` (5 new, the old "stays without a tie" test replaced), `tests/test_engine_conflicts.py` (4 new), `tests/test_protocol_dav.py` (3 new), `tests/fakedav.py` (MKCALENDAR, MKCOL, a failing home listing, a failing collection check), live `tests/live/test_live_dav.py::test_a_calendar_deleted_on_the_server_goes_here_only_on_its_confirmation` (Kerio, passed). The 11 new offline tests run fail on 1.1.233.
- Run: `tests/test_sync_dav.py`, `test_engine_conflicts.py`, `test_protocol_dav.py`, `test_engine_dav.py`, `test_engine_pim.py`, `tests/ui/test_ui_sync_conflicts.py`, `test_ui_calendar.py` (all passed), the live test above; Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-28 (step B), then R-29 (step C).

## 2026-10-21 1.1.235 R-28: Delete of a server's calendar deletes it on the server

**What landed**
- The owner's choice: Delete removes it from the server; every program that synchronises with that server follows by itself.
- `engine/collection_delete.py` (new): the server's items compared with the agreed versions (`sync/davdeletes.py` `changed_on_server`); changed there: nothing deleted, told; else DELETE, then here (`_gone_here`: deleted items, sync state and kept look dropped). A calendar holding events and tasks there (two collections here) goes as one (`together`). Offline: here at once, remembered in `collection-deletes` (encrypted private file of the pim store), a notice. Read only: refused.
- `sync/davsync.py` `_send_deletes`: a waiting delete is never made here again; at the next sync: gone there already: done; changed there: conflict `collection_changed_on_server`; refused for good: `collection_delete_refused`; a passing failure tries again later. `engine/commands_conflicts.py` `_settle_delete`; `sync.waiting` counts waiting deletes.
- Window: the Delete question names the server and what goes along; Delete greyed for read-only with a hover text (the owner's rule: grey, never hide).
- Found live on Kerio: a calendar made at `…/chrono-mail-test-x/` is listed at `…/<GUID>/` (the asked address stays an alias); an address book: MKCOL answers 207 with 200 but nothing is made; an unknown address under the contacts home answers an empty 207. So `make_collection` returns the collection as the server lists it and raises when none is listed; `exists` takes an empty answer as "not there"; Use Chrono Mail of R-25 ties to the listed address (before: it would have tied to the alias, been told "still there" at every sync, and got a twin).
- Tests: `tests/test_engine_conflicts.py` (8 new), `tests/test_protocol_dav.py` (2 new, 1 extended), `tests/ui/test_ui_calendar.py` (Delete greyed for read only), `tests/fakedav.py` (DELETE of a collection, a MKCOL that makes nothing, an empty answer for an unknown address), live `test_a_calendar_deleted_here_offline_is_deleted_on_the_server_at_the_next_sync` and `test_a_calendar_made_on_the_server_is_found_where_the_server_lists_it` (Kerio, passed). The 9 new or changed offline tests fail on 1.1.234.
- Run: `tests/test_sync_dav.py`, `test_protocol_dav.py`, `test_engine_dav.py`, `test_engine_conflicts.py`, `test_engine_pim.py`, `tests/ui/test_ui_calendar.py`, `test_ui_sync_conflicts.py`, `test_ui_dav.py` (all passed; `test_engine_pim_lists.py` has its known failure from before), the live tests above; Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-29 (step C).

## 2026-10-21 1.1.236 R-29: New calendar, task list or address book in the account chosen

**What landed**
- `ui/newcollection.py` (new): the New window with Name and Account (`collection.accounts`: the local ones, then every account with a calendar server), set to the account right-clicked (`ui/collectionpanel.py` `menu_at`: the heading or a collection's account); the local ones when opened from nothing.
- `engine/collection_create.py` (new): the local ones here; a server's account on the server first (`sync/davcreates.py` `make_on_server`, in the home of its kind at `chrono-mail-<random>/`, tied to the address the server lists it at); a lasting refusal makes nothing; a colour the server keeps not kept with the account. Offline: made here untied, remembered in `collection-creates`, a notice; `sync.waiting` counts it and its items.
- `sync/davsync.py` `_send_creates` (start of a synchronisation, before the listing): made on the server, tied, then its items go in the same round (`made_on_server`); a lasting refusal: conflict `collection_create_refused`; `engine/commands_conflicts.py` `_settle_create`: Keep here only copies it into the local ones (each item written and read back) and then takes it from the account; Retry sync.
- `collection.create` is typed (`COLLECTION_CREATE`) and answers the full summary (server, together).
- Live on Kerio: calendars and task lists made (listed at a GUID address); an address book refused in plain words ("made no address book").
- Tests: `tests/test_engine_conflicts.py` (8 new), `tests/ui/test_ui_dav.py::test_new_calendar_is_made_in_the_account_right_clicked`, `tests/ui/test_ui_sections.py` (the New window instead of the name question), `tests/fakedav.py` (no colour kept for a new calendar either), live `test_a_new_collection_is_made_on_the_server_or_refused_in_plain_words` (3, Kerio). The 10 new or changed offline tests fail on 1.1.235.
- Run: `tests/test_engine_conflicts.py`, `test_engine_pim.py`, `test_engine_dav.py`, `test_sync_dav.py`, `test_protocol_dav.py`, `tests/ui/test_ui_dav.py`, `test_ui_sections.py`, `test_ui_calendar.py`, `test_ui_sync_conflicts.py`, `test_ui_contacts.py` (all passed), `test_ui_tasks.py` (its known failures: the large list, and the quick-add test only in a combined run; the same on 1.1.235); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-30.

## 2026-10-21 1.1.237 R-30: IMAP folders deleted or renamed on the server follow here

**What landed**
- Checked live first: Dovecot and Kerio keep UIDVALIDITY on a rename; Kerio answers SELECT of a deleted folder with a plain NO (so the exact-name LIST is the confirmation).
- `protocols/imap.py` `folder_exists` (LIST of one name; only the exact name counts). `sync/imap_state.py` `server_names` (event "n", moved on a rename, dropped when forgotten, compacted). `sync/imap_folders.py` (new) `follow_server_folders`, run before the folders of a check are made here: asked by name; renamed (same UIDVALIDITY, folder of its own) → store, index, state renamed, `folder_renamed` told; deleted and nothing waiting → taken away here (subtree, to .deleted); deleted with mail only here or marks changed here → `folder_deleted_with_mail_here` / `folder_deleted_on_server` conflicts (`sync/mailconflicts.py`), state forgotten; a standard folder → the server's mail goes here, the rest stays, forgotten so the same check makes it again and uploads. `held_under`: a folder inside a held one is not made on the server.
- `sync/announce.py` `folder_renamed`; `engine/commands_mail.py` tells it and never reports a renamed folder as deleted; `engine/mailbox.py` `report_folder_renamed`. `engine/commands_conflicts.py` `_settle_gone_folder` (Use Chrono Mail: released, check at once; Use Server data: taken away here, only without mail that exists only here; Keep here only). Window: What = Folder; the Why icon "deleted on the server".
- Tests: `tests/test_sync_imap.py` (7 new; the old test that a vanished folder keeps its mail now expects it gone), `tests/test_engine_mail_conflicts.py` (4 new), `tests/fakeimap.py` (LIST of one name, refuse_single_list), live `tests/live/test_live_imap.py::test_a_folder_renamed_and_deleted_on_the_real_server_follows_here` (Dovecot and Kerio, passed). 11 of the 12 new or changed tests fail on 1.1.236 (the unsubscribed one passes there too: that behaviour did not change).
- Run: `tests/test_sync_imap.py`, `test_engine_mail_conflicts.py`, `test_engine_imap_folders.py`, `test_engine_imap_folder_actions.py`, `test_protocols_imap.py`, `test_engine_imap.py`, `test_engine_imap_online.py`, `test_imap_tags.py`, `test_engine_conflicts.py`, `tests/ui/test_ui_sync_conflicts.py`, `test_ui_folder_counts.py`, `test_ui_push.py` (all passed); `tests/test_mailbox_guard.py` fails as it did on 1.1.236 (see STATUS Problems); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-35.

## 2026-10-21 1.1.238 R-35: an expunge never removes more than the messages named

**What landed**
- The cause: `ImapClient.uid_expunge` sent a plain EXPUNGE without UIDPLUS (every message marked deleted in the folder goes, another program's too); the offline move (`uid_move` without MOVE) used it unguarded. Now `uid_expunge` refuses without UIDPLUS, `uid_move` removes the original only with UIDPLUS (else it stays marked deleted, as D-117 already said for online moves), and `engine/imap_online.py` moves through `uid_move` as the sync does.
- Live: Dovecot has MOVE and UIDPLUS, Kerio UIDPLUS without MOVE: neither was affected.
- Found: without UIDPLUS the original left marked deleted is downloaded again by the next check (it shows twice); this was already so for online moves; now R-38.
- Tests: `tests/test_sync_imap.py::test_an_offline_move_never_removes_another_program_s_deleted_mail`, `tests/test_engine_imap_online.py` (2 new), `tests/test_protocols_imap.py` and `test_sync_imap.py::test_a_move_without_the_new_uid_is_matched_by_message_id_afterwards` updated to the safe behaviour. 3 fail on 1.1.237 (the online move test passes there: it was guarded already).
- Run: `tests/test_sync_imap.py`, `test_engine_imap_online.py`, `test_protocols_imap.py`, `test_engine_imap.py`, `test_engine_mail_conflicts.py`, `test_engine_imap_folder_actions.py` (all passed); live move tests on both servers (6 passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-37.

## 2026-10-21 1.1.239 R-37: mail conflicts through the mailbox

**What landed**
- The owner's decision: through the mailbox. `engine/mailbox.py`: `sync_conflicts` (listed, or with kept ones), `forget_sync_conflict`, `keep_sync_conflict_here`, `waiting_for_server`. `engine/commands_conflicts.py` and `engine/commands_mail.py` use them and no longer hand a mailbox's store to other code. No behaviour changed.
- Test: `tests/test_mailbox_guard.py::test_the_store_or_index_is_handed_over_only_where_allowed` passes again (it failed since 1.1.227, also on 1.1.238).
- Run: `tests/test_mailbox_guard.py`, `test_engine_mail_conflicts.py`, `test_engine_conflicts.py`, `test_engine_imap.py`, `test_engine_imap_online.py`, `test_sync_imap.py`, `tests/ui/test_ui_sync_conflicts.py` (all passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-31 (the owner decides question 3.4).

## 2026-10-21 1.1.240 R-31: IMAP folder rename and delete work offline

**What landed**
- The owner's rule D-193 1.3 (REFACTOR.md had still listed it as an open question): allowed offline, kept, done on the server when the connection is back.
- `sync/imap_state.py`: `PendingFolder`, `pending`, `rename_later`, `delete_later`, `force_delete`, `pending_done` (events pr/pd/pf/pz, kept through compaction). `engine/imap_online.py`: offline (or the connection lost) a rename or delete commits through them instead of being refused (`_later`, told and counted as waiting); the old refusal text is gone. `sync/imap_pending.py` (new) `send_pending`, run right after the first LIST of a check: RENAME (subscriptions and server names moved) or DELETE; a delete checks the folder first (UIDVALIDITY and UIDs): new mail there: conflict `folder_deleted_here_changed`; a lasting no: `folder_rename_refused` / `folder_delete_refused`. A folder waiting for one is left out of the plans. `imap_waiting` counts them.
- `engine/mailbox.py`: `undo_folder_rename`, `give_up_folder_change`, `delete_folder_anyway` (under the account's link lock). `engine/commands_conflicts.py` `_settle_pending_folder`. Window: the Why icons.
- Also fixed: before, a folder deleted offline was only hidden by the next check (`_forget_deleted_folders`) and stayed on the server.
- Tests: `tests/test_engine_imap_folder_actions.py` (6 new; the old refusal test is now "a new folder follows at the next check"), `tests/test_sync_imap.py::test_folder_changes_made_offline_survive_a_reload_and_a_compaction`, live `test_a_folder_renamed_and_deleted_offline_follows_on_the_real_server` (Dovecot and Kerio, passed). The 7 new tests fail on 1.1.239.
- Run: `tests/test_sync_imap.py`, `test_engine_imap_folder_actions.py`, `test_engine_imap_folders.py`, `test_engine_imap_online.py`, `test_engine_mail_conflicts.py`, `test_mailbox_guard.py`, `test_engine_imap.py`, `test_engine_conflicts.py`, `test_protocols_imap.py`, `tests/ui/test_ui_sync_conflicts.py`, `test_ui_folder_counts.py` (all passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- R-38 dropped: the owner does not support servers without UIDPLUS.
- Next: R-32 (the owner decides question 3.3).

## 2026-10-21 (documents only) R-32 and R-33: the owner's rule D-201

- The owner: what the server supports is synced to it and reaches other clients; what it does not support stays on this computer only and changes nothing on the server. Recorded as D-201 (the case-by-case approval of D-193 1.6, given once for all). R-32 (snooze time, archive origin) and R-33 (Kerio tags, tag colours and shortcuts) work as intended; no code. Next: R-34.

## 2026-10-21 1.1.241 R-34: the server's list of folders to show is followed (D-202)

- The owner: the ticks in Edit account > Folders are this computer's only (hide folders never used); the server's list of folders to show (IMAP subscriptions) is followed. Contact groups split off as R-39 (later).
- `sync/imap_folders.py` `leaving_folders`, `take_away_unlisted`: a folder synchronised before, still on the server but off its list and not ticked here, is synchronised one last time in the check, then its copy here goes (store .deleted, index, state); kept while something the server has not got is in it, or a shown folder is inside it. `sync/imap_sync.py` `planned_anyway`: the ticked folders and the standard folders synchronised before are planned whether or not they are on the list (also in `engine/commands_imap.py` and `engine/imap_online.py`); a ticked folder the list now has follows the list (`ImapState.follow_list`, event "l").
- Found on the way: a standard folder unsubscribed on the server was silently no longer kept in step.
- Tests: 11 new in `tests/test_sync_imap.py` (the old "no longer subscribed is no deletion" test rewritten to the new rule), 1 in `tests/test_engine_imap_folders.py`, 1 live in `tests/live/test_live_imap.py` (DirectAdmin and Kerio, passed). 8 of the 12 new offline tests fail on 1.1.240; 4 guard what already worked. Run: `tests/test_sync_imap.py`, `test_engine_imap_folders.py`, `test_engine_imap_folder_actions.py`, `test_engine_imap_online.py`, `test_engine_imap.py`, `test_engine_mail_conflicts.py`, `test_mailbox_guard.py`, `test_engine_conflicts.py`, `test_imap_tags.py` (all passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-03.

## 2026-10-21 1.1.242 R-03: the filters narrow Favorites and Tags views (D-203)

- A fault: `MainWindow.view_spec` built a Favorites or Tags view with only the search words; the Filter menu turned blue and changed nothing; Advanced search's days and sizes were lost there too. `engine/commands_combined.py` takes the filters (`unread`, `flagged`, `has_attachments`, `filter_tag`, the days and sizes) for the list, groups, places and locate; they add to the view's own marks or tag (`SearchFilter.also_tag` for the second tag; a filter against the view's own mark is refused). `ui/models.py`: `ViewSpec.combined_tag` for the Tags view's tag, `tag` is Filter > Tagged with; the filters are sent; a message coming in must match the filter too.
- Tests: 3 new in `tests/test_engine_local.py`, 3 in `tests/ui/test_ui_tags.py`; all 6 fail without the change. Run: `tests/test_engine_local.py`, `test_engine_tags.py`, `test_engine_search.py`, `test_index_entry.py`, `test_index_index.py`, `test_index_rebuild.py`, `test_index_tags.py`, `test_index_text.py`, `tests/ui/test_ui_window.py`, `test_ui_list_changes.py`, `test_ui_models.py`, `test_ui_advancedsearch.py`, `test_ui_tags.py` (all passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-04.

## 2026-10-21 (documents only) R-04: was D-146 step 5 done for Tasks and Contacts?

- Checked against the DEVLOG of 0.5.159 and the code. Contacts: each fact changes its own row (`ContactListModel.put` / `remove_item`: insert, remove, move, dataChanged); a full load only when the person changes the address book, tag or search. Calendar: the blocks of the event only. Tasks: `apply_item` → `_show_rows` → `TaskListModel.load(keep_rows=True)`, a model reset per change with the selection, current task and scroll put back by hand (rows are not fetched again). Contacts after a save still fetches the whole tag list for its numbers, an error ignored.
- D-146 corrected (old wording in the archive); R-06 narrowed to Tasks and the Contacts tag list. No code. Next: R-05.

## 2026-10-21 1.1.243 R-05: one set of server boxes for Add and Edit account (D-204)

- `ui/serverfields.py` (new): `ServerFields` builds the incoming and outgoing boxes for `ui/accountdialog.py` and `ui/accountedit.py` (same rows, labels and order; Edit keeps "New password" and "(unchanged)"); the port follows Security (and the kind in Add) only while it holds the standard port of the old choice; `check_values`, `endpoint`, `test_result_text`; `kind_of` and `AccountKind` instead of kind text.
- `engine/commands_accounts.py` `account.check`: `account` given: a password not typed is the stored one (incoming; outgoing: the stored outgoing one for its own login, the incoming one with the one-login tick); nothing typed and no account: "No password was typed, and none is stored". Edit's Test connection no longer says "Type the password" (the owner: never ask for a password again).
- Tests: `tests/test_engine_commands.py` 1 new; `tests/ui/test_ui_settings.py` the old "needs the password typed" test rewritten to the new rule, 1 new (the same boxes, the port rule in Edit); `tests/ui/test_ui_dialogs.py` 1 new (a typed port stays); the 3 in the engine and settings files fail on 1.1.242 (the dialogs test cannot even import there). Run: `tests/ui/test_ui_settings.py`, `test_ui_certificates.py`, `test_ui_key_recovery.py`, `test_ui_dav.py`, `test_ui_dialogs.py`, `tests/test_engine_commands.py`, `test_engine_certificates.py`, `test_engine_imap.py` (all passed); Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-06.

## 2026-10-21 1.1.244 R-06: Tasks and Contacts follow one change without laying the list out again (D-146)

- `ui/tasksview.py`: `grouped` (the headings, also for `load`); `TaskListModel.follow(places, item)` moves the task's row (beginMoveRows, so a selection on it stays with it), inserts it, or takes it away (`drop`); a heading it needs is inserted at its place (`HEADING_ORDER`), one left empty goes; `apply_item` uses them; `_show_rows` (a model reset with the selection, current task and scroll put back by hand with blocked signals) is gone; `load` only for another view; `_after_save` selects the saved task directly or when its fact comes.
- `ui/contactsview.py` `_follow_tags`: the tag list changed in place (numbers, a tag that came or went, a renamed one moved), the chosen tag stays without loading the list again; a failure of `tag.list` is told in the status line (it was ignored).
- Tests: `tests/ui/test_ui_tasks.py` 2 new (the model follows one change without a reset, persistent indexes stay with their tasks, headings come and go; completing tasks keeps the selection and never resets), `test_a_task_is_added_quickly_completed_reopened_and_deleted` now waits for the reopened row's text (the heading moves at once, the text comes after); `tests/ui/test_ui_contacts.py` 1 new (in place, chosen tag kept, failure told). All 3 new fail on 1.1.243. Run: `tests/ui/test_ui_tasks.py`, `test_ui_contacts.py`, `test_ui_pim_changes.py`, `test_ui_sections.py`, `test_ui_sync_conflicts.py`: 40 passed, 2 failed, the two known since 1.1.224 (`test_every_task_of_a_large_list...` fails the same on 1.1.243, checked; `test_the_panel_changes_one_entry_and_its_numbers`). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-07.

## 2026-10-21 1.1.245 R-07: Tasks and Contacts share one list design

- `ui/pimlist.py` (new): `PimItemModel` (pending, mark_of, position_of, item_at, redraw), `MarkDelegate` (the triangle: a click opens its conflict, hovering says what a mark is), `PimListPage` (Delete with its question, Move to, New tag…, the greying and selection move at the key press, the search delay of 250 ms, focus on the search field, the error per item, conflict_sources, marks_changed); the words and commands are class attributes (`item_word`, `delete_command`, `move_command`, `move_field`). `PimPage._failed` with `section_name`: the error line of Calendar, Tasks and Contacts. `ui/tasksview.py` and `ui/contactsview.py` lost their copies (909 → 821 and 732 → 647 lines); the Calendar shares only the error line (one selection, occurrences).
- Nothing the user sees changed. Tests: `tests/ui/test_ui_pimlist.py` (new: both pages are the shared design, the same Delete question with their own word, the error line, the triangle). Run: `tests/ui/test_ui_pimlist.py`, `test_ui_tasks.py`, `test_ui_contacts.py`, `test_ui_calendar.py`, `test_ui_pim_changes.py`, `test_ui_sync_conflicts.py`, `test_ui_sections.py`: 55 passed, 1 failed (`test_the_panel_changes_one_entry_and_its_numbers`, known since 1.1.224). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-08.

## 2026-10-21 1.1.246 The GitHub check is removed (D-205)

- The owner's report: an endless stream of "Run failed: checks" emails. Cause: `.github/workflows/ci.yml` ran Pyrefly, the layer check and `pytest -q` at every push of the branch and of the tag; since 1.1.176 the tests are not in git (D-161), so pytest found nothing and ended with exit code 5 (Pyrefly and the layer check passed there). The owner chose to remove it: `.github/workflows/ci.yml` deleted; ROADMAP task 0.4 marked removed. `gitpush.sh` never used the check. No code changed, no tests run.

## 2026-10-21 1.1.247 R-08: many messages, one command per folder (D-206)

- `protocols/imap.py`: COPYUID with sets read into `AppendResult.uids` (`uid_set`, capped at 100,000; "4:" refused). `engine/imap_online.py`: `Ready`, `ServerSession.ready`, `prepare_flags` / `prepare_moves` / `prepare_copies` / `prepare_deletes` (one UID SEARCH per 500 for presence, then one command per 500 and per kind of mark change); the single steps take a ready result first. `engine/mailbox.py`: `_server()` (one session reused), `together()`, `prepare_*` (only for messages that pass the checks of their own step). `engine/commands_many.py` (new): `message.delete-many`, `move-many`, `copy-many`, `flag-many`, `archive-many`, `unarchive-many`, `junk-many`, `not-junk-many`, `snooze-many`, typed, `ManyResult`; the single commands split into per-message steps (`archive_one`, `unarchive_target`, `unarchive_one`, `junk_one`, `not_junk_one`, `snooze_one`, `flags_wanted`). `ui/backend.py`: `ask(in_order=)`. `ui/mainwindow.py`: `_act_many` and `_targets`; Delete, Delete for good, Move to, a drag, Copy to, Archive, Unarchive, Junk, Not junk, Snooze and the marks use them; one error line for the failures.
- Measured (fake server, this computer): 1,000 messages moved one by one 7.56 s and 3,002 server commands; as one command 0.37 s and 8.
- Tests: `tests/test_engine_many.py` (new, 9: one command per group for move, marks, Trash, for good, copy, archive, junk, snooze, back; a refused group; a message gone on the server; offline and back; `uid_set`; 1,000 messages); `tests/ui/test_ui_window.py` 1 new (one command, one error line, the failed message back); `tests/ui/test_ui_rapid_actions.py` follows the new command name; `tests/live/test_live_imap.py` 1 new (marks, move and delete for good in groups on DirectAdmin and Kerio, passed). The new tests fail on 1.1.246. Run: the engine IMAP, mailbox guard, protocol, commands, local, actions, schedule and conflict files (280 passed); window, rapid actions, list changes, drag and drop, schedule, folder counts, tags, backend, shortcuts (170 passed, 1 failed: `test_ui_schedule.py::test_a_new_scheduled_message_is_made_on_the_scheduled_page`, fails about one run in five on 1.1.246 too, known since 1.1.207). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-09.

## 2026-10-21 1.1.248 R-09: a check does work for what changed (D-207)

- `protocols/imap.py`: `enable_changes` (ENABLE QRESYNC, else CONDSTORE; a refusal switches nothing on), `uid_changes` (UID FETCH 1:* with CHANGEDSINCE and, with QRESYNC, VANISHED; a VANISHED set too large to read means the removals are not known), `FolderChanges`, `enabled`. `sync/imap_state.py`: `Checkpoint`, `checkpoints`, event "c" (UIDNEXT, HIGHESTMODSEQ or null; without them: forgotten), cleared by a new UIDVALIDITY, hide and forget, moved by a rename, kept by compaction. `sync/imap_sync.py`: `_Here` (every folder's messages, the places, the properties listing, read once per check, places kept current by removals, downloads, mark changes and Recovered moves); `_read_server` (checkpoint and counter: nothing or only the changes; the count against EXISTS; without a counter 1:UIDNEXT-1 marks and UIDNEXT:* with sizes; sizes asked for unknown messages without one; anything else the full listing); `_push_flags` asks the marks of the messages it sends first, 500 per command, when the server did not tell them in this check; a checkpoint is written only when the folder's check had no failure, no question and no server change waiting behind a change here. `store/properties.py`: `readable` and `tags` take an earlier listing.
- Checked live: DirectAdmin (Dovecot) has CONDSTORE and QRESYNC (HIGHESTMODSEQ only after ENABLE); Kerio has neither (and no ENABLE).
- Measured (`scratchpad/r09_measure.py`, fake server, 10 folders × 2,000 messages, a check with nothing changed): QRESYNC 1.35 s and 10 full listings before, 0.37 s and none after; without a counter 1.49 s before, 0.85 s after.
- Found in passing: R-40 (a message removed on the server gives a false "could not be read for sending" line; on 1.1.247 too). Not changed (not in the changelist).
- Tests: `tests/test_sync_imap_changes.py` (new, 10: an unchanged folder asks no list; a mark changed there comes alone; a removal without a full list; new mail with the changes; CONDSTORE without QRESYNC: a removal seen by the count; no counter: sizes only for new mail; after a failure everything is asked; a mark sent with the server's marks asked first; this computer's mail read once per check; the checkpoint kept, moved, forgotten); all 10 fail on 1.1.247. `tests/fakeimap.py`: CONDSTORE and QRESYNC on request. `tests/live/test_live_imap.py`: the sync test checks the way each server takes (passed on DirectAdmin and Kerio). Run: the IMAP sync, engine IMAP (4 files), many, properties (2), tags, mailbox guard, protocol files (235 passed); certificates, watch, mail conflicts (62 passed); `tests/ui/test_ui_push.py` (5 passed); live `test_live_imap.py` and `test_live_imap_online.py` (35 passed, 3 skipped). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-10 (or R-40, the owner decides its place).

## 2026-10-21 1.1.249 R-40: no false error line after a removal on the server

- Cause: `sync/imap_sync.py` `_remove_gone` deleted the message here and forgot its UID, but the check's list of the folder's messages (`_Folder.stored`) still held it, so `_upload` took it for mail made here, tried to read it and reported "A local message could not be read for sending: Message not found" (since the upload step exists; seen on 1.1.247 and 1.1.248). It also kept that check from writing the folder's checkpoint (R-09). Fix: the removed message leaves that list too.
- Tests: `tests/test_sync_imap_changes.py` 2 new (with and without a change counter: removed, no error line, nothing sent back, the checkpoint written); both fail on 1.1.248 with only the fix taken out. Run: the IMAP sync (2), engine IMAP (3), mail conflicts and tags files (133 passed); live `test_sync_against_the_real_server` (DirectAdmin and Kerio, passed). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-10.

## 2026-10-21 1.1.250 R-10: the folder numbers are the engine's (D-208)

- `index/schema.py`: format 10, `folder_numbers` (folder, messages, unread; WITHOUT ROWID) and three triggers (after insert, after delete, after an update of folder or flags that changes the folder or the read bit); `UPGRADE_9_TO_10` counts once with GROUP BY. `index/index.py`: `numbers()`, `_upgrade_to_10`. `engine/changes.py`: `FolderNumbers`, `NUMBERED_KINDS`, `Change.numbers`. `engine/mailbox.py`: `_tell` reads the numbers of folder and to_folder under the place lock (an RLock, also for folder read and folder emptied, which tell after their quiet work) and tells the fact with them; not in a quiet job. `engine/commands_folders.py`: `folder.list` reads the kept numbers; `FOLDER_LIST` typed. `ui/mainwindow.py`: `_folder_fact` puts the numbers in place (the adding and subtracting is gone); `_folders` is `dict[str, list[FolderInfo]]`; folder.list asked with `Backend.ask`. `ui/models.py`: the folder list takes any mapping of sequences.
- Measured (`scratchpad/r10_measure.py`, 100,000 messages in one folder): the numbers counted 5.76 ms, kept 0.004 ms, the same; writing 100,000 into a new index 4.24 s without the table, 5.03 s with it. A 1,000-message move (`test_engine_many.py`, fake IMAP): 0.42–0.50 s, before 0.42–0.47 s.
- Tests: `tests/test_index_index.py` 3 new (the numbers follow add, mark, move, remove, write again, rename and empty; a rebuilt index has them; format 9 upgraded in place with them counted); `tests/test_engine_changes.py` 1 new (every fact brings the numbers of its folders, the same as folder.list); `tests/ui/test_ui_folder_counts.py` 2 new (a fact told twice, a fact never heard: the engine's numbers). The old-format builders of `test_index_index.py` and `test_index_tags.py` leave the new table out and expect format 10. The new tests cannot load on 1.1.249 (the new names); the never-heard window test alone, on 1.1.249: 2 shown where the engine has 1. Run: the engine changes, commands, IMAP folders (2), rules, many, index (3), CLI and IMAP changes files (317 passed); window folder counts, models, list changes, window, rapid actions, drag and drop, startup view, tags, tree rows (160 passed). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-11.

## 2026-10-21 1.1.251 R-11: the folder list is built once (D-209)

- `ui/models.py`: `FolderTreeModel` is a `QAbstractItemModel` of its own (nodes `FolderNode`, read like QStandardItem: data, text, index, parent, child, row, isSelectable; `item`, `findItems`, `itemFromIndex`; a QModelIndex carries the node's number, never the node). `plan()` builds `TreeEntry` trees (the old build code, now data); `populate()` lines the list up with it (`_line_up`: moves with beginMoveRows, also across parents, inserts new entries empty and lines up their entries after, takes away what is wanted nowhere, deepest first); values compared without the icon objects (`ICON_NAME_ROLE`); `rename_folder` gives the entries their new keys first; `leaving` is told before Qt removes rows. Qt's QStandardItemModel refuses moveRows (checked). `ui/mainwindow.py`: `_lay_out_tree` (was `_populate_tree`: no guard, no putting back, a target chosen only when nothing is current), `_select_entry` (no scroll: Qt's scroll to a new current entry opens the branches above it, which was then remembered as opened), `_open_as_left` (only new entries), `_opened_by_itself` and `_tree_toggled` (only a difference from the list's own way is remembered), `_entries_leaving` (the account's Inbox before the entry goes), `_sidebar_arrived` (the answer of every sidebar command; before, every account's folders were asked again), `_startup_choices` reads `plan(everything=True)`; `_tree_changed` returns for the entry already shown (the highlight going back from a heading); the sidebar typed (`SidebarInfo`, `EMPTY_SIDEBAR`). `engine/commands_sidebar.py`: `SIDEBAR_SHOW` typed, `EMPTY_SIDEBAR`.
- Found in passing: R-41 (a pinned folder that is renamed loses its Favorites entry; the pin keeps the old path; the same before). Not changed: it needs an engine change that was not in the changelist (changelist item C2 promised "renamed"; only "gone" is true).
- Tests: `tests/ui/test_ui_tree_in_place.py` (new, 5: no change of the six kinds empties the list and the selection stays the same entry; a renamed folder keeps its entry, selection and open branch; a pin moved up keeps its selection; an account put in a group keeps its selected folder; the selected folder deleted gives its account's Inbox); 4 fail on 1.1.250 (lists emptied 1 to 3 times), the fifth passed there too (it guards the Inbox). `test_ui_models.py`: the hidden-folder test lays the list out instead of single calls; `test_ui_treestate.py`, `test_ui_tree_rows.py`, `test_ui_startup_view.py`: `_lay_out_tree`. Run: folder list files (60 passed); window, drag and drop, settings, preferences, tags, DAV, icons, rapid actions, sections, sync conflicts, import, key recovery, dialogs, advanced search, list changes, shortcuts, schedule (290 passed, 2 failed: `test_ui_preferences.py::test_messages_whose_file_is_gone_give_one_warning_per_action` and `::test_the_list_is_refreshed_when_a_message_of_an_action_fails_and_the_error_stays`, the same on 1.1.250); engine sidebar and CLI (34 passed). Pyrefly 0 (Linux and win32; one commented ignore: Qt's two parent() functions); layer check kept. The full suite was not run.
- Next: R-12 (or R-41, the owner decides its place).

## 2026-10-21 1.1.252 R-41: a renamed folder takes its pins along (D-210)

- `config/sidebar.py`: `Sidebar.pins_followed(account, old, new)`. `engine/session.py`: `sidebar_lock`. `engine/mailbox.py`: `_tell` moves the pins of a folder_renamed fact first (`_pins_follow`, under the lock; a damaged file is reported when next used). `engine/commands_sidebar.py`, `engine/commands_accounts.py`: their read-change-save of the sidebar file under the lock. `ui/mainwindow.py`: a rename of a pinned folder lays the list out when the new sidebar is in (so the pin's entry is never taken away and made again).
- Tests: `tests/test_engine_sidebar.py` 1 new (the pin of the folder and of one inside it follow, the own name and the order stay, another pin untouched); `tests/ui/test_ui_tree_in_place.py` 1 new (a selected pin keeps its entry and selection); both fail on 1.1.251. Run: engine sidebar, changes, commands, IMAP folders (2), IMAP sync, config changes, CLI (217 passed); window tree in place, folder counts, models, tree rows, tree state, window, settings (131 passed). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-12.

## 2026-10-21 1.2.253 R-12: every setting in one place (D-211)

- The owner chose 1.2 for this version.
- `ui/prefs.py`: `Preferences` (39 typed settings, read field by field: `read_preferences`; `save_preferences` writes only what differs), `StartupView`, `Remembered`, the choice lists (`COLOR_SCHEMES`, `LIST_LAYOUTS`, `READING_PANES`, `DATE_STYLES`), and the size readers moved from `ui/scale.py`. `ui/mainwindow.py`: `_preferences`, `_keep`, `self.memory`; every direct read and write gone (`bool_setting` gone). `ui/settingsdialog.py`: reads `Preferences` once; `chosen()` replaces `values()`; `save()` saves the object. `ui/sidebarpage.py`: `startup_forces(StartupView)`. `ui/appearance.py`, `ui/operations.py`: through the object.
- Found while doing it and fixed in the same step: the first `save_preferences` also wrote every setting missing from the file (it would have filled the owner's settings file with all defaults); now only what differs is written (tested: saving the defaults writes nothing).
- Measured honestly: on 1.1.252 a damaged list layout or reading pane was taken unchecked at start but corrected later in the start, so no visible difference was shown for them.
- Also: `STARTUP-TIMES.md` (new): the owner's real start on 1.1.252, two runs, 2.44 s and 2.27 s to fully loaded, with every step; measured with the private `scratchpad/start_timing.py`, which only watches.
- Tests: `tests/ui/test_ui_prefs.py` (new, 7: every default; every setting round trips and only a change is written; damaged values give the default; Show on startup read once; what the window remembers; the guard (56 reads by name outside prefs on 1.1.252, 0 now); the same checked value at start and after OK). Changed to the typed object: `test_ui_settings.py`, `test_ui_preferences.py`, `test_ui_appearance.py` (and the size readers from prefs), `test_ui_operations.py`. Run: the settings, preferences, appearance, operations, startup view, tree state, columns, shortcuts, window, tree in place, folder counts, search field, sections, theme, composer and reader files (331 passed, 5 failed, all the same on 1.1.252: the two of `test_ui_preferences.py` known since 1.1.250, `test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons` (only with other files), `test_ui_theme.py::test_the_selection_is_blue_with_the_focus_and_grey_without` (known), `test_ui_theme.py::test_one_menu_button_holds_the_menus_and_the_heading_follows_the_section` (new in Problems)). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-13.

## 2026-10-21 1.2.254 R-42: an imported rule's standard folder (D-212)

- Found while measuring the start: Favorites > Unread counted 32 messages of an ordinary Local Folders folder "Junk E-mail" (18 to 21 October), filled by the owner's imported rule "***SPAM***" (subject contains, move to "Junk E-mail" by name). The import (15 October) had put the export's "Junk E-mail" mail into Junk (110 messages, 11 September to 15 October), but the rule kept the name; its first run made the folder.
- `engine/emclient_source.py`: `STANDARD_FOLDERS` (the table that was `commands_emclient._ROLE_NAMES`), `_STANDARD_NAMES`; `_move` maps a named standard folder: Junk at the top is Move to Junk, Trash at the top is Delete, the others (and a folder inside one) the standard name. `engine/commands_emclient.py`: `_ROLE_NAMES` is that table.
- Not changed: the owner's existing rule and the 32 messages.
- Tests: `tests/test_emclient_source.py` 1 new (Junk E-mail, Deleted Items, Deleted Items/Old, Sent Items, an own folder); fails on 1.2.253. Run: `test_emclient_source.py`, `test_import_emclient.py`, `test_engine_rules.py` (70 passed), `tests/ui/test_ui_import.py` (14 passed). Pyrefly 0 (Linux and win32); layer check kept. The full suite was not run.
- Next: R-13.

## 2026-10-21 1.3.255 R-13: Settings OK changes only what changed (D-213)

- The owner chose 1.3 for this version.
- `ui/mainwindow.py`: the window remembers the settings it has in use (`_applied`, kept current by `_keep`); `apply_preferences` compares them with the file and takes each group in use only when it differs: colours and the two window sizes (palette, style sheet, fonts, scaled parts, message windows), tray, the check timer, Show conversations, the list (layout, columns, date style, tag text), the list look, the reading pane (so a dragged width stays), the readers (remote content, plain text, text size; composers' text size), the keys, the folder list's sections. The Operations window's tick is compared with itself (it saves that setting on its own). `_show_columns`: the model moves the chosen columns to the front and the header hides the rest, as one step of the window (`_fitting`: Qt reports the moves and the hiding as resizes, which were taken for a drag). `_visual_sections` leaves hidden columns out; a drag never lands among them. `_settings_context` builds one `SettingsContext`. Closing Settings after an action on the Scheduled messages page no longer fetches the list again (Send now, Cancel snooze, Pause, Resume go through the mailbox and tell their facts; read).
- `ui/models.py`: `MessageListModel` holds every column (`CARD` and every column of `ui/columns.py`), the shown ones first (`columns` = the shown ones); `set_columns` moves columns (`beginMoveColumns`), never a reset. Checked first: Qt's selection keeps whole rows through column moves and hidden sections, but not through an inserted or removed column (the new cell is not selected, so `selectedRows()` is empty).
- `ui/settingsdialog.py`: `SettingsContext` (16 values in one frozen dataclass) replaces the loose arguments.
- Tests: `tests/ui/test_ui_columns.py` 1 new (two selected messages and the current one stay through a column added, removed, cards and back; no reset); `tests/ui/test_ui_preferences.py` 2 new (OK without a change applies nothing, a change of colours or keys applies only that; a dragged reading pane width stays). All three fail on 1.2.254. Three column tests adjusted (the model holds every column now); every SettingsDialog call in the tests uses SettingsContext. Run: test_ui_columns, columnfit, preferences, appearance, theme, settings, operations, tray, windowstate, prefs, tree_in_place, shortcuts, schedule, sync_conflicts, one test of test_ui_window and of test_ui_dav: 5 failed, all failing on 1.2.254 too (the four in STATUS.md Problems and `test_ui_tray.py::test_the_settings_carry_the_tray_choices`, new in Problems). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-14.

## 2026-10-21 1.5.256 R-14: one record of what the list shows (D-214)

- The owner chose 1.5 for this version.
- `ui/currentview.py` (new): `CurrentView` (the entry of the folder list, its kind and the kind shown, folder and role, the Favorites or Tags view, the search that was run, All folders, Advanced search, the four filters) and `EntryKind` (the kinds of entry, StrEnum: the folder list and Show on startup keep their words).
- `ui/mainwindow.py`: the 11 fields (`_account_id`, `_folder`, `_folder_role`, `_tree_kind`, `_tree_key_kind`, `_tree_path`, `_combined`, `_combined_accounts`, `_combined_tag`, `_filter_tag`, `_advanced`) are `self._view`; `_change_view` sets it, shows it (`_show_view_controls`: filter ticks and bold, Filter button, scope, search field look, filter chip) and asks the list again only when `view_spec()` changed; `view_spec` reads only the value. Opening an entry makes its view in one step (`_tree_changed`; `_select_quick_filters_for` gone). The filters answer `triggered` (the person) instead of `toggled`; the search field's empty-text answer ends the search, a no-op when it had ended. 9 `blockSignals` gone (0 left in the window). Entry kinds compared as `EntryKind` (34 lines).
- `ui/searchfield.py`: `ScopeButton.show_choice` (shown without telling).
- Found by reading and proven by the new test: a sort, a filter click or a refetch searched the words typed but not run (the window read the search box each time).
- Tests: `tests/ui/test_ui_window.py` 3 new (typed text never searched by a sort or a filter: fails on 1.3.255 with query "number 2"; quick filter clicks and a folder change leave the ticks and the list agreeing; the guard: no `blockSignals` in the window); tests read `window._view.*`, filters are clicked with `trigger()`. Run: test_ui_window, searchfield, advancedsearch, searchhelp, toolbar, tags, tree_rows, tree_in_place, models, shortcuts, columns, theme, import: 4 failed, all known: the two theme tests (STATUS Problems), `test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons` (passes alone; the known order dependence) and `test_ui_toolbar.py::test_at_a_larger_size_the_minimum_is_what_the_contents_need_and_it_comes_back` (fails on 1.3.255 too; new in Problems). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-15.

## 2026-10-21 1.5.257 The link under the pointer in the bottom bar, with a warning for another domain (D-215)

- The owner's request: the address of a hovered link in the status bar of the message window; added by the owner: an orange triangle with "!" when the link's main domain is not the sender's ("alianz.nl and business.alianz.nl would not trigger the warning"). The reading pane got the same (proposed, approved).
- `mime/linkdomain.py` (new): `main_domain` (Public Suffix List, IDNA, lower case, capped at 253), `link_domain` (web server, mailto domain; capped link of 8,192), `leads_elsewhere`. `publicsuffixlist` 1.1.2.20261021 added to the dependencies: checked before adding: MPL-2.0, no Exhibit B notice on its files, no other packages, its list built in, nothing that goes online (only an unused address constant). It gives "1.1" for 192.168.1.1, so bare number addresses are found with `ipaddress` first.
- `ui/reader.py`: `link_hovered(address, elsewhere)` from the web page's `linkHovered` and the text view's `highlighted`; "" when the pointer leaves or the message changes. `ui/linkhint.py` (new): `StatusLine`, the window's own status text or the link line (triangle, plain-text address that never widens the window). `ui/mainwindow.py`, `ui/messagewindow.py`: the status bar holds a `StatusLine`.
- Packaging: `--include-package-data=publicsuffixlist:public_suffix_list.dat` in `build_in_container.sh` and `build_windows.ps1`; PACKAGING.md. The Windows computer needs `pip install -e .` (or `pip install publicsuffixlist`) after the pull.
- Tests: `tests/test_mime_linkdomain.py` (new, 22: same main domain, another one, look-alikes, numbers, mailto, no sender, links without a domain, a capped link); `tests/ui/test_ui_linkhint.py` (new, 3: the reader in HTML and plain text, the message window with and without the triangle, the reading pane with markup shown as text and the status text back). New code, so not run against 1.5.256. Run: those, test_ui_reader, window, columns, windowstate, preferences, theme, test_layers, test_legal_names: 5 failed, all known (the two preference and two theme tests in STATUS Problems; test_legal_names on this machine only). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-15.

## 2026-10-21 1.5.258 The search history never takes the mouse or the keyboard (D-216)

- The owner's report: on the laptop, a tap-tap-drag in the search field never selected text; the history appeared at the first tap. Cause: the history list was a popup window (`Qt.Popup`), opened at every press in the field, and a popup takes every press and key until it closes; so the second press (and any drag) went to the list. It also took the keyboard (`list.setFocus()`) and handed keys back.
- `ui/searchhistory.py`: `SearchHistory(parent, field)` is a `Qt.ToolTip` frameless window shown without activating, without focus (also its list); it filters the field's keys (Down, Up, Page Down, Page Up: `HistoryList.step`; Enter with a highlighted line; Escape) and, while shown, every press of the program (outside the field and the list: closes it); the keyboard going elsewhere closes it (`focusChanged`). `HistoryList.keyPressEvent` and the `typed` signal are gone. `ui/mainwindow.py`: the history is given the field.
- Tests: `tests/ui/test_ui_searchfield.py` 3 new (with the history shown a double click selects a word and a drag selects text, through the window, and no popup is open: fails on 1.5.257 on that check (offscreen no window grabs the mouse as on a real screen, so the selection itself is not reproduced there); the field keeps the keyboard and works the list; a press outside closes it); 3 tests type into the field instead of the list. Run: searchfield, searchhelp, advancedsearch, window, toolbar: 1 failed, known (`test_ui_toolbar.py::test_at_a_larger_size…`, STATUS Problems). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-15.

## 2026-10-21 1.5.259 The crash of 1.5.258; Enter and Insert only in the message list (D-216, D-217)

- The owner's crash (16:05, SIGSEGV in the window thread, 1.5.258 run from the dev folder): while the window became active (focus into the reading pane's web view, a QtQuick item inside WebEngine), every event went through an application-wide event filter written in Python; PySide crashed (`__dynamic_cast` in `getWrapperForQObject`, re-entered through a dynamic property change) making one of the web view's inner objects known to Python. The only application-wide filter was the search history's of 1.5.258. Fix: it watches the main window's own window (`windowHandle()`), installed while shown; a press on the history never reaches it (a window of its own). Guard test: no `installEventFilter` on the application in `src`.
- The owner's report: Enter typed into the search field opened the selected message instead of searching. Cause: Open had the key Return as a window-wide shortcut, which Qt handles before the field; the same on 1.5.257 (checked on an exported copy), seen now because the 1.5.258 history no longer holds the keyboard. A probe of every window-wide key while typing in the field: Return and Insert act on a selection elsewhere (Insert flagged the selected message); Ctrl keys too (left for the key review). The owner explained the principle (D-217); Open and Toggle flag are `WidgetShortcut` on the message list only (`_scope_shortcut`).
- Tests: `test_ui_shortcuts.py` 1 new (Enter and Insert typed in the field search and flag nothing; in the list they flag and open: fails on 1.5.258), the letter-key test updated (Open and Toggle flag list-only); `test_ui_searchfield.py` 2 new (the guard: 1.5.258 has one such filter, now 0; the history closes when the focus leaves and on a press in the window that takes no focus). Run: searchfield, shortcuts, searchhelp, window, columns, schedule, reader, linkhint: 185 passed. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Memory: keys act on the focused part (the owner: selected is not focused).
- Next: the key review changelist (D-217), then R-15.

## 2026-10-21 1.6.260 R-15: the message list holds every view one way (D-218)

- The owner's design (D-147): one list of every message of the view in order, plus the rows loaded. The owner chose 1.6 and left every implementation choice to me (also: Qt's tree view stays; the key review is dropped, R-43 instead).
- What the user saw before (checked in the code): in Favorites, Tags and searches the count line counted only the loaded rows and the scrollbar grew while scrolling; a sort reached a selected message far down by loading every row above it; in a folder a message gone from a part not loaded rearranged the whole list; every answer that crossed a fact was thrown away and asked again.
- Engine: `engine/commands_view.py` (new): `message.order` (a folder or a search of one mailbox), `message.combined-order` (Favorites, Tags), typed, answer `ViewOrder` (ids, mailboxes, folders, unread marks, totals, headings, rows of the first 100 and of the messages named, `complete`); `message.rows` (rows by the message, typed). `commands_combined.gather`: each mailbox of a Favorites view read once under its lock for rows, groups and numbers together (message.combined and -groups use it). `message.places` and `-combined-places` also name `before` / `before_mailboxes` (`commands_mail.neighbours`). `Index.ordered` (ids, folder, unread without the rows; one folder: its name not read per message), `Index.tally`, `Index.messages`. The sort and view Literals are named (`ListSort`, `CombinedSort`, `ViewName`).
- Window layer: `ui/messageorder.py` (new, no Qt): `MessageOrder`, blocks of 512 with a key-to-block map. `ui/messagelist.py` (new): `MessageListModel`, `ViewSpec`, the list's roles, out of `ui/models.py` (now the folder list only). The facts heard while a question is out are kept and applied again to its answer (`_ask`, `_answered`); arrivals wait with their row kept current and are put before the named message; rows drawn before the whole order came are asked when it comes. `ViewKind`, `canFetchMore`, `fetchMore`, `load_through`, `load_position`, `message_ids` are gone. `ui/mainwindow.py`: imports, `_rows_on_screen` (a list fetched again brings the rows on screen along).
- Measured (index of 500,000 messages in one folder, the Linux computer): opening, the first answer 0.0006 s (before: first page and counts 0.027 s; a first try with a counted tally was 0.062 s, slower, so a plain folder uses the numbers the index keeps); the whole order follows in the background, 0.46 s by date, 0.96 s by subject; the window takes it in 0.13 s (500,000 keys, one time per opening or sort). The order record: build 0.086 s, 1,000 removals 0.104 s, 1,000 insertions 0.103 s, 1,000 lookups 0.004 s. Memory of the order at 500,000 about 70 MB (ids and the map). Scripts: `scratchpad/r15_order_timing.py`, `r15_open_timing.py`, `r15_structure_timing.py`, `r15_gui_side_timing.py`.
- Tests: `tests/ui/test_ui_messageorder.py` (new, 4: against a plain list through 600 changes with small blocks; a folder renamed and many taken out; a Favorites key; half a million timed); `tests/test_engine_view_order.py` (new, 5: the order of a folder, the start of a large view, a search and a Favorites view as they list, rows by the message, the message one comes before). `tests/ui/test_ui_models.py`: a search and a Favorites view count all at once and load rows as they come into view; headings all at once; every kind of view asks its order with everything it holds. `tests/ui/test_ui_list_changes.py`: a row asked before a change shows it and is never asked again; a message gone from a part not loaded asks nothing; rows asked before a message came in go to their own messages. Four of them run against 1.5.259 (an exported copy): all four fail there. Other tests follow the new names (`message.order` instead of `message.list`, `messagelist` instead of `models`, the schedule test reads `row_at`). Run: test_engine_view_order, engine_commands, engine_local, engine_search, index_index, test_layers (232 passed); ui messageorder, list_changes, models, columns, advancedsearch, tags, window, rapid_actions, smoke, dragdrop, folder_counts, tree_rows, shortcuts, schedule, preferences, key_recovery, theme: 302 passed, 5 failed, all failing the same on 1.5.259: the two preference and two theme tests (STATUS Problems) and `test_ui_key_recovery.py::test_not_now_leaves_the_program_usable_and_says_what_is_wrong` (passes alone; fails after the files before it, also on 1.5.259: new in Problems). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-43 (the keys), then R-16.

## 2026-10-21 1.6.261 The folder list appears in one step; the start asks each place once (D-219)

- The owner's observation: the folder list (about 30 rows) visibly builds up from top to bottom at start, while the longer message list appears at once. Measured (`scratchpad/start_paint_timing.py`, the owner's start): not the painting (the folder list draws in 16 ms, its SVG icons 3 ms; the message list about 45 ms), but two lay-outs 0.3 s apart: an early one with the Favorites part (D-209) and the final one when all 27 folder lists were in. Why so late: the start asked every account for its folders; 25 of the owner's 27 accounts are POP3 accounts keeping their mail in Local Folders, so 25 questions read Local Folders' list again, 4 at a time (0.08 to 0.43 s each), and held up the message list's first question too.
- `ui/mainwindow.py`: `_load_folders` asks only the places where mail is kept (`_accounts_with_mail`); one lay-out when the folders, the sidebar file and the tags are in; `_start_needs`, the early build and the `final` argument of `_lay_out_tree` are gone. `_total_unread` counts each place once: found in the same change by reading the code, the tray icon's unread number counted Local Folders' Inbox once for itself and once per account keeping its mail there (the test: 12 instead of 3), and 25 of those copies were never updated after the start. `ui/models.py`: `pending_trees` gone.
- Measured twice on the owner's copy (Unread: 571, `STARTUP-TIMES.md`): the folder list complete in one step at 1.46 / 1.53 s (1.6.260: 1.68 / 1.64 s); fully loaded 1.72 / 1.75 s (1.6.260: 1.87 / 1.88 s; 1.5.257: 2.14 / 2.22 s); the first message.combined-order answer 0.13 / 0.09 s instead of 0.40 / 0.42 s.
- Tests: `tests/ui/test_ui_startup_view.py`: the start asks each place once and lays the folder list out once (on 1.6.260 it asked 4 times for 3 accounts in Local Folders), a chosen folder that is not there falls back at the one lay-out (replaces the early-build test), the tray counts Local Folders once (1.6.260: 12 instead of 3); four tests of that file read the window's view as `_view` (they read the fields R-14 removed and failed since). Run: startup_view, tree_rows, treestate, tree_in_place, folder_counts, models, window, tray, preferences: 3 failed, all known (`test_ui_tray.py::test_the_settings_carry_the_tray_choices` and the two preference tests, STATUS Problems). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-43 (the keys), then R-16.

## 2026-10-21 1.7.262 R-43: every key acts on the part that has the focus (D-217)

- The owner: standard behaviour, no key-by-key review; version 1.7. A probe pressed every key of the window with the focus on the folder list, the message list, the search field, the reading pane and a section's sidebar (on the test screen), and the search field's keys on the owner's real desktop (xcb: Ctrl+U clears the line, Ctrl+E goes to its end; the test screen has neither).
- Found: in the folder list Delete moved the selected (grey) messages to Trash and Shift+Delete deleted them for good; every one-letter key (A, S, R, M, C, N, P, F, B, G) acted on them; in the Calendar, Contacts and Tasks sidebars Delete deleted the selected items. In the reading pane (clicked into the message) Delete and the Ctrl commands acted on the message, the letters and Insert did nothing. Right already: the search field keeps its keys; Enter and Insert only in the list since 1.5.259.
- `ui/deletekey.py` (new): `DeleteKey`, a list claims Delete and Shift+Delete through Qt's ShortcutOverride on that widget only (as a text field does), so the window's Delete never sees them there and a click on the Delete button is untouched. `ui/mainwindow.py`: `_scope_shortcut`: the letters and Insert are WidgetWithChildrenShortcut on the message list and the reading pane, taken off the window (every menu action is also the window's, so a context with children on the window would be window-wide); `_delete_key_in_folder_list` (an own folder of an account's tree, never a standard folder, a heading or a Favorites entry). `ui/collectionpanel.py`: `_delete_pressed` (the collection selected; not a heading, not a read-only one). In the folder list a letter now reaches Qt's own jump to a folder.
- Not covered by a test: an HTML message in the reading pane (the web view's inner widget is a child of the reading pane, so the context with children includes it; the tests show plain text).
- Tests: `tests/ui/test_ui_keys.py` (new, 5: every key in the message list and the reading pane; in the folder list no key acts on the messages, Delete and Shift+Delete delete an own folder and nothing on the Inbox; a letter jumps to a folder; the search field keeps the keys of text; Delete in the Contacts sidebar deletes the address book, nothing on a heading): 4 of them fail on 1.6.261 (the search field one passes there, it did not change). `test_ui_shortcuts.py`: the letter test follows the new scopes. Run: keys, shortcuts, searchfield, window, rapid_actions, tree_rows, tree_in_place, treestate, startup_view, contacts, calendar, pimlist, pim_changes, schedule, preferences: 4 failed, all known (`test_ui_pim_changes.py::test_the_panel_changes_one_entry_and_its_numbers`, `test_ui_schedule.py::test_the_snooze_button_offers_ready_made_times` (R-22), the two preference tests). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-16.

## 2026-10-21 1.8.263 R-16: duplicated code in the lower layers exists once (D-220)

- The owner chose 1.8. Nothing visible changed; every error text is the same word for word (tested).
- `core/credentials.py`: `SERVICE_NAME`, `CredentialEntries` (read, write, remove; an entry already gone is no error), `CredentialFailure` (cause, reason = cause + ". Try again"). `crypto/keystore.py` and `config/passwords.py` lost their own `_cause`/`_reason`, service name and catches; they turn a `CredentialFailure` into their own error (`CredentialStoreError` with the cause for a key that could not be read, as before).
- `index/database.py`: `IndexDatabase` (version check, `MINIMUM_SQLITE_VERSION` moved here from both schema files, connect with 30 s wait, WAL, synchronous NORMAL, close on a failed open, damaged message, `_transaction`, `check`, `close`); `Index` keeps temp_store, `shown_name` and its upgrades in `_prepare`, `PimIndex` its layout and format check.
- `protocols/lineconnection.py`: `LineConnection` (send, fill, fill_within, read_line with the cap, read_exactly, waiting, line_waiting, close); `ImapClient` and `Pop3Client` hold one instead of a socket and a buffer. POP3 keeps its line-break guard (now checked before "Not connected").
- Tests: `tests/test_core_credentials.py` (3), `tests/test_index_database.py` (4), `tests/test_protocols_lineconnection.py` (3), new; the first two fail on 1.7.262 (cannot import). Run with them: crypto_keystore, config, index_index, index_rebuild, pimstore, protocols_imap, protocols_pop3, sync_imap, sync_pop3: 484 passed. Pyrefly 0 (Linux and win32), layer check kept. Full suite, live and real-wallet tests not run.
- Next: R-17.

## 2026-10-21 1.9.264 R-17: the main window is made of parts with one job each (D-221)

- The owner chose 1.9. Nothing visible changed.
- `ui/mainwindow.py` 3,444 → 1,014 code lines: it builds the screen, the actions, the menus and the toolbar and connects the parts. New parts, each the owner of its data: `folderpane.py` (FolderPane), `messagelistpane.py` (MessageListPane: the current view, the sort order, the selection, take_away, refetch), `messagecolumns.py`, `searchfilters.py` (ListFilters, MailSearch), `messageactions.py`, `mailtags.py`, `readingpane.py`, `composing.py`, `mailchecking.py` (with the Operations window), `statusreport.py`; shared data in `mailstate.py`; `prefs.InUse` for the settings in use. Parts talk through Qt signals, never back into the window.
- Unused code removed: the list reload of `_after_change`, `_refresh_counts`, `_after_sent` (empty), `_note_gone`, `AUTO_CHECK_MINUTES`, `_ICONS`.
- Found by the test runs: the Tab key went to the message list before the folder list (the parts are made in another order); the window now states the order with QWidget.setTabOrder; a test guards it.
- Tests: `tests/ui/test_ui_parts.py` new (14: the window under 1,100 code lines and running no mail command, no part importing the window, the parts under 650 code lines, MailState, the status line, the list asks again only on a changed view, the folder list's entry_chosen, composing, no check during an import, the reading pane off, no action on no messages, a failed key check, the Tab order); 27 window test files follow the code to the parts. All window tests (tests/ui) ran once before (757 passed, 12 failed) and once after (771 passed, 12 failed): the same known failures, except test_ui_schedule snooze (R-22) passing and test_ui_tasks large list (known, order-dependent timeout, in Problems) failing. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-18.

## 2026-10-21 1.9.265 R-44 part 1: four program faults found by old failing tests (D-222)

- Missing file: NotDone.missing (engine) and the window's warning again for actions on several messages (D-109; silent since 1.1.247).
- Tasks: KIND_ROLE for row heights; 5,104 tasks: 52 requests and ~10 s before, only the rows in view now (the test 15 s → 5 s).
- Calendars: Change.palette and read_only; collection.set-palette tells every calendar of the account; collectionpanel takes them from the fact (take_infos gone). Found on the way: read-only reached the sidebar only through the whole-list ask.
- Window: the minimum width worked out at the start; the count line an ElidedLabel (new ui/elidedlabel.py), the owner's choice.
- Tests: new test_engine_many (missing mark), test_engine_calendar (palette in facts), test_ui_toolbar (minimum at start, long count line); test_ui_tasks checks only the rows in view; test_ui_preferences waits for the one error line. Ran once each: engine many, calendar, changes, dav (60 passed); ui preferences, tasks, calendar, pim_changes, pimlist, contacts, toolbar, window, parts, tags, rapid_actions, dragdrop (197 passed, 1 failed: the narrowest toolbar, R-44 part 2). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- Next: R-44 part 2.

## 2026-10-21 R-44 part 2: the window tests that fell behind the program (tests only, private)

- Rewritten: folderchoices (the timer stand-in takes also_calendars), theme (the Menu with Sync conflicts…; the selection colour read in the Subject column), rules (its copy of the Menu check removed: theme has it), toolbar (one-letter keys on the message list, D-217), tray (read as the program reads, D-211), columns (the header icons in the program's own grey with a tolerance; before, Qt's black made it pass without testing grey).
- Ran once: theme, rules, folderchoices timer (31 passed); tray, toolbar, columns (63 passed); the icon test after the colour scheme test passes. The program did not change; no version.

## 2026-10-21 1.9.266 R-45: every test unambiguous, no test can hang (D-223)

- Full run with a 20 s limit per test: engine 3 failures (all test faults: calendar list above the D-174 cap, dav.link not in the destructive list, legal names read private files and matched inside SlowItemClient); window tests hung twice (stack: a message window left by an earlier test, its autosave failed after its world was gone, QMessageBox.warning waited for a click).
- Proven with two-test runs: the key recovery test fails right after test_settings_ok_takes_into_use_only_what_changed (dark left on: #ff8077 instead of #c0392b); the snooze test fails every time with the answer 0.3 s slower (R-22).
- tests/ui/conftest.py: after each test every top-level window deleted, the palette, menu palette, colour scheme, style (by name), font, accents and scale and appearance state put back; a message box no test answers is answered safely and fails the test. tests/conftest.py: 20 s per test (SIGALRM), faulthandler_timeout = 20 in pyproject.toml; perf exempt.
- Rewritten tests: both snooze tests, key recovery (red of the theme in use), pim lists (4,000 under the cap, hidden 3,000 would cut), registry, legal names (git ls-files, word start, the importer's names anywhere), config (cases with fixed names; random ids made new node ids each collection), three IMAP state compaction tests (monkeypatch; reverse order failed).
- New: tests/run_isolated.py (each test in a fork of one prepared process, 8 at a time, killed after 20 s, any order), tests/runorder.py (--run-order reverse/shuffle).
- Proof: isolated 2,495 of 2,495 (278 s); one process: normal engine 1,709 + window 784, reverse window 784, shuffle (seed 7) engine 1,709 + window 784, all passed; reverse engine failed once (the compaction setting), fixed; run again: reverse engine 1,709 passed, shuffle (seed 11) engine 1,709 passed. 2 skips are Windows-only (tests/test_running.py). Live tests (opt-in, real servers) not run.
- Pyrefly 0 (Linux and win32), layer check kept. Program unchanged.
- Open for the owner: a failed automatic save shows a warning box at every try (ui/composer.py save_draft ignores quiet for the error).

## 2026-10-22 1.9.267 A failed automatic save is told on the bottom line, without a box (the owner, D-037)

- ui/composer.py: `_say` writes the bottom line (red_style for an error, cleared by the next text); save_draft's failure shows the box only when not quiet (Save, closing with Save), the autosave never.
- Server rule D-193: unchanged (where and how a draft is saved is the same; only how a failure is told).
- Tests: test_ui_composer two new (failing autosave: red line, no box; failing Save: the box, and the red goes with "Draft saved at"); the first fails on 1.9.266 (shown). Ran: test_ui_composer each test in its own process (56 passed), composer and richtext in reverse order (74 passed). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.268 R-21: the Scheduled messages page changes only the line an action touched (D-224); R-18 skipped

- R-18 skipped (the owner): no file of the program is over 2,000 lines (the largest, ui/mainwindow.py, 1,291).
- engine/commands_schedule.py: `_line` (one waiting message as the list shows it, None when it no longer waits); `SendDueResult.line` for a message named, `RepeatState.line` for pause, resume and change; typed MAIL_SCHEDULED, MAIL_SEND_DUE, MAIL_REPEAT_PAUSE, MAIL_REPEAT_RESUME, MESSAGE_SNOOZED, MESSAGE_WAKE.
- ui/scheduledpanel.py: Send now, Cancel snooze, Pause and Resume apply their answer to their own line (`_place_line`, `_remove_line`; the next line selected, at the end the one before); `reload` brings the list in line in place (`_bring_in_line`), the selected message kept; a failed Send now shows the server's reason in red (it said "Done"); Cancel snooze says "Back in <folder>, unread"; all calls typed.
- Server rule D-193: Scheduled and Snoozed are on this computer only (D-172, D-201); nothing here reaches a server; the sending is unchanged.
- Tests: test_ui_schedule six new (send now once, send now repeating, failed send now, cancel snooze, pause and resume, showing the page again); all six fail on 1.9.267 (shown with the old two files). Ran: test_ui_schedule and test_ui_preferences (76 passed), test_engine_schedule, test_engine_recurring, test_engine_registry, test_layers (73 passed). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.269 Folders of outgoing messages show how many wait (the owner, D-225)

- The owner's screenshot: Favorites > Scheduled with ten messages showed no number. Cause: the folder list showed only unread messages (D-089), and own mail is saved as read, so Drafts, Outbox and Scheduled always showed nothing.
- ui/models.py: `holds_outgoing` (Drafts and Outbox by role, Outbox, Scheduled and Snoozed by place), `shown_number` (all messages in an outgoing folder, else unread), `favorite_number` (Favorites > Drafts, Outbox, Scheduled, Snoozed: the messages of every mailbox shown); `favorite_unread` takes which number; folder entries, pins, Favorites and `recount` set COUNT_ROLE (drawn, bold) apart from UNREAD_ROLE (window title, next unread).
- The owner: Snoozed is an outgoing folder (its messages wait to be delivered again); the number bold like the others.
- Server rule D-193: only what this computer's folder list shows; nothing reaches a server.
- Tests: test_ui_folder_counts two new (the four folders and their Favorites show how many wait while Inbox and Unread show unread; the number follows a send without counting again); both fail on 1.9.268 (shown). Ran: test_ui_folder_counts, test_ui_rapid_actions, test_ui_models, test_ui_tree_rows, test_ui_tree_in_place, test_ui_treestate, test_ui_window, test_ui_schedule (173 passed). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.270 R-19 round 1: every engine call of the mail parts is typed

- The owner: R-19 in three rounds (mail; accounts and settings; calendar, contacts, tasks with a guard test).
- Engine: typed forms (TypedCommand) of 40 commands next to their handlers: message.show, -thread, -attachment.save, -compose, -delete, -places, -move, -export, -source, -text, -tag, -combined-places; mail.send, -fetch, -settle, -wait, -send-outbox, -unschedule (its answer typed: the draft it became); folder.mark-read, -empty, -create, -rename, -delete; draft.open, -save; dav.sync; contact.addresses; tag.list; favorite.pin, -unpin, -rename, -reorder, -show; sidebar.show-tree, -show-local-folder; group.add, -rename, -remove, -assign. The command line unchanged.
- ui/backend.py: ask takes waiting and checking; Step and AnyStep (a list of typed calls with one kind of answer).
- Window: readingpane, messagewindow, messageactions, composer, composing, mailchecking, messagelist (`carried`: the places question from the order question's fields), messagelistpane, mailtags, folderpane (sidebar_call takes a typed command; change_favorites typed steps), sidebarpage (commands gives steps). Answers read by their fields instead of getattr. 106 untyped calls → 66.
- Found by Pyrefly: New folder could pass no account; can_make_folder already rules that out, so no fault (said in the code). No program fault found.
- Server rule D-193: unchanged (only how the window calls the engine).
- Tests: three tests call the typed sidebar calls (test_ui_preferences, test_ui_tree_in_place, test_ui_window). Ran: 23 window test files of the parts touched (390 passed), test_cli, test_engine_registry, test_layers, test_engine_schedule (103 passed). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.271 R-19 round 2: the account, settings, rules, tags, key and import windows typed

- Engine: typed forms of 37 more commands (account.add, -check, -edit, -list, -remove, -trust-certificate, -forget-certificates, -folders, -set-folders, -set-storage, -set-dav; certificate.inspect; key.status, -export-file, -recovery-code, -restore-file, -restore-code; rule.list, -add, -edit, -remove, -enable, -move, -run; tag.create, -edit, -delete, -rename, -colour; index.rebuild, -reconcile; store.verify; import.emclient-scan, import.emclient). The command line unchanged.
- Window: accountedit (the account an AccountSummary; changes built as AccountEditParameters with only what differs, SetFolders, SetDavParameters), accountdialog and serverfields (check_parameters; security_of, mail_kind_of, policy_of), settingsdialog (accounts typed, the maintenance report per job), rulespanel and ruleedit (RuleInfo; values gives RuleAddParameters), tagsdialog (typed steps), restorekeydialog, recoverydialog, importdialog, calendaraccount, mainwindow. 66 untyped calls → 31.
- Found (typing it): certificate.inspect takes kind pop3, imap or smtp only; the sections' Add account… asks with kind https, so Look at the server's certificate there always fails ("Parameter 'kind' must be one of: pop3, imap, smtp"), since 1.1.212. Told to the owner; not changed; that call (certificatedialog inspect_and_ask) stays untyped until decided. The account editor's own calendar certificate look passes imap as a stand-in.
- Server rule D-193: unchanged.
- Tests: the tests that read the old dictionaries now read the typed values (test_ui_folderchoices, test_ui_settings, test_ui_dialogs, test_ui_rules, test_ui_tags, test_ui_import). Ran: 12 window test files (217 passed), test_engine_rules, test_engine_tags, test_cli, test_engine_registry, test_layers (all passed). Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.272 The certificate look in the sections' Add account… works; Rule 20

- Cause: certificate.inspect took kind pop3, imap or smtp only; Calendar, Contacts or Tasks > Add account… > Look at the server's certificate asked with https and was always refused ("Parameter 'kind' must be one of: pop3, imap, smtp"), since 1.1.212. The account editor's calendar tab hid the gap by asking as imap.
- engine/commands_accounts.py: kind https (a calendar and contacts server), tls only (starttls refused: a web server has none). ui/certificatedialog.py: inspect_and_ask typed (security, kind). Both calendar windows ask with https.
- PY-DEVELOPMENT-RULES.md Rule 20 (the owner): open problems come last in a reply, in a quoted ⚠️ block, repeated until decided; ⚠️ only for real problems; an obvious fix is no decision.
- Tests: test_engine_certificates one new (kind https over tls works; starttls refused); fails on 1.9.271 (shown).

## 2026-10-22 1.9.273 R-19 round 3: the calendar, contacts and tasks windows typed; R-19 done

- Engine: typed forms of event.show, -add, -edit, -delete; task.show, -add, -edit, -complete, -reopen, -delete, -move; contact.show, -add, -edit, -delete, -move, -import, -export, -suggest; collection.list, -edit, -delete; pim.enable; message.search.
- Window: the editors give typed edits (EditParameters; the add built with `carried`, now in ui/backend.py); calendarview, tasksview, contactsview, contactdetail, addresscompleter, collectionpanel, pimpages typed; pimlist: delete and move are the section's typed steps (_delete_step, _move_step) instead of command names. 31 untyped calls → 0.
- Guard: tests/test_typed_calls.py fails on any `.call(` or `.run_now(` in the window outside backend.py; fails on 1.9.271 (shown).
- Found and fixed (a test, my miss in round 2): tests/ui/test_ui_parts.py still expected the window's key and account calls untyped; round 2 changed mainwindow.py without running it, so it failed on 1.9.271. The program was right.
- Server rule D-193: unchanged.
- Tests: test_ui_calendar, test_ui_contacts updated to the typed values. Ran: 23 window test files (calendar, contacts, tasks, lists, completer, sync conflicts, dav, sections, keys, tags, window, rapid actions, folder counts, models, parts, list changes, import, composer, settings, certificates, dialogs), the engine tests of calendar, contacts, pim, search, certificates, test_cli, test_engine_registry, test_layers, test_typed_calls: all passed. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.274 R-19 round 4: the mail parts keep the engine's answers as their real types

- The owner: R-19 in rounds until it is fully done. The calls were typed (1.9.273), but the windows kept answers in holders typed object and read them with getattr by name (485 places): Pyrefly checked none of them.
- Typed: MailState (accounts AccountSummary, tags TagInfo), the message list (rows MessageRow, facts Change, _with replaced by dataclasses.replace, which Pyrefly checks per field), the list pane, reading pane, actions, writing (the composer tells a reply ComposeResult from a draft DraftOpenResult instead of guessing their fields), tags, conversation bar, columns, mail checks, folder pane, folder list model (accounts, FolderInfo), main window, calendar account dialog, search filters. 485 guessed reads → 305; the mail parts keep 13 (Qt's painting option objects in delegate.py, the window's own act_ attributes): round 5 decides those with the guard.
- How: scratchpad/r19/plain_reads.py turns getattr(x, "f") into x.f for variables of a known type; scratchpad/r19/drop_conversions.py removes only the conversions Pyrefly calls unnecessary. Found in my own tool and corrected before anything ran: dropping str() around `row.account or ""` changed the meaning of two comparisons in messagelist.py (brackets restored; the tool now keeps brackets). Every converted read with a fallback value was checked to be a real field of the known type.
- Tests: the stand-ins of test_ui_models (an account) and test_ui_tags (a change notice) are the real types now. Ran: 34 window test files (533 passed), test_typed_calls, test_layers. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.275 R-19 round 5: R-19 done

- Typed the holders of calendar, contacts, tasks, scheduled messages, rules, certificates, the reader, printing, the source window, tags (TagColourBook, tagging), the sync marks and pages (CollectionSummary, ContactRow, ContactInfo, AddressInfo, EventInfo, OccurrenceInfo, TaskInfo, TaskRow, ScheduledInfo, SnoozedInfo, RuleInfo, CertificateResult, ShowResult, AttachmentInfo, SourceResult, TextResult, TagInfo, Change, AccountSummary, AccountCheckResult). The collection sidebar's _replace takes a function (dataclasses.replace per field) instead of loose values. Rows that Qt hands back are checked with isinstance before they are read (delegate.message_row, the task delegate, the Scheduled page's lines).
- Where a guessed read with a fallback handled "nothing there" (a calendar not found: empty name, colour, not read only; a snoozed message never repeats), that is now said explicitly, with the same result. One such place briefly changed behaviour in my first edit (Delete on an unknown calendar would have done nothing); restored to the old behaviour before anything ran.
- 305 getattr → 22, each with "# getattr: <why>" on its line: PySide's stubs give the fields of a style option no type (delegate, foldertree, messagelistview, syncconflicts), the window's own actions by name and the sync marks asked before they exist (mainwindow), settings and dataclasses compared or copied field by field (prefs, mainwindow, backend.carried), Qt's colour roles by name (colours), ctypes.windll (app).
- Guard: tests/test_typed_calls.py also refuses any getattr in the window without its reason; fails on 1.9.274 (shown).
- Server rule D-193: unchanged.
- Tests: Ran 34 window test files (590 passed), test_typed_calls, test_layers. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.9.276 Received is when the mail server received the message (the owner, D-226); "1 message"

- Cause (the owner's screenshot): the Received column showed the sender's Date line; mail from a shop's address has none (it has 3 Received lines and a Delivery-date), so it showed nothing and sat under "No date".
- The owner first chose "arrival time"; INTERNALDATE first was dropped after research (the moment a message entered that mailbox, a copy or migration date for moved mail; one properties file per message). Checked what others do: Thunderbird uses the Received line for IMAP and POP3 (Mozilla bug 570355, WONTFIX for INTERNALDATE); the reference client's POP3 Received column is the download moment (forum); Evolution reportedly the retrieval moment; Outlook documents "stored at the server". The owner: "we do what Thunderbird does".
- mime: ParsedMessage.received, the topmost Received line's moment, else Delivery-date; never raises. index/entry.py: date = received, else Date, else stored (received_moment). Store.stored_at (the file's modification time) for the rebuild. Index format 11; every older format is rebuilt: the in-place upgrades 6 to 10, their statements, send_times_pending and fill_send_times are gone (they could no longer give a usable index); set_send_times stays (old_files uses it).
- messagelistpane: "1 message" (it said "1 messages").
- Server rule D-193: only how this computer reads what the servers wrote; nothing sent.
- Tests: test_index_entry five new, test_index_rebuild one new, test_ui_window one new; 5 of the 7 fail on 1.9.275 (shown), the other 2 guard what must stay (own mail keeps its Date; an unreadable line falls back). The in-place upgrade tests (formats 6 to 9) and the send-times-after-upgrade test are replaced by "every older format is rebuilt" (formats 1 to 10). Ran: mime, index, rebuild, sync (IMAP, POP3, DAV, send), engine commands, search, schedule, properties, recurring, importer (552 passed); 13 window test files (296 passed); test_layers, test_typed_calls. Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.
- At the first start of 1.9.276 every index is rebuilt once (status line "Rebuilding the index").

## 2026-10-22 1.10.277 R-39: contact groups

- Kerio first (live, test account): a vCard 4 group (KIND:group, MEMBER) is kept as written; Apple's form (X-ADDRESSBOOKSERVER-KIND/-MEMBER) becomes a Kerio group (X-DL:TRUE, PRODID) that still reads back in Apple's form, and Kerio adds the group's name to its members' CATEGORIES. In Deleted Items Kerio keeps a deleted contact and a deleted vCard 4 group, not its own group. The probe's six leftovers there were removed (by UID, checked first).
- pim/contact.py: GroupForm, members, version; the card keeps its own version (a vCard 4 card was written back as 3.0 since 0.0.57; shown on 1.9.276). merge.py: labels for the member and kind lines (they were already merged entry by entry).
- pimstore: index format 3 (is_group, members); rebuilt by itself at the first start. ItemQuery.groups.
- engine/groups.py (shared), engine/commands_groups.py: contact-group.list, -create, -rename, -delete, -add-members, -remove-members, -addresses (group.* was taken by the sidebar's account groups). contact.list: groups left out, group= gives the members, each row carries its groups; contact.show: groups; contact.delete and contact.move take the contact out of the groups of its address book; contact.suggest: groups by name first.
- Window: CollectionPanel.set_groups keeps the groups under each address book in place; the Contacts page: New group…, Write an e-mail, Rename…, Delete… (asks), Delete key, Add to group (ticks, New group… with the selected contacts, as New tag…), Remove from group (greyed outside a group), drag onto a group, the count line "Members (n), and k more without a contact here", Groups in the details; completer shows a group's label and fills its addresses. Icon system-users from Breeze.
- Found by an existing test and fixed: the groups' answer wrote the count line before the list arrived ("Contacts (0)" for a moment); the count line is now only written for the view whose list has arrived.
- Found, not fixed (outside R-39): Kerio answers GET of an existing contact with 404 (events are fine); sync/davitems.py reads a single item with GET when the server says "changed meanwhile".
- Server rule D-193: a group is a card: changed on the server first, offline here and sent later, refused: nothing changed, changed on the server: followed and merged, two computers the same (tests for each).
- Tests: test_pim_contact (5 new), test_engine_groups (11 new), test_ui_contacts (4 new), test_ui_completer (1 new), live test_live_dav (1 new, passes on Kerio). Ran: 29 related files (582 passed), the new files in reverse and random order, Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.10.278 One item read by the multiget first (found in R-39)

- Cause (checked live on Kerio, own address book): GET of an existing contact answers 404 (the multiget gives the card; events are fine; a contact just written answers with Kerio's own mail message). The sync read one item with GET in 7 places: a contact changed on both sides became a false "deleted on the server" sync conflict when saved here (412, then GET 404), and a delete refused because the phone changed it in the moment between the sync's read and its delete lost the link without a word.
- protocols/dav.py: DavClient.read (the multiget first, GET when it is left out; None only on 404, any other failure raises, D-196); fetch and read share _multiget (fetch unchanged). sync/davitems.py (3 places), sync/davsync.py (4 places; _push_delete gets the remote collection).
- Found, not changed: a contact (event, task) deleted here offline and changed on the server before the next sync comes back with the server's version without a word (the pull comes before the push); online it is a sync conflict. Told to the owner.
- Server rule D-193: only how this computer reads the server; nothing new is sent.
- Tests: fakedav no_contact_by_get (Kerio) and gets_failing; test_protocol_dav 1 new, test_engine_conflicts 3 new; 3 of the 4 fail on 1.10.277 (shown), the fourth (changed offline and on the server, merged by the sync) guards what must stay; live test_live_dav 1 new (passes on Kerio). Ran: 11 related files (198 passed), the changed files in reverse and random order, Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 1.10.279 Deleted here offline, changed on the server: a sync conflict (the owner: A)

- Cause: a sync takes in the server's changes before it sends what changed here; `_receive` found no copy of an item deleted here offline and saved the server's version as a new item, without a word. Online the same is a sync conflict (davitems.delete).
- sync/davsync.py `_receive`: a linked item that is gone here (not among the files, so a damaged item is not "deleted") and changed on the server is recorded as a conflict changed_on_server with the server's version; nothing made here; the push skips it while it is open (held). Unchanged there (a server without version tags lists it as changed): nothing done, the push sends the delete.
- Server rule D-193: 1.4 (both sides changed: the person settles), 1.3 (the delete made offline kept until settled); nothing deleted on the server before the person chooses.
- Tests: test_engine_conflicts 5 new (events, tasks, contacts; settled either way; unchanged there deleted at the next sync); the 4 conflict tests fail without the fix (shown), the fifth guards what must stay. Ran: 10 related files (190 passed), test_engine_conflicts in reverse and random order, Pyrefly 0 (Linux and win32), layer check kept. Full suite not run.

## 2026-10-22 2.0.280 B-16: columns per folder (the owner chose 2.0)

- ui/columnsetup.py (new): the six kinds of folder and their starting setups, the key of each view's setup, the sort fallback, the setups as text.
- ui/messagecolumns.py: the setup of the open folder taken into use before the list asks (follow_view, also after a search starts or ends); every change of columns, order, widths or sort kept for that folder only; Apply to all folders (QAction in the header's menu and Menu > Folder); the sort falls back when its column goes; renamed and deleted folders (FolderPane.folder_moved); the one setup of earlier versions handed over to incoming mail once (Remembered.earlier_setup removes the old keys sort, oldest_first, columns, column_layout).
- ui/messagelistpane.py: use_sort (the folder's sort without asking the list); Sent in the Sorted by menu, Saved in Drafts. ui/messagelist.py: set_column_labels (Saved). ui/columns.py: the Sent column; the standard columns are incoming mail's (From instead of From / To); the pixel widths of before 0.1.89 are no longer taken over.
- ui/prefs.py: columns left Preferences; Remembered.folder_columns, type_columns, earlier_setup. ui/settingsdialog.py: the Message list page shows the open folder's columns (SettingsContext.columns, standard_columns, chosen_columns). ui/columnsdialog.py: Standard columns are the open kind's.
- Index format 12: sent (the Date line) with messages_by_sent; SortField.SENT; MessageRow.sent; message.list, message.order and the Favorites views sort and group by sent. Every mail index is rebuilt once at the first start.
- Server rule D-193: only how this computer shows mail; nothing is sent to a server, no server data changes; kept on this computer only (D-201).
- Tests: test_ui_columnsetup (9 new), test_index_index (1 new), test_engine_view_order (1 new), test_ui_prefs (1 new); test_ui_columns, test_ui_prefs, test_ui_preferences, test_ui_schedule and test_ui_window adapted to per-folder setups. The new tests fail on 1.10.279 (shown: import error, no sent field, no sort sent, no earlier_setup). Ran: 10 engine and index files (302 passed), 14 window files (306 passed), Pyrefly 0 (Linux and win32), layer check passed.
- Open: B-17 (where the column choice belongs, the owner later).

## 2026-10-22 2.0.281 The index rebuild window (the owner)

- Engine: `RebuildAborted`; `rebuild_index` counts the messages first, reports done and total, stops when aborted (the new file put away) and replaces the old index only inside `IndexGate.replacing` (waits until no command has it open; `Engine.open_index` holds the gate for every index it opens until it is closed, `Index.when_closed`). `Engine.rebuild`, `abort_rebuild`, `rebuilding` (`RebuildStep`: account, done, total, usable, ended). Commands `index.check`, `index.prepare`, `index.fill-sent`, `index.abort`; `index.rebuild` keeps the old index in use and catches up with `reconcile_index` after the swap. Format 11 is upgraded in place (`UPGRADE_FROM_11`, `SENT_FILL_KEY`; `sent_filling`, `sent_to_fill`, `fill_sent`). The command line tool shows the rebuild's progress.
- Window: `ui/indexrebuilds.py` (`RebuildWindow`, `IndexRebuilds`, `ask_abort`); the start checks and rebuilds before the accounts load; `MailChecking.rebuild_running` (automatic checks wait, one check after); `StatusReport.rebuild_progress`; Settings > Rebuild the search index goes to the main window (`rebuild_requested`), one place after the other, its lines back when all are done.
- Server rule D-193: nothing touches a server; the index is a copy of the mail files, an abort never changes a mail file.
- Tests: test_engine_rebuilds (5 new), test_ui_indexrebuilds (9 new); test_engine_commands (the index is opened inside its gate), test_index_rebuild (how many of how many), test_ui_preferences (the rebuild through the main window) adapted. The new files fail on 2.0.280 (shown: RebuildAborted and RebuildStep missing). Ran: 13 engine and index files (318 passed), 12 window files (248 passed), Pyrefly 0 (Linux and win32), layer check passed.

## 2026-10-22 2.0.282 gitpush.sh finishes a failed push (the owner)

- Cause: GitHub refuses a push now and then ("remote rejected ... (failed)", a few times a day); the release tool then stops after the branch or before the tag, and gitpush.sh refused to run again ("already has a tag", "nothing has changed").
- gitpush.sh 2.0.282 (private): after the release tool, what GitHub is missing (branch, tag) is pushed, up to 3 times 10 s apart; with nothing changed and the version's tag on the last commit, `./gitpush.sh` (with or without the message) only finishes the push. The branch is pushed only over an earlier commit of this computer or the commit an update replaces (same parent, --force-with-lease); a tag on GitHub is moved only from that commit; anything else stops with the reason.
- Tested against a local stand-in for GitHub whose hook refuses pushes on command (scratchpad/gitpush-test/run.sh, 7 cases, 17 checks pass): tag refused once, refused every time then repaired with and without the message, branch refused, an update refused (branch, tag), a foreign commit on GitHub (stops, nothing pushed), all there already, new changes on a tagged version.
- Open: 2.0.281's tag is not on GitHub (refused on 2026-10-22): `git push origin 2.0.281` once.

## 2026-10-22 2.0.283 No personal detail in the project folder or on GitHub (the owner)

- Found on GitHub: the test account's address, the test servers' names and one's network address, the two computers' names, a sender address and shop names from the owner's mail (in documents, four version-history comments and the git-ignored tests), and DECISIONS-ARCHIVE.md, which was tracked although private.
- Every such detail replaced by neutral words in every file of the project folder, tracked or not (D-230); the tests use made-up names (Corner Shop, dues@example.com, kerio.example); DECISIONS-ARCHIVE.md git-ignored.
- Out of the project to ~/.claude/chrono-mail: the three live-test login files (tests/live/account.py reads them from there; CHRONO_MAIL_LIVE_SETTINGS), four scratch files with account data and an old backup of DECISIONS.md (moved, not deleted), the private words and their scanner (private_scan.py; it also takes every account's address and servers from Chrono Mail's own settings).
- gitpush.sh 2.0.283: every file of the commit and the message are scanned first; a match stops the push.
- The repository on GitHub is made again with a fresh history (the owner deletes the old one; same name, description, website, labels, settings); the old history kept in ~/.claude/chrono-mail/old-git; the 7 old releases are gone.
- Server rule D-193: nothing here concerns a server.
- Checks: the scan of the whole project folder finds nothing; the live POP3 tests pass with the logins in their new place (6 passed); the tests whose made-up data changed (test_engine_dav, test_index_entry, test_index_index, test_sync_dav, test_ui_list_changes, test_ui_rapid_actions: 213 passed); the push-script bench, now with a planted word in a file and in the message (8 cases, 19 checks, all pass).
- `DEVELOPMENT-RULES.md` deleted (the owner: replaced by `PY-DEVELOPMENT-RULES.md`); every mention points to `PY-DEVELOPMENT-RULES.md` (AGENTS.md, README.md, TRANSFER-WINDOWS.md, packaging/PACKAGING.md, D-013); the archives keep their old text.

## 2026-10-22 2.0.284 A new IMAP folder is known to the sync at once (the Windows computer's report, F1)
- Found by the Windows computer (its report of 2.0.283, kept privately, F1): a folder made here was made on the server but not recorded in the sync state until the next check; renamed or deleted before that check (by another program, or here offline), the check took it for a folder made here offline and made it again on the server (the rename left both names; the delete brought it back). Also: taken off the server's list before the next check, it stayed here. Moving or copying into a folder the server did not have yet recorded how the server numbers it, but not its server name, so a later rename or delete on the server was not followed either.
- Cause: `ServerSession.server_folder` (create) wrote nothing to the sync state; `follow_server_folders` (R-30), `rename_later`/`delete_later` (R-31) and `leaving_folders` (R-34) need the folder's server name and UIDVALIDITY.
- Fix: `server_folder` records both the moment the server made the folder (`set_server_name`, `_known`), as a check does. D-231.
- Server rule D-193: 1.2 a rename or delete on the server is followed at the next check; 1.3 an offline rename or delete reaches the server; nothing is made again.
- Checks: 5 new tests in test_engine_imap_folder_actions.py (rename and delete, by another program and here offline; unsubscribed), all 5 fail on 2.0.283; related tests pass (test_engine_imap_folder_actions, _online, _folders, test_engine_imap, test_engine_mail_conflicts, test_mailbox_guard: 68; test_sync_imap, _changes, test_engine_many: 91); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run.
- Open from the same report: F2 (a server mark one check late) and F3 (a calendar name or colour clash never settled), next.

## 2026-10-22 2.0.285 A server mark shows in the same check as an offline mark (the Windows computer's report, F2)
- Found by the Windows computer (its report of 2.0.283, kept privately, F2): a message marked read here offline and flagged on the server meanwhile showed only "read" after the next check, "flagged" one check later; the same on a plain server, with CONDSTORE and with QRESYNC. Nothing was lost.
- Cause: `_push_flags` sent our marks (only the marks that differ, D-195), recorded our marks as the agreed ones and put the message in `pushed`, which `_update_flags` skipped ("the summary we hold is out of date"), so the server's other change waited for the next check.
- Fix: after sending, the check's picture of the server is brought up to date (as read, plus what was added, minus what was removed: what the server holds now); `pushed` is gone; `_update_flags` takes the server's other marks in the same check. A change on the server between the read and the send shows at the next check, as before.
- Server rule D-193: 1.2 every change on the server shows at the next sync.
- Checks: new test_sync_imap_changes.py test on plain, CONDSTORE and QRESYNC (3), and test_sync_imap.py's merge test now checks after one sync: all 4 fail on 2.0.284. test_a_local_flag_change_waiting_to_be_sent_is_not_overwritten encoded the old one-check-late result (exactly FLAGGED, nothing updated): now the change here is kept and the server's read mark comes along. Related tests pass (test_sync_imap, _changes, test_imap_tags, test_engine_imap, _online, _folders, _folder_actions, test_engine_mail_conflicts, test_engine_many: 168); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run.

## 2026-10-22 2.0.286 A calendar name or colour clash is a sync conflict (the Windows computer's report, F3)
- Found by the Windows computer (its report of 2.0.283, kept privately, F3): a server calendar renamed on computer A while offline and on computer B online meanwhile was never settled: A kept its name, an error line came at every sync, nothing in Sync conflicts, the two computers stayed different. D-192 said "until Sync conflicts exists (R-24)"; Sync conflicts came in 1.1.226, names and colours were never moved into it. The colour clash and a name the server refused took the same way.
- Fix: `sync/davsync.py` `_look_conflict`: a clash (name and/or colour) is a sync conflict `collection_look_clash` with the clashing fields; a name the server refused for good (sent by the sync, or the person's online rename kept here) is `collection_look_refused`; kept up to date by every sync, never doubled, forgotten once both sides agree; no error line. `conflict.settle` (`_settle_look`): the choice (one side, or per field) goes into the account's kept look: Chrono Mail's waits to be sent with the server's value of then as its base, the server's is followed and shown here at once; Retry sync for a refusal; the synchronisation started at once does the rest. The window shows them as Calendar, Task list or Address book with the Why icons (both sides, refused); the popup's per-field choice works as for items. A colour the server does not take stays here (D-201), as before.
- Server rule D-193: 1.4 a clash or lasting refusal is a sync conflict the person settles; 1.5 afterwards both computers and the server show the same (tested with two computers).
- Checks: 8 new tests in test_engine_conflicts.py (two computers, settled both ways and per field, gone by itself, refused online and offline, Use Server data) and test_engine_dav.py's clash test changed (a conflict, no line): all 9 fail on 2.0.285. Related tests pass (test_engine_conflicts, test_engine_dav: 71; test_sync_dav, test_engine_mail_conflicts, test_typed_calls, test_protocol_dav: 63; ui/test_ui_sync_conflicts: 12); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run. D-192 rewritten (old wording in the archive).
- The three findings of the Windows computer's first half of the report are done (2.0.284 to 2.0.286); its 22 Windows test failures are still under discussion; the second half follows.

## 2026-10-22 2.0.287 Eleven tests made right on Windows and everywhere (the Windows computer's report)
- Of the Windows computer's 22 failures (its report of 2.0.283, kept privately): three failed on Linux too. test_legal_names: the reference client's name was in D-226 and in the 1.9.276 entry here (now "the reference client's"). test_engine_registry: `contact-group.delete` (1.10.277) is destructive and was missing from the test's list. ui/test_ui_icons: its hand-made account and folder stand-ins fell behind (kind, storage since D-185; messages since D-225); it now uses the engine's own AccountSummary and FolderInfo.
- Windows only: the 0o600 permission checks (test_config, test_config_rules, test_engine_sidebar, ui/test_ui_dialogs) only where the system has such permissions; the XDG test only on Linux; test_engine_registry expects the path as the system writes it; test_import_emclient replaces os.sync with raising=False; test_type_strictness runs Pyrefly with `--python-interpreter-path` of the test run (it found no packages on Windows).
- Not changed, sent to the Windows computer as research (BRIEF-2.0.287.md in its private folder): 10 window tests on sizes, selection colour and Tab order (neither the tests nor the program set the "Sans Serif" the report names), and the contacts test that fails only in a full run (D-223).
- Server rule D-193: nothing here touches server data. The program is unchanged.
- Checks: the changed tests (test_config, test_config_rules, test_engine_sidebar, test_import_emclient, test_type_strictness, test_engine_registry, test_legal_names: 163; ui/test_ui_dialogs, ui/test_ui_icons: 26) pass on Linux (the three that failed on Linux before now pass; the Windows-only ones are for the Windows computer to confirm); Pyrefly 0 (Linux and win32); layer check kept.

## 2026-10-22 2.0.288 Every standard folder before the person's own folders (the owner's bug report)
- The owner snoozed a message: the Snoozed folder was made in Local Folders (the engine told folder_made, the list took it in) but sorted among the owner's own folders, number 23 of 25, so it seemed missing. The owner's rule: all standard folders first, before any folder the person made.
- Cause: two definitions of "standard folder" in the window: the order of the folder list (`_ROLE_ORDER` by role plus Outbox and Scheduled by place) did not know Snoozed, which has no role; Settings > Sidebar's list (`LOCAL_FOLDER_KINDS`, `local_folder_kind`) did, and put it last. Recovered (made by the program, D-049) was in neither.
- Fix: one definition: `config` `LOCAL_FOLDER_KINDS` is every standard folder in the one order (inbox, drafts, sent, outbox, scheduled, snoozed, archive, junk, trash, recovered); `ui/models.py` `standard_kind` knows a folder by its role or, for the folders the program makes without a role, by its place; the order (`_order`), Settings > Sidebar (`local_folder_kind`) and the icons (`_icon_kind`: Snoozed gets its clock in the tree) use it. `_ROLE_ORDER` is gone. D-232.
- Server rule D-193: only the order of the list on this computer; nothing on any server.
- Checks: two new guards in ui/test_ui_tree_rows.py (every kind in LOCAL_FOLDER_KINDS is known by the one definition; in Local Folders, a POP3 account and an IMAP account the standard folders come first in that order, then Aaa, Taxes, Zzz): they fail on 2.0.287 (the old order put Recovered and Snoozed after Aaa). ui/test_ui_models.py had the old order as expected (Projects before Snoozed): corrected. Related tests pass (test_engine_sidebar, test_config: 73; ui/test_ui_tree_rows, _preferences, _icons, _models, _folder_counts, _keys: 92); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run.

## 2026-10-22 2.0.289 The owner's order of the folders under every account
- The owner: Unread, Inbox, Flagged, Drafts, Outbox, Sent, Snoozed, Scheduled, Recovered, Archive, Junk, Trash, then the own folders. `config` `LOCAL_FOLDER_KINDS` holds the order of the standard folders (Settings > Sidebar's ticks follow it); `ui/models.py` `_account_entry` puts Flagged right after the Inbox (right after Unread when the Inbox is hidden). D-232 updated.
- Server rule D-193: only the order on this computer.
- Checks: the order tests (ui/test_ui_tree_rows.py, ui/test_ui_models.py) expect the owner's order: 6 fail on 2.0.288, all pass now; related tests pass (ui/test_ui_tree_rows, _models, _icons, _preferences, _folder_counts, _keys: 92; test_engine_sidebar, test_config: 73); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run.
- Next: Favorites in the same order (the owner, with the Proceed; its own changelist).

## 2026-10-22 2.0.290 Favorites in the owner's order; Event colours without an outline (the owner)
- Favorites: Unread, All Inboxes, Flagged, Unreplied, Drafts, Outbox, Sent, Snoozed, Scheduled, Archive, Junk, Trash (`ui/models.py` FAVORITES; the order under every account, D-232, with All Inboxes in the Inbox's place and Unreplied after Flagged, the owner). Settings > Sidebar and Show on startup follow it. D-061 updated.
- The Event colours window (`ui/paletteeditor.py`): the scroll area around the list of colours has no frame (the owner's screenshots, Linux and Windows).
- Found: 2.0.289 left ui/test_ui_startup_view.py::test_a_hidden_folder_of_local_folders_is_greyed_among_the_choices expecting the old order of Local Folders' standard folders (that file was not among the tests run for 2.0.289); corrected now.
- Server rule D-193: only the order and the look on this computer.
- Checks: the Favorites order tests (ui/test_ui_startup_view, _models, _preferences) fail on 2.0.289 (6 and 1 error) and pass now; the palette editor test checks the frame (fails on 2.0.289); related tests pass (ui/test_ui_startup_view, _models, _preferences, _tree_rows, _folder_counts, _calendar, _icons: 111 and 13); Pyrefly 0 (Linux and win32); layer check kept. Full suite not run.

## 2026-10-22 2.0.291 Event colours: room between the delete buttons and the scrollbar (the owner)
- `ui/paletteeditor.py`: the list of colours has a right margin of 8 px at the windows' size (`LIST_RIGHT_MARGIN`, `px_for`), so the delete buttons no longer touch the scrollbar (the owner's screenshot).
- Server rule D-193: only the look on this computer.
- Checks: ui/test_ui_calendar.py's palette editor test checks the margin (fails on 2.0.290); ui/test_ui_calendar: 13 passed; Pyrefly 0 (Linux and win32); layer check kept.

## 2026-10-22 2.0.292 The window tests that still expected the old order (the Windows computer's report of 2.0.290)
- The Windows computer confirmed 2.0.288 to 2.0.290 on the real Windows display (the order under every account, Favorites, Event colours) and found tests/ui/test_ui_window.py still expecting the old order (3 tests): that file was not among the tests run for 2.0.288 to 2.0.290. Now every window test file that reads the folder list or Favorites was run (22 files, 372 tests): two more expected the old positions (test_ui_advancedsearch: Flagged and Inbox by row; test_ui_schedule: Scheduled right after Outbox). All five now expect the owner's order (D-061, D-232). The program unchanged.
- Not changed: ui/test_ui_models.py::test_the_unread_count_is_drawn_bold_at_the_right_edge passes or fails by the font on Windows; added to the Windows computer's research of the "Sans Serif" font (its brief, point 5).
- Server rule D-193: nothing here touches server data.
- Checks: the 22 window test files: 370 passed and these 2 failed before the correction; test_ui_advancedsearch, test_ui_schedule, test_ui_window: 115 passed after it.

## 2026-10-22 2.0.293 Window tests on Windows get real fonts (the Windows computer's answer to its brief)
- Cause found by the Windows computer: Qt's offscreen platform (set by tests/conftest.py) finds no font files on Windows (0 families), "Sans Serif" falls back to a font about twice as wide; that alone made the ten window tests fail (minimum width 1029 instead of 768, Mark and Tag folded into More so the Tab test saw only the search field, the selection test's centre pixel on a white letter of the wider text). With the Windows font folder all ten pass; the real program is right.
- Changed (tests only): tests/conftest.py sets QT_QPA_FONTDIR to %WINDIR%\Fonts on Windows when it is not set; tests/ui/conftest.py gives the window tests on Windows Segoe UI 9 pt (the real program's font there; "Sans Serif" resolves to a decorative font with that folder) before the first look is taken; ui/test_ui_theme.py's selection test reads the row's most common colour, never one pixel.
- Still researched by the Windows computer (its brief, section 6): the contacts test that fails about 1 run in 4 (the edit's save has no effect within 8 s: an error, no answer, or dropped), and five look tests on the real Windows platform.
- Server rule D-193: nothing here touches server data. The program unchanged.
- Checks on Linux: ui/test_ui_theme, _toolbar, _appearance, _parts: 86 passed; test_config: 66 passed. The Windows part is for the Windows computer to confirm (this computer has its fonts).

## 2026-10-22 2.0.294 The window holds every job until its answer (the contacts save that never answered)
- The Windows computer found a contact's Save left on "Saving…" for ever in about 1 test run in 4: the command was registered in `Backend._pending`, no worker ran it, `_deliver` never came, the engine never applied it. Waiting for a failure to show which of three it was took too long (the owner stopped it); the code itself shows the gap: `Backend.call` made a Python `_Task` (QRunnable) and handed it to Qt's pool without keeping a reference, so the job could be thrown away before it ran.
- Fix: `Backend._jobs` holds every job from its start, in every lane (the pool, in-order queue, checking, waiting), until `_deliver` has its answer. D-030 (engine part).
- Server rule D-193: nothing here touches server data.
- Checks: new test ui/test_ui_backend.py (every lane held, let go after the answer; fails on 2.0.293); ui/test_ui_backend, _contacts, _rapid_actions: 36 passed; Pyrefly 0 (Linux and win32); layer check kept.

## 2026-10-22 2.0.295 A window never waits for ever (the Windows computer's note on 2.0.294)
- The Windows computer showed two more ways to "Saving…" for ever: (a) a job ending with an error that is no Exception (a BaseException) sent no answer; (b) a wait without a limit. Agreed with the owner: (a) is fixed at its cause; for (b) a 10-second timer in the windows was rejected (it would call a slow but healthy save failed while the server still takes it); instead every wait was checked for a limit: server requests have theirs (30 s per calendar request, the mail protocols' timeouts), SQLite 30 s, engine locks are our own code (fixed at their cause if one hangs); the one wait outside our code without a limit was the system's password store.
- `ui/backend.py`: `_Task.run` also catches what is no Exception and answers "The command ended without an answer: <kind>", so every job answers exactly once.
- `core/credentials.py`: every call to the store (read, write, remove) waits at most 60 s (`STORE_TIME_LIMIT`, time to unlock the wallet) in a thread of its own; then `CredentialFailure` "The password store did not answer within 60 seconds (is the wallet waiting to be unlocked?)"; the late answer is ignored. D-127, D-030.
- Server rule D-193: a save that fails this way changes nothing here or on the server.
- Checks: two new tests (test_core_credentials: a store that never answers, limit 0.2 s; ui/test_ui_backend: a BaseException still answers once), both fail on 2.0.294; test_core_credentials, test_crypto*, test_config: 146 passed; ui/test_ui_backend, _contacts, _key_recovery: 41 passed; Pyrefly 0 (Linux and win32); layer check kept.

## 2026-10-22 2.0.296 The tests are in git again (the owner)
- The owner's request of chat 36 (make the tests public) was stopped then because the tests held personal details; 2.0.283 removed them (D-230), so the reason is gone. `tests/` is no longer in `.gitignore` nor in `gitpush.sh`'s private list; the live-test logins stay outside the project. D-233 (replaces D-161). TRANSFER-WINDOWS.md: the Windows computer moves its own `tests` folder aside before its first pull of 2.0.296 (its Windows-only test changes stay there, the owner's one-way rule). PROMPT-NEW-SESSION.md: tests/ off the private list.
- Checks before the push: every file of tests/ scanned byte for byte for personal details (0 hits; 186 .py, 18 made-up .eml, 2 example .toml, 3.2 MB, no file over 500 KB); test_legal_names with tests/ published.
- Server rule D-193: nothing here touches server data.

## 2026-10-22 2.0.297 The Windows computer's Windows-only tests in the project
- `tests/ui/test_ui_windows_display.py` from the Windows computer (its README in the private report folder): four tests that skip themselves except on Windows, two only on the real Windows display (QT_QPA_PLATFORM=windows): the window tests have real fonts (2.0.293), Event colours fits its rows and keeps the margin to the scrollbar (2.0.291), the narrowest window is 768 px, the selection colours with the program's look at the display's own scale and scheme. Taken over with three corrections: the colour helper typed (QAbstractItemView, QModelIndex, no type ignores), QPalette.ColorRole, the calendar checked before use (Pyrefly). The five old look tests stay as they are (they hold offscreen; this file covers the real display).
- Found: Pyrefly honours .gitignore; with tests/ out of it (2.0.296) it checks the tests again for the first time since 1.1.176 and finds 288 type problems there (missing attributes 126, argument types 97, untyped lambdas 41, …; tests/ui 176), 0 in the program. tests/test_type_strictness.py::test_project_has_zero_type_errors fails since 2.0.296. Not changed: the owner decides.
- Server rule D-193: nothing here touches server data.
- Checks: the new file on Linux: 4 skipped; Pyrefly on it 0 and on src 0 (Linux and win32 the same apart from the tests); layer check kept.

## 2026-10-22 2.0.297 (documents) DECISIONS.md rewritten to short entries (the owner)
- DECISIONS.md had grown back to 143,877 characters (~36,000 tokens, 38% of a session start): the entries written since D-162 held how things are built (function names, formats, locks), measurements, the story behind them and full screen layouts. The 30 longest entries alone were 60 KB (D-194 6,424 characters).
- First the whole file, every entry in its wording of that day, appended to the end of DECISIONS-ARCHIVE.md ("DECISIONS.md as it stood until 2026-10-22 (2.0.297)"); a copy in scratchpad/DECISIONS-before-rewrite-2.0.297.md.
- Then every entry rewritten to its rule, a one-line Why and the Rejected options that may come up again; the safety guards and Rule 4.1 limits kept. 236 entries before and after (checked by number), 43,979 characters (~11,000 tokens). D-193 points to AGENTS.md §0 for its full text. D-162 now says an entry stays under about 600 characters and holds no function names, measurements, layouts or history. The "next number" line corrected (D-234; it said D-228).
- Server rule D-193: nothing here touches server data. The program unchanged.
- Checks: test_legal_names: 2 passed; the private-word scan of DECISIONS.md: clean.

## 2026-10-22 2.0.298 The owner's new folder order (D-232, D-061)
- Under every account and Local Folders: Inbox, Unread, Outbox, Sent, Drafts, Flagged, Snoozed, Scheduled, Recovered, Archive, Junk, Trash, then the person's folders. Favorites: All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Unreplied, Snoozed, Scheduled, Archive, Junk, Trash. Settings > Sidebar and Show on startup follow by themselves (they read the same two definitions).
- config/sidebar.py LOCAL_FOLDER_KINDS reordered; ui/models.py FAVORITES reordered; Unread and Flagged placed after Inbox and Drafts by `_place_after` (before the first folder later in the order, so a missing Inbox or Drafts leaves them in their place).
- A first version reused the name `unread` for the Unread entry while the folder loop rebinds it to a count: the folder list failed and every window test waited 20 s (found in the first test run, fixed before anything else).
- Server rule D-193: the order is this computer's window only; nothing reaches the server.
- Tests: the 12 order tests that failed on the new order updated (test_ui_models, test_ui_tree_rows, test_ui_startup_view) plus 6 in test_ui_preferences, test_ui_advancedsearch, test_ui_schedule, test_ui_window; test_ui_window's pinned-Inbox check now looks at the Inbox (it looked at Flagged since 2.0.289). Ran: those three files (50 passed), the 6 named tests (passed), test_engine_sidebar (7 passed). Pyrefly src 0 (Linux and win32); the touched test files 55 old problems before and after; layer check kept.

## 2026-10-22 2.0.299 Favorites: every standard favorite as a tick in the right-click menu (the owner, D-234)
- The owner's report: on the Windows computer the Favorites heading had no menu. The heading only had "Show again" while something was hidden (each computer keeps its own Settings > Sidebar), so the menu depended on what happened to be hidden.
- Now the heading, every standard entry and every pinned folder (after its own four items) list All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Unreplied, Snoozed, Scheduled, Archive, Junk, Trash with a tick; a click sends favorite.show (the setting of Settings > Sidebar). Show on startup's choice ticked and greyed; the last entry left in Favorites (no pins) greyed, so the heading and its menu never vanish. "Show again" and "Hide from Favorites" gone. ui/folderpane.py `_favorite_ticks`.
- Server rule D-193: this computer's sidebar setting only; nothing reaches the server.
- Tests: test_ui_window.py::test_the_folder_list_menu_offers_favorites_and_groups updated; new test_a_right_click_in_favorites_ticks_every_standard_favorite_and_a_click_shows_or_hides_it (fails on 2.0.298). Only these two ran: 2 passed. Pyrefly src 0 (Linux and win32); test_ui_window.py 3 old problems (line 626), none new; layer check kept.

## 2026-10-22 2.0.300 The Favorites menu lists only the favorites that can appear (the owner, D-234)
- The owner's screenshot: Archive was ticked in the menu, but no account had an Archive folder, so Favorites did not show it. The menu read all 12 favorites; Favorites and Settings > Sidebar read the favorites in use (Snoozed, Scheduled, Archive only once a mailbox has that folder). Now the menu reads that same list.
- Server rule D-193: this computer's sidebar setting only.
- Tests: the two Favorites menu tests in test_ui_window.py updated (the new one also checks that a Snoozed folder made through New folder brings its tick, after Unreplied; fails on 2.0.299). Only these two ran: 2 passed. Pyrefly src 0 (Linux and win32); test_ui_window.py 3 old problems (line 626); layer check kept.

## 2026-10-22 2.0.301 Attachments a text does not show are listed, and pictures shown below the text (the owner's bug report, B-11, D-235)
- The owner's message: a plain text, a 1.2 MB picture labelled Content-Disposition inline, and an 18-byte unnamed text part. Both were marked inline, so hidden from the attachment row, the paperclip, the filter, rules, Forward and Export; with no HTML text nothing could show them: they were shown nowhere. Checked from the owner's store (structure only).
- Cause: inline was taken from the sender's label, not from what Chrono Mail shows. Now mime/parser.py `_mark_shown_parts`: inline only when the HTML text refers to the part by cid:.
- B-11: message.show gives `pictures` (PNG, JPEG, GIF, WebP, BMP the text does not show, within the 5 MB / 20 MB limits); the reader shows them below the text: HTML with max-width 100%, plain text in `PlainTextView`, fitted on every resize. When the plain text of a message with HTML is shown, the pictures the HTML showed come below it too.
- Index format 13: 12 and 11 are upgraded in place; index.fill-sent reads the attachments with the Sent moment (no blocking rebuild at start; an unreadable message keeps its paperclip).
- Server rule D-193: only how the stored message is shown changes; nothing reaches the server.
- Tests: new test_mime_parse (2), test_engine_rebuilds (1: listed, pictures, the upgrade from 12 fills the paperclip), test_ui_reader (2); all fail on 2.0.300. Ran: test_mime_parse, test_mime_build, test_index_entry, test_engine_rebuilds, test_ui_reader in full; test_engine_commands, test_engine_local, test_engine_search, test_index_index, test_ui_window, test_ui_models only their attachment/inline/image/format tests: all passed. Pyrefly src 0 (Linux and win32), the new test lines 0; layer check kept.

## 2026-10-22 2.0.302 Open attachment, Save as… and selecting a file of the message (the owner, D-236)
- The owner: a single click on a file of the attachment row saved it at once; the reference client selects on a click and has Open Attachment and Save As in a right-click menu.
- Now ui/reader.py `AttachmentChip` (checkable, one at a time, double click opens, right-click menu), `attachment_menu` (Open attachment, Save as…), the same menu on a picture of the message: plain text through `PlainTextView.picture_at` (the layout's imageAt), HTML through the page's last context-menu request matched by the picture's data address (`picture_url`). InlineImage.position: the picture's place among the attachments.
- engine/commands_attachments.py: message.attachment.open (a copy in data/opened/<random>/<name>, folder 0700 and file 0600, `program` for files that run: .exe, .bat, .msi, .sh, .desktop, .js, .jar and others), message.attachment.clean-opened (ReadingPane.forget_opened_copies at start). ui/attachmentopening.py asks "Open a program?" (Cancel by default) before a program, says when no program opens the kind of file.
- Server rule D-193: opening and saving write files on this computer only.
- Tests: new tests/test_engine_attachments.py (3), tests/ui/test_ui_attachment_opening.py (2), test_ui_reader (2 new, the attachment row test updated), test_ui_window::test_an_attachment_can_be_saved through Save as…; the new ones fail on 2.0.301. Ran: those files, test_ui_parts, test_typed_calls, test_layers: all passed. Pyrefly src 0 (Linux and win32), the new and changed test lines 0; layer check kept.
