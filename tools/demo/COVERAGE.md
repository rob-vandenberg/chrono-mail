# What the demo shows

The demo profile (`./demo.sh`, D-243) is built so that every action and setting of Chrono Mail can be shown with made-up data. This list follows the program's own commands (177, by area) and the pages of Settings, and says what in the demo shows each. The last part lists what the demo cannot show, because it has no real server.

## Mail

| Area | What can be shown | In the demo |
|---|---|---|
| Reading (message show, text, source, thread, attachments open and save) | The reading pane, the message window, View source, conversations, attachments | 100,000 messages over five years; replies threaded by References; photos (PNG), PDFs (briefs, bills, tickets), a spreadsheet (CSV); HTML newsletters with their own look |
| The list (message list, order, rows, groups, position, places) | Columns and cards, sorting and grouping, Received and Sent, sizes, unread and flagged marks, the counts | Every folder of 14 accounts; unread mail in the last days, about 2% flagged, replied marks on answered mail |
| Favorites and combined views (message combined …, favorite pin, rename, reorder, show, unpin) | All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Unreplied, Snoozed, Scheduled, Archive, Junk, Trash; pinned folders | Mail in every one of them; Clients/Bluebird Bakery (Work) and Holidays (Personal) pinned |
| Groups of accounts (group add, assign, rename, remove) | Accounts in a named group with its own All Inboxes | The group "Clubs": Riverside Book Club and Green Street Garden |
| Sidebar (sidebar show, show-tree, show-local-folder) | Settings > Sidebar, the right-click menu of Favorites | Two POP3 accounts with their own tree, eight in Local Folders |
| Search (message search, Advanced search, the Filter menu) | Words, from:, to:, subject:, tag:, dates and sizes, Flagged, Unread, With attachments, Tagged with | Named people, subjects that repeat over the years, attachments, tags |
| Writing (message compose, draft save and open, mail send) | The composer, rich text, replies with the quote, signatures, address completion | Signatures on Work and Freelance studio; drafts in Work, Personal and Freelance studio; 250 correspondents to complete |
| Delayed sending and the Outbox (mail send-outbox, cancel-send, end-countdown) | The notice with Send now and Cancel, the Outbox, the question on Quit | A reply waiting in the Outbox of Freelance studio (Send now fails: its server does not exist) |
| Scheduled and repeating messages (mail scheduled, edit-scheduled, unschedule, repeat pause, resume, change, cancel, send-due) | Settings > Scheduled messages, the Scheduled folder, the Send at column | A monthly invoice (Freelance studio), a work party reminder every two weeks (Green Street Garden), a birthday wish (Personal) |
| Snooze (message snooze, snoozed, wake) | The Snoozed folder, the Snoozed until column, Cancel snooze | Five messages coming back over the next two weeks |
| Moving and archiving (move, copy, archive, unarchive, the …-many forms) | Move to, Copy to, drag and drop, Archive and Unarchive | Archives of older mail that know where each message came from; folders per client, project and holiday |
| Undo and Redo (mail undo, redo, undo-labels) | Ctrl+Z after any action on mail, the name in Menu > Message | Any action on the demo's mail |
| Junk (message junk, not-junk, junk learn-existing) | Junk and Not junk, the junk filter, the status line's count | Over 5,000 spam messages over five years, some marked by the server; the filter has learned from them |
| Rules (rule add, edit, enable, move, remove, run) | Settings > Rules, Run now | Two spam rules, eight rules moving each Local Folders account's mail into its folder |
| Tags (tag create, edit, colour, rename, delete, list; message tag) | Settings > Tags, the Tag button, the tag colours in the list, Tags in the folder list | Important, Clients, Family, Receipts, To read, Travel, with colours, on mail of every kind |
| Folders (folder create, rename, delete, empty, mark-read; local enable) | New folder, Rename, Delete, Empty Trash and Junk, Mark all as read | Standard and own folders in every account, Trash and Junk with mail |
| Accounts (account add, edit, list, remove, policy, set-storage, folders, set-folders) | Settings > Accounts, Add and Edit with servers, ports and security, Account name, Your name, the folder ticks, Local Folders or own folders | 4 IMAP and 10 POP3 accounts, each with servers on example domains and a stored password |

## Calendar, contacts and tasks

| Area | What can be shown | In the demo |
|---|---|---|
| Calendars (event add, edit, delete, move, show, list, import, export) | Day, Work Week, Week, Upcoming, Month, Agenda; the event editor; reminders; repeating events; colours | 500 entries from five years back to one year ahead: birthdays and holidays every year, a weekly studio meeting, weekly tennis, a monthly book club, appointments, trips of several days |
| Collections (collection create, edit, delete, list, accounts, set-palette; pim enable) | Calendars per account, their colours, the Event colours window, read-only calendars | On this computer: Home, Birthdays, Holidays; on servers: Work, Family, Book club; "Team holidays (Mark Ellis)", a colleague's read-only calendar shared under two IMAP accounts |
| Tasks (task add, edit, complete, reopen, delete, move, list, places, import, export) | All, Upcoming, Completed; due dates, priorities, progress | 100 tasks in Personal and Studio tasks: done, overdue, in progress, upcoming |
| Contacts (contact add, edit, delete, move, show, list, addresses, suggest, import, export) | The list by family name, Detail and History, birthdays, addresses | 100 contacts in Personal contacts and Studio contacts, with phones (the drama range), companies, titles, birthdays, addresses |
| Contact groups (contact-group create, rename, delete, add-members, remove-members, addresses, list) | Groups under their address book, Write an e-mail to a group | Family, Book club, Garden volunteers, Tennis, Design team, Clients |
| Tags on items (tag colour) | Tags with colours on events, tasks and contacts | Important, Family, Clients, Travel, Health, Errands |

## Settings

Every page of Settings (General, Appearance, Sidebar, Message list, Reading, Writing, Accounts, Rules, Tags, Scheduled messages, Operations window, Security and storage, Keyboard) works in the demo, with the demo's own settings file (never yours). Automatic refresh and the refresh at start are off, because the demo's servers do not exist; switch them on only to show the setting.

| Settings | In the demo |
|---|---|
| Security and storage: the key (key status, export-file, recovery-code, restore-code, restore-file), Rebuild and Repair the search index (index …), Check the stored messages (store verify, recover) | The demo's own key, in the wallet like a real one; indexes of 100,000 messages |
| Keyboard | Every key can be changed and tried on the demo's mail |

## What the demo cannot show

The demo has no real server, so whatever needs one answers with a connection error:

1. Refresh, Send and Receive, Send now and Test connection (mail fetch, mail send to a server, account check, mail wait): the Operations window then shows errors, not a normal send and receive.
2. Server certificates (certificate inspect, account trust-certificate, forget-certificates).
3. Finding calendars and contacts on a server (dav discover, find, find-new, link, sync, account add-dav, set-dav).
4. Sync conflicts (conflict list, settle; mail settle, sync waiting): the demo has none. One can be made on purpose when a screenshot needs it.
5. Importing from another mail program (import emclient …): it needs that program's export files.
