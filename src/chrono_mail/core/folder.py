import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "folder.py 4.2.387"

# --- Version history ----------------------------------------------------
# v4.2.387: One table of the standard folder names in ten languages (STANDARD_FOLDER_NAMES) and the rule that reads them (roles_by_name):
#           the language with the most matches over all names of one source wins, only its names are matched, one folder per role
# v3.6.354: The roles of Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered); OWN_FOLDERS, the one place their names are
#           written; own_role; a standard folder is one with a role (CR-36)
# v0.0.4: Initial folder model


class FolderRole(Enum):
    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    TRASH = "trash"
    JUNK = "junk"
    ARCHIVE = "archive"
    OUTBOX = "outbox"  # the roles below are Chrono Mail's own: folders it makes itself, at the top of an account (OWN_FOLDERS)
    SCHEDULED = "scheduled"
    SNOOZED = "snoozed"
    RECOVERED = "recovered"
    OTHER = "other"  # a folder the person made: the only kind that is not a standard folder


OWN_FOLDERS: dict[FolderRole, tuple[str, ...]] = {
    FolderRole.OUTBOX: ("Outbox",), FolderRole.SCHEDULED: ("Scheduled",), FolderRole.SNOOZED: ("Snoozed",), FolderRole.RECOVERED: ("Recovered",),
}  # where Chrono Mail makes its own folders: the one place their names are written; a folder there takes its role when it is made


def own_role(path: tuple[str, ...]) -> FolderRole | None:
    """The role of the folder Chrono Mail keeps at this path, or None when none of its own folders is there."""
    return next((role for role, own in OWN_FOLDERS.items() if own == path), None)


# The names common mail programs give their standard folders, per language and role: the desktop programs of the big office suites, the
# common open-source programs, the default program of macOS and the IMAP names of the big webmail services. Compared after trimming,
# without case (casefold). A name with "?" after it in its comment is a best guess.
STANDARD_FOLDER_NAMES: dict[str, dict[FolderRole, tuple[str, ...]]] = {
    "English": {
        FolderRole.INBOX: ("Inbox",),
        FolderRole.SENT: ("Sent", "Sent Items", "Sent Messages", "Sent Mail"),
        FolderRole.DRAFTS: ("Drafts", "Draft"),
        FolderRole.TRASH: ("Trash", "Deleted Items", "Deleted Messages", "Deleted", "Bin"),
        FolderRole.JUNK: ("Junk", "Junk E-mail", "Junk Email", "Junk Mail", "Spam", "Bulk Mail"),  # Bulk Mail: a webmail name?
        FolderRole.OUTBOX: ("Outbox",),
        FolderRole.ARCHIVE: ("Archive", "Archives"),
    },
    "Spanish": {
        FolderRole.INBOX: ("Bandeja de entrada", "Recibidos"),
        FolderRole.SENT: ("Elementos enviados", "Enviados", "Mensajes enviados", "Correo enviado"),
        FolderRole.DRAFTS: ("Borradores", "Borrador"),
        FolderRole.TRASH: ("Elementos eliminados", "Papelera", "Eliminados", "Mensajes eliminados"),
        FolderRole.JUNK: ("Correo no deseado", "Correo electrónico no deseado", "No deseado", "Spam"),
        FolderRole.OUTBOX: ("Bandeja de salida",),
        FolderRole.ARCHIVE: ("Archivo", "Archivados"),  # Archivados?
    },
    "French": {
        FolderRole.INBOX: ("Boîte de réception", "Courrier entrant"),  # Courrier entrant?
        FolderRole.SENT: ("Éléments envoyés", "Envoyés", "Messages envoyés", "Courrier envoyé"),
        FolderRole.DRAFTS: ("Brouillons", "Brouillon"),
        FolderRole.TRASH: ("Éléments supprimés", "Corbeille", "Messages supprimés"),
        FolderRole.JUNK: ("Courrier indésirable", "Courriers indésirables", "Indésirables", "Pourriel", "Spam"),
        FolderRole.OUTBOX: ("Boîte d'envoi", "Boîte d\u2019envoi"),
        FolderRole.ARCHIVE: ("Archives",),
    },
    "German": {
        FolderRole.INBOX: ("Posteingang",),
        FolderRole.SENT: ("Gesendete Elemente", "Gesendet", "Gesendete Objekte", "Gesendete Nachrichten"),
        FolderRole.DRAFTS: ("Entwürfe", "Entwurf"),
        FolderRole.TRASH: ("Gelöschte Elemente", "Papierkorb", "Gelöschte Objekte", "Gelöschte Nachrichten"),
        FolderRole.JUNK: ("Junk-E-Mail", "Spam", "Unerwünscht"),  # Unerwünscht?
        FolderRole.OUTBOX: ("Postausgang",),
        FolderRole.ARCHIVE: ("Archiv",),
    },
    "Italian": {
        FolderRole.INBOX: ("Posta in arrivo", "In arrivo"),
        FolderRole.SENT: ("Posta inviata", "Elementi inviati", "Inviati", "Inviata"),
        FolderRole.DRAFTS: ("Bozze", "Bozza"),
        FolderRole.TRASH: ("Posta eliminata", "Elementi eliminati", "Cestino", "Eliminati"),
        FolderRole.JUNK: ("Posta indesiderata", "Indesiderata", "Spam"),
        FolderRole.OUTBOX: ("Posta in uscita", "In uscita"),
        FolderRole.ARCHIVE: ("Archivio",),
    },
    "Dutch": {
        FolderRole.INBOX: ("Postvak IN",),
        FolderRole.SENT: ("Verzonden items", "Verzonden", "Verzonden berichten"),
        FolderRole.DRAFTS: ("Concepten", "Concept"),
        FolderRole.TRASH: ("Verwijderde items", "Prullenbak", "Verwijderd", "Verwijderde berichten", "Prullenmand"),  # Prullenmand?
        FolderRole.JUNK: ("Ongewenste e-mail", "Ongewenste mail", "Spam"),  # Ongewenste mail?
        FolderRole.OUTBOX: ("Postvak UIT",),
        FolderRole.ARCHIVE: ("Archief",),
    },
    "Portuguese": {
        FolderRole.INBOX: ("Caixa de entrada", "A receber"),  # A receber?
        FolderRole.SENT: ("Itens enviados", "Enviados", "Mensagens enviadas", "Enviadas"),
        FolderRole.DRAFTS: ("Rascunhos", "Rascunho"),
        FolderRole.TRASH: ("Itens excluídos", "Itens eliminados", "Lixeira", "Lixo", "Reciclagem"),  # Reciclagem?
        FolderRole.JUNK: ("Lixo eletrônico", "Lixo eletrónico", "Spam"),
        FolderRole.OUTBOX: ("Caixa de saída", "A enviar"),  # A enviar?
        FolderRole.ARCHIVE: ("Arquivo", "Arquivados"),  # Arquivados?
    },
    "Russian": {
        FolderRole.INBOX: ("Входящие",),
        FolderRole.SENT: ("Отправленные", "Отправленные элементы", "Отправленные письма"),  # Отправленные письма?
        FolderRole.DRAFTS: ("Черновики",),
        FolderRole.TRASH: ("Удаленные", "Удалённые", "Удаленные элементы", "Корзина"),
        FolderRole.JUNK: ("Нежелательная почта", "Спам", "Spam"),
        FolderRole.OUTBOX: ("Исходящие",),
        FolderRole.ARCHIVE: ("Архив",),
    },
    "Japanese": {
        FolderRole.INBOX: ("受信トレイ", "受信箱"),
        FolderRole.SENT: ("送信済みアイテム", "送信済み", "送信済みメール", "送信箱"),  # 送信箱?
        FolderRole.DRAFTS: ("下書き",),
        FolderRole.TRASH: ("削除済みアイテム", "ゴミ箱", "ごみ箱", "削除済みメール"),  # 削除済みメール?
        FolderRole.JUNK: ("迷惑メール", "Spam"),
        FolderRole.OUTBOX: ("送信トレイ",),
        FolderRole.ARCHIVE: ("アーカイブ",),
    },
    "Chinese": {  # simplified
        FolderRole.INBOX: ("收件箱",),
        FolderRole.SENT: ("已发送邮件", "已发送", "已发送项目"),  # 已发送项目?
        FolderRole.DRAFTS: ("草稿", "草稿箱"),
        FolderRole.TRASH: ("已删除邮件", "已删除", "废纸篓", "已删除项目"),  # 已删除项目?
        FolderRole.JUNK: ("垃圾邮件", "Spam"),
        FolderRole.OUTBOX: ("发件箱",),
        FolderRole.ARCHIVE: ("存档", "归档"),
    },
}
COMMON_FOLDER_NAMES: dict[str, FolderRole] = {"inbox": FolderRole.INBOX, "spam": FolderRole.JUNK, "junk": FolderRole.JUNK, "archive": FolderRole.ARCHIVE}
# names used in every language: they match whichever language wins and never decide which one does


