# Getting started

This page takes you through the first start of Chrono Mail: adding your first account, keeping your recovery code, and finding your way around the window.

[Back to the user guide](README.md)

## Starting Chrono Mail

Open your applications menu and choose **Chrono Mail**. You find it among the internet or office programs, or by typing its name.

## Your first account

At the first start, Chrono Mail welcomes you with the window **Add an account**. Fill it in from top to bottom.

### Your account

1. **Account type**: how Chrono Mail works with your mail.
   1. Choose **IMAP** if you also read your mail on your phone, on another computer or on your provider's website. Your mail stays with your provider, and Chrono Mail keeps it in step: what you read, move or delete here, you see everywhere.
   2. Choose **POP3** if you want your mail downloaded to this computer. Then Chrono Mail keeps your mail here.
   3. Not sure? Choose IMAP.
2. **Account name**: a name for the account, only for you, such as "Work" or "Home". You can leave it empty; Chrono Mail then shows your e-mail address.
3. **Your name**: the name people see when you write to them.
4. **E-mail address**: your full address, such as anna@example.com.

As soon as you type your address, Chrono Mail fills in a first guess for the servers and the user name below. Often that guess is right; if not, change it.

### Incoming mail and outgoing mail

These two boxes tell Chrono Mail where to fetch your mail and where to send it. Your provider lists these details on its help pages, usually under a name like "settings for mail programs", "IMAP settings" or "SMTP settings".

1. **Server**: the name of your provider's mail server, such as mail.example.com.
2. **Security**: keep the choice your provider gives. **TLS** is the most common; never choose **None** unless your provider says so.
3. **Port**: the number box under Security. Chrono Mail fills in the usual number for the security you chose, so you rarely need to change it.
4. **User name** and **Password**: what you use to log in to your mail. The user name is often your e-mail address.
5. **Use the same user name and password as for incoming mail**: leave this ticked, unless your provider gave you a separate login for sending.

Your password is kept in your computer's password store (on KDE: KDE Wallet; on GNOME: Passwords and Keys), never in a plain file.

### Options

1. **Server copies** (POP3 only): whether your provider keeps a copy of your mail after Chrono Mail has downloaded it. "Leave all mail on the server" is the safe choice.
2. **Keep the mail in** (POP3 only): in a mailbox of its own for this account, or in Local Folders together with your other POP3 accounts.
3. **Encrypt the stored mail on this computer (recommended)**: leave this ticked. Your mail on this computer is then locked, so that only you can read it.

### Test, then add

1. Click **Test connection**. Chrono Mail tries to reach both servers and shows the result for Incoming and Outgoing.
2. If both are fine, click **Add account**.

If the test fails, check the server names, the security and your password against your provider's help pages, and test again.

### If the server's certificate is questioned

A mail server shows a certificate to prove who it is. If Chrono Mail cannot confirm it, the test says so and the button **Look at the server's certificate…** appears. It shows who the certificate was issued to and by whom.

Only click **Accept this certificate** when you know the server belongs to you or your provider (for example, your own company's server). If you are not sure, click **Cancel** and ask your provider.

## Your calendars and contacts

After adding the account, Chrono Mail looks for calendars, contacts and task lists that belong to it on your provider's server. When it finds them, the window **Additional folders found** lists them with a tick each. Tick the ones you want, and Chrono Mail keeps them in step with your provider.

If your provider has no calendars or contacts, nothing appears, and you can simply carry on.

## Your recovery code

Next comes the window **Back up your encryption key**. It shows your recovery code.

Your stored mail is locked with a key, and that key is kept in your computer's password store. If the password store is ever lost (for example after reinstalling your computer), the recovery code is the only way to unlock your mail again.

1. Click **Copy the code** and keep it in your password manager, or write it down and keep it somewhere safe, away from this computer.
2. Or click **Save a recovery file…** to keep it as a file, protected by a passphrase you choose. Keep that file somewhere else than on this computer, such as a USB stick.
3. Tick **I have stored the recovery code (or a recovery file) in a safe place**, and click **Done**.

Treat the recovery code like a password: anyone who has it can unlock your mail.

You can make a new backup at any time: **Menu > File > Back up the encryption key…**.

## Finding your way around

### The four sections

Chrono Mail has four sections: **Mail**, **Calendar**, **Contacts** and **Tasks**. Switch between them with the square buttons at the bottom left of the window, or with the keys Ctrl+1, Ctrl+2, Ctrl+3 and Ctrl+4.

### The Mail section

1. **The folder list** on the left: Favorites at the top (such as All Inboxes and Unread), then each account with its folders (Inbox, Sent, Drafts, Trash and the others).
2. **The message list** in the middle: the messages of the folder you clicked.
3. **The reading pane** on the right: the message you clicked in the list.

### The toolbar and the Menu

1. Across the top is the toolbar, with the actions you use most: New, Reply, Delete and the others. When the window is narrow, some buttons fold into **More**.
2. **Menu**, at the top left, holds everything else, including **Settings…** and **Accounts…**.
3. **Refresh** (or the key F5) sends what is waiting in your Outbox and fetches your new mail. Chrono Mail also does this by itself every few minutes.

## Adding more accounts

To add another account later, open **Menu > Accounts…** and click **Add…**. The same Add an account window opens.

[Back to the user guide](README.md)
