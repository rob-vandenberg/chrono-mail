from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid

from dateutil.easter import easter

from chrono_mail.core import MessageFlag
from chrono_mail.pim.ical import Attendee, Event, EventSeries, Reminder, Task, events_to_text, tasks_to_text
from chrono_mail.pim.itip import answered_copy, reply_text, request_of
from tools.demo.cast import ACCOUNTS, PERSON
from tools.demo.people import Person

# --- Version ------------------------------------------------------------
__version__ = "meetings.py 4.4.614"

# --- Version history ----------------------------------------------------
# v4.4.614: (review) times follow the local zone with its clock changes; the repeating series are floating local times, so their weekday
#           holds in every zone; nothing of today carries a reminder (no pop-up during the screenshots); Good Friday from the Easter date
# v4.4.614: Initial: the demo's coming week (appointments with tags and reminders, a cancelled one, a repeating one with a cancelled date),
#           tasks with reminders, and the invitations in the Work Inbox: one to answer, one accepted, one declined, an answer to the person's
#           own meeting and a calendar file with several items (D-310, D-316, D-317, D-329)

# Every calendar text here is written by Chrono Mail's own code (pim.ical, pim.itip), as the program writes and reads it; nothing is guessed.

WORK = next(account for account in ACCOUNTS if account.key == "work")
ANNA: Attendee = Attendee(WORK.address, PERSON)


@dataclass(frozen=True, slots=True)
class MeetingMail:
    """A calendar mail in the Work Inbox, with its marks."""

    raw: bytes
    moment: datetime
    flags: frozenset[MessageFlag]


@dataclass(frozen=True, slots=True)
class Meetings:
    mails: tuple[MeetingMail, ...]  # for the Work Inbox
    copies: tuple[str, ...]  # for the Work calendar: the invitations answered, and the person's own meeting


def workdays(today: datetime, count: int = 6) -> list[date]:
    """The next working days from today on (today too, when it is one)."""
    found: list[date] = []
    day = today.date()
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day)
        day += timedelta(days=1)
    return found


def _at(today: datetime, day: date, hour: int, minute: int = 0) -> datetime:
    """A time of the person's day, written in UTC as the builder's other calendar texts; the computer's own zone with its clock changes on that day
    (a naive time is the local time of its own date)."""
    return datetime.combine(day, time(hour, minute)).astimezone(UTC)


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    """A floating time (no zone): a repeating series keeps its weekday and its hour on the person's clock in every zone and across clock changes."""
    return datetime.combine(day, time(hour, minute))


def _uid(name: str, today: datetime) -> str:
    return f"{name}-{today:%Y%m%d}@harbourdesign.example.com"


def _event(uid: str, summary: str, start: datetime | date, end: datetime | date, location: str = "", tags: tuple[str, ...] = (),
           reminder: int = 0, rule: str = "", status: str = "", description: str = "") -> Event:
    reminders = (Reminder(minutes_before=reminder),) if reminder else ()
    return Event(uid=uid, summary=summary, start=start, end=end, location=location, categories=tags, reminders=reminders, rrule=rule, status=status,
                 description=description, transparency="TRANSPARENT" if not isinstance(start, datetime) else "OPAQUE")


