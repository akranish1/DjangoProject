from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from registrations.models import Registration, RegistrationStatus

from .forms import EventForm
from .models import Category, Event, EventSession, EventStatus, Venue
from .services import EventStateError, cancel_event, complete_event, publish_event

User = get_user_model()


class BaseEventTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user("org1", "org1@example.com", "pw12345678",
                                                 first_name="Olive", last_name="Reed",
                                                 role=User.Role.ORGANIZER)
        cls.other_organizer = User.objects.create_user("org2", "org2@example.com", "pw12345678",
                                                       role=User.Role.ORGANIZER)
        cls.participant = User.objects.create_user("part1", "part1@example.com", "pw12345678")
        cls.admin = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        cls.category = Category.objects.create(name="Technology", icon="code")
        cls.venue = Venue.objects.create(name="Innov8 Hub", city="Chandigarh", capacity=200)
        cls.now = timezone.now()

    def make_event(self, **overrides):
        defaults = {
            "organizer": self.organizer,
            "category": self.category,
            "venue": self.venue,
            "title": "Django Deep Dive",
            "summary": "A focused workshop",
            "description": "Details",
            "start_time": self.now + timedelta(days=10),
            "end_time": self.now + timedelta(days=10, hours=6),
            "registration_deadline": self.now + timedelta(days=8),
            "capacity": 50,
            "price": Decimal("0"),
            "status": EventStatus.PUBLISHED,
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)


