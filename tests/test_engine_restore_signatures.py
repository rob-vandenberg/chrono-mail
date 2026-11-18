from pathlib import Path

import pytest

from chrono_mail.engine import Engine
from chrono_mail.engine.backup import cancel_backup
from chrono_mail.engine.restore import RestoreCancelled
from tests.enginefactory import make_engine
from tests.test_engine_commands import call
from tests.test_engine_restore import KEY_ENSURE, REGISTRY, _backup, _computer, _restore, _tree

# --- Version ------------------------------------------------------------
__version__ = "test_engine_restore_signatures.py 4.5.626"

# --- Version history ----------------------------------------------------
# v4.5.626: Initial tests: Backup and Restore carry the templates and signatures and each account's choice of them (B-24, D-358)


def keep(engine: Engine, *items: tuple[str, str]) -> list[str]:
    """Keep exactly these (name, html); the answer: their ids."""
    found = call(engine, "snippet.save-all", ids=tuple("" for _ in items), names=tuple(name for name, _ in items), htmls=tuple(text for _, text in items))
    return [entry.item_id for entry in found]


def kept(engine: Engine) -> list[tuple[str, str]]:
    return [(entry.name, entry.html) for entry in call(engine, "snippet.all")]


def test_the_texts_and_the_account_choice_come_back_on_an_empty_computer(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    _, work = keep(first, ("Offer", "<p>Dear</p>"), ("Work", '<p><b>Rob</b></p><img src="data:image/png;base64,iVBORw0KGgo=">'))
    anna = next(settings.account.account_id for settings in first.config().accounts if settings.account.email_address == "anna@example.com")
    call(first, "snippet.set-account-texts", account=anna, signature_new=work)
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    assert KEY_ENSURE.answer(REGISTRY.run(second, KEY_ENSURE.id, {})).made
    _restore(second, tmp_path / "all.cmz", password)
    assert kept(second) == kept(first)
    assert call(second, "snippet.account-texts", account=anna).signature_new == work


def test_one_here_stays_unless_replace_and_one_deleted_comes_back(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    keep(engine, ("Work", "<p>old</p>"), ("Home", "<p>home</p>"))
    password = _backup(engine, tmp_path / "all.cmz")
    keep(engine, ("Work", "<p>changed here</p>"), ("Only here", "<p>mine</p>"))
    _restore(engine, tmp_path / "all.cmz", password)
    assert kept(engine) == [("Work", "<p>changed here</p>"), ("Only here", "<p>mine</p>"), ("Home", "<p>home</p>")]
    _restore(engine, tmp_path / "all.cmz", password, replace_existing=True)
    assert kept(engine) == [("Work", "<p>old</p>"), ("Only here", "<p>mine</p>"), ("Home", "<p>home</p>")]


def test_remove_all_data_first_leaves_only_the_backup_s_texts(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    keep(engine, ("Work", "<p>work</p>"))
    password = _backup(engine, tmp_path / "all.cmz")
    keep(engine, ("Work", "<p>work</p>"), ("Only here", "<p>mine</p>"))
    _restore(engine, tmp_path / "all.cmz", password, remove_first=True)
    assert kept(engine) == [("Work", "<p>work</p>")]
    assert not list(engine.directories.data_directory.glob("restore-aside-*"))


def test_a_cancelled_restore_after_remove_all_data_first_puts_them_back(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first", messages=150)
    keep(engine, ("Work", "<p>work</p>"))
    password = _backup(engine, tmp_path / "all.cmz")
    keep(engine, ("Work", "<p>work</p>"), ("Only here", "<p>mine</p>"))
    before = _tree(engine.directories.data_directory), _tree(engine.directories.config_directory)

    def cancel(what: str, done: int, total: int | None) -> None:
        cancel_backup()

    engine.progress = cancel
    with pytest.raises(RestoreCancelled):
        _restore(engine, tmp_path / "all.cmz", password, remove_first=True)
    assert (_tree(engine.directories.data_directory), _tree(engine.directories.config_directory)) == before
    assert [name for name, _ in kept(engine)] == ["Work", "Only here"]


def test_a_backup_without_any_leaves_those_here(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    keep(engine, ("Work", "<p>work</p>"))
    _restore(engine, tmp_path / "all.cmz", password, replace_existing=True)
    assert kept(engine) == [("Work", "<p>work</p>")]


def test_a_restored_account_s_choice_follows_its_text_to_its_id_here(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    (work,) = keep(first, ("Work", "<p>work</p>"))
    anna = next(settings.account.account_id for settings in first.config().accounts if settings.account.email_address == "anna@example.com")
    call(first, "snippet.set-account-texts", account=anna, signature_new=work)
    password = _backup(first, tmp_path / "all.cmz")
    keep(first, ("Work", "<p>work</p>"))  # the same name here under another id
    _restore(first, tmp_path / "all.cmz", password, replace_existing=True)
    here = call(first, "snippet.all")[0].item_id
    assert here != work and call(first, "snippet.account-texts", account=anna).signature_new == here
