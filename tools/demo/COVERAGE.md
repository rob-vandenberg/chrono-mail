# What the demo shows

The demo profile (`./demo.sh`, D-243) is built so that every action and setting of Chrono Mail can be shown with made-up data. This list follows the program's own commands (177, by area) and the pages of Settings, and says what in the demo shows each. The last part lists what the demo cannot show, because it has no real server.

## Mail

| Area | What can be shown | In the demo |
|---|---|---|
| Reading (message show, text, source, thread, attachments open and save) | The reading pane, the message window, View source, conversations, attachments | 100,000 messages over five years; replies threaded by References; photos (PNG), PDFs (briefs, bills, tickets), a spreadsheet (CSV); HTML newsletters with their own look |
| The list (message list, order, rows, groups, position, places) | Columns and cards, sorting and grouping, Received and Sent, sizes, unread and flagged marks, the counts | Every folder of 14 accounts; unread mail in the last days, about 2% flagged, replied marks on answered mail |
| Favorites and combined views (message combined …, favorite pin, rename, reorder, show, unpin) | All Messages first, All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Unreplied, Snoozed, Scheduled, Archive, Junk, Trash; pinned folders | Mail in every one of them; All Messages shown (D-334); Clients/Bluebird Bakery (Work) and Holidays (Personal) pinned |
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
| Tags (tag create, edit, colour, rename, delete, list; message tag; the Tags block, Show everything tagged…) | Settings > Tags with its Show in ticks and shortcuts, the Tag button, the tag colours in the list, the Tags block below the folder tree with counts, the Tag overview | One list of ten tags (D-332): Important, Clients and Family in every section with Ctrl+Alt+1 to 3; Travel, Receipts (mail only), To read, Health, Errands, Book club, Garden, each shown only where it is used. Mail carries Important, Clients, Family, Receipts, To read, Travel and Book club |
| Folders (folder create, rename, delete, empty, mark-read; local enable) | New folder, Rename, Delete, Empty Trash and Junk, Mark all as read | Standard and own folders in every account, Trash and Junk with mail |
| Accounts (account add, edit, list, remove, policy, set-storage, folders, set-folders) | Settings > Accounts, Add and Edit with servers, ports and security, Account name, Your name, the folder ticks, Local Folders or own folders | 4 IMAP and 10 POP3 accounts, each with servers on example domains and a stored password |

## Calendar, contacts and tasks

