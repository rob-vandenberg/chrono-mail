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
