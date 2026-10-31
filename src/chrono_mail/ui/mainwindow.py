from collections.abc import Callable
from dataclasses import replace as changed
from typing import override

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QLabel, QLayout, QMainWindow, QMenu, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.core.colours import NEW_TAG
from chrono_mail.engine.commands_updates import interrupt_downloads
from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, KEY_STATUS, AccountSummary, KeyStatus, NoParameters
from chrono_mail.engine.commands_dav import DAV_FIND, DAV_LINK, FindParameters, FoundServer, LinkParameters
from chrono_mail.engine.commands_schedule import ScheduledInfo
from chrono_mail.ui import appearance, scale, theme
from chrono_mail.ui.accountdialog import CALDAV, CARDDAV, AccountDialog
from chrono_mail.ui.backend import Backend, alive
from chrono_mail.ui.certificatequestions import questions_for
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui.composing import Composing
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.savedsearches import SAVED_SEARCHES_FILE, SavedSearches
from chrono_mail.ui.folderpane import FolderMenu, FolderPane
from chrono_mail.ui.foundfolders import FoundFoldersDialog
from chrono_mail.ui.icons import application_icon, theme_icon
from chrono_mail.ui.backupdialog import MENU_TEXT as BACKUP_MENU_TEXT, BackupWindow, may_quit
from chrono_mail.ui.importdialog import MENU_TEXT as IMPORT_MENU_TEXT
from chrono_mail.ui.importdialog import ImportDialog
from chrono_mail.ui.exportdialog import ExportDialog
from chrono_mail.ui.restoredialog import MENU_TEXT as RESTORE_MENU_TEXT, RestoreWindow
from chrono_mail.ui.indexrebuilds import IndexRebuilds
from chrono_mail.ui.mailchecking import MailChecking
from chrono_mail.ui.updates import Updates
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.heldkey import HeldKey
from chrono_mail.ui.mailtags import MailTags
from chrono_mail.ui.messageactions import MessageActions
from chrono_mail.ui.messagecolumns import MessageColumns
from chrono_mail.ui.messagelist import CONTACT_ROLE
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.prefs import InUse, Preferences, Remembered
from chrono_mail.ui.readingpane import ReadingPane
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.restorekeydialog import RestoreKeyDialog, worth_telling
from chrono_mail.ui.scale import px
from chrono_mail.ui.searchfilters import ListFilters, MailSearch
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, SECTIONS, TASKS, SectionBar
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.syncconflicts import SyncConflictsWindow
from chrono_mail.ui.syncmarks import CONFLICT, SyncMarks, mark_icon
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import tag_icon
from chrono_mail.ui.tasksview import TasksPage
from chrono_mail.ui.toolbar import ToolbarRow, WindowToolbar
from chrono_mail.ui.sendnotices import SendNotices
from chrono_mail.ui.tray import TrayController
from chrono_mail.ui.undoing import Undoing
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: the certificate questions of a Refresh are asked over this window while it is on screen (UR-05, D-299)
# v4.2.439: the sidebars' Add account… opens the one Add window on CalDAV or CardDAV; a calendar server added there is synchronised (UR-04, D-298)
# v4.2.436: the Message list window and Settings > Message list show every change in the main window at once (Layout, Reading pane,
#           Dates, Row height through the Settings window's try-out; the columns, Apply to all, Default and Reset through the column
#           part's); OK keeps them, Cancel puts them back (D-104, D-228)
# v4.2.429: quit_for_update: after an update Chrono Mail closes without questions (unsaved messages go to Drafts)
# v4.2.427: quitting stops a download of an update at once; an update is offered only while the window is on screen
# v4.2.419: About is a window of its own with Check for updates; a Refresh that goes online also looks for updates (Updates, D-293)
# v4.2.416: Mail's toolbar folds Delete to Mark first, then the search field's folder choice, then Forward to Reply (D-290)
# v4.2.414: in a search of several folders a message opens, and its right-click menu and Junk or Not junk follow, as in its own folder (D-287)
# v4.2.410: Advanced search's saved searches are kept beside the window's settings, on this computer only (D-289)
# v4.2.407: Advanced search… also in the search field's ▾ menu, below its three choices (D-287)
# v4.2.405: Wayland: a tray click hides the window only while it or one of its dialogs has the focus, else brings it back; a second
#           start brings it back and to the front (shown again, never while a dialog waits for an answer or while it has the focus):
#           Wayland never says a window is minimized (D-286)
# v4.2.402: the start at login with the window minimized (start_minimized, D-284); the window opened from the tray or by a second
#           start keeps being maximized (it lost it after a minimize); a recovery code or the first account asked at a minimized start
#           opens the window first, so the question is seen; on Wayland, which never says whether a window is minimized, the window
#           remembers it was started out of sight until it is opened or activated
# v4.2.401: Settings > Message list is shown at once while it is changed, Cancel puts it back (D-104)
# v4.2.400: the new-mail notification writes its count with the one count helper (D-246)
# v4.2.399: Rename folder (F2, Menu > Folder) is greyed on a standard folder and on a top row, as Delete folder
# v4.2.394: the window tells whether an action was set off by a held key's repeat (HeldKey): New, Reply, Reply all, Forward, Open, View
#           source and Print act once per press; Undo, Redo, the marks and the tags throw a repeat away while an action is under way (D-266)
# v4.2.392: the status bar hears when a command's progress line ends (progress_ended)
# v4.2.389: the Import and Export windows get the window's Remembered: they keep the paths used last
# v4.2.388: after an import the mail check runs only when send and receive runs by itself (at startup or at an interval)
# v4.2.386: Menu > File > Export… (the Export window), below Import
# v4.0.374: the Settings window is deleted once it is closed and read (every opening kept one in memory for good); the refresh after an
#           account added from it reloads it only while it is open
# v3.6.371: the wallet's entries get readable labels at start (B-23, D-275)
# v3.6.369: Not junk shows the junk key in the menus (in Junk that key brings a message back), unless it has a key of its own; in Junk the
#           Message menu shows Not junk only, as the right-click menu does; Not junk works in Favorites > Junk too, as the junk key did,
#           and is greyed with nothing selected or in a view without an account
# v3.6.354: Delete folder… greyed for a standard folder, known by its role (CR-36)
# v3.6.353: the message list knows the roles of the folders (a message that comes back is placed by it, D-266)
# v3.6.352: the Archive button changes only when it switches between Archive and Unarchive (a new icon at every selection laid out the toolbar again); the sync marks repaint the whole list through repaint_all (D-266)
# v3.6.350: File > Use a recovery code says why the key is missing; Start without this mail is reported in the status line;
#           a click on the Delete icon is MessageActions.delete_by_icon (in turn with what waits); Redo can be pressed while busy
# v3.5.346: After Add account the recovery code is shown whenever it was never confirmed as stored (RV-03)
# v2.4.334: the Archive or Unarchive label stops at the first selected message that is not archived
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v2.4.332: Exit asks about a running backup or restore last, so its promise holds (W3); Restore a backup… from the first-account window (W2)
# v2.4.329: the Archive or Unarchive label follows rows_changed: once per moment, not once per changed row (W13)
# v2.4.325: after a restore that brought the window's settings: taken in at once as Settings OK does, every folder's columns read again,
#           the size and the panes as at start; then the startup folder opens (D-244)
# v2.4.324: File > Restore… (the mail checks wait meanwhile; afterwards every index usable, the accounts loaded again and one check);
#           Quit while a backup is made or restored asks first; the key's status line names "Use a recovery code" (D-244)
# v2.4.322: File > Back up… (the Back up window; a click on the busy bar brings it back while it runs) and Restore… (greyed until the
#           restore is built); the key items are "Show the recovery code…" and "Use a recovery code…" (D-244)
# v2.3.316: the refresh at start does its own choice (start_refresh, the owner)
# v2.3.315: the menu item is "Refresh automatically" (it may only send, the owner)
# v2.2.314: the notices of delayed sends (SendNotices, B-21); quitting with messages in the Outbox asks first, a countdown under way ends first (B-20)
# v2.2.313: Undo (Ctrl+Z) and Redo (Ctrl+Y, Ctrl+Shift+Z) of the actions on mail, at the top of Menu > Message, saying what they would do (B-18)
# v2.1.311: the status part knows the accounts' names and hears which commands run (the busy bar's hover text names them)
# v2.1.308: at start, once the indexes are usable, the junk filter learns from the mail already kept (D-238)
# v2.0.306: Archive or Unarchive follows the selected messages, not the folder on the left (D-101)
# v2.0.305: Menu > Message: Next and Previous message, Next and Previous unread message and Go to folder… in a Go to submenu (the owner)
# v2.0.303: a click on the busy bar brings the index window back while an index is updated, else the Operations window (D-229)
# v2.0.302: the copies of attachments opened in an earlier run are removed at start (D-236)
# v2.0.281: at start every index is checked and the ones that cannot be used are rebuilt before the mail is shown; the rebuild window,
#           the paused mail checks and Settings > Rebuild the search index go through IndexRebuilds (D-229)
# v2.0.280: Columns per folder (B-16): Apply to all folders in Menu > Folder; Settings > Message list and Message list… change the open
#           folder's columns; a renamed or deleted folder takes its column setup along; the sort is no longer saved at quitting (each folder keeps its own)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.274: R-19 round 4: the accounts are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.271: R-19: the key check and the account list are typed calls
# v1.9.265: The minimum width is worked out at the start as after a size change (768 px, or what the contents need): it was 768 px at the start
#           and the real minimum only after Settings (R-44)
# v1.9.264: R-17: the window only builds the screen, the menus and the toolbar and puts the parts together; each part keeps its own data:
#           the folder list (folderpane.py), the message list (messagelistpane.py), its columns (messagecolumns.py), the filters and the
#           search (searchfilters.py), the actions on messages (messageactions.py), the tags (mailtags.py), reading (readingpane.py),
#           writing (composing.py), the mail checks (mailchecking.py), the status line (statusreport.py), and what they share
#           (mailstate.py); unused code is gone (an action's reload of the list, _refresh_counts, _after_sent, _note_gone)
# v1.7.262: R-43, every key acts on the part that has the focus (D-217): Delete and Shift+Delete in the folder list delete the folder (asked
#           first), never the messages; the one-letter keys and Insert work in the message list and the reading pane, not in the folder
#           list (a letter jumps to a folder there)
# v1.6.261: The start asks the folders once per place where mail is kept (2 questions instead of 27 on the owner's profile) and lays the folder
#           list out once, so it appears in one step; the tray's unread number counts each place once (it counted Local Folders' Inbox once
#           per account keeping its mail there, and 25 of those copies were never updated)
# v1.6.260: The message list from messagelist.py (R-15); the list knows the rows on screen (a list fetched again brings them along)
# v1.5.259: Open (Enter) and Toggle flag (Insert) work only while the message list has the focus: Enter in the search field runs the search
# v1.5.258: The search history is given the search field: it works the list from the field's keys and never takes its mouse or keyboard
# v1.5.257: The status line shows where the link under the pointer in the reading pane leads, with the orange triangle for another main domain (D-215)
# v1.5.256: What the list shows is one value, CurrentView (R-14): the window changes it in one step (_change_view) and the filter menu,
#           the scope, the search field and the filter chip only show it; view_spec reads nothing from the widgets, so text being typed
#           never reaches a sort, a filter or a refetch; the filters react to the person's click only (triggered), so no signal is
#           blocked any more; the kinds of entry are EntryKind
# v1.3.255: Settings OK takes into use only what changed (R-13): the window remembers the settings it has in use (_applied) and
#           apply_preferences compares, one small step per group (colours and sizes, the list, the reading pane, the readers, the keys,
#           the tray, the check timer, the folder list); a column comes or goes by showing or hiding it (the list keeps every column),
#           so the selection stays; Settings gets one SettingsContext; closing Settings no longer fetches the list again after the
#           Scheduled messages page acted (its actions tell their facts)
# v1.2.253: Every setting through ui/prefs.py (R-12): the window reads the typed settings (_preferences), saves a change of its own menus
#           with them (_keep), keeps what it remembers by itself in Remembered (self.memory), and reads Show on startup once (StartupView)
# v1.1.252: A pinned folder that is renamed keeps its Favorites entry: the list is laid out with the pins the engine moved along (R-41)
# v1.1.251: The folder list is built once and never again (R-11): every change lays it out in place (_lay_out_tree: only what differs
#           changes), so the selection, the open branches and the scroll stay by themselves; no guard flag, no putting them back by hand;
#           the sidebar answer a command gives is used (no reload of every account's folders); the selected entry going away selects its
#           account's Inbox before it goes; only what the person opened or closed differently from the list's own way is remembered;
#           Show on startup reads the plan of the list (no hidden second list); the sidebar is typed (SidebarInfo)
# v1.1.250: The folder list's numbers are the engine's: each fact brings the new numbers of the folders it touched and the window puts them in
#           place; it no longer adds and subtracts itself (a second count that could drift, R-10); folder.list is asked typed (FOLDER_LIST)
# v1.1.247: Delete, Delete for good, Move to (also a drag), Copy to, Archive, Unarchive, Junk, Not junk, Snooze and the marks of several messages
#           go as one command per account and folder (on an IMAP server in groups); one line for what could not be done (R-08)
# v1.1.242: A Favorites or Tags view is built with the filters and the days and sizes of Advanced search, as a folder view is (R-03)
# v1.1.236: A calendar made on the server by the synchronisation counts as sent (R-29)
# v1.1.234: The calendar synchronisation's status says how many calendars, task lists and address books deleted on the server went here too (R-25)
# v1.1.229: Sync conflicts in a window of its own (Menu > Sync conflicts…, the sidebar entries); a click on a triangle opens its conflict; the entries
#           at the top of the sidebars (the owner)
# v1.1.228: Retry sync checks the accounts at once (the owner)
# v1.1.227: Sync conflicts (n) under the mail folders, only in Mail; the waiting mark of the message list follows the marks (D-195)
# v1.1.226: The window's SyncMarks: the sections' marks and Sync conflicts entry, Settings > Sync conflicts, the waiting count in the status line (D-194)
# v1.1.218: Refresh in Calendar, Contacts and Tasks synchronises with the servers: greyed while it runs, the status line says so and what came of it (D-190)
# v1.1.217: Every check of mail also brings the calendars, task lists and address books of the accounts it checks in step; the general timer also those of accounts of calendars only (D-079)
# v1.1.215: The separators between the panes are the program's one separator (D-188)
# v1.1.212: Accounts of calendars and contacts only are left out of everything about mail; Add account… in the Calendar, Contacts and Tasks sidebars (D-185)
# v1.1.210: A new account looks for its calendars, task lists and address books; the ones ticked in "Additional folders found" are kept in step (D-181)
# v1.1.209: A dragged column moves in the list's own columns and the header keeps no move of its own: adding a column or opening another folder no longer jumbles the order (D-179)
# v1.1.208: Scheduled views: Edit and (on the empty area) New in the menu, Favorites > Scheduled opens to edit; a scheduled message moved to Drafts becomes a draft again, other folders refuse it (D-178)
# v1.1.207: A scheduled message opens to be changed, never as a draft: from the Scheduled folder and from Settings > Scheduled messages > Edit (D-177)
# v1.1.204: Standard folders of Local Folders can be hidden (Settings > Sidebar); a folder moved out of a hidden one appears
# v1.1.202: A made or deleted folder brings its Favorites entry (Scheduled, Snoozed, Archive) in place; Favorites > Scheduled shows the recipients, like Favorites > Outbox
# v1.1.200: The Scheduled folder: its Send at column (as Snoozed until), recipients shown, opened in the editor like the Outbox, never a move or copy target
# v1.1.198: Settings > Scheduled messages > New…: a message window that schedules, over the Settings window (modal), the new message added to the page
# v1.1.197: The key dialog at start is told why the key could not be had (KeyStatus)
# v1.1.195: Show on startup lists what Settings > Sidebar hides too, greyed and not to be chosen
# v1.1.192: At start the folder list is built at most twice: early, when what the folder to open needs is in (selected, its messages load), and when everything is in (the fallback is decided only then); tags never build it before the folders; a build keeps the scroll position
# v1.1.191: Settings > Sidebar (replaces Favorites): hidden account trees; what Show on startup chose is always shown; when nothing to open is shown, the first entry of the folder list
# v1.1.190: New group… only in an account's Put in group menu (the group starts with that account); not on Favorites, headings or the empty area
# v1.1.189: New folder is offered only on a row of one account (not on Favorites, tags, headings or groups), greyed in the Folder menu there
# v1.1.188: The Empty item says Empty Junk folder… or Empty Trash… for the folder that is selected
# v1.1.182: Menu > Tags opens Settings > Tags; Settings > Favorites changes Favorites after OK; Show on startup gets Favorites and Tags as headings with their entries below them
# v1.1.181: F1 opens About Chrono Mail (changeable in Settings > Keyboard)
# v1.1.173: The window's minimum width is fixed in every section (768 px, or what the contents need at a larger size), so showing Mail never makes it wider
# v1.1.172: The Tag button's colour from the colour file (D-158)
# v0.5.170: one toolbar for the window that shows the row of the section on screen (switching made the window wider, D-156)
# v0.5.167: the toolbar of every section folds its buttons into More when the window is narrow (FoldingToolbar, D-154); Menu and the search field always stay
# v0.5.165: going back to the message in the reading pane calls off the message still opening (its answer showed it under the selected one)
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.161: only new group headers are spanned (not every header after every change)
# v0.5.160: a new sort, and fetching the list again after a change from outside, rearrange the list in place: Qt's selection moves with each message (whole rows, never one cell); the hand-made reselect is gone
# v0.5.158: the folder list follows the facts (numbers per folder, a folder made, renamed or deleted); a change from outside fetches only the mailbox it concerns, once, one at a time; no reload after a check, an import, sending or tags (D-146 step 4)
# v0.5.157: the facts of the engine drive the list (no reload after an action); an action takes the messages selected at the key press, the selection and the reading pane move at once, messages with an action under way are greyed (D-146 step 3)
# v0.5.153: A new sort order keeps the selected message selected and in view, also when it lands on a part of the list that was not loaded
# v0.5.152: A Favorites view is sorted by the chosen column too (its request passed no sort order)
# v0.5.151: While an import runs no mail is checked (timers and push skip their turn) and Refresh is off; one check runs when the import is done
# v0.5.146: Every send and receive opens the Operations window (also at startup, by the timers and the push); only Refresh gives it the keyboard focus
# v0.5.145: The Operations window: rows, errors and log of send and receive; opened by Refresh, by an error, by View > Operations… and by a click on the busy bar
# v0.5.144: bring_forward: a second start of the program shows this window (also from the tray)
# v0.5.144: The main window and its menus at the size of the Main window slider, the message windows and composers at that of Other windows and dialogs (both tried out from Settings)
# v0.5.143: Progress numbers in the status line have thousands separators
# v0.5.140: The first column of the message list can be dragged to another place too
# v0.5.135: Tags for mail: Menu > Tags, the Tag button and menu, Tag in the right-click menu, Filter > Tagged with, Tags in the folder list, the tags in the reading pane, tag shortcuts, the tag colour for the text
# v0.5.133: After an import the message list on screen is loaded again too (it kept the old messages); mail checks run in their own lane
# v0.5.132: Menu > File > Import (accounts, passwords, rules, contacts and mail from another program's exports)
# v0.5.126: The changes of a message (marks, delete, move, copy, archive, junk, snooze) run in the order they were given
# v0.5.125: A message deleted or moved right after it was clicked no longer reports that messages are missing
# v0.5.124: Show on startup: a chosen folder, Calendar, Contacts or Tasks, or the last selected folder (D-121)
# v0.5.123: An account or group row is selected and shows its Inbox (nothing for an account without one); the Favorites heading is never selected, a click folds it; the first Inbox at start is found inside groups too (D-119)
# v0.4.120: The engine's notices (an IMAP change waits for the server) in the red line; the IMAP connections close when the program ends
# v0.4.119: An IMAP folder held back by a safety rule is a question to the person (delete on the server or bring back; remove here or keep), answered with mail.settle
# v0.4.117: Alt+M opens the Menu (Open the menu, changeable in Settings > Keyboard); the contact addresses are loaded again after the calendar and contacts sync
# v0.3.116: Delete, Delete for good, Move to Junk, Move (also by dragging), Archive and Copy refresh the list also when a message failed (the error stays)
# v0.3.112: A message whose file is gone gives one warning per action (a window and the red line) with the advice to repair the search index
# v0.3.109: Every column but Delete sorts (icon columns: yes first; Messages in conversation: largest first; Folder and Preview: A to Z), the eight new sorts in the Sorted by menu; a click on Delete leaves the sort arrow where it was
# v0.3.108: The message list has its own size factor (text, rows, cards, icons and columns), tried out at once from Settings; the size of the text of messages is a factor for reading and writing, tried out at once from Settings (Cancel puts it back); open composers follow
# v0.3.106: The size factor of the interface is tried out while the slider in Settings moves (Cancel puts it back); the fixed sizes of the window follow it
# v0.2.102: In the Archive folder Archive is Unarchive (back to the folder it came from); Move to Junk and Not junk (move to Inbox) say what they do
# v0.2.101: Cancel snooze (in the Snoozed folder: the menu of the Snooze button and the right-click menu) brings the message back now
# v0.2.100: The Snoozed folder has the column Snoozed until (sortable, with its groups; added once after Received, kept off when switched off)
# v0.2.99: After a delete, move, archive, junk or snooze the list keeps its place and selects the next message (or keeps the selection when another row was deleted); F2 renames the folder while the folder list has the focus
# v0.2.97: The Message list window sets the row height too
# v0.2.96: In the card layout the one column always fills the list (it kept the width of the first column of the column layout); View > Message list: Columns or Cards; in the card layout the rows are not all one height (group headers are lower than cards); Message list… is no longer in the View menu (it stays in the menu of the column headers)
# v0.2.94: Group headers in the message list (fold by a click; Show groups in the View menu); sorting by To; searches and combined views follow the sort order
# v0.1.93: The search field has three looks (not in use, clicked in, search active) and a red ✕ that clears the search; clicking in it shows the last 10 searches
# v0.1.92: Search box hint "Search (type '?' for help)"; typing "?" opens the search help under the search box
# v0.1.91: Clear filter at the bottom of the Filter menu; the filters that are on are bold in the menu
# v0.1.90: The Unread, Flagged and Attachments buttons are replaced by a Filter button (funnel) with a menu: Filter by, Flagged, Unread, With attachments; the button is blue and bold while a filter is on
# v0.1.89: The columns always fill the list (column fit); fixed row heights; Contact and Delete columns; Delete and Contact added once to a saved set of columns
# v0.0.88: Object names for the two splitters (1 px lines) and the Sorted by button (bold)
# v0.0.86: Refresh sends and receives and has a menu (send and receive all, receive all, send all, one account only); a Snooze button with ready-made times; Forward has a menu with Forward as attachment; the Snooze button opens its menu wherever it is clicked; message buttons and actions are greyed out while no message is selected
# v0.0.85: Window title: the open folder and its unread messages, then the program; icons on Mark and Move to; Archive in the toolbar; Snooze uses the clock icon by its own name
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.82: Text size of the interface (8 to 48 pt, the system size at the first start) under Appearance; the Menu button uses the three-bar character U+2630
# v0.0.81: The Menu button is the first, blue button of every toolbar and the row of the menu bar is gone; New is a plain toolbar button
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.76: Blue accent, Settings Accounts Rules and Exit in the root of the Menu, plain section icons with the open one in blue, Contacts before Tasks
# v0.0.75: The folder list remembers which headings, accounts and folders are open, also after a restart
# v0.0.74: A message window gets the privacy settings before it shows the message and saves Always for this sender
# v0.0.73: The main window gets a new look: the Menu button, the toolbar, the search field, the folder list and the section buttons
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.66: Synchronisation of calendars, task lists and address books with a CalDAV and CardDAV server
# v0.0.64: The calendar section
# v0.0.63: The tasks section
# v0.0.62: The contacts section
# v0.0.61: The four section buttons and the sidebars of the calendar, task and contact sections
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.53: Standard keyboard shortcuts
# v0.0.52: Ctrl+Q marks as read, Quit is Ctrl+Shift+Q
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.48: Dragging messages shows a small badge, not the selected rows
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.42: Settings window with pages for everything
# v0.0.41: Message list as columns, column editor, reading pane position, message windows
# v0.0.35: Send later and snooze
# v0.0.34: Junk and unread navigation
# v0.0.33: Rules
# v0.0.32: IMAP IDLE: new mail pushed to the window
# v0.0.26: Version number in the About dialog
# v0.0.25: No other product named in the project
# v0.0.24: Rich text composing
# v0.0.22: Save as .eml, view source, print
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Settings window, account editing, signatures
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.17: Message actions: archive, mark all as read, empty folder, drag and drop
# v0.0.16: Drafts
# v0.0.15: the encryption key is checked at the start and can be restored
# v0.0.14: window icon, shared icon helpers
# v0.0.13: Initial main window

