import random

# --- Version ------------------------------------------------------------
__version__ = "texts.py 2.3.321"

# --- Version history ----------------------------------------------------
# v2.3.321: Initial: the subjects and texts of the demo's mail, by circle, written to read like real mail (D-243)

# Every subject and sentence may hold {placeholders}, filled from FILL. Nothing here names a real person, company or place.

FILL: dict[str, tuple[str, ...]] = {
    "day": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"),
    "time": ("9:30", "10:00", "11:15", "12:30", "14:00", "15:30", "16:00", "19:30"),
    "project": ("the website relaunch", "the spring catalogue", "the new logo", "the menu cards", "the shop window", "the annual report", "the brochure",
                "the packaging", "the signage", "the social media posts"),
    "client": ("Bluebird Bakery", "Northwind Travel", "Maple & Oak", "Copper Kettle Café", "Riverbend Dental", "Lumen Opticians", "Fernhill Nurseries",
               "Harbour Lights Cinema", "Saltmarsh Brewery", "Kestrel Cycles"),
    "thing": ("the colour proofs", "the photos", "the second draft", "the font licences", "the print quote", "the mood board", "the copy", "the sketches"),
    "place": ("the café on Mill Lane", "the studio", "the library", "the community hall", "the park", "Rosa's", "the garden shed", "the clubhouse"),
    "book": ("The Lighthouse Keeper's Daughter", "A Year of Small Things", "The Salt Road", "Winter Orchard", "The Cartographer", "Paper Boats",
             "The Long Afternoon", "Under the Linden Tree", "The Glass Harbour", "Quiet Rooms"),
    "plant": ("tomatoes", "runner beans", "courgettes", "sweet peas", "strawberries", "dahlias", "leeks", "pumpkins"),
    "amount": ("£24.90", "£68.00", "£112.45", "£39.99", "£250.00", "£17.50", "£84.20", "£460.00"),
    "item": ("a reading lamp", "garden gloves", "two cushions", "a rain jacket", "a cookbook", "a kettle", "walking boots", "a picture frame"),
    "destination": ("Lisbon", "Edinburgh", "the Isle of Skye", "Porto", "York", "Bruges", "the Lake District", "Cornwall"),
    "month": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
}

SUBJECTS: dict[str, tuple[str, ...]] = {
    "work": ("Feedback on {project}", "Planning for next week", "Studio meeting {day}", "{client}: next steps", "Can you look at {thing}?",
             "Holiday rota", "New printer in the studio", "Deadline for {project}", "Lunch {day}?", "Timesheets", "Update on {project}",
             "Team drinks on Friday", "Quick question about {thing}", "Client call at {time}", "Office closed on {day}"),
    "client": ("{project}", "Our call on {day}", "Thoughts on {thing}", "Invoice question", "Small change to {project}", "Thank you!",
               "Photos for {project}", "Can we move our meeting?", "Next season", "Approval for {thing}"),
    "family": ("Sunday lunch", "Photos from the weekend", "Birthday plans", "Can you pick up Lily?", "Holiday in {destination}", "Mum's garden",
               "Recipe you asked for", "Christmas plans", "Dentist on {day}", "Saturday?", "The car", "School play"),
    "friend": ("Coffee on {day}?", "Long time no see!", "Walk this weekend?", "That film we talked about", "Dinner at ours", "Happy birthday!",
               "Pictures from {destination}", "Quiz night", "A book for you", "Catching up"),
    "bookclub": ("Next meeting: {book}", "Book suggestions for {month}", "Venue for {day}", "Who is bringing snacks?", "Thoughts on {book}",
                 "Reading list for the autumn", "Meeting moved to {day}"),
    "garden": ("Watering rota", "Plot 14", "Seed swap on {day}", "Volunteers needed", "Our {plant} this year", "Open day", "The shed key",
               "Compost delivery", "Work party {day} morning"),
    "school": ("Letter from the class teacher", "Trip to the museum", "Parents' evening", "Sports day", "Lost property", "Term dates"),
    "tennis": ("Doubles on {day}", "Club tournament", "Court bookings", "Membership renewal", "Coaching session"),
    "alumni": ("Reunion this year", "Class of 2004", "News from the old campus", "Remember this?"),
    "freelance": ("Quote for {project}", "Logo for {client}", "Invoice {month}", "A small design job", "Your portfolio", "Business cards",
                  "Follow-up on our call"),
}

