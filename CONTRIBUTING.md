# Contributing to Chrono Mail

Chrono Mail is an open-source desktop email client for Linux (Windows and macOS as a bonus), with mail, calendars, contacts and tasks. This file is for developers. The description for people who use the program is in [`README.md`](README.md).

## Technology

Python 3.14, Qt 6 through PySide6, strict static type checking with Pyrefly. Storage is an encrypted Maildir with a rebuildable SQLite index.

## Run it from the source

```bash
git clone https://github.com/rob-vandenberg/chrono-mail && cd chrono-mail
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/chrono-mail          # the graphical program
.venv/bin/chrono-mail-cli --help   # the command line (the same actions, without a screen)
```

## The demo profile

`./demo.sh` starts Chrono Mail with a made-up person's mail, apart from your own (`~/chrono-mail-demo`, built by `tools/demo/` the first time, about a quarter of an hour for its 100,000 messages; `./demo.sh --fresh` builds it again). What it shows is listed in [`tools/demo/COVERAGE.md`](tools/demo/COVERAGE.md). It is meant for screenshots and for trying things out; it never contacts a server. Close your own Chrono Mail first: one copy runs at a time.

## Where to start reading

Start with [`AGENTS.md`](AGENTS.md), then [`PY-DEVELOPMENT-RULES.md`](PY-DEVELOPMENT-RULES.md) and [`STATUS.md`](STATUS.md). The design is in [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`DECISIONS.md`](DECISIONS.md); how the installers are made is in [`packaging/PACKAGING.md`](packaging/PACKAGING.md).

## License

GNU Affero General Public License v3.0. See [`LICENSE`](LICENSE).