MINIMUM_WIDTH: int = 768  # the window is never narrower, in every section (the owner's choice)
NOT_JUNK: str = "Not junk (move to Inbox)"
EMPTY_TEXTS: dict[str, str] = {"junk": "Empty Junk folder…", "trash": "Empty Trash…"}  # the Empty item says which folder it empties


def on_wayland() -> bool:
    """Whether the program runs on Wayland, which never tells a window that it is minimized or no longer minimized."""
    return QApplication.platformName().startswith("wayland")


class MainWindow(QMainWindow):
    """The three-pane window: folders, the messages of a folder, and the message being read.

    It builds the screen, the menus and the toolbar and puts the parts together; each part keeps its own data, and the window
    contains no mail logic: everything is a command of the engine, run on a worker thread.
    """

    closed_for_good = Signal()  # the window was closed and not merely hidden in the tray: the program should end

    MESSAGE_ACTIONS: tuple[str, ...] = (  # need a selected message: greyed out while none is selected
        "reply", "reply_all", "forward", "forward_attachment", "archive", "snooze", "read", "unread", "flag", "unflag", "toggle_read", "toggle_flag",
        "move_to", "quick_move", "copy_to", "quick_copy", "junk", "delete_forever", "open", "save_as", "source", "print", "edit_draft",
    )
    MAIL_ONLY: tuple[str, ...] = (
        "reply", "reply_all", "forward", "edit_draft", "save_as", "source", "print", "open", "junk", "not_junk", "next_unread", "previous_unread", "archive", "mark_all_read",
        "empty_folder", "read", "unread", "flag", "unflag", "snooze", "toggle_read", "toggle_flag", "delete_forever", "move_to", "quick_move", "copy_to", "quick_copy",
        "go_to_folder", "next_message", "previous_message", "advanced", "new_folder", "rename_folder", "delete_folder",
    )

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend: Backend = backend
        self.settings: QSettings = settings
        self.memory: Remembered = Remembered(settings)  # what the window keeps by itself between starts (R-12)
        self.prefs: InUse = InUse(settings)  # the settings the window has in use: Settings OK applies what differs (R-13)
        self.mail_state: MailState = MailState()
        self.setWindowTitle("Chrono Mail")
        self.setWindowIcon(application_icon())
        self.resize(1280, 800)
        self._section: str = MAIL
        self._mail_action_states: dict[str, bool] = {}
        self._tried_interface: float | None = None  # sizes being tried out in Settings (not saved yet)
        self._tried_windows: float | None = None
        self._quitting: bool = False
        self._for_update: bool = False  # closing after an update: no questions (quit_for_update)
        self.out_of_sight: bool = False  # started minimized at login and not opened since (D-284): Wayland never says a window is minimized
        self.setProperty(scale.MAIN_WINDOW_PROPERTY, True)  # this window and its menus follow the Main window slider
        self._apply_main_font()
        self.setStyleSheet(theme.stylesheet())  # only this window: a stylesheet on the application would hide the style from appearance.py
        self._build_parts()
        self._build_actions()
        self._default_shortcuts: dict[str, str] = {name: action.shortcut().toString() for name, action in vars(self).items() if name.startswith("act_") and isinstance(action, QAction)}
        self.checking: MailChecking = MailChecking(
            backend, self.mail_state, self.report, self.prefs, self.messages, self.act_refresh, self.act_auto, self.pages, lambda: self._section, self,
        )
        self.operations = self.checking.operations
        self.notices = SendNotices(backend, self.mail_state, self.checking, self.composing, self.report, self.prefs, self)  # delayed sends (B-21), Quit (B-20)
        self.composing.send_waiting.connect(self.notices.add)
        self.rebuilds: IndexRebuilds = IndexRebuilds(backend, self.report, self.mail_state.account_name, self)  # D-229
        self.backups: BackupWindow = BackupWindow(backend, settings, self.report, self)  # File > Backup… (D-244)
        self.restores: RestoreWindow = RestoreWindow(backend, settings, self)  # File > Restore…: the mail checks wait while it runs
        self.restores.running_changed.connect(self.checking.import_running)
        self.restores.restored.connect(self._after_restore)
        self.rebuilds.running_changed.connect(self.checking.rebuild_running)
        self.rebuilds.quit_requested.connect(self.quit_program)
        self.updates: Updates = Updates(  # D-293
            backend, self.prefs, self, self.quit_program, self.closed_for_good, self._on_screen, self.quit_for_update,
        )
        self.checking.went_online.connect(self.updates.went_online)
        questions_for(backend).watch(self, self._on_screen)  # a certificate met by a Refresh asks over this window, while it is on screen (D-299)
        self._build_toolbar()
        self._build_menus()
        self._build_status()
        self.columns.apply_list_layout()
        self.columns.apply_list_look()
        self.reading.apply_place()
        self.reading.apply_reader_preferences()
        self._apply_shortcuts()
        self._wire()
        self._restore_state()
        self.tray = TrayController(self)
        self.tray.toggle_requested.connect(self.toggle_visibility)
        self.tray.check_requested.connect(lambda: self.checking.check_accounts(None, manual=True))
        self.tray.settings_requested.connect(self._settings_from_tray)
        self.tray.quit_requested.connect(self.quit_program)
        self.tray.set_visible(self.prefs.read().tray)
        QTimer.singleShot(0, self._fix_minimum_width)  # once everything is laid out: the same minimum as after a size change in Settings

    # --- construction ---------------------------------------------------------------------------------

    def _build_parts(self) -> None:
        """The parts of the mail window and the panes they make, laid out: the folders (or a section's sidebar) at the left, the
        message list and the reading pane at the right."""
        backend, state, memory = self.backend, self.mail_state, self.memory
        self.report = StatusReport(self, state.account_name)
        book = TagColourBook()  # one set of tag colours for the list, the cards and the reading pane
        self.messages = MessageListPane(backend, state, self.report, self.prefs, memory, book, lambda: self.act_conversations.isChecked(), self)
        self.undoing = Undoing(backend, self.report, self.messages, self)  # B-18
        self.held_key = HeldKey(self)  # whether an action was set off by a held key's repeat, wherever the focus is in this window (D-266)
        self.undoing.is_repeat = self.held_key.take
        self.tags = MailTags(backend, state, self.report, self.messages, book, self.open_tags, self.undoing, self)
        self.tags.watch_action = self.held_key.watch  # a held tag shortcut stops when let go (D-266)
        self.folders = FolderPane(backend, state, self.report, self.prefs, memory, self.tags, self.messages, self)
        self.tree, self.tree_model = self.folders.tree, self.folders.model
        self.list_model, self.list_view = self.messages.model, self.messages.view
        self.list_model.role_of = self.mail_state.folder_role_of  # a message that comes back is placed by its folder's role (D-266)
        self.delegate, self.column_delegate = self.messages.delegate, self.messages.column_delegate
        self.sort_button, self.sort_menu, self.count_label = self.messages.sort_button, self.messages.sort_menu, self.messages.count_label
        self.setMinimumWidth(MINIMUM_WIDTH)  # one minimum for all sections: showing another section never makes the window wider
        self.filters = ListFilters(state, self.messages, book, self)
        self.messages.finish_header(self.filters.button, self.filters.chip)
        for first, then in ((self.tree, self.list_view), (self.list_view, self.sort_button), (self.sort_button, self.filters.button)):
            QWidget.setTabOrder(first, then)  # the Tab key goes as the person reads the window: folders, messages, Sorted by, Filter
        self.mail_actions = MessageActions(backend, state, self.report, self.messages, self.undoing, self)
        self.composing = Composing(backend, state, self.report, self.prefs, self.messages, self)
        self.columns = MessageColumns(self.messages, state, self.prefs, memory, self)
        self.splitter = PaneSplitter(Qt.Orientation.Horizontal, self)
        self.content_splitter = PaneSplitter(Qt.Orientation.Horizontal, self)  # the list and the reading pane, side by side or one above the other
        self.reading = ReadingPane(
            backend, state, self.report, self.prefs, self.messages, self.tags, self.mail_actions, self.composing, self.content_splitter,
            lambda: self.columns.layout == "columns", self,
        )
        self.reader, self.conversation_bar, self.reading_pane = self.reading.reader, self.reading.conversation_bar, self.reading.widget
        self.splitter.setObjectName("mainSplitter")
        self.content_splitter.setObjectName("contentSplitter")
        self.side_stack = QStackedWidget(self)  # the left side: the folders of the mail, or the sidebar of the section that is shown
        self.side_stack.addWidget(self.tree)
        self.content_stack = QStackedWidget(self)
        self.content_stack.addWidget(self.content_splitter)
        self.section_bar = SectionBar(self)
        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.side_heading = QLabel("Mail", left_panel)
        self.side_heading.setObjectName("sideHeading")
        left_layout.addWidget(self.side_heading)
        self.mail_conflicts_entry = QToolButton(left_panel)  # "Sync conflicts (n)" under the folders of the mail, only while there are any (D-195)
        self.mail_conflicts_entry.setObjectName("syncConflictsEntry")
        self.mail_conflicts_entry.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mail_conflicts_entry.setAutoRaise(True)
        self.mail_conflicts_entry.setIcon(mark_icon(CONFLICT))
        self.mail_conflicts_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mail_conflicts_entry.setStyleSheet("QToolButton#syncConflictsEntry { text-align: left; padding: 4px 10px; }")
        self.mail_conflicts_entry.clicked.connect(lambda: self.open_sync_conflicts())
        self.mail_conflicts_entry.setToolTip("Open the sync conflicts")
        self.mail_conflicts_entry.hide()
        left_layout.addWidget(self.mail_conflicts_entry)
        left_layout.addWidget(self.side_stack, 1)
        left_layout.addWidget(self.section_bar)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.content_stack)
        self.contacts_page = ContactsPage(self.backend, self.settings, self)
        self.pages: dict[str, PimPage] = {CALENDAR: CalendarPage(self.backend, self.settings, self), TASKS: TasksPage(self.backend, self.settings, self), CONTACTS: self.contacts_page}
        for page in self.pages.values():
            self.side_stack.addWidget(page.side)
            self.content_stack.addWidget(page.content)
            page.status = lambda text, shown=page: self.checking.page_status(shown, text)
            page.panel.add_account_requested.connect(lambda kind=CARDDAV if page is self.contacts_page else CALDAV: self.add_calendar_account(kind))
            page.servers = lambda: self.checking.refresh_calendars()
        self.content_splitter.addWidget(self.messages.pane)
        self.content_splitter.addWidget(self.reading_pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([240, 1040])
        self.content_splitter.setSizes([400, 640])
        self.splitter.setChildrenCollapsible(False)
        self.content_splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)

    def _action(self, text: str, handler: Callable[..., object], shortcut: str | None = None, icon: str | None = None) -> QAction:
        action = QAction(text, self)
        if icon:
            action.setIcon(theme_icon(icon))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(handler)
        return action

    def _build_actions(self) -> None:
        actions, composing, messages, reading = self.mail_actions, self.composing, self.messages, self.reading
        once = self.held_key.once  # a key that opens a window acts once per press: holding it opens no more windows (D-266)
        self.act_new = self._action("New", once(lambda: self._in_section("new")), "Ctrl+N", "mail-message-new")
        self.act_refresh = self._action("Refresh", lambda: self._in_section("refresh"), "F5", "view-refresh")
        self.act_reply = self._action("Reply", once(lambda: composing.compose_from_selection("reply")), "Ctrl+R", "mail-reply-sender")
        self.act_reply_all = self._action("Reply all", once(lambda: composing.compose_from_selection("reply_all")), "Ctrl+Shift+R", "mail-reply-all")
        self.act_forward = self._action("Forward", once(lambda: composing.compose_from_selection("forward")), "Ctrl+F", "mail-forward")
        self.act_forward_attachment = self._action("Forward as attachment", lambda: composing.compose_from_selection("forward_attachment"))
        self.act_edit_draft = self._action("Edit draft", composing.edit_draft, None, "document-edit")
        self.act_edit_scheduled = self._action("Edit", composing.edit_draft, None, "document-edit")  # a scheduled message: it stays scheduled (D-177)
        self.act_new_scheduled = self._action("New", lambda: composing.new_scheduled_message(None), None, "mail-message-new")  # a new scheduled message
        self.act_delete = self._action("Delete", lambda: self._in_section("delete"), "Delete", "edit-delete")
        self.act_save_as = self._action("Save as…", reading.save_selected_as, "Ctrl+Shift+S", "document-save-as")
        self.act_source = self._action("View source", once(lambda: reading.view_source()), "Ctrl+Alt+U")
        self.act_print = self._action("Print…", once(lambda: reading.print_selected()), "Ctrl+P", "document-print")
        self.act_open = self._action("Open", once(self.open_selected), "Return", "document-open")
        self.act_columns = self._action("Message list…", self.edit_columns)
        self.columns.columns_action = self.act_columns
        self.act_junk = self._action("Move to Junk", actions.junk_key, "Ctrl+J", "mail-mark-junk")  # one key does both, as the folder decides
        self.act_not_junk = self._action(NOT_JUNK, lambda: actions.junk_selected(False), None, "mail-mark-notjunk")
        self.act_cancel_snooze = self._action("Cancel snooze", actions.cancel_snooze_selected)
        self.act_not_junk.setEnabled(False)
        self.act_undo = self._action("Undo", self.undoing.undo, "Ctrl+Z")  # the text says what it would undo; a text field keeps Ctrl+Z for its typing
        self.act_redo = self._action("Redo", self.undoing.redo, "Ctrl+Y")
        self.act_redo_other = self._action("Redo (second key)", self.undoing.redo, "Ctrl+Shift+Z")  # the other usual key, not in the menu
        for action in (self.act_undo, self.act_redo, self.act_redo_other):
            action.setEnabled(False)
        self.act_next_unread = self._action("Next unread message", lambda: messages.go_to_unread(1), "N")
        self.act_previous_unread = self._action("Previous unread message", lambda: messages.go_to_unread(-1), "P")
        self.act_archive = self._action("Archive", actions.archive_selected, "A", "archive-insert")
        self.act_mark_all_read = self._action("Mark all as read", actions.mark_folder_read, icon="mail-mark-read")
        self.act_empty_folder = self._action("Empty folder…", actions.empty_folder, icon="edit-clear")
        self.act_empty_folder.setEnabled(False)
        self.act_read = self._action("Mark as read", lambda: actions.mark_selected(seen=True), "Ctrl+Q")
        self.section_actions: dict[str, QAction] = {}
        for section, name, icons, shortcut in SECTIONS:
            action = self._action(name, lambda checked=False, chosen=section: self.switch_section(chosen), shortcut, icons[0])
            self.section_actions[section] = action
            setattr(self, f"act_section_{section}", action)
        self.act_next_message = self._action("Next message", lambda: messages.step_message(1), "F")
        self.act_previous_message = self._action("Previous message", lambda: messages.step_message(-1), "B")
        self.act_toggle_read = self._action("Toggle read / unread", actions.toggle_read, "R")
        self.act_toggle_flag = self._action("Toggle flag", actions.toggle_flag, "Insert")
        self.act_delete_forever = self._action("Delete permanently…", actions.delete_permanently, "Shift+Delete", "edit-delete")
        self.act_move_to = self._action("Move to folder…", lambda: actions.popup_folders("move"), "Ctrl+Shift+V")
        self.act_quick_move = self._action("Quick move", lambda: actions.popup_folders("move"), "M")
        self.act_copy_to = self._action("Copy to folder…", lambda: actions.popup_folders("copy"), "Ctrl+Shift+C")
        self.act_quick_copy = self._action("Quick copy", lambda: actions.popup_folders("copy"), "C")
        self.act_go_to_folder = self._action("Go to folder…", self.folders.go_to_folder, "G")
        self.act_toggle_folders = self._action("Folder list", lambda: self.tree.setVisible(self.act_toggle_folders.isChecked()), "F9")
        self.act_toggle_folders.setCheckable(True)
        self.act_toggle_folders.setChecked(True)
        self.act_unread = self._action("Mark as unread", lambda: actions.mark_selected(seen=False), "Ctrl+U")
        for action in (self.act_undo, self.act_redo, self.act_redo_other, self.act_read, self.act_unread, self.act_toggle_read, self.act_toggle_flag,
                       self.act_new, self.act_reply, self.act_reply_all, self.act_forward, self.act_open, self.act_source, self.act_print):
            self.held_key.watch(action)  # told when a held key's repeat sets it off (D-266)
        self.act_flag = self._action("Flag", lambda: actions.mark_selected(flagged=True), icon="mail-mark-important")
        self.act_unflag = self._action("Remove flag", lambda: actions.mark_selected(flagged=False))
        self.act_add_account = self._action("Add an account…", lambda: self.add_account(), icon="list-add")
        self.act_recovery = self._action("Show the recovery code…", lambda: RecoveryDialog(self.backend, self).exec())
        self.act_backup = self._action(BACKUP_MENU_TEXT, lambda: self.backups.show())
        self.act_restore = self._action(RESTORE_MENU_TEXT, lambda: self.restores.start())
        self.act_import = self._action(IMPORT_MENU_TEXT, self.show_import)
        self.act_export = self._action("Export…", self.show_export)
        self.act_restore_key = self._action("Use a recovery code…", self.restore_key)
        self.act_restore_key.setToolTip("Get the encryption key back with its recovery code or a recovery file")
        self.act_settings = self._action("Settings…", lambda: self.open_settings(), "Ctrl+,", "configure")
        self.act_scheduled = self._action("Scheduled and snoozed messages…", lambda: self.open_settings("Scheduled messages"))
        self.act_snooze = self._action("Snooze…", actions.snooze_selected, "S", "clock")
        self.act_snooze.setIconText("Snooze")  # on the toolbar button
        self.act_rules = self._action("Rules…", lambda: self.open_settings("Rules"), None, "view-filter")
        self.act_sync_conflicts = self._action("Sync conflicts…", lambda: self.open_sync_conflicts(), None, "emblem-warning")
        self.act_tags = self._action("Tags…", self.open_tags)
        self.act_accounts = self._action("Accounts…", lambda: self.open_settings("Accounts"), None, "user-identity")
        self.act_quit = self._action("Exit", self.quit_program, None, "application-exit")
        self.act_open_menu = self._action("Open the menu", self.open_menu, "Alt+M")
        self.act_search = self._action("Search", lambda: self._in_section("search"), "Ctrl+E")
        self.act_advanced = self._action("Advanced search…", lambda: self.search.advanced(), "Ctrl+Shift+F")
        self.act_auto = QAction("Refresh automatically", self)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(self.prefs.read().auto_check)
        self.act_conversations = QAction("Show conversations", self)
        self.act_conversations.setCheckable(True)
        self.act_conversations.setChecked(self.prefs.read().conversations)
        self.act_show_groups = QAction("Show groups", self)
        self.act_show_groups.setCheckable(True)
        self.act_show_groups.setChecked(self.memory.show_groups)
        self.list_model.show_groups = self.act_show_groups.isChecked()
        self.act_new_folder = self._action("New folder…", self.folders.new_folder)
        self.act_rename_folder = self._action("Rename folder…", self.folders.rename_folder, "F2")
        self.act_delete_folder = self._action("Delete folder…", self.folders.delete_folder)
        self.act_delete_folder.setEnabled(False)  # only a folder the person made (CR-36)
        self.act_about = self._action("About Chrono Mail", self._about, "F1")
        self.folders.use_menu(FolderMenu(self.act_new_folder, self.act_rename_folder, self.act_delete_folder, self.act_mark_all_read, self.act_empty_folder))

    def _split_button(self, action: QAction, menu: QMenu) -> None:
        """The toolbar button of an action does the action when clicked and opens the menu from its arrow."""
        button = self.toolbar.button_for(action)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def _fill_refresh_menu(self) -> None:
        """Send and receive all, receive all, send all, or receive the mail of one account only."""
        checking = self.checking
        self.refresh_menu.clear()
        self.refresh_menu.addAction("Send and Receive All", checking.send_and_receive)
        self.refresh_menu.addAction("Receive All", lambda: checking.check_accounts(None, manual=True))
        self.refresh_menu.addAction("Send All", lambda: checking.send_outbox(manual=True))
        accounts = self.mail_state.mail_accounts()
        if accounts:
            self.refresh_menu.addSeparator()
        for account in accounts:
            account_id = account.account_id
            self.refresh_menu.addAction(f"\"{account.shown_name}\" only", lambda chosen=account_id: checking.check_accounts({chosen}, manual=True))

    def _build_toolbar(self) -> None:
        self.window_toolbar = WindowToolbar("Main", self)  # the one toolbar: it shows the row of the section on screen
        self.window_toolbar.setObjectName("mainToolbar")
        self.addToolBar(self.window_toolbar)
        self.toolbar = ToolbarRow(self)  # Mail's row
        self.window_toolbar.add_row(self.toolbar)
        self.toolbar.add_left(self.act_new)
        self.toolbar.add_left(self.act_refresh)
        self.refresh_menu = QMenu(self)
        self.refresh_menu.aboutToShow.connect(self._fill_refresh_menu)
        self._split_button(self.act_refresh, self.refresh_menu)
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            self.toolbar.add_center(action)
        self.forward_menu = QMenu(self)
        self.forward_menu.addAction(self.act_forward_attachment)
        self._split_button(self.act_forward, self.forward_menu)
        self.mark_button = QToolButton(self)
        self.mark_button.setText("Mark")
        self.mark_button.setIcon(theme_icon("crosshairs"))
        self.mark_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mark_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mark_menu = QMenu(self.mark_button)
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            mark_menu.addAction(action)
        self.mark_button.setMenu(mark_menu)
        self.toolbar.add_center(self.mark_button)
        self.tag_button = QToolButton(self)
        self.tag_button.setText("Tag")
        self.tag_button.setIcon(tag_icon(QColor(NEW_TAG)))
        self.tag_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_menu.aboutToShow.connect(lambda: self.tags.fill_tag_menu(self.tag_menu))
        self.tag_button.setMenu(self.tag_menu)
        self.toolbar.add_center(self.tag_button)
        self.toolbar.add_center(self.act_archive)
        snooze_button = self.toolbar.add_center(self.act_snooze)
        self.snooze_menu = QMenu(self)
        self.snooze_menu.aboutToShow.connect(lambda: self.mail_actions.fill_snooze_menu(self.snooze_menu, self.act_cancel_snooze))
        snooze_button.setMenu(self.snooze_menu)  # a click anywhere on it opens the menu: the time dialog is its last entry
        snooze_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_button = QToolButton(self)
        self.move_button.setText("Move to")
        self.move_button.setIcon(theme_icon("go-parent-folder"))
        self.move_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = self.mail_actions.move_menu
        self.move_button.setMenu(self.move_menu)
        self.toolbar.add_center(self.move_button)
        self.toolbar.add_center(self.act_delete)
        saved = SavedSearches(self.backend.engine.directories.config_directory / SAVED_SEARCHES_FILE)  # on this computer only (OR-21)
        self.search = MailSearch(self.mail_state, self.messages, self.memory, self, saved)
        self.search_pill, self.search_box, self.scope = self.search.pill, self.search.box, self.search.scope
        self.scope.add_command(self.act_advanced)  # below where the search looks, after a line
        self.search_history, self.search_help = self.search.history, self.search.help
        self.toolbar.set_search(self.search_pill)
        self.toolbar.set_scope_gives_way_after(self.mark_button)  # Delete to Mark fold first, then where the search looks (D-290)
        for page in self.pages.values():  # the rows of the other sections, in the same toolbar
            self.window_toolbar.add_row(page.toolbar)

    def _toolbars(self) -> list[ToolbarRow]:
        """The row of buttons of every section: Mail's first."""
        return [self.toolbar, *(page.toolbar for page in self.pages.values())]

    def _menu_actions(self, menu: QMenu) -> list[QAction]:
        """Every action in a menu and in its submenus: they are also given to the window, so that their keys work while the menu bar is hidden."""
        found: list[QAction] = []
        for action in menu.actions():
            submenu = action.menu()
            if isinstance(submenu, QMenu):
                found.extend(self._menu_actions(submenu))
            elif not action.isSeparator():
                found.append(action)
        return found

    def _build_menus(self) -> None:
        bar = self.menuBar()
        root = bar.addMenu("≡  &Menu")  # one Menu button holds the menus that make up the menu bar
        for action in (self.act_settings, self.act_accounts, self.act_rules, self.act_tags, self.act_sync_conflicts):
            root.addAction(action)
        root.addSeparator()
        file_menu = root.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_refresh)
        file_menu.setToolTipsVisible(True)
        file_menu.addSeparator()
        file_menu.addAction(self.act_backup)
        file_menu.addAction(self.act_restore)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addAction(self.act_export)
        file_menu.addSeparator()
        file_menu.addAction(self.act_recovery)
        file_menu.addAction(self.act_restore_key)
        message_menu = root.addMenu("&Message")
        message_menu.addAction(self.act_undo)
        message_menu.addAction(self.act_redo)
        message_menu.addSeparator()
        message_menu.addAction(self.act_open)
        message_menu.addAction(self.act_edit_draft)
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            message_menu.addAction(action)
        message_menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            message_menu.addAction(action)
        message_menu.addSeparator()
        message_menu.addAction(self.act_toggle_read)
        message_menu.addAction(self.act_toggle_flag)
        message_menu.addSeparator()
        for action in (self.act_move_to, self.act_quick_move, self.act_copy_to, self.act_quick_copy):
            message_menu.addAction(action)
        message_menu.addAction(self.act_archive)
        message_menu.addAction(self.act_snooze)
        message_menu.addAction(self.act_junk)
        message_menu.addAction(self.act_not_junk)
        message_menu.addAction(self.act_delete)
        message_menu.addAction(self.act_delete_forever)
        message_menu.aboutToShow.connect(lambda: self._show_junk_items(message_menu))
        message_menu.addSeparator()
        go_to_menu = message_menu.addMenu("Go to")  # the owner: the menu was very tall; the keys work as before
        for action in (self.act_next_message, self.act_previous_message, self.act_next_unread, self.act_previous_unread, self.act_go_to_folder):
            go_to_menu.addAction(action)
        message_menu.addSeparator()
        for action in (self.act_save_as, self.act_print, self.act_source):
            message_menu.addAction(action)
        folder_menu = root.addMenu("F&older")
        for action in (self.act_new_folder, self.act_rename_folder, self.act_delete_folder):
            folder_menu.addAction(action)
        folder_menu.addSeparator()
        folder_menu.addAction(self.act_mark_all_read)
        folder_menu.addAction(self.act_empty_folder)
        folder_menu.addSeparator()
        folder_menu.addAction(self.columns.apply_action)
        view_menu = root.addMenu("&View")
        for section_action in self.section_actions.values():
            view_menu.addAction(section_action)
        view_menu.addSeparator()
        view_menu.addAction(self.act_search)
        view_menu.addAction(self.act_toggle_folders)
        self.columns.fill_layout_menu(view_menu.addMenu("Message list"))
        self.layout_actions: dict[str, QAction] = self.columns.layout_actions
        self.reading.fill_pane_menu(view_menu.addMenu("Reading pane"))
        self.pane_actions: dict[str, QAction] = self.reading.pane_actions
        view_menu.addAction(self.act_advanced)
        view_menu.addAction(self.act_conversations)
        view_menu.addAction(self.act_show_groups)
        view_menu.addAction(self.act_auto)
        view_menu.addSeparator()
        view_menu.addAction("Operations…", self.checking.show_operations)
        help_menu = root.addMenu("&Help")
        help_menu.addAction(self.act_about)
        root.addSeparator()
        root.addAction(self.act_quit)
        # The Menu button is the first button of every toolbar, so that no row of the window is used for it. The menu bar
        # stays as the home of the menus (and of their keys) but is not shown.
        self.menu_buttons: list[QToolButton] = []
        for toolbar in self._toolbars():
            button = QToolButton(self)
            button.setObjectName("menuButton")
            button.setText("☰  Menu")
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMenu(root)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            toolbar.set_menu_button(button)
            self.menu_buttons.append(button)
        self.addActions(self._menu_actions(root))
        self.addAction(self.act_open_menu)  # not in a menu: the window itself listens for its key
        bar.hide()

    def _build_status(self) -> None:
        self.report.install(self.statusBar())
        self.status_label, self.progress_bar, self.status_line = self.report.label, self.report.progress_bar, self.report.line
        self.report.bar_clicked.connect(self._bar_clicked)
        self.rebuilds.running_changed.connect(self.report.bar_hint)

    def _bar_clicked(self) -> None:
        """The busy bar: while an index is updated its window comes back (after Run in background), else the Operations window (D-229)."""
        if self.rebuilds.running:
            self.rebuilds.show_window()
        elif self.backups.running:
            self.backups.show()
        else:
            self.checking.show_operations()

    def _wire(self) -> None:
        self.section_bar.chosen.connect(self.switch_section)
        self.undoing.labels_changed.connect(self._show_undo)
        self.contacts_page.compose_requested.connect(self.composing.compose_to)
        self.contacts_page.mail_accounts_source = self.mail_state.accounts_with_mail
        self.folders.entry_chosen.connect(self._open_entry)
        self.folders.numbers_changed.connect(self._update_unread_summary)
        self.folders.folder_moved.connect(self._folder_moved)
        self.tree.messages_dropped.connect(self.mail_actions.messages_dropped)
        self.act_conversations.toggled.connect(self.messages.toggle_conversations)
        self.act_show_groups.toggled.connect(self.messages.toggle_groups)
        self.list_view.selectionModel().selectionChanged.connect(self._update_message_actions)
        self.list_view.selectionModel().currentChanged.connect(self._show_not_junk)  # in a search of several folders it follows the focused message (D-287)
        self.list_view.selectionModel().selectionChanged.connect(self._show_archive_action)  # it follows the messages (D-101)
        self.messages.rows_changed.connect(self._show_archive_action)  # a selected message moved to or from an archive folder
        self.list_model.modelReset.connect(self._show_archive_action)
        self.list_model.modelReset.connect(self._update_message_actions)
        self.list_model.rowsRemoved.connect(self._update_message_actions)
        self.list_model.rowsInserted.connect(self._update_message_actions)
        self._update_message_actions()
        self.list_view.customContextMenuRequested.connect(self._list_menu)
        self.list_view.doubleClicked.connect(self._row_double_clicked)
        self.list_view.icon_clicked.connect(self._icon_clicked)
        self.messages.view_changed.connect(self.filters.show_state)
        self.messages.view_changed.connect(self.search.show_state)
        self.backend.busy_changed.connect(self.report.busy_changed)
        self.backend.jobs_changed.connect(self.report.jobs_changed)
        self.backend.progress.connect(self.report.progress)
        self.backend.progress_ended.connect(self.report.progress_ended)
        self.backend.notice.connect(self.report.notice)
        self.backend.activity.connect(self.checking.activity)
        self.backend.changed.connect(self.list_model.apply)  # every change of mail, applied to the rows on screen (D-146)
        self.backend.changed.connect(self.folders.fact)  # ... to the numbers and folders of the folder list
        self.backend.changed.connect(self.messages.fact)  # ... and a mailbox changed from outside fetches the list again when it shows it
        self.checking.new_mail.connect(self._notify_new_mail)
        self.sync_marks = SyncMarks(self.backend, self)  # the sync conflicts and what waits for the servers (D-194)
        self.waiting_label = QLabel("", self)
        self.waiting_label.setObjectName("waitingLabel")
        self.waiting_label.hide()
        self.statusBar().addPermanentWidget(self.waiting_label)
        self.sync_marks.changed.connect(self._show_waiting)
        self.delegate.mark_of = self.sync_marks.state  # the waiting mark before a message's subject (D-195)
        self.column_delegate.mark_of = self.sync_marks.state
        self._conflicts_window: SyncConflictsWindow | None = None  # Sync conflicts, a window of its own (the owner), made when first wanted
        for page in self.pages.values():
            page.use_marks(self.sync_marks)
            page.panel.conflicts_requested.connect(self.open_sync_conflicts)
            page.conflict_requested.connect(self._open_conflict)
        for page in self.pages.values():
            self.backend.changed.connect(page.apply_fact)  # ... and to the calendars, task lists and address books

    # --- state ------------------------------------------------------------------------------------------

    def _after_restore(self, settings_back: bool) -> None:
        """After a restore: its window settings in use at once, every index usable, the accounts and the startup folder, one check (D-244)."""
        if settings_back:
            self.apply_preferences()
            self.columns.take_in_setups()
            self._restore_state()
        self.rebuilds.start(lambda: self.reload_accounts(select_saved=True, then=self.checking.check_mail))

    def _restore_state(self) -> None:
        restore_geometry(self, self.settings, "geometry")
        startup = self.prefs.read().startup
        saved_section = startup.section if startup.kind == "section" else MAIL if startup.kind == "mail" else (self.memory.last_section or MAIL)
        if saved_section != MAIL:
            self.switch_section(saved_section)
        sizes = self.memory.sizes("splitter2")
        if isinstance(sizes, list) and len(sizes) == 2:
            self.splitter.setSizes([int(size) for size in sizes])
        inner = self.memory.sizes("content_splitter")
        if isinstance(inner, list) and len(inner) == 2 and self.reading.place != "off":
            self.content_splitter.setSizes([int(size) for size in inner])

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.tray.visible and self.prefs.read().close_to_tray:
            self.hide()  # the program keeps running in the notification area
            event.ignore()
            return
        if self._for_update:  # no question, no Cancel: what is not saved is saved, what waits stays waiting
            self.composing.save_all_and_close()
            self.notices.drop_all()
        elif not self.composing.close_all() or not self.notices.before_quit(self.quit_program) or not may_quit(self, self.backups.running, self.restores.running):  # the backup question last: what it promises then happens (W3)
            event.ignore()
            return
        save_geometry(self, self.settings, "geometry")
        self.memory.remember_sizes("splitter2", self.splitter.sizes())
        if self.reading.place != "off":
            self.memory.remember_sizes("content_splitter", self.content_splitter.sizes())
        self.prefs.keep(changed(self.prefs.read(), auto_check=self.act_auto.isChecked()))
        view = self.mail_state.view
        self.memory.remember_view(view.account or "", view.path, view.key_kind)
        self.checking.stop()
        self.backend.engine.stop_event.set()  # waits for new mail end now
        interrupt_downloads(self.backend.engine)  # a download of an update stops at once, never waited for
        self.backend.wait_for_done(3000)
        self.backend.engine.close_imap_links()  # log out of the connections kept open for the actions on the server
        self.tray.set_visible(False)
        event.accept()
        self.closed_for_good.emit()

    # --- tray, notifications, closing ---------------------------------------------------------------------

    def quit_for_update(self) -> None:
        """After an update: Chrono Mail closes without a Cancel (the owner); unsaved messages go to Drafts, the Outbox keeps its mail for the
        next start, a backup or restore that runs stops as at Quit."""
        self._for_update = True
        self.quit_program()
        self._for_update = False

    def quit_program(self) -> None:
        """Really quit (closing the window may only hide it, when the tray is used)."""
        self._quitting = True
        if not self.close():
            self._quitting = False

    def _on_screen(self) -> bool:
        """The window is on screen: shown, not minimized, not only in the notification area. Wayland never says a window is minimized: there it
        counts only while it is the active window (D-286)."""
        return self.isVisible() and not self.isMinimized() and not self.out_of_sight and (not on_wayland() or self.isActiveWindow())

    def toggle_visibility(self) -> None:
        """A click on the tray icon: hide the window that is in sight, else bring it back. Wayland never says a window is minimized
        (Qt reports it as not minimized at once), so there it counts as in sight only while it, or a dialog waiting for an answer, has the focus (D-286)."""
        in_sight = self.isVisible() and not self.isMinimized() and not self.out_of_sight
        modal = QApplication.activeModalWidget()  # the last dialog opened that waits for an answer, whether it has the focus or not
        in_use = self.isActiveWindow() or (modal is not None and modal.isActiveWindow())  # never another window of the program (a message window)
        if in_sight and (in_use or not on_wayland()):
            self.hide()
        else:
            self.open_window()

    def bring_forward(self) -> None:
        """A second start of the program asked for the window: show it (also when it was hidden in the tray), raise it and give it the focus."""
        self.open_window()

    def open_window(self) -> None:
        """Show the window as it was before it was minimized or hidden (a maximized one stays maximized), raised, with the focus."""
        modal = QApplication.activeModalWidget()  # a dialog waiting for an answer stays in front of its window; it is never hidden (that answers it)
        dialog_in_use = modal is not None and modal.isActiveWindow()
        if on_wayland() and self.isVisible() and not dialog_in_use and not self.isActiveWindow():
            self.hide()  # Wayland: a window that may be minimized comes back only by being shown again (D-286)
        self.out_of_sight = False
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        if modal is not None:
            modal.raise_()
            modal.activateWindow()

    def start_minimized(self) -> None:
        """The start at login with the window minimized (D-284): minimized from its first moment; a maximized one stays maximized when opened."""
        self.out_of_sight = on_wayland()  # X11 and Windows say when a window is minimized; Wayland never does
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMinimized)

    def _in_sight(self) -> None:
        """Before the start asks the person something: a window started minimized is opened, so the question is seen."""
        if self.isMinimized() or self.out_of_sight:
            self.open_window()

    @override
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.out_of_sight = False  # the person opened it from the taskbar
        super().changeEvent(event)

    def _settings_from_tray(self) -> None:
        if not self.isVisible():
            self.toggle_visibility()
        self.open_settings()

    def _update_unread_summary(self) -> None:
        if self._section == MAIL:
            self.setWindowTitle(self.folders.title())
        self.tray.set_unread(self.mail_state.total_unread())

    def _notify_new_mail(self, new_by_account: object) -> None:
        """A desktop notification for mail that arrived while the window was not in use. It tells how many, never what."""
        counts: dict[str, int] = {str(account): int(count) for account, count in new_by_account.items()} if isinstance(new_by_account, dict) else {}
        total = sum(counts.values())
        if not total or not self.prefs.read().notifications or self.isActiveWindow():
            return
        names = {account.account_id: account.shown_name for account in self.mail_state.accounts}
        if len(counts) > 1:
            lines = [f"{count} new in {names.get(account, account)}" for account, count in sorted(counts.items()) if count]
            self.tray.notify("New mail", "\n".join(lines))
        else:
            self.tray.notify("New mail", counted(total, "new message"))

    def open_menu(self) -> None:
        """Alt+M: open the Menu button of the section that is shown."""
        for button in self.menu_buttons:
            if button.isVisible():
                button.showMenu()
                return

    def _show_undo(self) -> None:
        """Undo and Redo say what they would do ("Undo Move to Trash (3 messages)"); greyed when there is nothing, and outside Mail.
        Undo stays usable while an action is under way: it waits for it, then undoes it."""
        undoing, mail = self.undoing, self._section == MAIL
        self.act_undo.setText(f"Undo {undoing.undo_label}" if undoing.undo_label else "Undo")
        self.act_redo.setText(f"Redo {undoing.redo_label}" if undoing.redo_label else "Redo")
        self.act_undo.setEnabled(mail and (bool(undoing.undo_label) or undoing.busy))
        self.act_redo.setEnabled(mail and (bool(undoing.redo_label) or undoing.busy))  # pressed while busy it waits in turn, as Undo
        self.act_redo_other.setEnabled(self.act_redo.isEnabled())

    KEY_NAMES: dict[str, str] = {"undo": "Undo", "redo": "Redo", "not_junk": NOT_JUNK}  # their text says what they would do, or shows another
    # action's key; Settings > Keyboard shows only the name

    def _shortcut_registry(self) -> dict[str, tuple[str, str]]:
        """Every action that can be given keys: its name, the text, and the standard keys."""
        return {
            name[4:]: (self.KEY_NAMES.get(name[4:], action.text()), self._default_shortcuts.get(name, ""))
            for name, action in vars(self).items() if name.startswith("act_") and isinstance(action, QAction)
        }

    # --- sync conflicts -------------------------------------------------------------------------------------

    def _conflicts(self) -> SyncConflictsWindow:
        if self._conflicts_window is None:
            self._conflicts_window = SyncConflictsWindow(self.backend, self.sync_marks, self.settings, self)
            self._conflicts_window.panel.sync_requested.connect(self._retry_sync)
        return self._conflicts_window

    def _retry_sync(self, accounts: list[str]) -> None:
        """Retry sync in the Sync conflicts window released refusals: those accounts are checked at once, mail and calendars (the owner)."""
        self.checking.check_accounts(set(accounts), manual=True)

    def open_sync_conflicts(self) -> None:
        """Menu > Sync conflicts… and the entry in the sidebars: the window of its own, brought to the front when it is open already."""
        window = self._conflicts()
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_conflict(self, item: str) -> None:
        """A click on an item's triangle: that conflict's popup at once (what a person would guess, the owner); the window when it has gone."""
        if not self._conflicts().open_conflict(item):
            self.open_sync_conflicts()

    def _show_waiting(self) -> None:
        """The changes that wait for the servers, counted on the right of the status line while there are any (D-194); the Mail entry of the
        sync conflicts; the waiting mark in the message list."""
        count = self.sync_marks.waiting_count
        self.waiting_label.setText(f"Waiting for the server: {counted(count, 'change')}")
        self.waiting_label.setVisible(count > 0)
        self._show_mail_conflicts()
        self.list_view.repaint_all()

    def _show_mail_conflicts(self) -> None:
        marks = getattr(self, "sync_marks", None)  # getattr: the sync marks are made later in the start and asked before they exist
        count = len(marks.conflicts) if isinstance(marks, SyncMarks) else 0
        self.mail_conflicts_entry.setText(f"Sync conflicts ({count})")
        self.mail_conflicts_entry.setVisible(count > 0 and self._section == MAIL)

    # --- settings -----------------------------------------------------------------------------------------------

    def open_settings(self, page: str | None = None) -> None:
        """File > Settings: the way the program behaves, how it looks, writing, the accounts, rules, scheduled messages, the keys and the safety of the mail."""
        dialog = SettingsDialog(self.backend, self.settings, self._settings_context(), self)
        if page is not None:
            dialog.show_page(page)
        dialog.add_account_requested.connect(lambda: self.add_account(after=lambda: dialog.reload() if alive(dialog) else None))  # it may be closed by then
        dialog.accounts_changed.connect(lambda: self.reload_accounts(select_saved=False, then=self.checking.sync_calendars))
        dialog.restore_key_requested.connect(self.restore_key)
        dialog.rebuild_requested.connect(self.rebuilds.rebuild_all)
        dialog.scheduled_panel.new_requested.connect(lambda: self.composing.new_scheduled_message(dialog.scheduled_panel))
        panel = dialog.scheduled_panel

        def edit_requested(info: ScheduledInfo) -> None:
            self.composing.edit_scheduled(info.account, info, panel)

        panel.edit_requested.connect(edit_requested)
        dialog.interface_scale_tried.connect(self.try_interface_scale)
        dialog.windows_scale_tried.connect(self.try_windows_scale)
        dialog.reading_scale_tried.connect(self.try_reading_scale)
        dialog.list_scale_tried.connect(self.try_list_scale)
        dialog.list_settings_tried.connect(self.try_list_settings)
        self.columns.try_in(dialog.columns_picker)
        try:
            self._settings_closed(dialog, dialog.exec() == SettingsDialog.DialogCode.Accepted)
        finally:
            self.prefs.try_out(None)  # never a try left behind
            self.columns.end_try(False)  # nor a column setup (after _settings_closed this does nothing)
            dialog.deleteLater()  # it was kept for good with every opening (about 490 parts each time)

    def _settings_closed(self, dialog: SettingsDialog, accepted: bool) -> None:
        """What the Settings window leaves behind once it is closed: the sizes back on Cancel, what its pages did at once, and on OK the
        settings themselves."""
        if not accepted:  # Cancel: the sizes they had
            if dialog.interface_slider.value() != scale.slider_position(dialog.interface_scale_saved):
                self.try_interface_scale(dialog.interface_scale_saved)
            if dialog.windows_slider.value() != scale.slider_position(dialog.windows_scale_saved):
                self.try_windows_scale(dialog.windows_scale_saved)
            self.try_reading_scale(dialog.reading_scale_saved)
            self.try_list_scale(dialog.list_scale_saved)
        self._tried_interface = self._tried_windows = None  # from now on the saved sizes
        if dialog.rules_panel.ran:  # rules and scheduled messages act at once, also when the settings are cancelled
            self.report.finished("Rules applied")  # its "mailbox changed" fetches the list and the numbers again
        elif dialog.scheduled_panel.changed:
            self.report.finished("Scheduled messages changed")  # each action told its facts: the rows already follow (D-146)
        if dialog.tags_saved:
            self.tags.load()  # the rows follow the "mailbox changed" of renaming or deleting a tag (D-146)
        self.columns.end_try(accepted)  # the columns shown since they were chosen: kept, or put back (B-16, D-228)
        tried = self.prefs.trying is not None
        self.prefs.try_out(None)
        if not accepted:
            if tried:
                self.apply_preferences()  # Cancel: the Message list settings as they were, in the same steps that showed the try
            return
        dialog.save()
        self.apply_preferences()
        self.folders.change_favorites(dialog.sidebar_page.commands())

    def _settings_context(self) -> SettingsContext:
        """What the Settings window shows besides the settings, in one piece."""
        state, model = self.mail_state, self.tree_model
        return SettingsContext(
            tray_available=self.tray.available, shortcut_actions=self._shortcut_registry(), accounts=tuple(state.mail_accounts()),
            folders=state.rule_folders, current_folder=self._rule_folder_now, startup_choices=tuple(self.folders.startup_choices()),
            tags=state.tags, favorites=state.favorites_in_use(), hidden_favorites=frozenset(model.hidden), pins=tuple(self.folders.pin_labels()),
            has_tags=bool(model.tags), local_id=state.local_id(), tree_accounts=tuple(state.tree_accounts()),
            hidden_trees=frozenset(model.hidden_trees), local_folders=tuple(state.local_standard_folders()),
            hidden_local=frozenset(model.hidden_local), columns=self.columns.columns, column_choices=self.columns.picker_setup(),
        )

    def _folder_moved(self, account: str, old: str, new: str) -> None:
        """A folder was renamed or deleted (here or on the server): its column setup goes along or is forgotten (B-16)."""
        if new:
            self.columns.folder_renamed(account, old, new)
        else:
            self.columns.folder_deleted(account, old)

    def _rule_folder_now(self) -> tuple[str, str] | None:
        view = self.mail_state.view
        return (view.account, view.folder) if view.account is not None and view.folder is not None and view.kind == EntryKind.FOLDER else None

    def open_tags(self) -> None:
        """Menu > Tags: make, change, order and delete tags, on the Tags page of the Settings window."""
        self.open_settings("Tags")

    def apply_preferences(self) -> None:
        """Take into use what changed in the settings file since the window last took them in (Settings OK): each group in its
        own step, and nothing for what stayed the same, so OK without a change does nothing at all (R-13)."""
        before, after = self.prefs.take_in()

        def differ(*names: str) -> bool:
            return any(getattr(before, name) != getattr(after, name) for name in names)  # getattr: the settings of a group compared field by field

        self.operations.sync_hide_setting()  # its own tick saves this setting too, so it is compared with the tick: unchanged when they agree
        looks = differ("color_scheme", "accent_dark", "accent_light", "interface_scale", "windows_scale")
        app = QApplication.instance()
        if looks and isinstance(app, QApplication):
            appearance.apply(app, self.settings)  # the colours and the sizes take effect at once
            self._apply_main_font()
            self.setStyleSheet(theme.stylesheet())  # the colours in it come from the palette: ask for them again
            self.report.restyle()  # the red of an error follows the colours
            self._apply_scaled_parts()
            self.reading.restyle_message_windows()
        if differ("tray"):
            self.tray.set_visible(after.tray)
        if differ("auto_check", "auto_check_minutes"):
            self.checking.take_in_auto_check(after.auto_check)
        if differ("conversations"):
            self.act_conversations.setChecked(after.conversations)
        if differ("list_layout", "date_style", "tag_text_colour"):
            self.columns.take_in()  # in place: the rows and the selection stay
        if looks or differ("list_scale", "list_density"):
            self.columns.apply_list_look(after.list_scale if self.prefs.trying is not None else None)  # tried: the size the slider shows
        if differ("reading_pane"):
            self.reading.set_place(after.reading_pane)  # only then: a width the person dragged stays
        if differ("remote_always", "remote_senders", "prefer_plain", "reading_scale"):
            self.reading.take_in_reader_preferences()
            self.composing.set_text_size()
        if differ("shortcuts"):
            self._apply_shortcuts()
        if differ("show_favorites", "show_tags", "show_local_folders") and self.mail_state.accounts:
            self.folders.lay_out()  # the sections of the folder list follow the settings

    def try_list_settings(self, chosen: object) -> None:
        """Settings > Message list changed: shown now without saving, by the same steps as OK (only what differs); Cancel puts it
        back, OK keeps it (D-104). Only the Message list's own settings are tried; the other pages wait for OK."""
        if not isinstance(chosen, Preferences):
            return
        self.prefs.try_out(changed(self.prefs.saved(), list_layout=chosen.list_layout, reading_pane=chosen.reading_pane, date_style=chosen.date_style,
                                   list_density=chosen.list_density, conversations=chosen.conversations, tag_text_colour=chosen.tag_text_colour,
                                   list_scale=chosen.list_scale))  # the size its slider tries too: a Row height redraws the list at it
        self.apply_preferences()

    def try_interface_scale(self, factor: float) -> None:
        """Show the main window at this size factor now, without saving it (the slider in Settings moves, or Cancel)."""
        self._tried_interface = factor
        self._apply_tried_sizes()

    def try_windows_scale(self, factor: float) -> None:
        """Show the other windows and dialogs at this size factor now, without saving it."""
        self._tried_windows = factor
        self._apply_tried_sizes()

    def _apply_tried_sizes(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        appearance.apply(app, self.settings, self._tried_interface, self._tried_windows)  # a size not being tried out is the saved one
        self._apply_main_font()
        self.setStyleSheet(theme.stylesheet())
        self._apply_scaled_parts()
        self.columns.apply_list_look(scale.message_list())  # the list keeps the size it shows (also one being tried out, not saved yet)

    def try_list_scale(self, factor: float) -> None:
        """Show the message list at this size now, without saving it."""
        self.columns.apply_list_look(factor)

    def try_reading_scale(self, factor: float) -> None:
        """Show the text of messages, read and being written, at this size now, without saving it."""
        self.reading.try_reading_scale(factor)
        self.composing.set_text_size(factor)

    def _apply_main_font(self) -> None:
        """The text of this window and of the menus and popups opened from it, at the size of the Main window slider."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        font = appearance.main_font(app)
        self.setFont(font)
        for child in self.findChildren(QWidget):
            if child.isWindow() and child.windowType() == Qt.WindowType.Popup:
                child.setFont(font)  # menus made already; new ones get it when they are first shown (appearance.MainWindowPopups)

    def _fix_minimum_width(self) -> None:
        """One minimum width for every section: 768 px, or what the contents need at a larger size factor (they need
        926 px at 2.00). Fixed, so that Qt never makes the window wider when another section is shown."""
        self.setMinimumWidth(0)  # what the contents need, without the minimum that was set before
        for part in self.findChildren(QLayout):  # parts that are not shown keep their old sizes until asked again
            part.invalidate()
        layout = self.layout()
        needed = layout.totalMinimumSize().width() if layout is not None else 0
        self.setMinimumWidth(max(MINIMUM_WIDTH, needed))

    def _apply_scaled_parts(self) -> None:
        """The sizes the style list does not hold: this window's at its own size factor, the message windows' and the
        composers' at that of the other windows."""
        self.section_bar.apply_scale()
        self.window_toolbar.apply_scale()
        self.tree.setMinimumWidth(px(180))
        QTimer.singleShot(0, self._fix_minimum_width)  # once the new sizes and the style sheet are all in place
        self.reading.apply_scale()
        self.reading.restyle_message_windows()
        self.composing.restyle()

    def _apply_shortcuts(self) -> None:
        chosen = self.prefs.read().shortcuts
        for name, action in vars(self).items():
            if name.startswith("act_") and isinstance(action, QAction) and name in self._default_shortcuts:
                action.setShortcut(QKeySequence(chosen.get(name[4:], self._default_shortcuts[name])))
                self._scope_shortcut(action)
        self._show_not_junk_key()

    def _show_not_junk_key(self) -> None:
        """Not junk shows a key in the menus: its own when the person gave it one, else the junk key, which in Junk brings a message
        back (one key does both). Only shown: the junk key stays the one action that has it, so the key never acts twice."""
        own, junk = self.act_not_junk.shortcut(), self.act_junk.shortcut()
        shown = "" if not own.isEmpty() or junk.isEmpty() else junk.toString(QKeySequence.SequenceFormat.NativeText)
        self.act_not_junk.setText(f"{NOT_JUNK}\t{shown}" if shown else NOT_JUNK)

    def _show_junk_items(self, menu: QMenu) -> None:
        """In Junk the Message menu shows Not junk only (Move to Junk would bring the message back there), as the right-click menu does."""
        in_junk = self.mail_state.in_junk(self.messages.current_row())
        if in_junk and self.act_junk in menu.actions():
            menu.removeAction(self.act_junk)
        elif not in_junk and self.act_junk not in menu.actions():
            menu.insertAction(self.act_not_junk, self.act_junk)

    def _scope_shortcut(self, action: QAction) -> None:
        """A key acts on what has the focus (D-217). A key that is a letter by itself (A, N, S ...) and Toggle flag (Insert) act on
        the selected messages: in the message list and in the reading pane (and everything in it) only; in the folder list a
        letter jumps to a folder, in a text field it is text. Open (Enter) only in the message list (in the reading pane Enter
        follows a link), Rename folder (F2) only in the folder list. Every other key works in the whole window."""
        text = action.shortcut().toString()
        letter = len(text) == 1 and text.isalpha()
        messages = letter or action is self.act_toggle_flag
        folder_only = action is self.act_rename_folder
        list_only = action is self.act_open
        for widget in (self.list_view, self.tree, self.reader):
            widget.removeAction(action)
        if messages:
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.removeAction(action)  # never the window's: the window holds every part, the folder list and the search field too
            self.list_view.addAction(action)
            self.reader.addAction(action)
        elif folder_only or list_only:
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            (self.tree if folder_only else self.list_view).addAction(action)
        else:
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            if action not in self.actions():
                self.addAction(action)  # a key changed in Settings > Keyboard from a letter to another key

    # --- the encryption key and the accounts ----------------------------------------------------------------

    def start(self) -> None:
        """Called once the window is shown: make sure the key is there, load the accounts (or ask for the
        first one), then check for mail."""
        check = self.checking.start_refresh if self.prefs.read().check_on_start else None
        self.messages.load_contact_addresses()
        self.reading.forget_opened_copies()  # the copies of attachments opened in an earlier run go (D-236)

        def ready() -> None:
            self.reload_accounts(select_saved=True, then=check)
            self.checking.learn_existing_junk()  # once per place where mail is kept (D-238)
            self.checking.label_wallet()  # readable labels for the wallet's entries (B-23)

        self._with_key(lambda: self.rebuilds.start(ready))  # every index usable first (D-229)

    def _with_key(self, then: Callable[[], None]) -> None:
        """Continue when the encryption key can be read; otherwise ask for it first."""

        def got(status: KeyStatus) -> None:
            if worth_telling(status) is not None:  # a key missing, or Local Folders written with an older one
                self._ask_for_key(then, status)
            else:
                then()

        def failed(error: ChronoMailError) -> None:
            self.report.report("Could not check the encryption key", error)
            then()

        self.backend.ask(KEY_STATUS, NoParameters(), got, failed, owner=self)

    def _ask_for_key(self, then: Callable[[], None], status: KeyStatus | None = None) -> None:
        self._in_sight()  # a start at login with the window minimized (D-284): the question must be seen, not wait under the taskbar
        dialog = RestoreKeyDialog(self.backend, self, status)  # status: why the key could not be had (None when it could not be asked)
        accepted = dialog.exec() == RestoreKeyDialog.DialogCode.Accepted
        self.backend.engine.forget_keys()
        dialog.tell(self.report, accepted)  # the key is back, or why not (nothing when nothing was missing)
        if accepted or dialog.only_local_folders:  # this computer's key is there: the rest of the mail is opened whatever Local Folders waits for
            then()

    def restore_key(self) -> None:
        """File > Use a recovery code: ask for the key and, when it is back, load everything; the key's status first, so the window says why."""
        self.backend.ask(KEY_STATUS, NoParameters(), lambda status: self._ask_for_key(lambda: self.reload_accounts(select_saved=True), worth_telling(status)), lambda error: self._ask_for_key(lambda: self.reload_accounts(select_saved=True)), owner=self)

    def reload_accounts(self, select_saved: bool = False, then: Callable[[], None] | None = None) -> None:
        def listed(result: tuple[AccountSummary, ...]) -> None:
            self.mail_state.set_accounts(list(result))
            if not self.mail_state.all_accounts:
                self.folders.clear()
                self.add_account(first=True)
                return
            self.folders.load(select_saved, then)
            self.checking.accounts_loaded()

        self.tags.reload()
        self.backend.ask(ACCOUNT_LIST, NoParameters(), listed, lambda error: self.report.report("Accounts", error), owner=self)

    def add_account(self, first: bool = False, after: Callable[[], None] | None = None, kind: str = "pop3", parent: QWidget | None = None) -> None:
        """Add an account of any kind in the one Add window (D-298): from Settings > Accounts, Menu, the first start, or a section's sidebar."""
        self._in_sight()
        without_server = [account for account in self.mail_state.mail_accounts() if not account.dav_url]  # those CalDAV and CardDAV may give a server
        dialog = AccountDialog(
            self.backend, first_account=first, parent=parent or self, restore=self.restores.start, mail_accounts=without_server, start_kind=kind,
        )  # Restore a backup… (W2)
        if dialog.exec() != AccountDialog.DialogCode.Accepted:
            return
        show_code = dialog.show_recovery_code
        if dialog.calendars_added:

            def synchronised() -> None:
                if show_code:
                    RecoveryDialog(self.backend, self).exec()
                if after is not None:
                    after()
                self.checking.sync_calendars()

            self.reload_accounts(select_saved=False, then=synchronised)
            return
        if dialog.created_account_id:
            self._find_calendars(dialog.created_account_id)

        def loaded() -> None:
            if show_code:
                RecoveryDialog(self.backend, self).exec()
            if after is not None:
                after()
            self.checking.check_mail()

        self.reload_accounts(select_saved=False, then=loaded)

    def add_calendar_account(self, kind: str) -> None:
        """Add account… in the Calendar, Contacts and Tasks sidebars: the one Add window on CalDAV (Calendar, Tasks) or CardDAV (Contacts), for a
        mail account that has no calendar server or an account of calendars, contacts and tasks only (D-185, D-298)."""
        self.add_account(kind=kind, parent=QApplication.activeModalWidget() or self)

    def _find_calendars(self, account_id: str) -> None:
        """After an account was added: look for its calendars, task lists and address books in the background. When there are any, the person ticks
        which to keep in step; when there are none, the account is mail only and nothing is asked (D-181)."""

        def found(result: FoundServer) -> None:
            if not result.folders:
                return
            dialog = FoundFoldersDialog(result, QApplication.activeModalWidget() or self)
            if dialog.exec() != FoundFoldersDialog.DialogCode.Accepted or not dialog.any_ticked():
                return

            def linked(_summary: AccountSummary) -> None:
                self.reload_accounts(select_saved=False, then=self.checking.sync_calendars)

            parameters = LinkParameters(account_id, result.url, dialog.left_out(), result.pin)
            self.backend.ask(DAV_LINK, parameters, linked, lambda error: self.report.report("Calendars and contacts could not be added", error), owner=self)

        self.backend.ask(DAV_FIND, FindParameters(account_id), found, lambda _error: None, owner=self)  # nothing found: the account is mail only

    def show_import(self) -> None:
        """File > Import: accounts, rules, contacts and mail; the folder list is loaded again after each import."""
        ImportDialog(self.backend, self._after_import, self, on_running=self.checking.import_running, memory=self.memory).exec()

    def show_export(self) -> None:
        """File > Export: data files, accounts and rules for another program; nothing here changes."""
        ExportDialog(self.backend, self, memory=self.memory).exec()

    def _after_import(self) -> None:
        """New accounts, folders and mail: the folder list with its counts, then the list on screen (its selection did not change, so it would keep the old messages)."""
        prefs = self.prefs.read()
        check = prefs.check_on_start or prefs.auto_check  # send and receive only when the owner lets it run by itself (at startup or at an interval)
        self.reload_accounts(select_saved=True, then=self.checking.check_mail if check else None)  # the checks that waited for the import: once, with the accounts it made

    # --- an entry of the folder list is opened ---------------------------------------------------------------------

    def _open_entry(self, chosen: object) -> None:
        """Another entry of the folder list: its view opens in one step, and the buttons, the filters, the search field, the
        reading pane, the window title, the sort menu and the columns follow it before the list asks for it."""
        if not isinstance(chosen, CurrentView):
            return
        self.messages.open(chosen)
        if chosen.account is None:  # an account without an Inbox: an empty list that says so
            self.act_empty_folder.setEnabled(False)
            self._show_not_junk()
            self.act_mark_all_read.setEnabled(False)
            self.act_delete_folder.setEnabled(False)
            self.act_rename_folder.setEnabled(False)
            self.reading.clear()
            self.columns.follow_view()  # nothing shown: no folder whose setup Apply to all could give, so it is greyed
            self.messages.show_empty()
            self._update_unread_summary()
            return
        folder_shown, role = chosen.kind is EntryKind.FOLDER, chosen.folder_role
        self.act_empty_folder.setEnabled(folder_shown and role in ("trash", "junk"))
        own_folder = folder_shown and role == "other" and self.folders.chosen_row_is_folder()  # never a top row, which shows its Inbox
        self.act_delete_folder.setEnabled(own_folder)  # a standard folder, known by its role, is never deleted (CR-36)
        self.act_rename_folder.setEnabled(own_folder)  # nor renamed (D-269)
        self.act_new_folder.setEnabled(self.mail_state.can_make_folder())
        self.act_empty_folder.setText(EMPTY_TEXTS.get(role, "Empty folder…") if folder_shown else "Empty folder…")
        self._show_not_junk()
        self._show_archive_action()
        self.act_mark_all_read.setEnabled(folder_shown)
        self.filters.show_state()
        self.search.show_state()
        self.search.ended()
        self.reading.clear()
        self._update_unread_summary()
        self.messages.update_sort_menu()
        self.columns.follow_view()
        self.messages.apply_view()

    def _show_archive_action(self, *_: object) -> None:
        """The Archive button, key and menu item are Unarchive when every selected message is archived, wherever they are shown (D-101)."""
        archived = self.mail_state.all_archived(self.messages.each_selected())  # stops at the first that is not archived
        if self.act_archive.text() != (text := "Unarchive" if archived else "Archive"):  # only a real switch: a new icon always counts as a change and lays the toolbar out again (D-266)
            self.act_archive.setText(text)
            self.act_archive.setIconText(text)
            self.act_archive.setIcon(theme_icon("archive-extract" if archived else "archive-insert"))

    # --- the sections: mail, calendar, tasks, contacts ---------------------------------------------------------

    def _update_message_actions(self, *_: object) -> None:
        """Grey out what needs a selected message while none is selected (in the mail section; Delete also serves the other sections)."""
        if self._section != MAIL:
            return
        selected = self.messages.has_selection()
        for name in self.MESSAGE_ACTIONS:
            getattr(self, f"act_{name}").setEnabled(selected)  # getattr: the window's own actions, named in MAIL_ONLY
        self.act_delete.setEnabled(selected)
        self._show_not_junk()
        self.mark_button.setEnabled(selected)
        self.tag_button.setEnabled(selected)
        self.move_button.setEnabled(selected)

    def _show_not_junk(self) -> None:
        """Not junk can be clicked only where it acts: messages selected in the Junk folder or in Favorites > Junk (where the junk key
        brings them back too), never in a view without an account or with nothing selected."""
        view = self.mail_state.view
        self.act_not_junk.setEnabled(self.mail_state.in_junk(self.messages.current_row()) and view.account is not None and self.messages.has_selection())

    @property
    def section(self) -> str:
        return self._section

    def _in_section(self, what: str) -> None:
        """File > New, Refresh, Delete and Search do what the section that is shown needs."""
        page = self.pages.get(self._section)
        if page is not None:
            {"new": page.new_item, "refresh": page.refresh_from_servers, "delete": page.delete_selected, "search": page.focus_search}[what]()
            return
        {"new": self.composing.new_message, "refresh": self.checking.send_and_receive, "delete": self.mail_actions.delete_selected, "search": self.search.focus}[what]()

    def switch_section(self, section: str) -> None:
        """Show another section: its sidebar, its main area and its toolbar. The mail-only actions are off while another section is shown."""
        if section not in {name for name, *_ in SECTIONS} or section == self._section:
            self.section_bar.show_section(self._section)  # the button of the section that is shown stays pressed
            return
        if self._section == MAIL and section != MAIL:
            self._mail_action_states = {name: getattr(self, f"act_{name}").isEnabled() for name in self.MAIL_ONLY if hasattr(self, f"act_{name}")}  # getattr: the window's own actions, named in MAIL_ONLY
            for name in self._mail_action_states:
                getattr(self, f"act_{name}").setEnabled(False)  # getattr: the window's own actions, named in MAIL_ONLY
        elif self._section != MAIL and section == MAIL:
            for name, enabled in self._mail_action_states.items():
                getattr(self, f"act_{name}").setEnabled(enabled)  # getattr: the window's own actions, named in MAIL_ONLY
        came_from = self._section
        self._section = section
        if section == MAIL:
            self._update_message_actions()
            if came_from == CONTACTS:
                self.messages.load_contact_addresses()  # contacts may have been added or changed
        else:
            self.act_delete.setEnabled(True)  # Delete of the calendar, contacts and tasks
        self._show_undo()
        index = 0 if section == MAIL else 1 + list(self.pages).index(section)
        self.side_stack.setCurrentIndex(index)
        self._show_mail_conflicts()
        self.content_stack.setCurrentIndex(index)
        self.window_toolbar.show_row(self.toolbar if section == MAIL else self.pages[section].toolbar)  # one step: the window keeps its size
        self.section_bar.show_section(section)
        self.side_heading.setText(next(name for key, name, *_ in SECTIONS if key == section))
        page = self.pages.get(section)
        if page is not None:
            page.on_show()
        self.memory.last_section = section
        if page is not None:
            self.setWindowTitle("Chrono Mail" if section == MAIL else f"Chrono Mail — {dict((name, label) for name, label, *_ in SECTIONS)[section]}")
        else:
            self._update_unread_summary()

    # --- the message list's menus and clicks ---------------------------------------------------------------------

    def open_selected(self) -> None:
        """Enter or a double click: edit a draft, or read a message in a window of its own."""
        if self.mail_state.in_drafts(self.messages.current_row()):  # in a search of several folders: as in the message's own folder (D-287)
            self.composing.edit_draft()
        else:
            self.reading.open_message_window()

    def _row_double_clicked(self, index: QModelIndex) -> None:
        self.open_selected()

    def _icon_clicked(self, row: int, column_id: str) -> None:
        """A click on the Delete icon deletes that message; a click on the Contact icon opens the contact."""
        message = self.list_model.row_at(row)
        if message is None:
            return
        if column_id == "trash":
            self.mail_actions.delete_by_icon(message)
            return
        found = self.list_model.index(row, 0).data(CONTACT_ROLE)
        if column_id == "contact" and isinstance(found, tuple) and len(found) == 2:
            self.contacts_page.show_contact(str(found[0]), str(found[1]))
            self.switch_section(CONTACTS)

    def edit_columns(self) -> None:
        """Message list…: layout, columns, reading pane, date style and row height."""
        density = self.prefs.read().list_density
        dialog = ColumnsDialog(ListChoice(
            self.columns.layout, self.columns.columns, self.reading.place, self.columns.date_style, density,
        ), self, self.columns.picker_setup())
        dialog.list_settings_tried.connect(self._try_list_choice)
        self.columns.try_in(dialog.picker)
        accepted = False
        try:
            accepted = dialog.exec() == ColumnsDialog.DialogCode.Accepted
        finally:
            tried = self.prefs.trying is not None
            self.prefs.try_out(None)  # never a try left behind
            self.columns.end_try(accepted)
            if accepted:
                self.apply_list_choice(dialog.choice())
            elif tried:
                self.apply_preferences()  # Cancel: Layout, Reading pane, Dates and Row height as they were, as Settings does

    def _try_list_choice(self, choice: object) -> None:
        """The Message list window changed its Layout, Reading pane, Dates or Row height: shown at once, as Settings > Message list does (D-104)."""
        if isinstance(choice, ListChoice):
            self.try_list_settings(changed(
                self.prefs.saved(), list_layout=choice.layout, reading_pane=choice.reading_pane, date_style=choice.date_style, list_density=choice.density,
            ))

    def apply_list_choice(self, choice: ListChoice) -> None:
        self.columns.choose(choice.layout, choice.columns, choice.date_style, choice.density)
        self.reading.set_place(choice.reading_pane)

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        state = self.mail_state
        clicked = self.list_view.indexAt(position)
        row = self.list_model.row_at(clicked.row()) if clicked.isValid() else None  # in a search of several folders the menu follows this message (D-287)
        scheduled = state.in_scheduled_view(row)
        if scheduled and not self.list_view.indexAt(position).isValid():  # the empty area: only a new scheduled message
            menu.addAction(self.act_new_scheduled)
            menu.exec(self.list_view.viewport().mapToGlobal(position))
            return
        if state.in_drafts(row):
            menu.addAction(self.act_edit_scheduled if scheduled else self.act_edit_draft)
            menu.addSeparator()
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            menu.addAction(action)
        menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            menu.addAction(action)
        self.tags.fill_tag_menu(menu.addMenu(tag_icon(QColor(NEW_TAG)), "Tag"))
        menu.addSeparator()
        self.mail_actions.fill_move_menu()
        menu.addMenu(self.move_menu).setText("Move to")
        menu.addAction(self.act_archive)
        menu.addAction(self.act_snooze)
        if state.in_snoozed(row):
            menu.addAction(self.act_cancel_snooze)
        self._show_not_junk()  # as the message the menu is for: the click made it the current one
        menu.addAction(self.act_not_junk if state.in_junk(row) else self.act_junk)
        menu.addAction(self.act_delete)
        menu.addSeparator()
        for action in (self.act_save_as, self.act_print, self.act_source):
            menu.addAction(action)
        menu.exec(self.list_view.viewport().mapToGlobal(position))

    def show_status(self, text: str, error: bool = False) -> None:
        self.report.show(text, error)

    def _about(self) -> None:
        self.updates.about()