class EventModelTests(BaseEventTestCase):
    def test_slug_is_generated_and_unique(self):
        first = self.make_event(title="Same Title")
        second = self.make_event(title="Same Title")
        self.assertEqual(first.slug, "same-title")
        self.assertEqual(second.slug, "same-title-2")

    def test_full_clean_rejects_end_before_start(self):
        event = Event(
            organizer=self.organizer, category=self.category, venue=self.venue, title="Broken",
            description="x", start_time=self.now + timedelta(days=2),
            end_time=self.now + timedelta(days=1), registration_deadline=self.now,
            capacity=10,
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("end_time", ctx.exception.message_dict)

    def test_full_clean_rejects_deadline_after_start(self):
        event = Event(
            organizer=self.organizer, category=self.category, venue=self.venue, title="Late deadline",
            description="x", start_time=self.now + timedelta(days=2),
            end_time=self.now + timedelta(days=2, hours=3),
            registration_deadline=self.now + timedelta(days=3), capacity=10,
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("registration_deadline", ctx.exception.message_dict)

    def test_capacity_cannot_exceed_venue(self):
        event = Event(
            organizer=self.organizer, category=self.category, venue=self.venue, title="Too big",
            description="x", start_time=self.now + timedelta(days=2),
            end_time=self.now + timedelta(days=2, hours=3),
            registration_deadline=self.now + timedelta(days=1), capacity=500,
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("capacity", ctx.exception.message_dict)

    def test_database_check_constraint_blocks_invalid_dates(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Event.objects.create(
                    organizer=self.organizer, category=self.category, venue=self.venue,
                    title="Raw insert", description="x",
                    start_time=self.now + timedelta(days=3), end_time=self.now + timedelta(days=1),
                    registration_deadline=self.now, capacity=5, status=EventStatus.DRAFT,
                )

    def test_derived_availability_properties(self):
        event = self.make_event(capacity=2)
        self.assertEqual(event.seats_taken, 0)
        self.assertEqual(event.seats_remaining, 2)
        self.assertFalse(event.is_full)
        for i in range(2):
            user = User.objects.create_user(f"p{i}", f"p{i}@example.com", "pw12345678")
            Registration.objects.create(event=event, participant=user)
        event.refresh_from_db()
        self.assertTrue(event.is_full)
        self.assertEqual(event.seats_remaining, 0)
        self.assertEqual(event.occupancy, 100)
        self.assertEqual(event.availability_label, "Sold out")

    def test_can_register_rules(self):
        event = self.make_event()
        self.assertEqual(event.can_register(None)[0], False)
        self.assertEqual(event.can_register(self.participant)[0], True)
        self.assertFalse(event.can_register(self.organizer)[0])

        draft = self.make_event(title="Draft one", status=EventStatus.DRAFT)
        self.assertFalse(draft.can_register(self.participant)[0])

        closed = self.make_event(title="Closed", registration_deadline=self.now - timedelta(hours=2))
        self.assertFalse(closed.can_register(self.participant)[0])

    def test_category_and_venue_str(self):
        self.assertEqual(str(self.category), "Technology")
        self.assertEqual(str(self.venue), "Innov8 Hub, Chandigarh")
        self.assertEqual(self.venue.capacity_label, "200 seats")


class EventPermissionTests(BaseEventTestCase):
    def test_organizer_dashboard_requires_organizer_role(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:organizer"))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_redirected_from_create(self):
        response = self.client.get(reverse("events:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_other_organizer_cannot_manage_event(self):
        event = self.make_event()
        self.client.force_login(self.other_organizer)
        for url in [reverse("events:manage", args=[event.slug]),
                    reverse("events:update", args=[event.slug]),
                    reverse("events:delete", args=[event.slug])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertEqual(response.url, reverse("dashboard:organizer"))

    def test_owner_can_open_manage_page(self):
        event = self.make_event()
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("events:manage", args=[event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, event.title)

    def test_admin_can_manage_any_event(self):
        event = self.make_event()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("events:manage", args=[event.slug]))
        self.assertEqual(response.status_code, 200)

    def test_participants_cannot_see_draft_events(self):
        draft = self.make_event(status=EventStatus.DRAFT, title="Secret draft")
        response = self.client.get(reverse("events:detail", args=[draft.slug]))
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.organizer)
        self.assertEqual(self.client.get(reverse("events:detail", args=[draft.slug])).status_code, 200)


class EventCrudViewTests(BaseEventTestCase):
    def setUp(self):
        self.client.force_login(self.organizer)
        self.payload = {
            "title": "New Live Event", "summary": "Short summary",
            "description": "Long description text",
            "category": self.category.pk, "venue": self.venue.pk,
            "start_time": (self.now + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M"),
            "end_time": (self.now + timedelta(days=15, hours=5)).strftime("%Y-%m-%dT%H:%M"),
            "registration_deadline": (self.now + timedelta(days=12)).strftime("%Y-%m-%dT%H:%M"),
            "capacity": 60, "price": "499.00", "status": EventStatus.PUBLISHED,
            "sessions-TOTAL_FORMS": "0", "sessions-INITIAL_FORMS": "0",
            "sessions-MIN_NUM_FORMS": "0", "sessions-MAX_NUM_FORMS": "25",
        }

    def test_create_event_assigns_owner(self):
        response = self.client.post(reverse("events:create"), self.payload, follow=True)
        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(title="New Live Event")
        self.assertEqual(event.organizer, self.organizer)
        self.assertEqual(event.status, EventStatus.PUBLISHED)
        self.assertContains(response, "saved as published")

    def test_create_rejects_end_before_start(self):
        payload = self.payload | {
            "end_time": (self.now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M"),
        }
        response = self.client.post(reverse("events:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.filter(title="New Live Event").exists())
        self.assertContains(response, "end time must be after the start time", html=False)

    def test_create_rejects_past_deadline_for_published_event(self):
        payload = self.payload | {
            "registration_deadline": (self.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        }
        response = self.client.post(reverse("events:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "future registration deadline", html=False)

    def test_create_rejects_capacity_over_venue_limit(self):
        small = Venue.objects.create(name="Tiny Room", city="Pune", capacity=10)
        payload = self.payload | {"venue": small.pk, "capacity": 40}
        response = self.client.post(reverse("events:create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "can host at most 10 attendees", html=False)

    def test_update_event_by_owner(self):
        event = self.make_event()
        response = self.client.post(reverse("events:update", args=[event.slug]), {
            **self.payload,
            "title": "Renamed Event",
            "start_time": event.start_time.strftime("%Y-%m-%dT%H:%M"),
            "end_time": event.end_time.strftime("%Y-%m-%dT%H:%M"),
            "registration_deadline": event.registration_deadline.strftime("%Y-%m-%dT%H:%M"),
        }, follow=True)
        event.refresh_from_db()
        self.assertEqual(event.title, "Renamed Event")
        self.assertContains(response, "updated successfully")

    def test_delete_event_removes_registrations(self):
        event = self.make_event()
        Registration.objects.create(event=event, participant=self.participant)
        response = self.client.post(reverse("events:delete", args=[event.slug]), follow=True)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        self.assertEqual(Registration.objects.count(), 0)

    def test_publish_and_unpublish_flow(self):
        draft = self.make_event(status=EventStatus.DRAFT, title="Publish me")
        response = self.client.post(reverse("events:publish", args=[draft.slug]), {"action": "publish"}, follow=True)
        draft.refresh_from_db()
        self.assertEqual(draft.status, EventStatus.PUBLISHED)
        self.assertContains(response, "now live")

        self.client.post(reverse("events:publish", args=[draft.slug]), {"action": "unpublish"})
        draft.refresh_from_db()
        self.assertEqual(draft.status, EventStatus.DRAFT)

    def test_duplicate_event_creates_draft_copy(self):
        event = self.make_event()
        EventSession.objects.create(event=event, title="Opening", start_time=event.start_time,
                                    end_time=event.start_time + timedelta(hours=1))
        response = self.client.post(reverse("events:duplicate", args=[event.slug]), follow=True)
        copy = Event.objects.get(title=f"{event.title} (copy)")
        self.assertEqual(copy.status, EventStatus.DRAFT)
        self.assertEqual(copy.sessions.count(), 1)
        self.assertContains(response, "duplicated as a draft")


class EventDiscoveryTests(BaseEventTestCase):
    def setUp(self):
        self.tech = Category.objects.create(name="Music", icon="music")
        self.sports_venue = Venue.objects.create(name="Arena", city="Mohali", capacity=500)
        self.a = self.make_event(title="Alpha Tech Meetup", capacity=10)
        self.b = self.make_event(title="Beta Music Night", category=self.tech,
                                 venue=self.sports_venue, capacity=5)
        self.c = self.make_event(title="Gamma Draft", status=EventStatus.DRAFT)

    def test_list_only_shows_published(self):
        response = self.client.get(reverse("events:list"))
        self.assertContains(response, "Alpha Tech Meetup")
        self.assertContains(response, "Beta Music Night")
        self.assertNotContains(response, "Gamma Draft")

    def test_search_by_title_and_venue(self):
        response = self.client.get(reverse("events:list"), {"q": "Music"})
        self.assertContains(response, "Beta Music Night")
        self.assertNotContains(response, "Alpha Tech Meetup")
        response = self.client.get(reverse("events:list"), {"q": "Arena"})
        self.assertContains(response, "Beta Music Night")

    def test_filter_by_category(self):
        response = self.client.get(reverse("events:list"), {"category": self.tech.slug})
        self.assertContains(response, "Beta Music Night")
        self.assertNotContains(response, "Alpha Tech Meetup")

    def test_filter_by_availability_full(self):
        for i in range(5):
            user = User.objects.create_user(f"f{i}", f"f{i}@example.com", "pw12345678")
            Registration.objects.create(event=self.b, participant=user)
        response = self.client.get(reverse("events:list"), {"availability": "full"})
        self.assertContains(response, "Beta Music Night")
        self.assertNotContains(response, "Alpha Tech Meetup")

    def test_sorting_by_newest_first(self):
        response = self.client.get(reverse("events:list"), {"sort": "title"})
        titles = [e.title for e in response.context["events"]]
        self.assertEqual(titles, sorted(titles))

    def test_pagination_limits_results(self):
        for index in range(12):
            self.make_event(title=f"Bulk event {index}", start_time=self.now + timedelta(days=20 + index),
                            end_time=self.now + timedelta(days=20 + index, hours=2),
                            registration_deadline=self.now + timedelta(days=18))
        response = self.client.get(reverse("events:list"))
        self.assertEqual(len(response.context["events"]), 9)
        self.assertTrue(response.context["page_obj"].has_next())

    def test_ajax_request_returns_partial(self):
        response = self.client.get(reverse("events:list"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTemplateUsed(response, "events/partials/event_grid.html")

    def test_detail_page_of_published_event(self):
        response = self.client.get(reverse("events:detail", args=[self.a.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register now")


class EventServiceTests(BaseEventTestCase):
    def test_publish_requires_future_deadline(self):
        event = self.make_event(status=EventStatus.DRAFT,
                                registration_deadline=self.now - timedelta(days=1))
        with self.assertRaises(EventStateError):
            publish_event(event, self.organizer)

    def test_complete_only_published_events(self):
        draft = self.make_event(status=EventStatus.DRAFT)
        with self.assertRaises(EventStateError):
            complete_event(draft, self.organizer)
        published = self.make_event(title="Finish me")
        complete_event(published, self.organizer)
        published.refresh_from_db()
        self.assertEqual(published.status, EventStatus.COMPLETED)

    def test_cancel_event_cancels_and_notifies_registrations(self):
        event = self.make_event()
        registration = Registration.objects.create(event=event, participant=self.participant)
        cancel_event(event, self.organizer, "Venue flooded")
        event.refresh_from_db()
        registration.refresh_from_db()
        self.assertEqual(event.status, EventStatus.CANCELLED)
        self.assertEqual(event.cancellation_reason, "Venue flooded")
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertTrue(self.participant.notifications.filter(event=event).exists())


class VenueAndCategoryTests(BaseEventTestCase):
    def test_organizer_can_create_venue(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("events:venue_create"), {
            "name": "New Hall", "address_line": "1 Road", "city": "Delhi", "state": "Delhi",
            "country": "India", "postal_code": "110001", "capacity": 100,
            "map_url": "", "description": "Nice hall", "is_active": "on",
        }, follow=True)
        self.assertTrue(Venue.objects.filter(name="New Hall").exists())
        self.assertContains(response, "added")

    def test_duplicate_venue_in_same_city_rejected(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("events:venue_create"), {
            "name": self.venue.name, "address_line": "", "city": self.venue.city, "state": "",
            "country": "India", "postal_code": "", "capacity": 10, "map_url": "", "description": "",
        })
        self.assertContains(response, "already exists", html=False)

    def test_only_admin_can_access_categories(self):
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("events:categories"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("events:categories")).status_code, 200)

    def test_admin_can_create_category(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("events:category_create"), {
            "name": "Gaming", "description": "Esports", "color": "#22c55e", "icon": "trophy",
        }, follow=True)
        self.assertTrue(Category.objects.filter(name="Gaming").exists())


class EventFormTests(BaseEventTestCase):
    def test_form_exposes_status_but_hides_featured_for_organizers(self):
        form = EventForm(user=self.organizer)
        self.assertNotIn("is_featured", form.fields)
        self.assertIn("status", form.fields)
        admin_form = EventForm(user=self.admin)
        self.assertIn("is_featured", admin_form.fields)
