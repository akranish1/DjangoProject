"""Dashboard metrics, admin user management and export tests."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from announcements.models import Announcement, Notification
from events.models import Category, Event, EventStatus, Venue
from registrations.models import Attendance, Registration, RegistrationStatus
from registrations.services import mark_attendance, register_for_event

User = get_user_model()


class DashboardBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.admin = User.objects.create_superuser("root", "root@example.com", "pw12345678",
                                                  first_name="Root")
        cls.organizer = User.objects.create_user("org", "org@example.com", "pw12345678",
                                                 role=User.Role.ORGANIZER, first_name="Olive",
                                                 last_name="Reed")
        cls.participant = User.objects.create_user("part", "part@example.com", "pw12345678",
                                                   first_name="Pam")
        cls.category = Category.objects.create(name="Technology", icon="code")
        cls.venue = Venue.objects.create(name="Hub", city="Chandigarh", capacity=100)
        cls.event = Event.objects.create(
            organizer=cls.organizer, category=cls.category, venue=cls.venue,
            title="Metrics Event", description="d",
            start_time=cls.now + timedelta(days=4), end_time=cls.now + timedelta(days=4, hours=3),
            registration_deadline=cls.now + timedelta(days=2), capacity=10,
            status=EventStatus.PUBLISHED,
        )


class OrganizerDashboardTests(DashboardBaseTestCase):
    def test_metrics_come_from_database(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("dashboard:organizer"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totals"]["published"], 1)
        self.assertEqual(response.context["registration_stats"]["confirmed"], 1)
        self.assertEqual(response.context["seats_used"], 1)
        self.assertEqual(response.context["seats_free"], 9)
        self.assertEqual(response.context["capacity"], 10)
        self.assertContains(response, "Metrics Event")

    def test_organizer_only_sees_own_events(self):
        other_organizer = User.objects.create_user("org2", "org2@example.com", "pw12345678",
                                                   role=User.Role.ORGANIZER)
        Event.objects.create(
            organizer=other_organizer, category=self.category, venue=self.venue,
            title="Someone Else Event", description="d",
            start_time=self.now + timedelta(days=9), end_time=self.now + timedelta(days=9, hours=2),
            registration_deadline=self.now + timedelta(days=8), capacity=5,
            status=EventStatus.PUBLISHED,
        )
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("dashboard:organizer"))
        self.assertContains(response, "Metrics Event")
        self.assertNotContains(response, "Someone Else Event")

    def test_chart_data_endpoint_returns_series(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("dashboard:organizer_chart"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["registrations"]["labels"]), 14)
        self.assertEqual(sum(payload["registrations"]["values"]), 1)

    def test_participant_cannot_open_organizer_chart(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:organizer_chart"))
        self.assertEqual(response.status_code, 403)


class ParticipantDashboardTests(DashboardBaseTestCase):
    def test_participant_metrics_and_upcoming(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:participant"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"]["confirmed"], 1)
        self.assertEqual(response.context["upcoming_count"], 1)
        self.assertContains(response, "Metrics Event")

    def test_participant_does_not_see_other_people_registrations(self):
        other = User.objects.create_user("other", "other@example.com", "pw12345678")
        register_for_event(self.event, other)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:participant"))
        self.assertEqual(response.context["stats"]["total"], 0)

    def test_recommendations_exclude_own_registrations(self):
        second = Event.objects.create(
            organizer=self.organizer, category=self.category, venue=self.venue,
            title="Recommended Event", description="d",
            start_time=self.now + timedelta(days=6), end_time=self.now + timedelta(days=6, hours=2),
            registration_deadline=self.now + timedelta(days=5), capacity=20,
            status=EventStatus.PUBLISHED,
        )
        register_for_event(self.event, self.participant)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:participant"))
        recommended = list(response.context["recommendations"])
        self.assertIn(second, recommended)
        self.assertNotIn(self.event, recommended)


class AdminDashboardTests(DashboardBaseTestCase):
    def test_platform_statistics(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:admin"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user_stats"]["total"], 3)
        self.assertEqual(response.context["event_stats"]["published"], 1)
        self.assertEqual(response.context["registration_stats"]["confirmed"], 1)
        self.assertEqual(response.context["fill_rate"], 10)

    def test_chart_endpoint(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:admin_chart"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["series"]), 2)

    def test_non_admin_blocked(self):
        self.client.force_login(self.organizer)
        for url in [reverse("dashboard:admin"), reverse("dashboard:admin_users"),
                    reverse("dashboard:admin_activity")]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_user_list_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:admin_users"), {"role": "organizer"})
        self.assertContains(response, "org@example.com")
        self.assertNotContains(response, "part@example.com")

    def test_role_change_records_activity(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("dashboard:admin_user", args=[self.participant.pk]),
                                    {"role-role": "organizer", "save_role": "1"}, follow=True)
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.role, User.Role.ORGANIZER)
        self.assertTrue(self.admin.activities.filter(action="user_role_changed").exists())
        self.assertContains(response, "is now a")

    def test_cannot_demote_organizer_that_owns_events(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("dashboard:admin_user", args=[self.organizer.pk]),
                         {"role-role": "participant", "save_role": "1"})
        self.organizer.refresh_from_db()
        self.assertEqual(self.organizer.role, User.Role.ORGANIZER)

    def test_toggle_user_activation(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("dashboard:admin_toggle_user", args=[self.participant.pk]),
                         {"next": reverse("dashboard:admin_users")})
        self.participant.refresh_from_db()
        self.assertFalse(self.participant.is_active)

    def test_admin_cannot_deactivate_self(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("dashboard:admin_toggle_user", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_event_and_registration_moderation_pages(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard:admin_events")), "Metrics Event")
        self.assertContains(self.client.get(reverse("dashboard:admin_registrations")), "EV-")
        self.assertEqual(self.client.get(reverse("dashboard:admin_activity")).status_code, 200)

    def test_csv_exports(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.admin)
        users_csv = self.client.get(reverse("dashboard:export_users"))
        registrations_csv = self.client.get(reverse("dashboard:export_registrations"))
        self.assertEqual(users_csv.status_code, 200)
        self.assertIn("text/csv", users_csv["Content-Type"])
        self.assertIn("part@example.com", users_csv.content.decode())
        self.assertIn("Metrics Event", registrations_csv.content.decode())

    def test_attendance_metrics_in_admin_registrations(self):
        past = Event.objects.create(
            organizer=self.organizer, category=self.category, venue=self.venue,
            title="Past Event", description="d",
            start_time=self.now - timedelta(days=2), end_time=self.now - timedelta(days=2) + timedelta(hours=3),
            registration_deadline=self.now - timedelta(days=3), capacity=10,
            status=EventStatus.COMPLETED,
        )
        registration = Registration.objects.create(event=past, participant=self.participant)
        mark_attendance(registration, self.organizer, Attendance.Status.PRESENT)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard:admin_registrations"))
        self.assertContains(response, "Present")
