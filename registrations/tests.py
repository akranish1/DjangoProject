"""Registration integrity, attendance and concurrency tests."""
from datetime import timedelta
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Category, Event, EventStatus, Venue
from registrations.models import Attendance, Registration, RegistrationStatus, WaitlistEntry
from registrations.services import (
    RegistrationError, attendance_summary, bulk_mark_attendance, cancel_registration,
    mark_attendance, register_for_event,
)

User = get_user_model()


class RegistrationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user("org", "org@example.com", "pw12345678",
                                                 role=User.Role.ORGANIZER, first_name="Olive")
        cls.other_organizer = User.objects.create_user("org2", "org2@example.com", "pw12345678",
                                                       role=User.Role.ORGANIZER)
        cls.admin = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        cls.category = Category.objects.create(name="Technology", icon="code")
        cls.venue = Venue.objects.create(name="Hub", city="Chandigarh", capacity=300)
        cls.now = timezone.now()

    def make_event(self, **overrides):
        defaults = {
            "organizer": self.organizer, "category": self.category, "venue": self.venue,
            "title": "Test Event", "description": "d",
            "start_time": self.now + timedelta(days=7),
            "end_time": self.now + timedelta(days=7, hours=4),
            "registration_deadline": self.now + timedelta(days=5),
            "capacity": 3, "status": EventStatus.PUBLISHED,
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    _participant_counter = 0

    def make_participant(self, index=None):
        type(self)._participant_counter += 1
        index = index or type(self)._participant_counter
        return User.objects.create_user(f"p{index}", f"p{index}@example.com", "pw12345678",
                                        first_name=f"P{index}")

    def make_past_registration(self, event, participant, status=RegistrationStatus.CONFIRMED):
        """Create a historical registration directly (past the deadline window)."""
        registration = Registration(event=event, participant=participant, status=status,
                                    ticket_code=Registration.generate_ticket_code())
        registration.save()
        return registration


class RegistrationServiceTests(RegistrationTestCase):
    def test_successful_registration_creates_ticket_and_notification(self):
        event = self.make_event()
        participant = self.make_participant()
        registration, created = register_for_event(event, participant, "Window seat please")
        self.assertTrue(created)
        self.assertEqual(registration.status, RegistrationStatus.CONFIRMED)
        self.assertTrue(registration.ticket_code.startswith("EV-"))
        self.assertEqual(registration.notes, "Window seat please")
        self.assertTrue(participant.notifications.filter(event=event).exists())
        self.assertTrue(event.organizer.notifications.filter(event=event).exists())

    def test_duplicate_registration_is_rejected(self):
        event = self.make_event()
        participant = self.make_participant()
        register_for_event(event, participant)
        with self.assertRaises(RegistrationError):
            register_for_event(event, participant)
        self.assertEqual(Registration.objects.filter(event=event, participant=participant).count(), 1)

    def test_database_constraint_blocks_duplicate_active_rows(self):
        event = self.make_event()
        participant = self.make_participant()
        Registration.objects.create(event=event, participant=participant)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Registration.objects.create(event=event, participant=participant)

    def test_organizer_cannot_register_for_own_event(self):
        event = self.make_event()
        with self.assertRaises(RegistrationError):
            register_for_event(event, self.organizer)

    def test_deadline_is_enforced(self):
        event = self.make_event(registration_deadline=self.now - timedelta(hours=1))
        with self.assertRaises(RegistrationError) as ctx:
            register_for_event(event, self.make_participant())
        self.assertIn("deadline", str(ctx.exception).lower())

    def test_draft_and_cancelled_events_reject_registrations(self):
        draft = self.make_event(status=EventStatus.DRAFT, title="Draft")
        cancelled = self.make_event(status=EventStatus.CANCELLED, title="Cancelled")
        for event in (draft, cancelled):
            with self.assertRaises(RegistrationError):
                register_for_event(event, self.make_participant())

    def test_capacity_enforcement_and_waitlist(self):
        event = self.make_event(capacity=2)
        first, second, third = [self.make_participant(i) for i in range(1, 4)]
        register_for_event(event, first)
        register_for_event(event, second)
        waitlisted, _ = register_for_event(event, third)
        self.assertEqual(waitlisted.status, RegistrationStatus.WAITLISTED)
        self.assertEqual(waitlisted.waitlist_position, 1)
        self.assertEqual(event.registrations.confirmed().count(), 2)

    def test_cancellation_promotes_oldest_waitlisted_participant(self):
        event = self.make_event(capacity=1)
        holder, waiter = self.make_participant(1), self.make_participant(2)
        registration, _ = register_for_event(event, holder)
        waitlisted, _ = register_for_event(event, waiter)
        cancel_registration(registration, holder, "Cannot make it")
        waitlisted.refresh_from_db()
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertEqual(waitlisted.status, RegistrationStatus.CONFIRMED)
        self.assertTrue(waiter.notifications.filter(title__icontains="waitlist").exists())
        self.assertFalse(WaitlistEntry.objects.filter(registration=waitlisted).exists())

    def test_participant_cancellation_blocked_after_deadline(self):
        event = self.make_event(registration_deadline=self.now - timedelta(minutes=5))
        participant = self.make_participant()
        registration = Registration.objects.create(event=event, participant=participant)
        with self.assertRaises(RegistrationError):
            cancel_registration(registration, participant)

    def test_organizer_can_cancel_after_deadline(self):
        event = self.make_event(registration_deadline=self.now - timedelta(minutes=5))
        participant = self.make_participant()
        registration = Registration.objects.create(event=event, participant=participant)
        cancel_registration(registration, self.organizer, "Removed on request")
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertEqual(registration.cancelled_by, self.organizer)

    def test_third_party_cannot_cancel_someone_elses_registration(self):
        event = self.make_event()
        participant, stranger = self.make_participant(1), self.make_participant(2)
        registration, _ = register_for_event(event, participant)
        with self.assertRaises(RegistrationError):
            cancel_registration(registration, stranger)

    def test_re_registering_after_cancellation_reuses_the_row(self):
        event = self.make_event(capacity=5)
        participant = self.make_participant()
        registration, _ = register_for_event(event, participant)
        cancel_registration(registration, participant)
        again, created = register_for_event(event, participant)
        self.assertEqual(again.pk, registration.pk)
        self.assertFalse(created)
        self.assertEqual(again.status, RegistrationStatus.CONFIRMED)
        self.assertIsNone(again.cancelled_at)


class RegistrationViewTests(RegistrationTestCase):
    def setUp(self):
        self.participant = self.make_participant(1)
        self.event = self.make_event()

    def test_register_page_requires_login(self):
        response = self.client.get(reverse("registrations:register", args=[self.event.slug]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_participant_registration_flow(self):
        self.client.force_login(self.participant)
        response = self.client.post(
            reverse("registrations:register", args=[self.event.slug]),
            {"notes": "Extra chair", "agree": "on"}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        registration = Registration.objects.get(event=self.event, participant=self.participant)
        self.assertContains(response, "saved it to your dashboard")
        self.assertContains(response, registration.ticket_code)

    def test_registration_form_requires_agreement(self):
        self.client.force_login(self.participant)
        self.client.post(reverse("registrations:register", args=[self.event.slug]), {"notes": ""})
        self.assertFalse(Registration.objects.filter(event=self.event).exists())

    def test_my_registrations_only_lists_own_records(self):
        other = self.make_participant(2)
        mine, _ = register_for_event(self.event, self.participant)
        theirs, _ = register_for_event(self.event, other)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("registrations:mine"))
        self.assertContains(response, mine.ticket_code)
        self.assertNotContains(response, theirs.ticket_code)

    def test_ticket_page_is_private(self):
        other = self.make_participant(3)
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(other)
        response = self.client.get(reverse("registrations:detail", args=[registration.ticket_code]))
        self.assertEqual(response.status_code, 404)

    def test_organizer_can_view_participant_ticket(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("registrations:detail", args=[registration.ticket_code]))
        self.assertEqual(response.status_code, 200)

    def test_cancellation_flow_from_ui(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.participant)
        response = self.client.post(
            reverse("registrations:cancel", args=[registration.ticket_code]),
            {"reason": "Travel clash", "confirm": "on"}, follow=True,
        )
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertContains(response, "was cancelled")

    def test_cancellation_requires_confirmation_checkbox(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.participant)
        self.client.post(reverse("registrations:cancel", args=[registration.ticket_code]), {"reason": "x"})
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CONFIRMED)

    def test_participant_list_is_organizer_only(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("registrations:participants", args=[self.event.slug]))
        self.assertEqual(response.status_code, 403)

    def test_other_organizer_cannot_see_participants(self):
        self.client.force_login(self.other_organizer)
        response = self.client.get(reverse("registrations:participants", args=[self.event.slug]))
        self.assertEqual(response.status_code, 302)

    def test_organizer_sees_participant_list(self):
        register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("registrations:participants", args=[self.event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.participant.display_name)

    def test_status_update_endpoint_by_organizer(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.post(
            reverse("registrations:update_status", args=[registration.pk]),
            {"status": "no_show"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "no_show")
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.NO_SHOW)

    def test_status_update_rejected_for_other_organizer(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.other_organizer)
        response = self.client.post(reverse("registrations:update_status", args=[registration.pk]),
                                    {"status": "no_show"})
        self.assertEqual(response.status_code, 302)

    def test_csv_export_contains_participants(self):
        registration, _ = register_for_event(self.event, self.participant)
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("registrations:export", args=[self.event.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn(registration.ticket_code, body)
        self.assertIn(self.participant.email, body)


class AttendanceTests(RegistrationTestCase):
    def setUp(self):
        self.participant = self.make_participant(1)
        self.event = self.make_event(start_time=self.now - timedelta(hours=2),
                                     end_time=self.now + timedelta(hours=2),
                                     registration_deadline=self.now - timedelta(days=1))
        # the registration window has closed, so seed the record directly
        self.registration = self.make_past_registration(self.event, self.participant)

    def test_organizer_marks_attendance_with_timestamp(self):
        attendance = mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)
        self.assertIsNotNone(attendance.checked_in_at)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, RegistrationStatus.ATTENDED)

    def test_other_organizer_cannot_mark_attendance(self):
        with self.assertRaises(RegistrationError):
            mark_attendance(self.registration, self.other_organizer, Attendance.Status.PRESENT)

    def test_absent_clears_check_in_timestamp(self):
        mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)
        attendance = mark_attendance(self.registration, self.organizer, Attendance.Status.ABSENT)
        self.assertIsNone(attendance.checked_in_at)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, RegistrationStatus.CONFIRMED)

    def test_attendance_cannot_be_recorded_for_cancelled_registration(self):
        self.registration.status = RegistrationStatus.CANCELLED
        self.registration.save(update_fields=["status"])
        with self.assertRaises(RegistrationError):
            mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)

    def test_one_attendance_row_per_registration(self):
        mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)
        mark_attendance(self.registration, self.organizer, Attendance.Status.LATE)
        self.assertEqual(Attendance.objects.filter(registration=self.registration).count(), 1)

    def test_summary_aggregates_correctly(self):
        second = self.make_participant()
        self.make_past_registration(self.event, second)
        mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)
        summary = attendance_summary(self.event)
        self.assertEqual(summary["present"], 1)
        self.assertEqual(summary["marked"], 1)
        self.assertEqual(summary["unmarked"], 1)
        self.assertEqual(summary["attendance_rate"], 100)

    def test_bulk_attendance_sheet(self):
        second = self.make_participant()
        second_registration = self.make_past_registration(self.event, second)
        payload = {
            str(self.registration.pk): {"status": "late", "note": "Traffic"},
            str(second_registration.pk): {"status": "absent", "note": ""},
        }
        updated = bulk_mark_attendance(self.event, self.organizer, payload)
        self.assertEqual(updated, 2)
        self.assertEqual(Attendance.objects.get(registration=self.registration).status, "late")
        self.assertEqual(Attendance.objects.get(registration=second_registration).status, "absent")

    def test_attendance_sheet_page_and_save(self):
        self.client.force_login(self.organizer)
        response = self.client.get(reverse("registrations:attendance", args=[self.event.slug]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("registrations:attendance_save", args=[self.event.slug]), {
            f"status_{self.registration.pk}": "present",
            f"note_{self.registration.pk}": "Front row",
        }, follow=True)
        self.assertContains(response, "Attendance saved")
        self.assertEqual(Attendance.objects.count(), 1)

    def test_check_in_by_ticket_code(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("registrations:check_in", args=[self.event.slug]),
                                    {"ticket_code": self.registration.ticket_code.lower()},
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(Attendance.objects.filter(registration=self.registration).exists())

    def test_check_in_unknown_ticket_returns_404_json(self):
        self.client.force_login(self.organizer)
        response = self.client.post(reverse("registrations:check_in", args=[self.event.slug]),
                                    {"ticket_code": "EV-NOPE123"},
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])

    def test_participant_cannot_open_attendance_sheet(self):
        self.client.force_login(self.participant)
        response = self.client.get(reverse("registrations:attendance", args=[self.event.slug]))
        self.assertEqual(response.status_code, 403)

    def test_participant_sees_own_attendance_history(self):
        mark_attendance(self.registration, self.organizer, Attendance.Status.PRESENT)
        self.client.force_login(self.participant)
        response = self.client.get(reverse("registrations:my_attendance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Present")


class ConcurrentRegistrationTests(TransactionTestCase):
    """Capacity must hold even when requests arrive at the same instant."""

    reset_sequences = True

    def setUp(self):
        self.organizer = User.objects.create_user("corg", "corg@example.com", "pw12345678",
                                                  role=User.Role.ORGANIZER)
        self.category = Category.objects.create(name="Concurrency", icon="spark")
        self.venue = Venue.objects.create(name="Thread Hall", city="Pune", capacity=100)
        now = timezone.now()
        self.event = Event.objects.create(
            organizer=self.organizer, category=self.category, venue=self.venue,
            title="Race Condition Live", description="d",
            start_time=now + timedelta(days=5), end_time=now + timedelta(days=5, hours=3),
            registration_deadline=now + timedelta(days=3), capacity=5, status=EventStatus.PUBLISHED,
        )
        self.participants = [
            User.objects.create_user(f"race{i}", f"race{i}@example.com", "pw12345678")
            for i in range(12)
        ]

    def _register_in_thread(self, participant, barrier, results):
        # each thread needs its own connection
        barrier.wait()
        try:
            register_for_event(Event.objects.get(pk=self.event.pk), participant)
            results.append("confirmed")
        except RegistrationError:
            results.append("rejected")
        except Exception as exc:  # pragma: no cover - surfaces unexpected failures
            results.append(f"error:{type(exc).__name__}")
        finally:
            connections.close_all()

    def test_no_overbooking_under_concurrent_registrations(self):
        barrier = Barrier(len(self.participants))
        results = []
        threads = [Thread(target=self._register_in_thread, args=(p, barrier, results))
                   for p in self.participants]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        seated = Registration.objects.filter(
            event=self.event, status__in=[RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED]
        ).count()
        confirmed = Registration.objects.filter(event=self.event, status=RegistrationStatus.CONFIRMED).count()
        waitlisted = Registration.objects.filter(event=self.event, status=RegistrationStatus.WAITLISTED).count()

        self.assertEqual(seated, len(self.participants), results)
        self.assertEqual(confirmed, 5, f"capacity breached: {results}")
        self.assertEqual(waitlisted, len(self.participants) - 5)

    def test_concurrent_registration_of_the_same_user_creates_one_row(self):
        participant = self.participants[0]
        barrier = Barrier(6)
        results = []

        def attempt():
            barrier.wait()
            try:
                register_for_event(Event.objects.get(pk=self.event.pk), participant)
                results.append("added")
            except RegistrationError:
                results.append("duplicate-blocked")
            finally:
                connections.close_all()

        threads = [Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(
            Registration.objects.filter(event=self.event, participant=participant).count(), 1
        )
        self.assertEqual(results.count("added"), 1, results)
