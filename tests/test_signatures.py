import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.config.signatures import LIST_FILE, MAXIMUM_HTML_BYTES, load_items
from chrono_mail.core import ConfigurationError
from chrono_mail.engine import Engine, UsageError
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_signatures.py 4.5.626"

# --- Version history ----------------------------------------------------
# v4.5.626: Initial tests: the library of templates and signatures, an account's choice of texts, the snippet.* commands (B-24, D-358)

PICTURE: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path)


def save(engine: Engine, *items: tuple[str, str, str]) -> list[str]:
    """Keep exactly these (id, name, html); the answer: the id of each."""
    found = call(engine, "snippet.save-all", ids=tuple(item[0] for item in items), names=tuple(item[1] for item in items), htmls=tuple(item[2] for item in items))
    return [entry.item_id for entry in found]


def kept(engine: Engine) -> list[tuple[str, str]]:
    return [(entry.name, entry.html) for entry in call(engine, "snippet.all")]


def test_save_all_keeps_the_texts_in_order(engine: Engine) -> None:
    save(engine, ("", "Offer", "<p>Dear</p>"), ("", "Work", "<p><b>Rob</b></p>"))
    assert kept(engine) == [("Offer", "<p>Dear</p>"), ("Work", "<p><b>Rob</b></p>")]


def test_save_all_keeps_ids_changes_texts_and_deletes_what_it_is_not_given(engine: Engine) -> None:
    work, home = save(engine, ("", "Work", "<p>w</p>"), ("", "Home", "<p>h</p>"))
    again = save(engine, ("", "Offer", "<p>o</p>"), (work, "Office", "<p>changed</p>"))
    assert again[1] == work  # the same text, renamed
    assert kept(engine) == [("Offer", "<p>o</p>"), ("Office", "<p>changed</p>")]
    assert not (engine.directories.signatures_directory / f"{home}.html").exists()


def test_a_name_is_used_once_in_any_capitals_and_nothing_changes_on_a_refusal(engine: Engine) -> None:
    save(engine, ("", "Work", "<p>w</p>"))
    with pytest.raises(UsageError, match="called WORK already exists"):
        save(engine, ("", "Work", "<p>1</p>"), ("", "WORK", "<p>2</p>"))
    assert kept(engine) == [("Work", "<p>w</p>")]


def test_an_empty_name_or_a_text_too_large_is_refused(engine: Engine) -> None:
    with pytest.raises(UsageError, match="needs a name"):
        save(engine, ("", "  ", ""))
    with pytest.raises(UsageError, match="at most 5 MB"):
        save(engine, ("", "Huge", "x" * (MAXIMUM_HTML_BYTES + 1)))
    assert kept(engine) == []


def test_a_missing_html_file_is_an_empty_text_never_a_failure(engine: Engine) -> None:
    (item,) = save(engine, ("", "Work", "<p>x</p>"))
    (engine.directories.signatures_directory / f"{item}.html").unlink()
    assert kept(engine) == [("Work", "")]


def test_a_damaged_list_is_copied_aside_and_said(engine: Engine) -> None:
    folder = engine.directories.signatures_directory
    folder.mkdir(parents=True)
    (folder / LIST_FILE).write_text("{ not json", "utf-8")
    with pytest.raises(UsageError, match=f"copied to {LIST_FILE}.bad"):
        call(engine, "snippet.all")
    assert (folder / f"{LIST_FILE}.bad").read_text("utf-8") == "{ not json"


def test_a_list_naming_a_file_outside_its_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / LIST_FILE).write_text('{"format": 1, "items": [{"id": "../../evil", "name": "X"}]}', "utf-8")
    with pytest.raises(ConfigurationError, match="not valid"):
        load_items(tmp_path)