def coming_week(today: datetime) -> tuple[tuple[str, str], ...]:
    """(calendar, text) of what fills the coming week, so Day, Work Week and Week look busy and real: client calls and reviews with tags (the first
    tag gives the colour, D-329), reminders, a cancelled print check (D-317), a weekly yoga class with one cancelled date, deadlines of whole days."""
    days = workdays(today)
    saturday = today.date() + timedelta(days=(5 - today.weekday()) % 7 or 7)
    wednesday = today.date() + timedelta(days=(2 - today.weekday()) % 7 or 7)
    found: list[tuple[str, str]] = []

    def add(calendar: str, event: Event, overrides: tuple[Event, ...] = ()) -> None:
        if not event.rrule and isinstance(event.start, datetime) and event.start.astimezone().date() == today.date():
            event = replace(event, reminders=())  # a reminder of today could pop up while the screenshots are taken
        found.append((calendar, events_to_text(EventSeries(event, overrides))))

    def timed(day: date, hour: int, minute: int, minutes: int) -> tuple[datetime, datetime]:
        start = _at(today, day, hour, minute)
        return start, start + timedelta(minutes=minutes)

    add("Work", _event(_uid("call-lumen", today), "Client call: Lumen Opticians", *timed(days[0], 14, 0, 45), "Video call", ("Clients",), 10))
    add("Work", _event(_uid("bluebird-presentation", today), "Presentation for Bluebird Bakery", *timed(days[1], 10, 0, 90), "Bluebird Bakery, High Street",
                       ("Clients", "Important"), 30, description="Bring the printed boxes and the colour proofs."))
    add("Work", _event(_uid("catalogue-review", today), "Design review: spring catalogue", *timed(days[1], 15, 30, 60), "Studio, meeting room", ("Important",)))
    add("Work", _event(_uid("print-check", today), "Print check", *timed(days[2], 14, 0, 120), "Printworks", ("Clients",), status="CANCELLED",
                       description="The printer moved it: new date to follow."))
    add("Work", _event(_uid("lunch-maple", today), "Lunch with Maple & Oak", *timed(days[3], 12, 30, 90), "Rosa's", ("Clients",), 15))
    add("Work", _event(_uid("planning", today), "Planning next week", *timed(days[4], 16, 0, 45), "Studio, meeting room", reminder=15))
    add("Home", _event(_uid("dentist", today), "Dentist", *timed(days[0], 8, 15, 30), "Riverbend Dental", ("Health",), 60))
    add("Home", _event(_uid("dinner", today), "Dinner with the Heskeths", *timed(days[2], 19, 30, 150), "Rosa's", ("Family",)))
    add("Home", _event(_uid("car-service", today), "Car service", *timed(days[3], 8, 0, 60), "Main Street Garage", ("Errands",), 24 * 60))
    add("Home", _event(_uid("work-party", today), "Work party at the garden", *timed(saturday, 10, 0, 180), "Green Street Garden", ("Garden",), 60))
    yoga_start = _local(wednesday - timedelta(days=7 * 20), 19, 30)
    cancelled = yoga_start + timedelta(days=7 * 20)  # this coming Wednesday, on the person's clock as the rule repeats
    add("Home", _event(_uid("yoga", today), "Yoga", yoga_start, yoga_start + timedelta(minutes=60), "Community hall", ("Health",), rule="FREQ=WEEKLY;BYDAY=WE"),
        (Event(uid=_uid("yoga", today), summary="Yoga", start=cancelled, end=cancelled + timedelta(minutes=60), location="Community hall", recurrence_id=cancelled,
               status="CANCELLED", categories=("Health",)),))
    add("Family", _event(_uid("concert", today), "School concert", *timed(days[4], 17, 30, 90), "Westbrook Primary School", ("Family", "Important"), 60))
    add("Deadlines", _event(_uid("catalogue-deadline", today), "Spring catalogue to the printer", days[3], days[3] + timedelta(days=1), tags=("Clients", "Important")))
    add("Deadlines", _event(_uid("relaunch", today), "Website relaunch goes live", days[5], days[5] + timedelta(days=1), tags=("Clients",)))
    swimming = _local(wednesday - timedelta(days=7 * 30), 17, 0)
    add("Kids' activities", _event(_uid("swimming", today), "Lily's swimming lesson", swimming, swimming + timedelta(minutes=45), "Leisure centre", ("Family",),
                                   rule="FREQ=WEEKLY;BYDAY=WE"))
    football = _local(saturday - timedelta(days=7 * 30), 9, 30)
    add("Kids' activities", _event(_uid("football", today), "Sam's football", football, football + timedelta(minutes=90), "Park pitches", ("Family",),
                                   rule="FREQ=WEEKLY;BYDAY=SA"))
    return tuple(found)


