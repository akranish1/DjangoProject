"""Announcements and in-app notification tests."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Category, Event, EventStatus, Venue
from registrations.models import RegistrationStatus
from registrations.services import register_for_event

from .models import Announcement, Notification

User = get_user_model()


class AnnouncementTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.organizer = User.objects.create_user("org", "org@example.com", "pw12345678",
                                                 role=User.Role.ORGANIZER, first_name="Olive")
        cls.other_organizer = User.objects.create_user("org2", "org2@example.com", "pw12345678",
                                                       role=User.Role.ORGANIZER)
        cls.participant = User.objects.create_user("part", "part@example.com", "pw12345678")
        cls.other_participant = User.objects.create_user("other", "other@example.com", "pw12345678")
        cls.category = Category.objects.create(name="Technology", icon="code")
        cls.venue = Venue.objects.create(name="Hub", city="Chandigarh", capacity=100)
        cls.event = Event.objects.create(
            organizer=cls.organizer, category=cls.category, venue=cls.venue,
            title="Announcement Event", description="d",
            start_time=cls.now + timedelta(days=5), end_time=cls.now + timedelta(days=5, hours=3),
            registration_deadline=cls.now + timedelta(days=3), capacity=20,
            status=EventStatus.PUBLISHED,
        )


class AnnouncementTests(AnnouncementTestCase):
    def test_organizer_can_post_announcement_and_notify_registrants(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.post(
            reverse("announcements:create", args=[self.event.slug]),
            {"title": "Doors open at 9 AM", "body": "Please arrive 15 minutes early with your ticket code.",
             "notify": "on"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        announcement = Announcement.objects.get(event=self.event)
        self.assertEqual(announcement.author, self.organizer)
        self.assertEqual(announcement.notified_count, 1)
        self.assertTrue(self.participant.notifications.filter(title__icontains="Doors open").exists())
        self.assertFalse(self.organizer.notifications.filter(title__icontains="Doors open").exists())

    def test_announcement_without_notify_does_not_create_notifications(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        self.client.post(reverse("announcements:create", args=[self.event.slug]),
                         {"title": "Silent note", "body": "Saved only on the event page."})
        self.assertFalse(self.participant.notifications.filter(title__icontains="Silent note").exists())

    def test_short_body_is_rejected(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("announcements:create", args=[self.event.slug]),
                                    {"title": "Too short", "body": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Announcement.objects.exists())

    def test_other_organizer_cannot_post_announcement(self):
        self.client.force_login(self.other_organizer)
        response = self.client.post(reverse("announcements:create", args=[self.event.slug]),
                                    {"title": "Hijack", "body": "Not allowed to post this at all."},
                                    follow=True)
        self.assertFalse(Announcement.objects.exists())
        self.assertContains(response, "only post announcements for events you organise")

    def test_announcement_visible_on_event_page(self):
        announcement = Announcement.objects.create(event=self.event, author=self.organizer,
                                                   title="Gate 3 is open", body="Use gate 3 for faster entry.")
        response = self.client.get(reverse("events:detail", args=[self.event.slug]))
        self.assertContains(response, "Gate 3 is open")

    def test_pin_and_delete_announcement(self):
        announcement = Announcement.objects.create(event=self.event, author=self.organizer,
                                                   title="Pin me", body="Body text for pinning test.")
        self.client.force_login(self.organizer)
        self.client.post(reverse("announcements:toggle_pin", args=[announcement.pk]))
        announcement.refresh_from_db()
        self.assertTrue(announcement.is_pinned)
        self.client.post(reverse("announcements:delete", args=[announcement.pk]))
        self.assertFalse(Announcement.objects.filter(pk=announcement.pk).exists())

    def test_other_organizer_cannot_delete_announcement(self):
        announcement = Announcement.objects.create(event=self.event, author=self.organizer,
                                                   title="Mine", body="Body text for deletion test.")
        self.client.force_login(self.other_organizer)
        self.client.post(reverse("announcements:delete", args=[announcement.pk]))
        self.assertTrue(Announcement.objects.filter(pk=announcement.pk).exists())

    def test_announcement_detail_page_is_public_for_published_events(self):
        announcement = Announcement.objects.create(event=self.event, author=self.organizer,
                                                   title="Detail page", body="Body text for the detail page.")
        response = self.client.get(reverse("announcements:detail", args=[announcement.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail page")

    def test_announcement_on_draft_event_is_private(self):
        draft = Event.objects.create(
            organizer=self.organizer, category=self.category, venue=self.venue,
            title="Draft event", description="d",
            start_time=self.now + timedelta(days=9), end_time=self.now + timedelta(days=9, hours=2),
            registration_deadline=self.now + timedelta(days=8), capacity=10,
            status=EventStatus.DRAFT,
        )
        announcement = Announcement.objects.create(event=draft, author=self.organizer,
                                                   title="Internal note", body="Not for the public yet.")
        response = self.client.get(reverse("announcements:detail", args=[announcement.pk]))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.organizer)
        self.assertEqual(self.client.get(reverse("announcements:detail", args=[announcement.pk])).status_code, 200)


class BroadcastTests(AnnouncementTestCase):
    def test_organizer_broadcast_reaches_active_registrants(self):
        register_for_event(self.event, self.participant)
        register_for_event(self.event, self.other_participant)
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("events:broadcast", args=[self.event.slug]),
                                    {"title": "Venue change", "message": "We moved to hall B for all sessions."},
                                    follow=True)
        self.assertContains(response, "Notification delivered to 2 participant(s)")
        self.assertTrue(Notification.objects.filter(recipient=self.participant).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.other_participant).exists())

    def test_broadcast_validation(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("events:broadcast", args=[self.event.slug]),
                                    {"title": "x", "message": "y"})
        self.assertContains(response, "Please provide a subject")

    def test_other_organizer_cannot_broadcast(self):
        self.client.force_login(self.other_organizer)
        response = self.client.get(reverse("events:broadcast", args=[self.event.slug]), follow=True)
        self.assertContains(response, "cannot message the participants")
        self.assertNotContains(response, "Send notification")


class NotificationTests(AnnouncementTestCase):
    def setUp(self):
        self.notification = Notification.objects.create(
            recipient=self.participant, title="Registration confirmed",
            message="You are registered.", kind=Notification.Kind.REGISTRATION,
            link="/registrations/mine/",
        )

    def test_notification_list_requires_login(self):
        response = self.client.get(reverse("announcements:notifications"))
        self.assertEqual(response.status_code, 302)

    def test_notification_list_shows_own_notifications(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("announcements:notifications"))
        self.assertContains(response, "Registration confirmed")
        self.assertEqual(response.context["unread_count"], 1)

    def test_unread_filter(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("announcements:notifications"), {"state": "unread"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.notification.mark_read()
        response = self.client.get(reverse("announcements:notifications"), {"state": "unread"})
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        response = self.client.get(reverse("announcements:notifications"), {"state": "read"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_kind_filter(self):
        Notification.objects.create(recipient=self.participant, title="Event moved",
                                    kind=Notification.Kind.EVENT)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("announcements:notifications"), {"kind": "event"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_notification_is_private_to_recipient(self):
        self.client.force_login(self.other_participant)
        response = self.client.get(reverse("announcements:notifications"))
        self.assertNotContains(response, "Registration confirmed")

    def test_mark_read_endpoint_returns_unread_count(self):
        self.client.force_login(self.participant)
        response = self.client.post(reverse("announcements:mark_read", args=[self.notification.pk]),
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unread"], 0)
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)
        self.assertIsNotNone(self.notification.read_at)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.participant, title="Second")
        self.client.force_login(self.participant)
        response = self.client.post(reverse("announcements:mark_all_read"),
                                    {"next": reverse("announcements:notifications")}, follow=True)
        self.assertEqual(Notification.objects.filter(recipient=self.participant, is_read=False).count(), 0)
        self.assertContains(response, "marked as read")

    def test_unread_badge_appears_in_dashboard(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("dashboard:participant"))
        self.assertEqual(response.context["unread"], 1)
        self.assertContains(response, "data-bell-count")

    def test_opening_notification_redirects_to_link(self):
        self.client.force_login(self.participant)
        response = self.client.post(reverse("announcements:open", args=[self.notification.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/registrations/mine/")
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)