def folder_name_key(name: str) -> str:
    """A folder name as the table compares it: trimmed, one Unicode form, without case."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def _by_language() -> dict[str, dict[str, FolderRole]]:
    languages: dict[str, dict[str, FolderRole]] = {}
    for language, roles in STANDARD_FOLDER_NAMES.items():
        known = {folder_name_key(name): role for name, role in COMMON_FOLDER_NAMES.items()}
        for role, names in roles.items():
            known.update((folder_name_key(name), role) for name in names)
        languages[language] = known
    return languages


_NAMES_BY_LANGUAGE: dict[str, dict[str, FolderRole]] = _by_language()
_SHARED_NAMES: frozenset[str] = frozenset(
    name for name in {name for known in _NAMES_BY_LANGUAGE.values() for name in known}
    if sum(name in known for known in _NAMES_BY_LANGUAGE.values()) > 1
)  # a name in more than one language counts for each, so it never decides


def folder_language(names: Sequence[str]) -> str:
    """The language of one source's folder names: the one with the most matches over all of them (English on a tie)."""
    keys = [folder_name_key(name) for name in names]
    best, best_count = "English", 0
    for language, known in _NAMES_BY_LANGUAGE.items():
        count = sum(1 for key in keys if key in known and key not in _SHARED_NAMES)
        if count > best_count:
            best, best_count = language, count
    return best


def standard_role_in(language: str, name: str) -> FolderRole | None:
    """The role this name gives a folder in that language, or None for an ordinary folder."""
    return _NAMES_BY_LANGUAGE.get(language, {}).get(folder_name_key(name))


def roles_by_name(names: Sequence[str]) -> dict[int, FolderRole]:
    """The standard roles of one source's folders by their names (index into names -> role): the names of the source's language only,
    at most one folder per role, the first in order. A caller gives it only folders without a role from the source's own marks, and uses
    only roles not already taken."""
    language = folder_language(names)
    found: dict[int, FolderRole] = {}
    for index, name in enumerate(names):
        role = standard_role_in(language, name)
        if role is not None and role not in found.values():
            found[index] = role
    return found


@dataclass(frozen=True, slots=True)
class Folder:
    """A mail folder. The path is a tuple of names, so no server delimiter leaks into the model."""

    account_id: str
    path: tuple[str, ...]
    role: FolderRole

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Folder path must not be empty")
        if any(not segment.strip() for segment in self.path):
            raise ValueError("Folder path segments must not be empty")