def tasks_with_reminders(today: datetime) -> tuple[tuple[str, str], ...]:
    """(task list, text) of open tasks with reminders (D-316): minutes before the due time, and one at a set time without a due date."""
    days = workdays(today)

    def task(name: str, summary: str, due: datetime | None, tags: tuple[str, ...], reminder: Reminder | None, priority: int = 0) -> str:
        return tasks_to_text(Task(uid=_uid(name, today), summary=summary, due=due, categories=tags, priority=priority,
                                  reminders=(reminder,) if reminder is not None else ()))

    return (
        ("Studio tasks", task("proofs", "Send the proofs to Bluebird Bakery", _at(today, days[1], 9, 0), ("Clients", "Important"), Reminder(minutes_before=60), 1)),
        ("Studio tasks", task("brand-guide", "Read the new brand guidelines", _at(today, days[4], 12, 0), ("To read",), Reminder(minutes_before=24 * 60))),
        ("Personal", task("dry-cleaning", "Pick up the dry cleaning", _at(today, days[2], 17, 0), ("Errands",), Reminder(minutes_before=30))),
        ("Personal", task("passport", "Renew the passport", None, ("Travel",), Reminder(minutes_before=None, at=_at(today, days[3], 9, 0)))),
        ("Personal", task("compost", "Order compost for the plot", _at(today, days[5], 18, 0), ("Garden",), None)),
        ("Shopping list", task("bulbs", "Light bulbs for the hall", None, ("Errands",), None)),
        ("Shopping list", task("card", "Birthday card for Jenny", _at(today, days[2], 12, 0), ("Family", "Errands"), None)),
        ("Shopping list", task("batteries", "Batteries for the smoke alarm", None, ("Errands",), None)),
    )


def _mail(sender: Person | Attendee, subject: str, text: str, moment: datetime, calendar: str, method: str, filename: str, key: str) -> bytes:
    """A calendar mail as it arrives: the text, and the calendar part as an attachment of type text/calendar with its method (RFC 6047), as Chrono
    Mail itself sends one (engine/commands_drafts.py)."""
    name, address = (sender.name, sender.address)
    mail = EmailMessage(policy=SMTP)
    mail["Received"] = f"from mx.{address.rpartition('@')[2]} by {WORK.incoming_host} with ESMTPS; {format_datetime(moment + timedelta(seconds=7))}"
    mail["From"] = f"{name} <{address}>"
    mail["To"] = f"{PERSON} <{WORK.address}>"
    mail["Subject"] = subject
    mail["Date"] = format_datetime(moment)
    mail["Message-ID"] = make_msgid(idstring=key, domain=address.rpartition("@")[2])
    mail.set_content(text)
    mail.add_attachment(calendar.encode("utf-8"), maintype="text", subtype="calendar", filename=filename, params={"method": method})
    return mail.as_bytes()


def _items_together(texts: list[str]) -> str:
    """Several events in one calendar text, as a calendar file of several items holds them."""
    blocks = [text[text.index("BEGIN:VEVENT"):text.rindex("END:VEVENT") + len("END:VEVENT\r\n")] for text in texts]
    first = texts[0]
    end = first.rindex("END:VCALENDAR")
    return first[:first.index("BEGIN:VEVENT")] + "".join(blocks) + first[end:]


