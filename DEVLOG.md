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