def test_an_interrupted_save_leaves_the_library_as_it_was(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    import chrono_mail.engine.commands_signatures as module

    save(engine, ("", "Work", "<p>w</p>"))

    def full(directory: Path, items: object) -> None:
        raise ConfigurationError("The templates and signatures could not be saved: No space left on device")

    monkeypatch.setattr(module, "save_items", full)
    with pytest.raises(UsageError, match="No space left"):
        save(engine, ("", "Home", "<p>h</p>"))
    monkeypatch.undo()
    assert [name for name, _ in kept(engine)] == ["Work"]


def test_open_takes_the_pictures_beside_the_file_inside_and_keeps_nothing(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "export"
    (folder / "Work_files").mkdir(parents=True)
    (folder / "Work_files" / "logo.png").write_bytes(PICTURE)
    (folder / "Work.htm").write_text('<p>Rob</p><img src="Work_files/logo.png"><img src="https://example.com/x.png"><img src="gone.png">', "utf-8")
    opened = call(engine, "snippet.open", file=str(folder / "Work.htm"))
    assert (opened.item_id, opened.name) == ("", "Work")
    assert f'src="data:image/png;base64,{base64.b64encode(PICTURE).decode("ascii")}"' in opened.html
    assert 'src="https://example.com/x.png"' in opened.html and 'src="gone.png"' in opened.html
    assert kept(engine) == []


def test_open_of_a_file_that_cannot_be_read_says_so(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="cannot be read"):
        call(engine, "snippet.open", file=str(tmp_path / "missing.html"))


def test_save_as_writes_a_page_open_reads_back(engine: Engine, tmp_path: Path) -> None:
    target = tmp_path / "Work copy.html"
    call(engine, "snippet.save-as", name="Work", html="<p><b>Rob</b></p>", file=str(target))
    assert "<title>Work</title>" in target.read_text("utf-8")
    again = call(engine, "snippet.open", file=str(target))
    assert again.name == "Work copy" and "<p><b>Rob</b></p>" in again.html


def test_export_and_import_carry_every_text(engine: Engine, tmp_path: Path) -> None:
    target = tmp_path / "all.json"
    call(engine, "snippet.export", file=str(target), names=("Offer", "Work"), htmls=("<p>o</p>", "<p>w</p>"))
    found = call(engine, "snippet.import", file=str(target))
    assert [(entry.item_id, entry.name, entry.html) for entry in found] == [("", "Offer", "<p>o</p>"), ("", "Work", "<p>w</p>")]
    assert kept(engine) == []  # kept only at the OK of Settings


def test_import_of_another_file_is_refused_plainly(engine: Engine, tmp_path: Path) -> None:
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": 1, "items": []}), "utf-8")
    with pytest.raises(UsageError, match="not a file of templates and signatures"):
        call(engine, "snippet.import", file=str(other))
    (tmp_path / "broken.json").write_text("{ broken", "utf-8")
    with pytest.raises(UsageError, match="not a file of templates and signatures"):
        call(engine, "snippet.import", file=str(tmp_path / "broken.json"))


def account_of(tmp_path: Path) -> tuple[Engine, str]:
    from tests.test_engine_restore import _computer

    engine = _computer(tmp_path / "computer")
    return engine, next(settings.account.account_id for settings in engine.config().accounts if settings.account.email_address == "anna@example.com")


def test_an_account_starts_with_no_texts_and_keeps_what_it_is_given(tmp_path: Path) -> None:
    engine, anna = account_of(tmp_path)
    assert call(engine, "snippet.account-texts", account=anna).signature_new == ""
    offer, work = save(engine, ("", "Offer", "<p>o</p>"), ("", "Work", "<p>w</p>"))
    chosen = call(engine, "snippet.set-account-texts", account=anna, template_new=offer, signature_reply=work, signature_below=True)
    assert (chosen.template_new, chosen.signature_new, chosen.signature_reply, chosen.signature_below) == (offer, "", work, True)
    listed = next(item for item in call(engine, "account.list") if item.account_id == anna)
    assert listed.texts.template_new == offer and listed.texts.signature_reply == work
    with pytest.raises(UsageError, match="does not exist"):
        call(engine, "snippet.set-account-texts", account=anna, signature_new="0123456789ab")


def test_deleting_a_text_takes_it_off_every_account_that_used_it(tmp_path: Path) -> None:
    engine, anna = account_of(tmp_path)
    offer, work = save(engine, ("", "Offer", "<p>o</p>"), ("", "Work", "<p>w</p>"))
    call(engine, "snippet.set-account-texts", account=anna, template_new=offer, signature_new=work)
    save(engine, (offer, "Offer", "<p>o</p>"))
    chosen = call(engine, "snippet.account-texts", account=anna)
    assert (chosen.template_new, chosen.signature_new) == (offer, "")
    assert engine.config().find(anna).texts.signature_new == ""


def test_the_old_signature_becomes_a_text_and_the_account_s_signature_once(tmp_path: Path) -> None:
    engine, anna = account_of(tmp_path)
    engine.change_config(lambda config: config.with_account(replace(config.find(anna), signature="Anna <b>\nPhone 123")))
    taken = call(engine, "snippet.take-old-signatures", below_quote=True)
    assert taken.names == ("anna@example.com",)
    ((item_id, name, html),) = [(entry.item_id, entry.name, entry.html) for entry in call(engine, "snippet.all")]
    assert (name, html) == ("anna@example.com", "<p>Anna &lt;b&gt;<br>Phone 123</p>")
    texts = engine.config().find(anna).texts
    assert (texts.signature_new, texts.signature_reply, texts.signature_forward, texts.signature_below, texts.template_new) == (item_id, item_id, item_id, True, "")
    assert engine.config().find(anna).signature == ""
    assert call(engine, "snippet.take-old-signatures", below_quote=False).names == ()  # done once
