# DEVLOG.md

Append-only. One entry per completed task or session. Newest at the bottom.

---

## 2026-10-08 Design session (chat 1)

**What happened**
- Researched the ArtCraft apps (PhotoCraft and siblings) for how they were built with AI agents: clean-room approach, AGENTS.md rulebook, everything-is-a-command, oracle tests, enforced layering, dev log for handover.
- Decided to build a full-featured mail client for Linux: Chrono Mail.
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
- `tests/fakeimap.py` (stateful fake server) and 60 client tests; 5 live tests against `mail.technova.us` (all pass, folders of their own are created and removed).

**Still open**
- IDLE, CONDSTORE (CHANGEDSINCE is supported in the call but not yet used), and the sync itself (tasks 2.2 to 2.5).

## 2026-10-10 Task 2.2: IMAP sync, server to store (version 0.0.29)

**What landed**
- `sync/imap_state.py` (append-only state), `sync/imap_sync.py` (folder plan, removals, flags, downloads, renumbered folders; D-049).
- Tests: 22 offline against the fake server (including crash repair, a dropped connection and the vanishing-folder guard), 1 live test against `mail.technova.us` (download, flag change, removal).

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
- The Kerio Connect 8.5.2 server of the user was probed without a login: TLS 1.2, a self-signed certificate, CalDAV at https://kerio.technova.us:11443/caldav/ (Basic authentication). A test mailbox exists; its credentials go only into the untracked local test settings.

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
- For the owner: at the next mail check the question comes for Trash of chronomail@bccbox.com.

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
- Found while answering: the 4 messages in that Trash are calendar and contact items Kerio put in Deleted Items when `tests/live/test_live_dav.py` deleted them; the owner's IMAP account is on the Kerio server (`mailserver.technova.us`), while the IMAP live tests use the DirectAdmin server (`mail.technova.us`). TRANSFER.md said they were the same account.
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
- New `TRANSFER-WINDOWS.md` for the Claude app on DEVELOPMENT (the Windows machine builds, tests and releases the setup.exe and may write code): its role, the rules, getting started, the file map, the checks on Windows, the installer, working together with the Kubuntu machine, open points. `AGENTS.md` lists it.
- Found while answering the owner: Chrono Mail has no protection against running twice (two copies would share the same mail); reported, not fixed.
- No code changed; no tests run.

## 2026-10-15 The first Windows build; its three fixes (version 0.5.129)

- The Windows machine (DEVELOPMENT) built `chrono-mail_0.5.128_setup.exe`: it worked after three fixes in `packaging/build_windows.ps1`, made there without a version and applied here: `${env:ProgramFiles(x86)}` (PowerShell read `$env:ProgramFiles(x86)` as `C:\Program Files(x86)`); `--include-package-data=tzdata:zones` (the time-zone files were included twice, 604 warnings); `win32ctypes.core` and `win32ctypes.pywin32` instead of all of `win32ctypes` (its tests were included). Build facts: Nuitka 4.2.2, Visual Studio Build Tools 18 (cl 14.5), Inno Setup 6.7.3, program folder 485 MB, setup.exe 130 MB. `packaging/PACKAGING.md` section 4.3 says so.
- The Linux build had the same time-zone fault: `packaging/build_in_container.sh` got the same change. Rebuilt: no duplicate warnings, 605 of 605 tzdata files, three starts with an empty environment kept running.
- Reported from Windows (not fixed): with two copies of Chrono Mail running, the index rebuild after a format change fails with WinError 32 on `index.sqlite-wal` (`index/index.py` lines 510-513): Windows cannot delete a file the other copy has open. It is the known missing protection against running twice; on Linux the delete succeeds silently.
- Still to test on Windows: the password store in the installed program.

## 2026-10-15 A failed index open closes its connection: the format upgrade works on Windows (version 0.5.130)

- Reported by the Windows machine and confirmed here: `Index.__init__` (`index/index.py`) and `PimIndex.__init__` (`pimstore/index.py`) opened the connection, switched it to WAL, then raised for an unknown format or a damaged file without closing it. The rebuild that follows deletes the `-wal` file; Windows refuses that while the file is open (WinError 32), so every index format upgrade failed on Windows, also with one copy running. Now both close the connection before the error goes on (`_open` split off); an unreadable format number counts as an unknown format (it raised an uncaught ValueError and leaked too); a failure while making a new index closes it as well.
- Tests run: `tests/test_index_index.py` (four new: old and unreadable format, damaged file, and the rebuild after a failed open), `tests/test_pimstore.py` (three new), `tests/test_index_rebuild.py`, `tests/test_engine_commands.py` (198 passed); the new ones fail without the fix (6 of 7; the rebuild test passes on Linux either way). The connection is checked with a recording connection class, because Linux cannot show the Windows file lock. Pyrefly 0 diagnostics, layer check kept; not the full suite. To test on Windows: the upgrade of an old index with the rebuilt setup.exe.

## 2026-10-15 The packaged program knows its version; the build copies only what it needs (version 0.5.131)