def meetings(today: datetime, people: list[Person]) -> Meetings:
    """The calendar mail of the Work Inbox and the copies they left in the Work calendar (D-310)."""
    colleagues = [person for person in people if person.circle == "work"]
    clients = [person for person in people if person.circle == "client"]
    days = workdays(today)
    mails: list[MeetingMail] = []
    copies: list[str] = []

    def person(of: Person, answer: str = "NEEDS-ACTION") -> Attendee:
        return Attendee(of.address, of.name, status=answer, rsvp=True)

    def request(name: str, organizer: Person, summary: str, start: datetime, minutes: int, location: str, others: tuple[Person, ...], description: str,
                sent: datetime) -> str:
        event = Event(uid=_uid(name, today), summary=summary, start=start, end=start + timedelta(minutes=minutes), location=location, description=description,
                      organizer=Attendee(organizer.address, organizer.name), attendees=(Attendee(WORK.address, PERSON, rsvp=True), *(person(other) for other in others)))
        text = request_of(events_to_text(EventSeries(event)), sent)
        assert text is not None
        return text

    # 1. an invitation not answered yet
    sent = today - timedelta(hours=2)
    asked = request("kick-off", colleagues[0], "Kick-off: spring catalogue", _at(today, days[1], 11, 30), 60, "Studio, meeting room", (colleagues[1], colleagues[2]),
                    "The plan, the dates and who does what.", sent)
    mails.append(MeetingMail(_mail(colleagues[0], "Invitation: Kick-off: spring catalogue", f"Hi Anna,\n\nLet's get the spring catalogue started: the plan, the dates "
                                   f"and who does what. Coffee will be there.\n\n{colleagues[0].name.partition(' ')[0]}\n", sent, asked, "REQUEST", "request.ics",
                                   "kick-off"), sent, frozenset()))
    # 2. one already accepted: in the Work calendar, the mail answered
    sent = today - timedelta(days=2, hours=3)
    client = clients[0]
    asked = request("tasting", client, f"Tasting and photo planning at {client.company}", _at(today, days[3], 14, 0), 120, client.company, (),
                    "We'll try the new spring range and plan the photos for the boxes.", sent)
    mails.append(MeetingMail(_mail(client, f"Invitation: Tasting and photo planning at {client.company}", f"Dear Anna,\n\nWe'd love you to come and taste the "
                                   f"new spring range before the photo shoot.\n\nKind regards,\n{client.name}\n{client.company}\n", sent, asked, "REQUEST",
                                   "request.ics", "tasting"), sent, frozenset({MessageFlag.SEEN, MessageFlag.REPLIED})))
    accepted = answered_copy(asked, WORK.address, "ACCEPTED", PERSON)
    assert accepted is not None
    copies.append(accepted)
    # 3. one declined: kept in the Work calendar, hidden until Show declined
    sent = today - timedelta(days=3, hours=5)
    asked = request("quiz", colleagues[3], "Studio quiz night", _at(today, days[4], 19, 0), 150, "The Old Ferry", (colleagues[4], colleagues[5]),
                    "Teams of four. Winners choose the next team lunch.", sent)
    mails.append(MeetingMail(_mail(colleagues[3], "Invitation: Studio quiz night", "Hi all,\n\nQuiz night is back! Sign up by answering this invitation.\n\n"
                                   f"{colleagues[3].name.partition(' ')[0]}\n", sent, asked, "REQUEST", "request.ics", "quiz"), sent,
                             frozenset({MessageFlag.SEEN, MessageFlag.REPLIED})))
    declined = answered_copy(asked, WORK.address, "DECLINED", PERSON)
    assert declined is not None
    copies.append(declined)
    # 4. the person's own meeting, and an attendee's answer to it, not yet taken into the meeting
    start = _at(today, days[3], 9, 30)
    own = Event(uid=_uid("relaunch-planning", today), summary="Website relaunch: planning", start=start, end=start + timedelta(minutes=60),
                location="Studio, meeting room", description="Where we are, what is left, and the date we go live.", organizer=ANNA,
                attendees=(Attendee(WORK.address, PERSON, status="ACCEPTED"), person(colleagues[1]), person(colleagues[6]), person(colleagues[7])),
                reminders=(Reminder(minutes_before=15),), categories=("Clients",))
    own_text = events_to_text(EventSeries(own))
    copies.append(own_text)
    sent = today - timedelta(hours=1)
    invited = request_of(own_text, today - timedelta(days=1))
    answer = reply_text(invited, colleagues[1].address, "ACCEPTED", sent, colleagues[1].name) if invited is not None else None
    assert answer is not None
    mails.append(MeetingMail(_mail(colleagues[1], "Accepted: Website relaunch: planning", f"See you there.\n\n{colleagues[1].name.partition(' ')[0]}\n", sent,
                                   answer, "REPLY", "reply.ics", "relaunch-reply"), sent, frozenset()))
    # 5. a calendar file with several items
    sent = today - timedelta(days=5, hours=2)
    year = today.year + 1
    last_of_may = date(year, 5, 31)
    closing = [(easter(year) - timedelta(days=2), "Studio closed: Good Friday"), (last_of_may - timedelta(days=last_of_may.weekday()), "Studio closed: spring break"),
               (date(year, 8, 25), "Studio closed: summer break"), (date(year, 12, 24), "Studio closed until the new year")]
    texts = [events_to_text(EventSeries(_event(_uid(f"closed-{number}", today), summary, day, day + timedelta(days=1), "Harbour Design Studio")))
             for number, (day, summary) in enumerate(closing)]
    published = request_of(_items_together(texts), sent, "PUBLISH")
    assert published is not None
    mails.append(MeetingMail(_mail(colleagues[4], "Studio closing days", "Hi everyone,\n\nAttached are the days the studio is closed next year. Add them to your "
                                   f"calendar in one go.\n\n{colleagues[4].name.partition(' ')[0]}\n", sent, published, "PUBLISH", "closing-days.ics", "closing"),
                             sent, frozenset({MessageFlag.SEEN})))
    return Meetings(tuple(mails), tuple(copies))
