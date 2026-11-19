"""Seed realistic, repeatable demo data.

    python manage.py seed_demo            # idempotent - safe to run repeatedly
    python manage.py seed_demo --refresh  # rebuild events/registrations first

Everything is keyed on stable identifiers (usernames, category slugs, event
slugs), so running the command again never duplicates rows.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Profile, User
from announcements.models import Announcement
from core.models import ActivityLog
from core.services import log_activity
from events.models import Category, Event, EventSession, EventStatus, Venue
from registrations.models import Attendance, Registration, RegistrationStatus
from registrations.services import (
    RegistrationError, mark_attendance, register_for_event,
)

DEMO_PASSWORD = "demo1234"
ADMIN_PASSWORD = "admin1234"

CATEGORIES = [
    ("Technology", "code", "#6366f1", "Conferences, hackathons and developer meetups"),
    ("Music", "music", "#ec4899", "Concerts, gigs and live sessions"),
    ("Business", "briefcase", "#0ea5e9", "Networking, startup and leadership events"),
    ("Sports", "trophy", "#f59e0b", "Tournaments, runs and fitness challenges"),
    ("Arts & Culture", "palette", "#8b5cf6", "Exhibitions, theatre and cultural nights"),
    ("Education", "graduation", "#10b981", "Workshops, bootcamps and masterclasses"),
    ("Community", "heart", "#ef4444", "Volunteering, meetups and social good"),
    ("Travel", "globe", "#14b8a6", "Treks, retreats and city tours"),
    ("Wellness", "leaf", "#22c55e", "Yoga, mindfulness and wellbeing"),
    ("Food & Drink", "spark", "#f97316", "Tastings, pop-ups and food festivals"),
]

VENUES = [
    ("Riverside Convention Centre", "12 Canal Road", "Ludhiana", "Punjab", 1200,
     "Six-hall convention centre with river-facing lawns and full AV support."),
    ("Innov8 Tech Hub", "5th Floor, Sector 34", "Chandigarh", "Chandigarh", 220,
     "Co-working auditorium built for product demos and hackathons."),
    ("Harmony Auditorium", "MG Road", "Amritsar", "Punjab", 640,
     "Acoustic auditorium with a Steinway stage and pro lighting rig."),
    ("Greenfield Sports Arena", "Airport Road", "Mohali", "Punjab", 3000,
     "Indoor turf, track and spectator stands for tournaments."),
    ("The Courtyard Arts Studio", "Old Market Lane", "Patiala", "Punjab", 140,
     "Intimate studio for exhibitions, poetry and acoustic sets."),
    ("Lakeside Retreat", "Bali Chowk Road", "Nalagarh", "Himachal Pradesh", 180,
     "Hill retreat with cottages, bonfire pits and a lakeside deck."),
    ("Sangrur Community Hall", "College Road", "Sangrur", "Punjab", 350,
     "Neighbourhood hall used for workshops and community drives."),
    ("Skyline Rooftop Lounge", "Model Town Extension", "Ludhiana", "Punjab", 120,
     "Rooftop venue for networking evenings and food pop-ups."),
]

ORGANIZERS = [
    ("priya.organizer", "Priya", "Sharma", "priya.sharma@eventhub.local", "Events Lead", "BrightLoop Events", "Ludhiana"),
    ("marcus.organizer", "Marcus", "Lee", "marcus.lee@eventhub.local", "Programme Director", "NorthStar Collective", "Chandigarh"),
    ("aisha.organizer", "Aisha", "Khan", "aisha.khan@eventhub.local", "Community Manager", "OpenCircle Foundation", "Amritsar"),
    ("diego.organizer", "Diego", "Fernandes", "diego.fernandes@eventhub.local", "Sports Coordinator", "Peak Sports League", "Mohali"),
]

PARTICIPANTS = [
    ("aarav.participant", "Aarav", "Mehta", "aarav.mehta@example.com"),
    ("sneha.participant", "Sneha", "Iyer", "sneha.iyer@example.com"),
    ("rohan.participant", "Rohan", "Verma", "rohan.verma@example.com"),
    ("isha.participant", "Isha", "Nair", "isha.nair@example.com"),
    ("kabir.participant", "Kabir", "Singh", "kabir.singh@example.com"),
    ("meera.participant", "Meera", "Raghavan", "meera.raghavan@example.com"),
    ("vikram.participant", "Vikram", "Joshi", "vikram.joshi@example.com"),
    ("zoya.participant", "Zoya", "Ahmed", "zoya.ahmed@example.com"),
    ("nikhil.participant", "Nikhil", "Chopra", "nikhil.chopra@example.com"),
    ("tanvi.participant", "Tanvi", "Deshpande", "tanvi.deshpande@example.com"),
]

# title, category, venue index, organizer index, start offset (days), hours, capacity, price, status, featured
EVENTS = [
    ("Django & Python Developers Summit", "technology", 1, 0, 9, 8, 180, 1499, "published", True),
    ("Sunset Acoustic Sessions", "music", 2, 1, 5, 3, 300, 799, "published", True),
    ("Founder Fireside: Scaling in Bharat", "business", 7, 0, 14, 4, 110, 999, "published", False),
    ("City Half Marathon 2026", "sports", 3, 3, 26, 6, 1500, 0, "published", True),
    ("Watercolour Workshop for Beginners", "arts-culture", 4, 2, 3, 3, 8, 1200, "published", False),
    ("Data Storytelling Masterclass", "education", 1, 0, 18, 6, 60, 2500, "published", False),
    ("Community Clean-up Drive", "community", 6, 2, 2, 4, 80, 0, "published", False),
    ("Himalayan Weekend Retreat", "travel", 5, 1, 31, 48, 40, 8500, "published", False),
    ("Morning Yoga in the Park", "wellness", 6, 2, 1, 2, 8, 0, "published", False),
    ("Street Food Festival", "food-drink", 7, 1, 21, 7, 500, 350, "published", False),
    ("AI Product Sprint (Invite Only)", "technology", 1, 0, 40, 16, 40, 0, "draft", False),
    ("Winter Arts Gala", "arts-culture", 2, 1, 55, 5, 400, 1800, "draft", False),
    ("Startup Demo Day (Postponed)", "business", 1, 0, 12, 5, 150, 0, "cancelled", False),
    ("Annual Music Festival", "music", 2, 1, -18, 9, 900, 1250, "completed", False),
    ("Tech Careers Bootcamp", "education", 1, 0, -32, 8, 120, 0, "completed", False),
    ("Inter-College Basketball Finals", "sports", 3, 3, -6, 4, 800, 250, "completed", False),
]

SESSION_TEMPLATES = {
    "technology": [
        ("Registration & welcome coffee", "Event team", 0.0, 0.75),
        ("Keynote: The state of the Python ecosystem", "Priya Sharma", 0.75, 1.75),
        ("Hands-on workshop: shipping Django at scale", "Marcus Lee", 2.0, 4.0),
        ("Panel: hiring and growing backend teams", "Guest panel", 4.5, 5.5),
        ("Networking & lightning talks", "Community", 6.0, 8.0),
    ],
    "music": [
        ("Doors open & soundcheck", "Stage crew", 0.0, 1.0),
        ("Opening act", "Local trio", 1.0, 2.0),
        ("Headline set", "Sunset Collective", 2.0, 3.0),
    ],
    "sports": [
        ("Warm-up and bib collection", "Volunteers", 0.0, 1.0),
        ("Race start", "Race director", 1.0, 1.5),
        ("Prize distribution", "Organisers", 4.5, 6.0),
    ],
    "education": [
        ("Welcome & course map", "Facilitator", 0.0, 0.5),
        ("Core workshop block", "Lead trainer", 0.5, 4.0),
        ("Q&A and certificates", "Team", 5.0, 6.0),
    ],
    "community": [
        ("Safety briefing & kit hand-out", "Volunteer lead", 0.0, 0.75),
        ("Clean-up drive", "All volunteers", 0.75, 3.0),
        ("Snacks and wrap-up", "Team", 3.0, 4.0),
    ],
}

ANNOUNCEMENTS = {
    "django-python-developers-summit": [
        ("Doors open at 9:00 AM", "Registration desk opens at 9:00 AM sharp in the Innov8 lobby. Please carry a photo ID and your ticket code for check-in.", True),
        ("Wi-Fi and workshop prerequisites", "Network details are printed on your badge. Bring a laptop with Python 3.12 and Docker installed for the afternoon workshop.", False),
    ],
    "sunset-acoustic-sessions": [
        ("Gate timings for tonight", "Gates open at 5:30 PM. The headline set starts at 7:30 PM — please arrive early to avoid queues at the gate.", True),
    ],
    "city-half-marathon-2026": [
        ("Bib collection starts tomorrow", "Collect your bib from the Greenfield Arena annexe between 10:00 AM and 7:00 PM. Carry your registration confirmation.", False),
    ],
    "himalayan-weekend-retreat": [
        ("Packing list & travel plan", "Temperatures drop to 8°C at night. The shuttle leaves Nalagarh bus stand at 6:00 AM — please be there 15 minutes early.", True),
    ],
}

FEEDBACK_NOTES = [
    "Vegetarian meal preference", "Wheelchair access needed near the front row",
    "Interested in the advanced workshop track", "Will arrive 15 minutes late",
    "", "", "First time attending a tech summit",
]


class Command(BaseCommand):
    help = "Create realistic demo users, venues, events, registrations, attendance and announcements."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Delete existing demo events and rebuild them.")
        parser.add_argument("--seed", type=int, default=2026, help="Random seed for deterministic data.")

    def _event_rng(self, event) -> random.Random:
        """Deterministic per-event generator so re-runs produce identical data."""
        return random.Random(f"{self.seed}:{event.slug}")

    @transaction.atomic
    def handle(self, *args, **options):
        self.seed = options["seed"]
        rng = random.Random(self.seed)
        verbosity = options["verbosity"]
        now = timezone.now()

        if options["refresh"]:
            self._purge_demo_data(verbosity)

        categories = self._seed_categories(verbosity)
        venues = self._seed_venues(verbosity)
        admin = self._seed_admin(verbosity)
        organizers = self._seed_organizers(verbosity)
        participants = self._seed_participants(verbosity)

        events = self._seed_events(categories, venues, organizers, nodesk_now=now, verbosity=verbosity)
        self._seed_sessions(events, verbosity)
        registrations = self._seed_registrations(events, participants, rng, verbosity)
        self._seed_attendance(events, registrations, organizers, rng, verbosity)
        self._seed_announcements(events, organizers, participants, verbosity)

        summary = (
            f"Demo data ready: {User.objects.count()} users, {Category.objects.count()} categories, "
            f"{Venue.objects.count()} venues, {Event.objects.count()} events, "
            f"{Registration.objects.count()} registrations, {Attendance.objects.count()} attendance records, "
            f"{Announcement.objects.count()} announcements."
        )
        self.stdout.write(self.style.SUCCESS(summary))
        self.stdout.write("")
        self.stdout.write("Demo credentials (development only):")
        self.stdout.write(f"  Administrator  admin / {ADMIN_PASSWORD}            admin@eventhub.local")
        self.stdout.write(f"  Organiser      priya.organizer / {DEMO_PASSWORD}    marcus.organizer / {DEMO_PASSWORD}")
        self.stdout.write(f"  Organiser      aisha.organizer / {DEMO_PASSWORD}    diego.organizer / {DEMO_PASSWORD}")
        self.stdout.write(f"  Participant    aarav.participant / {DEMO_PASSWORD}  sneha.participant / {DEMO_PASSWORD}")
        self.stdout.write(f"  Participant    rohan.participant / {DEMO_PASSWORD}  isha.participant / {DEMO_PASSWORD}")
        self.stdout.write("")

    # -- purge --------------------------------------------------------------- #
    def _purge_demo_data(self, verbosity):
        demo_usernames = ["admin"] + [o[0] for o in ORGANIZERS] + [p[0] for p in PARTICIPANTS]
        Event.objects.filter(organizer__username__in=demo_usernames).delete()
        if verbosity >= 1:
            self.stdout.write("  - existing demo events removed (driven by registrations/attendance cascades)")

    # -- users --------------------------------------------------------------- #
    def _seed_categories(self, verbosity):
        categories = {}
        for name, icon, color, description in CATEGORIES:
            slug = slugify(name)
            category, created = Category.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "icon": icon, "color": color, "description": description},
            )
            categories[slug] = category
        if verbosity >= 1:
            self.stdout.write(f"  - {len(categories)} categories ready")
        return categories

    def _seed_venues(self, verbosity):
        venues = []
        for name, address, city, state, capacity, description in VENUES:
            venue, _ = Venue.objects.update_or_create(
                name=name, city=city,
                defaults={
                    "address_line": address, "state": state, "capacity": capacity,
                    "description": description, "country": "India", "is_active": True,
                },
            )
            venues.append(venue)
        if verbosity >= 1:
            self.stdout.write(f"  - {len(venues)} venues ready")
        return venues

    def _make_user(self, username, first, last, email, role, **profile_fields):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first, "last_name": last, "email": email, "role": role,
                "is_active": True,
            },
        )
        changed = False
        for field, value in (("first_name", first), ("last_name", last), ("email", email), ("role", role)):
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if created or not user.has_usable_password():
            user.set_password(ADMIN_PASSWORD if role == User.Role.ADMIN else DEMO_PASSWORD)
            changed = True
        if changed:
            user.save()
        profile, _ = Profile.objects.get_or_create(user=user)
        for field, value in profile_fields.items():
            setattr(profile, field, value)
        profile.save()
        return user, created

    def _seed_admin(self, verbosity):
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "first_name": "Ananya", "last_name": "Rao", "email": "admin@eventhub.local",
                "role": User.Role.ADMIN, "is_staff": True, "is_superuser": True, "is_active": True,
            },
        )
        admin.first_name, admin.last_name = "Ananya", "Rao"
        admin.email, admin.role = "admin@eventhub.local", User.Role.ADMIN
        admin.is_staff = admin.is_superuser = admin.is_active = True
        if created or not admin.has_usable_password():
            admin.set_password(ADMIN_PASSWORD)
        admin.save()
        profile, _ = Profile.objects.get_or_create(user=admin)
        profile.job_title = "Platform Administrator"
        profile.organization = "EventHub"
        profile.city = "Chandigarh"
        profile.save()
        if verbosity >= 1:
            self.stdout.write("  - administrator account ready (admin / admin1234)")
        return admin

    def _seed_organizers(self, verbosity):
        organizers = []
        for username, first, last, email, title, org, city in ORGANIZERS:
            user, _ = self._make_user(
                username, first, last, email, User.Role.ORGANIZER,
                job_title=title, organization=org, city=city,
                bio=f"{first} runs {org} and has been organising events for the last few years.",
            )
            user.is_staff = False
            organizers.append(user)
        if verbosity >= 1:
            self.stdout.write(f"  - {len(organizers)} organiser accounts ready")
        return organizers

    def _seed_participants(self, verbosity):
        participants = []
        for index, (username, first, last, email) in enumerate(PARTICIPANTS):
            user, _ = self._make_user(
                username, first, last, email, User.Role.PARTICIPANT,
                city=["Ludhiana", "Chandigarh", "Amritsar", "Mohali", "Sangrur", "Patiala"][index % 6],
                job_title=["Software Engineer", "Product Designer", "Student", "Marketing Lead",
                           "Data Analyst", "Teacher", "Entrepreneur", "Physiotherapist",
                           "Content Writer", "Research Associate"][index % 10],
            )
            participants.append(user)
        if verbosity >= 1:
            self.stdout.write(f"  - {len(participants)} participant accounts ready")
        return participants

    # -- events -------------------------------------------------------------- #
    def _seed_events(self, categories, venues, organizers, nodesk_now, verbosity):
        events = []
        for (title, category_slug, venue_index, organizer_index, day_offset, hours, capacity,
             price, status, featured) in EVENTS:
            organizer = organizers[organizer_index]
            venue = venues[venue_index]
            start = (nodesk_now + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
            start = start.replace(hour=9 if start.hour < 8 else min(start.hour, 19))
            end = start + timedelta(hours=hours)
            deadline = start - timedelta(days=2, hours=6)
            slug = slugify(title)[:180]
            defaults = {
                "organizer": organizer,
                "category": categories[category_slug],
                "venue": venue,
                "title": title,
                "summary": self._summary_for(title, category_slug, venue),
                "description": self._description_for(title, category_slug, venue, start),
                "start_time": start,
                "end_time": end,
                "registration_deadline": deadline,
                "capacity": capacity,
                "price": Decimal(price),
                "status": status,
                "is_featured": featured,
            }
            event = Event.objects.filter(slug=slug).first()
            if event is None:
                event = Event(slug=slug, **defaults)
                event.save()
                log_activity(organizer, ActivityLog.Action.EVENT_CREATED,
                             f"“{title}” created ({dict(EventStatus.choices).get(status, status)})",
                             event.get_absolute_url())
            else:
                for field, value in defaults.items():
                    setattr(event, field, value)
                event.save()
            events.append(event)
        if verbosity >= 1:
            self.stdout.write(f"  - {len(events)} events ready ({Event.objects.filter(status='published').count()} published)")
        return events

    def _summary_for(self, title, category_slug, venue):
        summaries = {
            "technology": f"A full day of talks, workshops and hallway conversations with the developer community at {venue.name}.",
            "music": f"Live sets, warm acoustics and an evening to remember at {venue.name}.",
            "business": f"Founders share what actually worked while scaling their companies — candid stories, no slideshows.",
            "sports": f"Timed routes, hydration points and a finish-line celebration at {venue.name}.",
            "arts-culture": f"Hands-on creative session with all materials provided at {venue.name}.",
            "education": f"Practical, exercise-driven training that you can apply the very next working day.",
            "community": f"Come together with neighbours to make our city a little better — all supplies provided.",
            "travel": f"Two days off the grid with guided trails, local food and a lakeside bonfire.",
            "wellness": f"Gentle guided practice that works for every level, including complete beginners.",
            "food-drink": f"A curated line-up of local stalls, chef specials and live music under the lights.",
        }
        return summaries.get(category_slug, f"Join us at {venue.name} for {title}.")[:240]

    def _description_for(self, title, category_slug, venue, start):
        local_time = timezone.localtime(start).strftime("%A, %d %B %Y at %H:%M")
        return (
            f"{title} takes place at {venue.name}, {venue.city} on {local_time}.\n\n"
            f"What to expect\n"
            f"- A carefully planned programme with clear start times\n"
            f"- On-site registration desk with digital check-in using your ticket code\n"
            f"- Networking breaks with refreshments\n"
            f"- Venue capacity of {venue.capacity_label if hasattr(venue, 'capacity_label') else venue.capacity} attendees\n\n"
            f"Who should attend\n"
            f"Anyone curious about {Category.objects.get(slug=category_slug).name.lower()} — beginners and "
            f"professionals alike are welcome.\n\n"
            f"Accessibility\n"
            f"{venue.name} has step-free access and accessible washrooms. Tell the organiser in your "
            f"registration note if you need anything specific and we will arrange it.\n\n"
            f"Cancellation policy\n"
            f"You may cancel your registration from your dashboard any time before the registration deadline. "
            f"If a seat is limited, your spot is offered to the next person on the waitlist."
        )

    def _seed_sessions(self, events, verbosity):
        created = 0
        for event in events:
            if event.sessions.exists() or event.status == EventStatus.DRAFT:
                continue
            template = SESSION_TEMPLATES.get(event.category.slug)
            if not template:
                continue
            event_hours = (event.end_time - event.start_time).total_seconds() / 3600
            for order, (title, speaker, offset, end_offset) in enumerate(template):
                if end_offset > event_hours:
                    break
                session, was_created = EventSession.objects.get_or_create(
                    event=event, title=title,
                    defaults={
                        "speaker": speaker,
                        "location": event.venue.name,
                        "start_time": event.start_time + timedelta(hours=offset),
                        "end_time": event.start_time + timedelta(hours=end_offset),
                        "order": order,
                    },
                )
                created += int(was_created)
        if verbosity >= 1:
            self.stdout.write(f"  - {created} schedule sessions added")

    # -- registrations ------------------------------------------------------- #
    def _seed_registrations(self, events, participants, rng, verbosity):
        """Register participants through the real service where possible.

        Events whose registration window has already closed (completed or
        cancelled events) are seeded with historical records instead, because
        the service correctly refuses late registrations.
        """
        created = 0
        now = timezone.now()
        for event in events:
            if event.status == EventStatus.DRAFT:
                continue
            # a per-event generator keeps repeated runs byte-for-byte identical
            rng = self._event_rng(event)
            pool = participants[:]
            rng.shuffle(pool)
            historical = event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED)
            small = event.capacity <= 20
            if event.status == EventStatus.CANCELLED:
                target = min(len(pool), max(3, int(event.capacity * 0.05)))
            elif small:
                target = min(len(pool), event.capacity + 2)    # produces a waitlist
            elif historical:
                target = min(len(pool), max(5, int(event.capacity * 0.12)))
            else:
                target = min(len(pool), max(4, int(event.capacity * 0.35)))

            for participant in pool[:target]:
                if historical:
                    registration = self._historical_registration(event, participant, rng)
                    if registration is None:
                        continue
                    created += 1
                    if event.status == EventStatus.CANCELLED:
                        Registration.objects.filter(pk=registration.pk).update(
                            status=RegistrationStatus.CANCELLED,
                            cancelled_at=now - timedelta(days=3),
                            cancellation_reason="Event cancelled by organiser",
                        )
                    continue

                try:
                    registration, was_created = register_for_event(
                        event, participant, notes=rng.choice(FEEDBACK_NOTES),
                    )
                except RegistrationError:
                    continue
                created += int(was_created)
                # small workshops keep their waitlist intact; larger events show a
                # few cancelled registrations for realistic history
                if (
                    was_created
                    and not small
                    and rng.random() < 0.12
                    and registration.status == RegistrationStatus.CONFIRMED
                ):
                    registration.status = RegistrationStatus.CANCELLED
                    registration.cancelled_at = timezone.now()
                    registration.cancelled_by = participant
                    registration.cancellation_reason = "Change of plans"
                    registration.save(update_fields=["status", "cancelled_at", "cancelled_by",
                                                     "cancellation_reason", "updated_at"])
        if verbosity >= 1:
            self.stdout.write(f"  - {created} registrations created "
                              f"({Registration.objects.count()} total, "
                              f"{Registration.objects.waitlisted().count()} waitlisted)")
        return created

    def _historical_registration(self, event, participant, rng):
        """Insert a past registration directly (deadline has long passed)."""
        if Registration.objects.filter(event=event, participant=participant).exists():
            return None
        registration = Registration(
            event=event, participant=participant, status=RegistrationStatus.CONFIRMED,
            notes=rng.choice(FEEDBACK_NOTES), ticket_code=Registration.generate_ticket_code(),
        )
        registration.save()
        days_ago = max(int((timezone.now() - event.start_time).days) + rng.randint(5, 25), 6)
        registered_at = timezone.now() - timedelta(days=days_ago)
        Registration.objects.filter(pk=registration.pk).update(registered_at=registered_at)
        Registration.objects.filter(pk=registration.pk).update(updated_at=registered_at)
        registration.registered_at = registered_at
        return registration

    def _seed_attendance(self, events, registrations, organizers, rng, verbosity):
        marked = 0
        for event in events:
            if event.end_time > timezone.now() or event.status == EventStatus.CANCELLED:
                continue
            rng = self._event_rng(event)
            organizer = event.organizer
            active = list(event.registrations.filter(
                status__in=[RegistrationStatus.CONFIRMED, RegistrationStatus.ATTENDED]
            ).select_related("participant"))
            for index, registration in enumerate(active):
                if Attendance.objects.filter(registration=registration).exists():
                    continue  # already seeded - keeps repeated runs cheap and idempotent
                roll = rng.random()
                if roll < 0.68:
                    status = Attendance.Status.PRESENT
                elif roll < 0.8:
                    status = Attendance.Status.LATE
                elif roll < 0.92:
                    status = Attendance.Status.ABSENT
                else:
                    status = Attendance.Status.EXCUSED
                # leave the most recent completed event partially unmarked for realism
                if event.status == EventStatus.COMPLETED and index % 5 == 4:
                    continue
                try:
                    mark_attendance(registration, organizer, status,
                                    "Arrived at the second session" if status == Attendance.Status.LATE else "")
                    marked += 1
                except RegistrationError:
                    continue
        if verbosity >= 1:
            self.stdout.write(f"  - {marked} attendance records marked")
        return marked

    # -- announcements ------------------------------------------------------- #
    def _seed_announcements(self, events, organizers, participants, verbosity):
        created = 0
        for event in events:
            posts = ANNOUNCEMENTS.get(event.slug, [])
            for title, body, pinned in posts:
                announcement, was_created = Announcement.objects.get_or_create(
                    event=event, title=title,
                    defaults={"author": event.organizer, "body": body, "is_pinned": pinned,
                              "notified_count": event.registrations.confirmed().count()},
                )
                created += int(was_created)
            if event.status == EventStatus.CANCELLED and not event.announcements.exists():
                Announcement.objects.get_or_create(
                    event=event, title="Event postponed",
                    defaults={
                        "author": event.organizer,
                        "body": "We have postponed this event due to a venue scheduling conflict. All registrations "
                                "have been cancelled and refunds (where applicable) are automatic. We will share "
                                "the new date through this page soon.",
                        "is_pinned": True,
                        "notified_count": 0,
                    },
                )
                created += 1
        if verbosity >= 1:
            self.stdout.write(f"  - {created} announcements posted")
        return created