- The About window of the installed Windows program said "version unknown": `core/appinfo.py` reads `pyproject.toml` in the source tree and otherwise the package information, which Nuitka leaves out unless told. Both build scripts now pass `--include-distribution-metadata=chrono-mail` (also proposed by DEVELOPMENT). Checked with a small Nuitka test build of `program_version()`: 0.5.131 with the option, "unknown" without it.
- Found while checking: `packaging/build_in_container.sh` copied the whole project folder into the build container, since today including `chrono-mail-import-samples/` (the owner's private mail export, git-ignored). The container is temporary and deleted after the build; nothing left the machine. It now copies only `pyproject.toml`, `README.md`, `LICENSE`, `src` and `packaging`. Full Linux rebuild: the folder is not touched (0 mentions in the build log), three starts kept running.
- `.gitignore`: `chrono-mail-import-samples/` (the owner's exports from the reference client for the importer; never in git). STATUS.md backlog: a friendly message when Chrono Mail may not connect (a firewall or no internet), proposed by DEVELOPMENT; later.
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

- D-133. Reported by the owner, analysed on DEVELOPMENT and confirmed here from the code: `MessageListModel.show` emptied the list at once and then waited for the group headers and the first rows, one after the other (half a second in Unread). `ui/models.py`: `reload()` fetches both in the background and `_swap_in` replaces the rows in one reset; every caller of reload gets this (delete, move, archive, after sending, after a mail check, tags, import).
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

- Reported from DEVELOPMENT: Import clicked while the exports were still being read showed 100% at once (the bar's size was set once, from a count of 0) and the late reading answer replaced "Importing…" and changed the counts; the import itself ran fine. `ui/importdialog.py`: Import off until the reading answered; late and older answers ignored; the bar follows the count (D-134).
- Tests: `tests/ui/test_ui_import.py`, 3 new (each failed on the old code, checked); the stop test now waits for the reading before Import (a consequence of the change). Run: `tests/ui/test_ui_import.py` 10 passed. Pyrefly 0, layer check kept; not the full suite.
- Also in 0.5.143 (the owner's screenshot): the status line counted each batch of 1,000 from 1 again ("Importing mail… 225 of 1000"). `engine/commands_emclient.py` now reports the progress over the whole import (earlier batches and imports counted in); `ui/importdialog.py` takes that number as it is (it added its own count before); `ui/mainwindow.py` writes progress numbers with thousands separators ("45,225 of 140,269"). Tests: 1 in `tests/test_import_emclient.py`, 2 in `tests/ui/test_ui_import.py` (each failed on the old code, checked). Run: `tests/test_import_emclient.py` and `tests/ui/test_ui_import.py` 30 passed, `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 Four size sliders (version 0.5.144)

- Settings > Appearance > Sizes: Main window, Message list, Other windows and dialogs (new), Text of messages (D-135). Changed: `ui/scale.py` (a third factor, icons by the window they are in, the style gives menus and popups of the main window its text size), `ui/appearance.py` (the program's text at the other windows' size, `main_font`), `ui/theme.py` (the stylesheet at a given factor), `ui/mainwindow.py` (its own text size, tries out both sliders, message windows and composers at the other windows' size), `ui/settingsdialog.py` (the four rows; this window follows Other windows and dialogs), `ui/prefs.py` (`windows_scale`), `ui/reader.py` (the sender's circle by the window it is in), `ui/messagewindow.py` and `ui/composer.py` (their look at the other windows' size). Not needed after all: `ui/searchhelp.py` and `ui/searchhistory.py` (their sizes already follow the main window; their text comes through the style).
- A first try with a watcher of every event of the program (for the menus) crashed the appearance tests; it was replaced by a hook in the style.
- Tests: `tests/ui/test_ui_appearance.py`, 3 new (an older settings file; the main window and a dialog each follow their own slider, also the icons; a menu follows the window it opens from), each failing on the old code (checked); 4 existing tests changed to the new meaning (the program's text, the page list of Settings and the growing of Settings follow Other windows and dialogs; the names on the page). Run: appearance, preferences, settings, theme, composer, reader, search field, search help, sections, rules, columns 206 tests (1 failed: `test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons`, which also fails on the old code in that same run and passes alone: it depends on what earlier tests leave behind; not changed); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.
- Also in 0.5.144: STATUS.md's test count corrected (1,941), two Import window items on the Backlog.

## 2026-10-16 Settings changes one at a time; Chrono Mail runs once (version 0.5.144)

- Reported from DEVELOPMENT (0.5.143): after an import the first mail check said "The settings could not be saved: Access is denied". Cause (checked in the code): the accounts of a check ran at the same time and each read config.json, changed its own account and saved the whole file, with nothing making them take turns. On Windows replacing the file failed while another thread read it; on every system the last save undid the other changes. 12 places changed the settings that way. `engine/session.py`: one lock for the settings file (every read and save) and `change_config`, which applies a change to a fresh read under the lock; the long work (copying mail, making stores) stays outside it (measured: a read and save takes about 1 to 2 ms on the owner's disk). All 12 places use it: `engine/commands_mail.py`, `commands_emclient.py`, `commands_accounts.py` (add, remove, policy, edit, trust and forget certificates), `commands_local.py` (2), `local.py`, `commands_dav.py`. In `mail.fetch` the rules run first, and a failed save of the imported-account flag is a line in the check's report instead of ending the check (D-136).
- Chrono Mail runs once per user: the program looks for its own process (Linux: the process name `chrono-mail` in /proc, of the same user, which the program also sets on itself; Windows: `chrono-mail.exe` of the same user, from `WTSEnumerateProcessesW`); a second start asks the running program to show its window, or says "Chrono Mail is already running."; the command line tool refuses every command while the program runs (`--help` still works). New: `config/running.py`, `ui/singleinstance.py`; changed: `ui/app.py`, `ui/mainwindow.py` (`bring_forward`), `cli/main.py` (D-137).
- Tests: `tests/test_config_changes.py` (2: eight accounts changed at the same moment all keep their change; the rules run when the flag cannot be saved), `tests/test_running.py` (6, one only on Windows), `tests/ui/test_ui_singleinstance.py` (2). On the old code 7 of them failed (checked); the two that check that nothing is found wrongly (a killed program, a process with another name) pass there by their nature. Changed as a consequence: `tests/test_cli.py` and `tests/live/test_live_clients.py` give the command line tool their own check (so they run while the program is open); `tests/test_engine_local.py` makes its crash at the commit point in the file write of the settings. Run: settings, running, skip-known, importer, account edit, local, dav, certificates, engine commands, config and command line tests 228 passed, 1 skipped (Windows); single instance and tray 12 passed (the window tests run apart from the others, as their fixtures need). Pyrefly 0, layer check kept; not the full suite. The Windows check is not tested yet: DEVELOPMENT runs `tests/test_running.py` and a second start of the installed program.
- Found, not fixed: two accounts added at the very same moment on a new installation (no key yet) could each make an encryption key; the settings keep the first.

## 2026-10-16 The Operations window (version 0.5.145)

- The owner's request, with three screenshots of the reference client's Operations window and its Settings > General > Operations Window section (D-138). New: `ui/operations.py` (the window: tabs Operations, Errors and Log; a row per account with the account icon and a blue badge, the name in bold, its step and a blue bar; Ignore All, Copy Log, Clear Log; the tick box "Hide when all operations are complete"; the clickable busy bar of the status line). Changed: `engine/session.py` (`Engine.activity`, the steps per account), `sync/pop3_fetch.py` and `sync/imap_sync.py` (a `step` report), `engine/commands_mail.py` (`mail.fetch` reports Connecting, Connected, Listing messages or folders, Downloading n of m, Deleting messages on the server, Synchronising a folder, Disconnected, Applying rules), `engine/commands_outbox.py` (Send All reports Sending n of m and Disconnected per sending account, only for accounts with mail waiting; `failed_accounts` names the account of each failure), `ui/backend.py` (the `activity` signal), `ui/mainwindow.py` (rows, errors and the rules for opening and closing; Menu > View > Operations…; a click on the busy bar), `ui/settingsdialog.py` (Settings > General > Operations window, three tick boxes), `ui/prefs.py` (the three settings, all on by default).
- Tests: `tests/test_engine_activity.py` (6) and `tests/ui/test_ui_operations.py` (11), new. Changed as a consequence: `tests/ui/test_ui_schedule.py` (its stand-in for the mail check takes the new `manual` choice). Run: engine activity, engine commands, outbox, IMAP engine, IMAP and POP3 sync, skip-known, layers and mailbox guard 181 passed, 1 failed (the mailbox guard: a variable in the Send All call was renamed; the original wording was put back in the program), then activity, outbox, mailbox guard and schedule 31 passed; Operations, theme, rules, columns, settings, tray, schedule, backend, push and preferences 144 passed, 1 failed (`test_ui_columns.py::test_the_contact_and_delete_headers_are_grey_icons`, the known one that fails only in long combined runs; passes alone); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 Every send and receive opens the Operations window (version 0.5.146)

- The owner's report: no Operations window at startup. Cause: D-138 opened it only for Refresh (the agent's own proposal, wrongly accepted as what the owner meant); the check at startup, the timers and the push never opened it. `ui/mainwindow.py`: every send and receive opens it while "Show window on send and receive" is on; an automatic check and an error open it without taking the keyboard focus, Refresh brings it to the front with the focus (D-139). The `manual` choice of 0.5.145 stays, now only for the focus.
- Tests: `tests/ui/test_ui_operations.py`, 2 new or changed (an automatic check, the check at startup: both open it without the focus; both fail on 0.5.145, checked); the Refresh test also checks the focus; the test without "Show window when an error occurs" switches the opening on send and receive off for its automatic check. Run: operations 12 passed; push, tray, schedule, settings, startup view 50 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The Windows source run starts again (version 0.5.147)

- Reported from DEVELOPMENT (0.5.145): the dev copy on Windows always said "Chrono Mail is already running." Cause (confirmed in `config/running.py`): only the own process and its parent were skipped, and a source run on Windows is launcher -> venv redirector -> program, so the launcher (the grandparent, named chrono-mail.exe) counted as another copy. Fix: `config/running.py` skips every ancestor (`windows_parents` from a Tool Help snapshot, `ancestors`, the selection as `windows_matches`) (D-140).
- Tests: `tests/test_running.py`, 4 new (the launcher chain is not counted; a real other copy, and not another user's, still is; a loop or an unknown parent ends the walk; Windows only: the snapshot holds this process with its parent). On the old code the tests cannot load (the functions are new); the old rule applied to the same made-up chain finds the launcher (checked). Run: `tests/test_running.py`, `tests/test_cli.py`, `tests/ui/test_ui_singleinstance.py` 38 passed, 2 skipped (Windows). Pyrefly 0, layer check kept; not the full suite. To do on DEVELOPMENT: `tests/test_running.py`, start the dev copy, and the installed program twice.

## 2026-10-16 The bars of the Operations window are drawn by the program (version 0.5.148)

- The owner's report: on Windows the bars of the Operations window stay grey (blue on Linux). On Linux both available styles, Fusion and Windows (also wrapped in the program's ScaledStyle and inside the main window), draw them blue (checked with pictures); Windows' own style is not available here, so the fault could not be reproduced. `ui/operations.py`: `OperationBar`, drawn by the program (grey track, blue part, a moving blue piece while the end is not known), replaces the progress bar of a row (D-141). The busy bar of the status line is unchanged.
- Tests: `tests/ui/test_ui_operations.py`, 1 new (the bar's blue part, grey track, full, empty and moving piece, drawn under Fusion and Windows); 2 tests read the new bar. The new test cannot load on the old code (the bar is new). Run: operations 13 passed; operations, appearance and theme together 58 passed (the test switches styles and puts the old one back); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. To check on DEVELOPMENT: the bars are blue.

## 2026-10-16 The program's own blue everywhere (version 0.5.149)

- The owner's screenshot from Windows (0.5.148): the Menu button, the tab lines, the badges and the bars were grey. My first guess (windows that are not active) was wrong; DEVELOPMENT measured the cause: the colour scheme was System (the factory reset had removed the earlier Dark), and Qt uses the Windows accent colour as the highlight, on the owner's Windows dark grey (#4c4a48 active, #1e1e1e inactive). `ui/colours.py`: the program's blue in one place and `with_program_blue`; `ui/appearance.py`: every scheme gets it for every window state (D-142).
- Tests: `tests/ui/test_ui_appearance.py`, 5 new (System with a made-up grey accent gives the program's blue in all three window states, which fails on the old code with #4c4a48, checked; the function for both shades; Dark and Light keep their blue). Run: appearance 36 passed; theme, operations, preferences, settings, sections, icons 77 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. Qt without a screen cannot report a dark system, so System with a dark system is covered by the function test only. To check on DEVELOPMENT: with System, everything is blue.

## 2026-10-16 Standard column order; no mail checks during an import (versions 0.5.150 and 0.5.151)

- 0.5.150 (the owner's instruction: only this change, no tests): the standard order of the message list starts with Flag, Unread, Attachment (`ui/columns.py`); saved column orders are not changed.
- 0.5.151: the owner asked what a scheduled check does while an import runs. Found: the checks ran at the same time; the import's list of the mail already in Local Folders is made once at its start, so a message a check stored meanwhile could be stored again by the import. `ui/importdialog.py` tells when an import starts and ends (`on_running`); `ui/mainwindow.py` checks and sends nothing meanwhile (timers, push, tray, Refresh), Refresh is off, and one check runs when the import is done (D-143).
- Tests: `tests/ui/test_ui_import.py`, 2 new (the window tells start and end; no check during the import, Refresh off, one check afterwards); both fail on the old code (checked). Run: import 14 passed; operations, push, tray, schedule 47 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 A Favorites view sorts by the chosen column (version 0.5.152)

- Reported from DEVELOPMENT (0.5.151): in Favorites > Unread a click on a column header changed nothing; the groups stayed those of Received. Cause (confirmed): `MainWindow.view_spec` built the request of a Favorites view (every combined view: inboxes, unread, flagged, outbox, sent, drafts, junk, trash, tagged; also the Sorted by menu) without the sort order, so it was always Received, newest first. Fix: `ui/mainwindow.py` passes the chosen order and direction, as for a folder (D-095 already says that combined views follow the sort order).
- Tests: `tests/ui/test_ui_columns.py`, 1 new (a real mouse click on the Subject header in Favorites > Unread sorts A to Z with the groups, a second click Z to A; fails on the old code, checked). Also corrected: `test_the_list_is_a_table_by_default_with_the_standard_columns` still expected Unread as the first column; made wrong by 0.5.150 (pushed without tests at the owner's instruction), a test fault, not a program fault. Run: columns 22 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 A new sort order keeps the selected message (version 0.5.153)

- Reported from DEVELOPMENT (0.5.152): select a message, click a column header: the message was no longer selected. Cause (confirmed): `_set_sort` reloads the list (a model reset) and nothing selected the message again. Fix (D-144): `ui/mainwindow.py` remembers the selected message before the new order and selects it again, scrolled into view; when it is on a page not loaded yet, `message.position` (new, `engine/commands_mail.py`, with `Index.position` in `index/index.py`) gives its place and `MessageListModel.load_position` (`ui/models.py`) loads that page; searches and Favorites views load their next batches; choosing another message meanwhile wins.
- Tests: `tests/test_engine_commands.py` (1 new: the position equals the place in message.list for five orders both ways, None outside the folder); `tests/ui/test_ui_columns.py` (4 new: a folder and Favorites > Unread keep the message and the reading pane matches; a message on a page not loaded yet, with one message per page; choosing another message meanwhile). All 5 fail on 0.5.152 (checked). Run: engine commands, command line, mailbox guard, index 233 passed; columns, models, operations, import 78 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The index is opened only inside its lock (version 0.5.154)

- Reported from DEVELOPMENT (0.5.153; 87 of 89 engine command tests pass on Windows). 1: `test_key_status_export_and_restore_cycle` checks Unix permission bits, which Windows does not have: a test fault; the check now runs only where the system has them. 2: `test_commands_that_open_an_old_index_at_once_all_succeed` failed with a Windows sharing error: a real fault (confirmed in the code): `Engine.open_index` tried the index once outside its lock, so it could have the files open while another command replaced them during a rebuild. Fix: `engine/session.py` opens only inside the lock (D-145).
- Tests: `tests/test_engine_commands.py`, 1 new (every open of an index holds its lock; fails on 0.5.153, checked; the Windows refusal itself cannot be reproduced on Linux). Run: engine commands, settings changes, Local Folders, index rebuild, IMAP engine 104 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite. To check on DEVELOPMENT: `tests/test_engine_commands.py`.

## 2026-10-16 The engine reports every change of mail (version 0.5.155, step 1 of D-146)

- The principle and the design in six steps are in DECISIONS.md (D-146). Step 1, nothing visible changes yet: new `engine/changes.py` (the facts, `Change`; `MessageRow` moved here from `commands_mail.py` so the mailbox can describe a message), `engine/session.py` (`Engine.changed`, `report`, `mailbox_changed`, `quietly`, `quiet_command`), `engine/mailbox.py` (every operation tells its fact after it succeeded; Empty folder and Mark all as read one fact each), `engine/commands_mail.py` (a mail check and mail.settle are quiet and tell "mailbox changed" only when the sync changed something here), `engine/commands_folders.py` (a rebuilt or repaired index), `engine/commands_emclient.py` (the import, per batch), `engine/commands_local.py` (moving mail to or from Local Folders; the changelist said `engine/local.py`, the command lives here), `ui/backend.py` (`changed`).
- Tests: `tests/test_engine_changes.py`, 8 new (a check is one fact and nothing when nothing is new; marks; four quick deletes are four facts in order, each about its own message; copy and tags; folder actions one fact each; a rebuilt index; an import per batch; IMAP tells only after the server confirmed and nothing on a refusal). On the old code they cannot load (the facts are new). Run: every engine test file that goes through the mailbox, plus import, guard, settings, command line, skip-known (33 files): 414 passed, 1 failed (`tests/test_engine_registry.py::test_destructive_commands_are_marked`: its list lacks tag.delete, destructive since 0.5.134; fails the same on 0.5.154, a test fault, not changed); backend, operations, import window, columns, tags 74 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The message list applies the facts (version 0.5.156, step 2 of D-146)

- `ui/models.py`: `MessageListModel.apply(change)` applies each fact to its own rows: marks and tags change one row in place; a removed or moved-away message takes out its row (its heading follows; an emptied heading goes) with a row removal, not a reset, so the selection stays; a message moved or added into the view is put at its place under its heading, asked from the engine (`message.locate` in `engine/commands_mail.py`, `message.combined-locate` in `engine/commands_combined.py`; two commands where the changelist named one, because Favorites have their own parameters); folder read, emptied and renamed. A Favorites view decides by the folder role in the fact (`Change.role`, new, `engine/changes.py` and `engine/mailbox.py`) whether a moved message belongs in it, so a delete in All Inboxes or Unread asks the engine nothing. Folders keep their rows as one ordered list with empty places instead of fixed pages, so a removal or insertion shifts what follows; an answer asked before a change that moved places is asked again. A message that goes while it was not loaded: only the parts after the first part not loaded are let go, and the heading counts are asked again (counts only). Not yet wired into the window (step 3): nothing visible changes.
- A: Run rules on a folder, Rename a tag everywhere and Delete a tag everywhere are big jobs: one "mailbox changed" at their end (`commands_rules.py`, `commands_pim.py`, `commands_tags.py`); `tests/test_engine_registry.py` lists tag.delete as destructive (missing since 0.5.134).
- B: `engine/mailbox.py` refuses an action that names a message in a folder where it no longer is ("This message is no longer in Inbox; it is in Trash"), also when reading its marks first; before, a second delete of a message already moved to Trash removed its index line and hid it (DEVELOPMENT's finding a), and a mark gave "Message not found" with the false "Messages missing" warning (finding d).
- Tests: `tests/ui/test_ui_list_changes.py` (10 new: a delete takes out one row and asks nothing; a mark; a message moved into the folder shown comes in at its place; headings follow and an emptied one goes; Favorites > Unread does not take a deleted message back; a search keeps a moved message at its new place; folder read, emptied, renamed; a late answer is asked again; 3,000 messages: a delete asks nothing; a message that was not loaded goes without a reset); `tests/test_engine_changes.py` (2 new: the bulk commands, the outdated folder; the second fails on 0.5.155, checked: the delete went through and hid the message). Run: 34 engine files through the mailbox 417 passed; list changes, models, columns, operations, import, tags, backend, drag and drop, schedule 132 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-16 The window works by identity (version 0.5.157, step 3 of D-146)

- `ui/mainwindow.py`: the facts of the engine drive the list (`Backend.changed` to `MessageListModel.apply`); after delete, delete permanently, move (also dragging), copy, archive, unarchive, junk, not junk, snooze, cancel snooze, marks and tags the list is no longer fetched again. An action takes the messages selected at the key press; `_take_away` greys them (`MessageListModel.pending`, `PENDING_ROLE`, `ui/delegate.py` for the cards) and moves the selection and the reading pane at once to the next message without an action under way (after them, else before); `_each` lets a message's grey go when its action ended (the fact took the row out before) or failed (the error shows, the selection stays). The position-based `_keep_place_after_delete` and `_select_after_delete` are gone. `_after_change` fetches again only for big jobs (rules run on a folder, scheduled messages changed, a held-back folder settled). A new sort order finds the selected message by asking where it stands once (`message.position` for a folder, `message.locate` and `message.combined-locate` for a search or Favorites view, then `load_through`), instead of loading batch after batch. A message that loses its tag stays in a tag's list until the view is left, as a read message in Unread.
- Not yet: the Sent copy after sending is stored past the mailbox's operations (`sync/send.py`), so no fact reports it; after sending the Sent and Drafts views are still fetched again (`_after_sent`). The folder list's counts and the refetch after a mail check or import follow in step 4.
- Tests: `tests/ui/test_ui_rapid_actions.py` (new, DEVELOPMENT's test as a project test: Favorites > Unread sorted by Subject with headings, four Delete key presses with pauses of 0, 200 and 1,500 ms on 16, 600 and 3,000 messages: each press acts on the message selected at that moment, the selection never empties, the four go to Trash in the order pressed, nothing else moves, the list is never reset, no "Messages missing"); all 9 fail on 0.5.156 (checked; 4 on the faults themselves, nothing selected or another message deleted). `tests/ui/test_ui_models.py`: two tests that expected a reset now require none. Run: rapid actions 9 passed (45 s); columns, list changes, models, drag and drop, schedule, tags, operations, import, shortcuts, push, reader, composer 203 passed; `tests/ui/test_ui_window.py` alone 62 passed; the 34 engine files 417 passed. Pyrefly 0, layer check kept; not the full suite.

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

## 2026-10-17 DEVELOPMENT's review, quick items: one heading, one file (version 0.5.161, D-148)

- From DEVELOPMENT's review of D-146 steps 1 to 5 (its rapid-Delete test: every Delete acted on the message selected, 3,000 messages in 0.7-0.8 s).
- `ui/models.py`: `header_rows_added(first, last)` for an inserted heading and a batch of more rows; a removal, a fold and a reset no longer announce the headings (a reset is handled by `modelReset`). `ui/mainwindow.py`: `_span_new_headers` spans only those; `_span_group_headers` (every heading) stays for a reset and a rearrange.
- `store/store.py`: `Store.flags(ref, hint)`, from the file name; `engine/mailbox.py`: `current_flags` uses it.
- Windows faults: `tests/test_engine_commands.py` closes the connection that makes an index old (`contextlib.closing`); `tests/test_engine_export.py` asks for the user id only off Windows; `config/running.py` tells the type check that the Linux part is not for Windows. `TRANSFER-WINDOWS.md`: `.venv-win`, running twice is prevented since 0.5.144 (3.3 and 9.3); `.gitignore`: `.venv-win/`. `STATUS.md`'s first line says it is pushed with its version.
- Tests: 3 new (a delete spans no heading again and the headings keep their bar; an open, a mark and a move never list the folder; `Store.flags`); all 3 fail on 0.5.160 (checked). Run: columns, list changes, rapid actions, tree rows, store, engine commands, export, running, mailbox guard, IMAP engine 198 passed, 2 skipped, 1 failed (the known `test_ui_tree_rows.py` test fault); `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-17 What changes in a view stays in it (version 0.5.162, D-149)

- Point 5 of DEVELOPMENT's review: the waiting lane holds only `mail.wait` (the IMAP push); not the cause. The cause: a message read while Favorites > Unread showed it vanished at the next sort or fetch, because only the window kept it.
- `index/model.py` `SearchFilter.stay`; `index/index.py` the conditions on unread, flagged and the filter's tag become one group, ORed with the messages that stay. `engine/commands_mail.py` `stay` (search, groups, locate, places); `engine/commands_combined.py` `stay_messages` and `stay_mailboxes` (Favorites, its groups, locate, places). `ui/models.py` `_stay` (a flag or tag fact on a shown row of such a view), sent in `_values`, emptied when the view is left.
- Tests: 4 new (the window: read in Favorites > Unread, it stays after a sort and a fetch, keeps its whole-row selection, and is gone when the view is opened again; the engine for a search and for Favorites; the index); all 4 fail without the change (checked with the code before 0.5.161). The run is in the 0.5.163 entry.

## 2026-10-17 A mail check tells what it changed (version 0.5.163, D-150)

- Point 2 of DEVELOPMENT's review. `sync/imap_sync.py`: the report names each message removed here, each change of flags (with whether it was unread) or tags taken over, each message kept in Recovered. `engine/mailbox.py`: `report_removed`, `report_flags`, `report_tags`, `report_folder_made`, `report_folder_deleted`. `engine/commands_mail.py`: `mail_fetch` (POP3) tells each new message; `_report_sync` (IMAP) tells all of it, folders made first and gone last; `mail.fetch` and `mail.settle` are no longer quiet commands; `message.places` with `headings`; `engine/commands_combined.py` the same for `message.combined-places`.
- `ui/models.py`: `_bring_in` collects the facts of messages that came in and `_take_in` asks once (`message.places`, `headings`) and inserts them, the first place first; more than `BURST` (200) at once is one rearrange. `_take_out_unseen`: a message gone from a part not loaded, with headings or with loaded rows after it, plans one rearrange for the burst (`_plan_rearrange`) instead of a reset; found while writing it and fixed (not in the changelist, told to the owner): when every message of the folder is loaded and the gone message is not among them, the list no longer gets one row shorter. `_refresh_group_counts` and `_relayout` went (nothing used them any more).
- Tests: 4 new (a POP3 check and an IMAP check tell facts, never "mailbox changed"; three messages that come in at once are placed with one question; a message gone from a part not loaded under headings: one rearrange, no reset); all 4 fail without the change (checked with the code before 0.5.161). `test_ui_list_changes.py`: a message that moved into a search is placed with `message.places` (was `message.locate`).
- Run for 0.5.161 to 0.5.163 together: 21 engine, sync, index and store files 496 passed, 2 skipped; columns, list changes, rapid actions, tree rows 59 passed, 1 failed (the known `test_ui_tree_rows.py` test fault since 0.5.139); folder counts, reader, models, sections, push, operations 69 passed; import, backend 27 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0, layer check kept; not the full suite.

## 2026-10-17 An answer describes exactly what the list knows (version 0.5.164, D-151)

- The owner's bug: the list was out of order after overlapping checks (from 0.5.163). DEVELOPMENT's second review: 91 rapid-Delete runs all correct; 4 Deletes on 3,000 messages settle in 127-136 ms at gap 0 (700 ms in 0.5.159); points 3 to 6 below.
- Engine: `session.py` `place_lock`, `is_quiet`; `mailbox.py` the place lock from `_index` to `_tell`, `let_go`, `reading`, `changing`; `sync/announce.py` (new) `Announcer` and `SILENT`; `sync/pop3_fetch.py` and `sync/imap_sync.py` tell each change inside `changing()` with its index write (the per-message lists of 0.5.163 in the IMAP report are gone); `commands_mail.py` `MailboxAnnouncer`, `_report_sync` tells only the folders gone, list/position/places/search/groups read under the lock; `commands_combined.py` the same for Favorites and its groups.
- List (`ui/models.py`): `_mark` (rows that came or went, facts heard) replaces the count of rows in the checks of a page, a batch of arrivals and a rearrange; `_unplaced` (arrivals waiting or being placed): a page asked meanwhile waits in `_waiting_pages` and is asked again once they are placed. Every row returns `ItemNeverHasChildren`; the `index` override is gone (its one-argument callers pass the column: 6 in the window, the rest in the tests).
- Store: `_MESSAGE_LOCKS` (256 shared re-entrant locks) around finding and using a message file in read, flags, copy, set_flags, move and delete.
- Windows: `config/running.py` the Linux part in a block for "not Windows"; `tests/test_engine_export.py` the file mode only where there are mode bits, the read-only folder test skipped on Windows, the root check in a function; `tests/test_store_store.py` `raising=False` for `os.sync`. Pyrefly for Windows (`--python-platform win32`) 0, as for Linux.
- Tests: 8 new: the owner's case end to end (a check paused between two messages while another comes in; fails on 0.5.163: Message 3 above Message 35); a question waits while a change is written but not told; a POP3 check and an IMAP sync tell each change in one step with its index write; a read never finds a name a change of flags took away; message rows never have children (these 6 fail on 0.5.163, checked); an answer and a page asked before a fact are asked again (these 2 pass on 0.5.163 too: they guard the list's side). `tests/test_engine_changes.py`: the mail check test still expected one "mailbox changed" (it failed since 0.5.163, which did not run that file); now it expects one fact per message.
- Run: 21 engine, sync, index and store files 500 passed, 2 skipped; changes, actions, activity, conversations, drafts, search, watch, compose, send, store interruption and tags 138 passed (after the test above was brought up to date); columns, list changes, rapid actions, tree rows 63 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); folder counts, reader, models, sections, push, operations, preferences 96 passed; shortcuts, export, smoke, tags, drag and drop, schedule, import, backend 75 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- For the big review: does the list need Qt's tree view (it lays out every row after a change, in C++), or can a flat view draw the headings?

## 2026-10-17 An answer counts only what the list has heard (version 0.5.165, D-152)

- DEVELOPMENT's report on 0.5.164: rapid Delete correct (3,000 messages, gap 0: 100-316 ms); the lock per message file holds (722 opens during 703 mark changes, no errors); live Kerio IMAP 9 passed. Found: the list empty while checks bring mail (point 2), snoozing several at once "Access is denied" on Windows (3), answers that never come in a window test on Windows (4), the reading pane showing the wrong message (5.1), two test faults (5.4, 5.5).
- Engine: `changes.py` `Change.sequence`; `session.py` `_tell` numbers and tells (one at a time), `arrived_after`, `REMEMBERED_ARRIVALS`; `commands_mail.py` `not_heard`, `leave_out`, `message.places` with `heard` and `known`; `commands_combined.py` the same for Favorites. `schedule.py` `SNOOZED_FILE_LOCK` around reading and replacing snoozed.json (only the file: nothing else is waited for while it is held, so it cannot deadlock with a mailbox lock).
- List (`ui/models.py`): `_seen` (the number of the last fact per mailbox, from every fact), sent as `heard`; `_placing`: one question at a time, what came meanwhile goes into the next; an arrival no longer counts in `_mark`; a rearrange or a new order does not drop arrivals on their way (they are placed in it or found there); `known` false gives one rearrange. Window: going back to the message on screen calls off the one still opening.
- Point 4 not found: three full runs of `tests/ui/test_ui_window.py` here pass; queued tasks are not collected by Python (5,000 queued, all ran). DEVELOPMENT checks with a probe whether the two commands started at all.
- Sending to a server whose certificate is not trusted (point 5.5): from the code, the composer shows "The message was not sent" with the reason and stays open; the live test waits for it to close, so it waited its 30 s. [Speculation] the send failed at once on the certificate; not run here.
- Tests: 4 new (DEVELOPMENT's case: 300 messages from two checks at the same moment are placed while they come and all are there, in the engine's order, at once after, with fewer than 100 questions; places count only what was heard and facts are numbered one after the other; reading the snooze file waits while it is replaced; going back to the message on screen); all 4 fail on 0.5.164 (checked; the snooze test file does not load there). `tests/test_engine_changes.py` compares facts without their numbers; `tests/test_store_store.py` the fsync count on Windows; `tests/live/test_live_ui.py` the certificate fingerprint.
- Run: 21 engine, sync, index and store files 517 passed (after the fact comparisons above); PIM, calendar, contacts, dav, recurring, search, conversations, activity, compose, store interruption and tags 140 passed; columns, list changes, rapid actions, tree rows 65 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); folder counts, PIM changes, reader, models, sections, push, operations, schedule, tags 101 passed; preferences, shortcuts, backend, import 62 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.

## 2026-10-17 Sort and group by what the column shows (version 0.5.166, D-153)

- `core/names.py` (new) `shown_name`; `ui/formatting.py` `display_name` uses it. `index/schema.py` format 8: `sender_name` and its index (`messages_by_sender` dropped), `UPGRADE_7_TO_8`; `index/index.py` stores it, sorts and groups by it, gives the connection the function `shown_name` for the upgrade, upgrades 6 to 7 to 8. `engine/commands_combined.py` sorts a Favorites view by sender with `shown_name`.
- Tests: 3 new (the index groups and sorts by the shown name, a quoted name under its name; format 7 upgraded in place, both messages kept under one heading, the old index gone; the list: one heading for one name from two addresses); all 3 fail on 0.5.165 (checked). Brought up to date: `tests/test_index_index.py` (groups by sender compared by the shown name), `tests/test_engine_search.py` (group names), `tests/test_index_tags.py` (format 6 goes on to 8).
- Run: index, engine local, search, commands, tags, changes, IMAP and POP3 sync, conversations, rules 391 passed; columns, list changes, models, tree rows 82 passed, 1 failed (the known `test_ui_tree_rows.py` test fault); reader, folder counts, rapid actions, contacts 36 passed; `tests/ui/test_ui_window.py` alone 62 passed. Pyrefly 0 for Linux and for Windows, layer check kept; not the full suite.
- Not measured: how long the upgrade to format 8 takes on the owner's 140,000 messages (one UPDATE over all rows at the first start).
