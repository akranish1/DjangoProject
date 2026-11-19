"""Landing page, activity log, error pages and demo-data seeding tests."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from announcements.models import Announcement
from core.models import ActivityLog
from core.services import log_activity, paginate
from events.models import Category, Event, EventStatus, Venue
from registrations.models import Attendance, Registration

User = get_user_model()


class CoreViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.organizer = User.objects.create_user("org", "org@example.com", "pw12345678",
                                                 role=User.Role.ORGANIZER)
        cls.participant = User.objects.create_user("part", "part@example.com", "pw12345678")
        cls.category = Category.objects.create(name="Technology", icon="code")
        cls.venue = Venue.objects.create(name="Hub", city="Chandigarh", capacity=100)
        cls.event = Event.objects.create(
            organizer=cls.organizer, category=cls.category, venue=cls.venue,
            title="Homepage Event", description="d",
            start_time=cls.now + timedelta(days=3), end_time=cls.now + timedelta(days=3, hours=2),
            registration_deadline=cls.now + timedelta(days=2), capacity=50,
            status=EventStatus.PUBLISHED, is_featured=True,
        )
        Event.objects.create(
            organizer=cls.organizer, category=cls.category, venue=cls.venue,
            title="Hidden Draft", description="d",
            start_time=cls.now + timedelta(days=6), end_time=cls.now + timedelta(days=6, hours=2),
            registration_deadline=cls.now + timedelta(days=5), capacity=50,
            status=EventStatus.DRAFT,
        )

    def test_home_page_shows_live_statistics(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"]["events"], 1)
        self.assertEqual(response.context["stats"]["organizers"], 1)
        self.assertContains(response, "Homepage Event")
        self.assertNotContains(response, "Hidden Draft")

    def test_home_page_works_for_authenticated_users(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Go to my dashboard")

    def test_404_page(self):
        response = self.client.get("/this-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_403_page_rendered_for_wrong_role(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:organizer"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)


class ActivityLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("actor", "actor@example.com", "pw12345678")

    def test_log_activity_records_actor(self):
        entry = log_activity(self.user, ActivityLog.Action.EVENT_CREATED, "Created something")
        self.assertEqual(entry.actor, self.user)
        self.assertIn("Created something", str(entry))

    def test_anonymous_actor_is_stored_as_null(self):
        entry = log_activity(None, ActivityLog.Action.SIGNUP, "Anonymous signup")
        self.assertIsNone(entry.actor)

    def test_admin_activity_page_requires_admin(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("dashboard:admin_activity")).status_code, 403)
        admin = User.objects.create_superuser("root2", "root2@example.com", "pw12345678")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("dashboard:admin_activity")).status_code, 200)

    def test_event_creation_is_logged(self):
        self.client.force_login(User.objects.create_user(
            "orgx", "orgx@example.com", "pw12345678", role=User.Role.ORGANIZER))
        category = Category.objects.create(name="Logging", icon="code")
        venue = Venue.objects.create(name="Log Hall", city="Pune", capacity=50)
        now = timezone.now()
        self.client.post(reverse("events:create"), {
            "title": "Logged Event", "summary": "s", "description": "long enough description",
            "category": category.pk, "venue": venue.pk,
            "start_time": (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
            "end_time": (now + timedelta(days=10, hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "registration_deadline": (now + timedelta(days=9)).strftime("%Y-%m-%dT%H:%M"),
            "capacity": 20, "price": "0", "status": EventStatus.PUBLISHED,
            "sessions-TOTAL_FORMS": "0", "sessions-INITIAL_FORMS": "0",
            "sessions-MIN_NUM_FORMS": "0", "sessions-MAX_NUM_FORMS": "25",
        })
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.EVENT_CREATED).exists())


class PaginateHelperTests(TestCase):
    def test_paginate_handles_bad_input(self):
        queryset = list(range(50))
        page = paginate(queryset, "not-a-number", 10)
        self.assertEqual(page.number, 1)
        page = paginate(queryset, 999, 10)
        self.assertEqual(page.number, 5)
        page = paginate(queryset, 3, 10)
        self.assertEqual(list(page.object_list), list(range(20, 30)))


class SeedDemoCommandTests(TestCase):
    def test_seed_is_idempotent(self):
        out = StringIO()
        call_command("seed_demo", stdout=out, verbosity=0)
        first = {
            "users": User.objects.count(),
            "events": Event.objects.count(),
            "registrations": Registration.objects.count(),
            "announcements": Announcement.objects.count(),
            "venues": Venue.objects.count(),
            "categories": Category.objects.count(),
        }
        call_command("seed_demo", stdout=out, verbosity=0)
        second = {
            "users": User.objects.count(),
            "events": Event.objects.count(),
            "registrations": Registration.objects.count(),
            "announcements": Announcement.objects.count(),
            "venues": Venue.objects.count(),
            "categories": Category.objects.count(),
        }
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["events"], 10)
        self.assertLessEqual(first["events"], 16)
        self.assertGreaterEqual(first["registrations"], 50)
        self.assertGreater(Attendance.objects.count(), 0)

    def test_seed_creates_demo_credentials_and_states(self):
        call_command("seed_demo", stdout=StringIO(), verbosity=0)
        self.assertTrue(User.objects.filter(username="admin", is_superuser=True).exists())
        self.assertTrue(User.objects.filter(role=User.Role.ORGANIZER).count() >= 3)
        self.assertTrue(User.objects.filter(role=User.Role.PARTICIPANT).count() >= 8)
        self.assertEqual(User.objects.filter(is_active=True).count(), User.objects.count())
        statuses = set(Event.objects.values_list("status", flat=True))
        self.assertEqual(statuses, {"draft", "published", "cancelled", "completed"})
        # a full event with waitlisted participants must exist
        self.assertTrue(
            Registration.objects.filter(status="waitlisted").exists(),
            "expected at least one waitlisted demo registration",
        )
        # demo credentials must actually work
        self.assertTrue(self.client.login(username="admin", password="admin1234"))
        self.assertTrue(self.client.login(username="aarav.participant", password="demo1234"))

    def test_refresh_flag_rebuilds_events_without_duplicating_users(self):
        call_command("seed_demo", stdout=StringIO(), verbosity=0)
        users_before = User.objects.count()
        call_command("seed_demo", "--refresh", stdout=StringIO(), verbosity=0)
        self.assertEqual(User.objects.count(), users_before)
        self.assertGreaterEqual(Event.objects.count(), 10)


class EndToEndJourneyTests(TestCase):
    """Walk the full participant journey using only the HTTP layer."""

    def test_participant_journey_from_signup_to_ticket(self):
        now = timezone.now()
        admin = User.objects.create_superuser("root3", "root3@example.com", "pw12345678")
        organizer = User.objects.create_user("journeyorg", "journeyorg@example.com", "pw12345678",
                                             role=User.Role.ORGANIZER, first_name="Jo")
        category = Category.objects.create(name="Journey", icon="spark")
        venue = Venue.objects.create(name="Journey Hall", city="Sangrur", capacity=30)

        # 1. organizer signs in and creates a published event
        self.assertTrue(self.client.login(username="journeyorg", password="pw12345678"))
        self.client.post(reverse("events:create"), {
            "title": "Journey Event", "summary": "End to end", "description": "A full journey test event",
            "category": category.pk, "venue": venue.pk,
            "start_time": (now + timedelta(days=12)).strftime("%Y-%m-%dT%H:%M"),
            "end_time": (now + timedelta(days=12, hours=3)).strftime("%Y-%m-%dT%H:%M"),
            "registration_deadline": (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
            "capacity": 2, "price": "0", "status": EventStatus.PUBLISHED,
            "sessions-TOTAL_FORMS": "1", "sessions-INITIAL_FORMS": "0",
            "sessions-MIN_NUM_FORMS": "0", "sessions-MAX_NUM_FORMS": "25",
            "sessions-0-title": "Opening keynote", "sessions-0-speaker": "Jo",
            "sessions-0-location": "Journey Hall", "sessions-0-order": "0",
            "sessions-0-start_time": (now + timedelta(days=12)).strftime("%Y-%m-%dT%H:%M"),
            "sessions-0-end_time": (now + timedelta(days=12, hours=1)).strftime("%Y-%m-%dT%H:%M"),
        }, follow=True)
        event = Event.objects.get(title="Journey Event")
        self.assertEqual(event.sessions.count(), 1)
        self.client.logout()

        # 2. two participants register through the public flow
        tickets = []
        for index in range(2):
            username = f"journey{index}"
            response = self.client.post(reverse("accounts:register"), {
                "first_name": f"J{index}", "last_name": "Traveller", "username": username,
                "email": f"{username}@example.com", "role": "participant",
                "password1": "s3cret-Event-9", "password2": "s3cret-Event-9", "terms": "on",
            }, follow=True)
            self.assertEqual(response.status_code, 200)
            response = self.client.post(reverse("registrations:register", args=[event.slug]),
                                        {"notes": "", "agree": "on"}, follow=True)
            self.assertContains(response, "Ticket")
            registration = Registration.objects.get(event=event, participant__username=username)
            tickets.append(registration)
            self.client.logout()

        self.assertEqual(Registration.objects.filter(event=event).count(), 2)

        # 3. the third participant is waitlisted (capacity is 2)
        self.client.post(reverse("accounts:register"), {
            "first_name": "Late", "last_name": "Comer", "username": "journey_late",
            "email": "late@example.com", "role": "participant",
            "password1": "s3cret-Event-9", "password2": "s3cret-Event-9", "terms": "on",
        }, follow=True)
        response = self.client.post(reverse("registrations:register", args=[event.slug]),
                                    {"notes": "", "agree": "on"}, follow=True)
        self.assertContains(response, "waitlist")
        late = Registration.objects.get(event=event, participant__username="journey_late")
        self.assertEqual(late.status, "waitlisted")
        self.client.logout()

        # 4. first participant cancels -> the waitlisted one is promoted
        self.client.login(username="journey0", password="s3cret-Event-9")
        self.client.post(reverse("registrations:cancel", args=[tickets[0].ticket_code]),
                         {"reason": "Plans changed", "confirm": "on"}, follow=True)
        late.refresh_from_db()
        self.assertEqual(late.status, "confirmed")
        self.client.logout()

        # 5. organizer posts an announcement -> participant notified
        self.client.login(username="journeyorg", password="pw12345678")
        self.client.post(reverse("announcements:create", args=[event.slug]), {
            "title": "Bring your laptop", "body": "The workshop needs a laptop with Python installed.",
            "notify": "on",
        }, follow=True)
        self.client.logout()
        self.assertTrue(late.participant.notifications.filter(title__icontains="Bring your laptop").exists())

        # 6. on event day the organizer checks participants in at the door
        #    (attendance cannot be recorded more than a day before the event)
        Event.objects.filter(pk=event.pk).update(
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            registration_deadline=now - timedelta(days=1),
        )
        event.refresh_from_db()
        self.client.login(username="journeyorg", password="pw12345678")
        response = self.client.post(reverse("registrations:check_in", args=[event.slug]),
                                    {"ticket_code": late.ticket_code},
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(response.json()["ok"])
        late.refresh_from_db()
        self.assertEqual(late.status, "attended")
        self.client.logout()

        # 7. admin sees accurate platform statistics
        self.client.force_login(admin)
        response = self.client.get(reverse("dashboard:admin"))
        self.assertEqual(response.context["event_stats"]["total"], 1)
        self.assertEqual(response.context["registration_stats"]["total"], 3)
        self.assertEqual(response.context["registration_stats"]["waitlisted"], 0)