| Area | What can be shown | In the demo |
|---|---|---|
| Calendars (event add, edit, delete, move, show, list, cancel, restore, import, export) | Day, Work Week, Week, Upcoming, Month, Agenda; the event editor; reminders; repeating events; cancelled events in grey | 500 entries from five years back to one year ahead (birthdays and holidays every year, a weekly studio meeting, weekly tennis, a monthly book club, appointments, trips of several days) and a full coming week counted from the day the demo is built: client calls, a presentation, reviews, the dentist, a dinner, a car service, a school concert, a work party, deadlines of whole days, weekly yoga, swimming and football; reminders on many; a print check that was cancelled (D-317) and one cancelled date of the weekly yoga |
| Tags give appointments their colour (D-329) | The colour tag first, the other tags as small tags at the end of the line, the Tags filter | Most appointments carry a tag; "Presentation for Bluebird Bakery" has Clients (its colour) and Important, the school concert Family and Important |
| Collections (collection create, edit, delete, list, accounts, set-palette; pim enable) | Calendars per account, their colours, read-only calendars, the blue plus with Use for new appointments (D-326) | On this computer: Home, Birthdays, Holidays; on servers: Work, Deadlines (Work account), Family, Kids' activities (Personal), Book club; "Team holidays (Marcus Elden)", a colleague's read-only calendar shared under two IMAP accounts. New appointments go to Work, new tasks to Personal (the blue plus) |
| Invitations (invitation answer, choices, add, the card above a mail, Show declined) | The card with Accept, Tentative, Delegate, Decline; Change my answer; an answer to one's own meeting; a calendar file of several items with Add all | In the Work Inbox: "Kick-off: spring catalogue" not answered yet; "Tasting and photo planning at Bluebird Bakery" accepted (in Work, the mail marked Replied); "Studio quiz night" declined (in Work, hidden until Show declined); a colleague's Accepted answer to "Website relaunch: planning", the person's own meeting with three attendees; "Studio closing days" with closing-days.ics, four days of next year. Each a text/calendar part with its method, written by Chrono Mail's own calendar code |
| Tasks (task add, edit, complete, reopen, delete, move, list, places, import, export) | All, Upcoming, Completed; due dates, priorities, progress; reminders (D-316) | 108 tasks in Personal, Shopping list and Studio tasks: done, overdue, in progress, upcoming; four with reminders, before the due time or at a set time without a due date |
| Contacts (contact add, edit, delete, move, show, list, addresses, suggest, import, export) | The list by family name, Detail and History, birthdays, addresses, a tick per address book (D-334) | 100 contacts in three address books: Personal contacts (here), Studio contacts (Work's server) and Members (the book club's server), with phones (the drama ranges), companies, titles, birthdays, addresses; the health centre and the dentist |
| Contact groups (contact-group create, rename, delete, add-members, remove-members, addresses, list) | Groups under their address book, Write an e-mail to a group | Family, Garden volunteers, Tennis (Personal contacts), Design team, Clients (Studio contacts), Book club (Members) |
| Tags on items | Tags with colours on events, tasks and contacts, each section's Tags block with counts | Events: Important, Clients, Family, Travel, Health, Errands, Book club, Garden; tasks: Important, Clients, Family, Travel, To read, Health, Errands, Garden; contacts: Important, Clients, Family, Health, Book club, Garden |
| Sync conflicts (conflict list, settle; Menu > Sync conflicts…, the entry above the sidebar) | The two sides field by field, Use Chrono Mail, Use Server data, Keep both | Two conflicts made as a synchronisation records them (D-194, D-308): "Photo shoot: Copper Kettle Café" moved here to an hour later, on the server half an hour earlier and to the café; a client's card in Studio contacts whose phone number was changed on both sides, with a new job title on the server |

## Settings

Every page of Settings (General, Appearance, Sidebar with its Mail, Calendar, Contacts and Tasks tabs, Message list, Reading, Writing, Accounts, Rules, Tags, Scheduled messages, Sync conflicts, Operations window, Security and storage, Keyboard) works in the demo, with the demo's own settings file (never yours). Automatic refresh and the refresh at start are off, because the demo's servers do not exist; switch them on only to show the setting.

| Settings | In the demo |
|---|---|
| Security and storage: the key (key status, export-file, recovery-code, restore-code, restore-file), Rebuild and Repair the search index (index …), Check the stored messages (store verify, recover) | The demo's own key, in the wallet like a real one; indexes of 100,000 messages |
| Keyboard | Every key can be changed and tried on the demo's mail |

## What the demo cannot show

The demo has no real server, so whatever needs one answers with a connection error:

1. Refresh, Send and Receive, Send now and Test connection (mail fetch, mail send to a server, account check, mail wait): the Operations window then shows errors, not a normal send and receive.
2. Server certificates (certificate inspect, account trust-certificate, forget-certificates).
3. Finding calendars and contacts on a server (dav discover, find, find-new, link, sync, account add-dav, set-dav).
4. Settling a sync conflict: the demo's two conflicts show in the Sync conflicts window (Menu > Sync conflicts…) with every choice, but Use Chrono Mail and Keep both try to reach the server and end in a connection error; mail sync conflicts (mail settle) the demo has none.
5. Importing from another mail program (Menu > File > Import…): it needs that program's export files.
6. Sending an answer to an invitation: Accept, Decline and the others work in the demo's calendars, but a mail to the organizer, or the write to the calendar server first, ends in a connection error.
7. Google accounts (Gmail, and Google Calendar and Contacts through Google sign-in, D-335): they work only with Google's own servers, a real address on Google and a sign-in with Google, so the demo has none; it would need a real address, which the demo never has.
8. The eleven starting tags of a first start (D-333): the demo is the profile of someone who uses Chrono Mail for years, so it has her own ten tags instead. A new empty profile (chrono-mail --profile with a new folder) shows the starting tags.