OPENERS: tuple[str, ...] = ("Hi Anna,", "Hello Anna,", "Dear Anna,", "Hi,", "Morning Anna,", "Hey Anna,", "Anna,")
SENTENCES: dict[str, tuple[str, ...]] = {
    "work": ("I've had a look at {thing} and I think we're nearly there.", "Could you send me the latest version before {day}?",
             "The client wants to see {project} at {time}.", "Let's go through it together after lunch.", "I've put the files in the shared folder.",
             "We still need {thing} for {project}.", "I'm out of the studio on {day}, but I can join the call.", "Nice work on {project}!",
             "Can we keep the colours a bit warmer?", "I'll book the meeting room for {time}."),
    "client": ("Thank you for {thing}, we really like the direction.", "Could we try a slightly bigger logo?", "Our team loved {project}.",
               "Would {day} at {time} work for a quick call?", "I've attached a few photos from the shop.", "We'd like to go ahead.",
               "Is there still time to change the text on the back?", "The invoice has been paid, thanks again."),
    "family": ("We'd love to see you all on Sunday.", "I've attached some photos, the light was lovely.", "Lily says hello!",
               "Can you bring the big dish back when you come?", "Dad has been in the garden all day.", "Shall we book the cottage again?",
               "Let me know what time suits you.", "The kids had a wonderful time."),
    "friend": ("It's been far too long!", "Are you free on {day}?", "I'm reading {book} at the moment, you'd like it.",
               "We could meet at {place}.", "Thanks again for a lovely evening.", "I've finally been to {destination}, it was beautiful.",
               "Bring David and the kids if you like."),
    "bookclub": ("We're meeting at {place} on {day} at {time}.", "I really enjoyed {book}, especially the ending.", "Could someone bring cake?",
                 "For next time I'd like to suggest {book}.", "Please let me know if you can't make it.", "I've added the dates to the calendar."),
    "garden": ("The {plant} are doing really well this year.", "Could someone water plot 14 on {day}?", "We need four more people on {day} morning.",
               "The shed key is back under the blue pot.", "There's a seed swap at {place}.", "Bring gloves and a flask!"),
    "school": ("Please return the slip by {day}.", "Children should bring a packed lunch.", "The trip leaves at {time} from the main gate.",
               "Thank you for your support.", "Appointments can be booked online."),
    "tennis": ("Courts are free on {day} at {time}.", "We need one more for doubles.", "The tournament draw is on the notice board.",
               "Membership fees are due next month."),
    "alumni": ("It would be lovely to see everyone again.", "Do you remember the old library?", "We're planning a dinner in {month}."),
    "freelance": ("We'd like a quote for {project}.", "Here is the brief we talked about.", "Could you have a first idea by {day}?",
                  "The budget is around {amount}.", "We loved your work for {client}."),
}
CLOSERS: tuple[str, ...] = ("Thanks,", "Best,", "Cheers,", "See you soon,", "Speak soon,", "Kind regards,", "Love,", "Best wishes,", "Many thanks,")
REPLIES: tuple[str, ...] = ("Thanks, that sounds good.", "Yes, {day} works for me.", "Great, I'll take a look this afternoon.", "Lovely, see you then!",
                            "Perfect, thank you.", "I'll check and come back to you.", "Sounds great, count me in.", "Thanks for letting me know.",
                            "Sorry for the slow reply, it's been a busy week.", "Good idea, let's do that.")
SPAM_SUBJECTS: tuple[str, ...] = ("You have won a £1,000 gift card", "Your account will be suspended", "Cheap watches, 90% off", "Claim your prize today",
                                  "Urgent: verify your details", "Lose weight fast", "Your parcel could not be delivered", "Investment opportunity",
                                  "Meet new people near you", "Last chance: free trial", "Re: your invoice", "Congratulations, you are selected",
                                  "Refund waiting for you", "Work from home and earn £500 a day", "Limited offer just for you")
SPAM_LINES: tuple[str, ...] = ("Click the link below to claim your reward.", "This offer ends tonight!", "Dear customer, we noticed unusual activity.",
                               "No credit card needed.", "Reply with your details to receive your money.", "Act now, places are limited.")


def fill(text: str, generator: random.Random) -> str:
    """The text with every {placeholder} filled in."""
    return text.format(**{key: generator.choice(values) for key, values in FILL.items()})


def subject(circle: str, generator: random.Random) -> str:
    return fill(generator.choice(SUBJECTS[circle]), generator)


def body(circle: str, writer: str, generator: random.Random, signature: str = "") -> str:
    """A short message as a person writes it: a greeting, two to four sentences, a closing and the writer's first name."""
    lines = [generator.choice(OPENERS), ""]
    lines.extend(fill(sentence, generator) for sentence in generator.sample(SENTENCES[circle], k=min(len(SENTENCES[circle]), generator.randint(2, 4))))
    lines.extend(["", generator.choice(CLOSERS), writer.partition(" ")[0]])
    if signature:
        lines.extend(["", "-- ", signature])
    return "\n".join(lines) + "\n"


def reply_body(generator: random.Random, quoted_from: str, quoted: str, signature: str) -> str:
    """Anna's answer above the quoted message, as Chrono Mail writes a reply."""
    text = [fill(generator.choice(REPLIES), generator), "", "Anna"]
    if signature:
        text.extend(["", "-- ", signature])
    text.extend(["", f"{quoted_from} wrote:"])
    text.extend(f"> {line}" if line else ">" for line in quoted.splitlines())
    return "\n".join(text) + "\n"
