# Chrono Mail

Chrono Mail is a free e-mail program for your computer. It keeps your mail, your calendar, your contacts and your tasks together in one window.

It works with the mail account you already have: the address and password your provider gave you are all it needs. Your mail is stored safely on your computer, locked so that only you can read it, and Chrono Mail stays quick even with hundreds of thousands of messages.

Chrono Mail is made for Linux. A version for Windows is on its way.

## What you can do with it

1. Read, write and send e-mail from one or more accounts.
2. Keep your mail in order with folders, Favorites, tags, the archive and snooze.
3. Let the junk filter put spam aside for you.
4. Find any message quickly with the search field.
5. Keep your calendar, contacts and tasks next to your mail, and in step with your provider's.
6. Keep working without an internet connection: what you do is sent as soon as you are back online.
7. Bring your mail, contacts and calendar over from another mail program.

## Install

### The easy way

1. Open the [download page](https://github.com/rob-vandenberg/chrono-mail/releases/latest).
2. Download the file for your computer:
   1. Ubuntu, Kubuntu, Linux Mint, Debian: the file that ends in **.deb**.
   2. Fedora, openSUSE: the file that ends in **.rpm**.
3. Double-click the downloaded file. Your software centre opens (Discover on KDE, Software on GNOME).
4. Click **Install**, and type your computer password when asked.

That is all. Chrono Mail is now in your applications menu.

If double-clicking the file does not open a software centre, use the terminal way below.

### If you prefer the terminal

Open a terminal in the folder where the file was downloaded, and type the line for your system (the version number in the file name may be different):

Ubuntu, Kubuntu, Linux Mint, Debian:

```bash
sudo apt install ./chrono-mail_2.0.297_amd64.deb
```

Fedora:

```bash
sudo dnf install ./chrono-mail_2.0.297_x86_64.rpm
```

openSUSE:

```bash
sudo zypper install ./chrono-mail_2.0.297_x86_64.rpm
```

### Updating

Download the newer file and install it the same way. Your mail and settings stay as they are.

## Uninstall

### The easy way

1. Open your software centre (Discover on KDE, Software on GNOME).
2. Go to **Installed**.
3. Find **Chrono Mail** and click **Uninstall**.

### If you prefer the terminal

Ubuntu, Kubuntu, Linux Mint, Debian:

```bash
sudo apt remove chrono-mail
```

Fedora:

```bash
sudo dnf remove chrono-mail
```

openSUSE:

```bash
sudo zypper remove chrono-mail
```

### Your mail stays

Uninstalling removes the program only. Your mail, your settings and the key that unlocks your mail stay on your computer, so if you install Chrono Mail again later, everything is there as you left it.

Your mail and settings are in these two folders in your home folder (they are hidden; press Ctrl+H in the file manager to see them):

1. `.local/share/chrono-mail`: your mail, calendars, contacts and tasks.
2. `.config/chrono-mail`: your settings.

If you want to remove everything, delete those two folders. Mail that exists only on this computer is then gone for good, so only do this when you are sure you no longer need it.

## The first start

1. Chrono Mail welcomes you and asks for your first e-mail account: your name, your e-mail address and your password, and the server details from your provider.
2. Choose **IMAP** if you also read your mail on your phone or another computer: your mail stays with your provider and Chrono Mail keeps it in step. Choose **POP3** if you want the mail downloaded to this computer.
3. Chrono Mail then shows your **recovery code**. Write it down, or save it, and keep it somewhere safe.

### Why the recovery code matters

Chrono Mail locks your stored mail with a key, and keeps that key in your computer's password store. If that password store is ever lost (for example after reinstalling your computer), the recovery code is the only way to unlock your mail again.

You can see the recovery code again at any time: **Menu > File > Show the recovery code…**.

## Help and feedback

Found a problem, or missing something? Tell us on the [issues page](https://github.com/rob-vandenberg/chrono-mail/issues).

How to use Chrono Mail is explained in the [user guide](docs/README.md). Its first page, [Getting started](docs/getting-started.md), takes you through the first start; more pages follow.

## For developers

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Chrono Mail is free software under the GNU Affero General Public License v3.0. See [`LICENSE`](LICENSE).
