from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone


class RegistrationStatus(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed"
    WAITLISTED = "waitlisted", "Waitlisted"
    CANCELLED = "cancelled", "Cancelled"
    ATTENDED = "attended", "Attended"
    NO_SHOW = "no_show", "No show"


#: Statuses that occupy a seat.
SEAT_HOLDING_STATUSES = (RegistrationStatus.CONFIRMED, RegistrationStatus.ATTENDED, RegistrationStatus.NO_SHOW)


class RegistrationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=SEAT_HOLDING_STATUSES)

    def confirmed(self):
        return self.filter(status=RegistrationStatus.CONFIRMED)

    def waitlisted(self):
        return self.filter(status=RegistrationStatus.WAITLISTED)

    def cancelled(self):
        return self.filter(status=RegistrationStatus.CANCELLED)

    def for_event(self, event):
        return self.filter(event=event)

    def with_related(self):
        return self.select_related(
            "event", "event__venue", "event__category", "participant", "participant__profile"
        )


class Registration(models.Model):
    """A participant's registration for an event. Enforces seat integrity."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="registrations")
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="registrations",
    )
    status = models.CharField(
        max_length=20, choices=RegistrationStatus.choices, default=RegistrationStatus.CONFIRMED, db_index=True,
    )
    ticket_code = models.CharField(max_length=12, unique=True)
    notes = models.CharField(max_length=240, blank=True, help_text="Anything the organizer should know.")
    waitlist_position = models.PositiveIntegerField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=240, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="cancellations_made",
    )

    objects = RegistrationQuerySet.as_manager()

    class Meta:
        ordering = ["-registered_at"]
        constraints = [
            # A participant can hold at most one active (seat or waitlist) registration per event.
            models.UniqueConstraint(
                fields=["event", "participant"],
                condition=models.Q(status__in=SEAT_HOLDING_STATUSES),
                name="unique_active_registration_per_event",
            ),
            models.UniqueConstraint(
                fields=["event", "participant"],
                condition=models.Q(status=RegistrationStatus.WAITLISTED),
                name="unique_waitlist_per_event",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[c.value for c in RegistrationStatus]),
                name="registration_status_is_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "status"]),
            models.Index(fields=["participant", "status"]),
        ]

    def __str__(self):
        return f"{self.participant} → {self.event} ({self.get_status_display()})"

    # -- validation --------------------------------------------------------- #
    def clean(self):
        if self.event_id and self.participant_id:
            if self.event.organizer_id == self.participant_id:
                raise ValidationError({"participant": "Organizers cannot register for their own event."})
            if self.participant.role == "organizer" and not self.participant.is_admin:
                raise ValidationError({"participant": "Organizer accounts cannot hold participant registrations."})

    def save(self, *args, **kwargs):
        if not self.ticket_code:
            self.ticket_code = self.generate_ticket_code()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_ticket_code() -> str:
        import secrets
        import string

        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "EV-" + "".join(secrets.choice(alphabet) for _ in range(7))
            if not Registration.objects.filter(ticket_code=code).exists():
                return code

    def get_absolute_url(self):
        return reverse("registrations:detail", kwargs={"ticket_code": self.ticket_code})

    # -- state helpers ------------------------------------------------------ #
    @property
    def holds_seat(self) -> bool:
        return self.status in SEAT_HOLDING_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in SEAT_HOLDING_STATUSES

    @property
    def can_be_cancelled(self) -> bool:
        """Participants may cancel while registration is open and the event has not started."""
        if self.status not in (RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED):
            return False
        event = self.event
        return (
            event.status == "published"
            and not event.registration_closed
            and timezone.now() < event.start_time
        )

    @property
    def cancelled_by_participant(self) -> bool:
        return bool(self.cancelled_by_id and self.participant_id == self.cancelled_by_id)

    @property
    def status_tone(self) -> str:
        return {
            RegistrationStatus.CONFIRMED: "success",
            RegistrationStatus.ATTENDED: "success",
            RegistrationStatus.WAITLISTED: "warning",
            RegistrationStatus.CANCELLED: "danger",
            RegistrationStatus.NO_SHOW: "danger",
        }.get(self.status, "neutral")


class WaitlistEntry(models.Model):
    """Ordered waitlist so promotions are deterministic and auditable."""

    registration = models.OneToOneField(
        Registration, on_delete=models.CASCADE, related_name="waitlist_entry",
    )
    position = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"#{self.position} {self.registration.participant}"


class Attendance(models.Model):
    """Attendance record for a single registration (one per registration)."""

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        ABSENT = "absent", "Absent"
        EXCUSED = "excused", "Excused"

    registration = models.OneToOneField(Registration, on_delete=models.CASCADE, related_name="attendance")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT, db_index=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_marked",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["status", "checked_in_at"])]

    def __str__(self):
        return f"{self.registration.participant} - {self.get_status_display()}"

    def clean(self):
        if self.status in (self.Status.PRESENT, self.Status.LATE) and not self.checked_in_at:
            raise ValidationError({"checked_in_at": "A check-in time is required for present or late participants."})
        if self.registration_id and self.registration.status == RegistrationStatus.CANCELLED:
            raise ValidationError("Attendance cannot be recorded for a cancelled registration.")
        if self.registration_id:
            event = self.registration.event
            now = timezone.now()
            if now < event.start_time - timedelta(days=1):
                raise ValidationError("You cannot record attendance more than a day before the event starts.")

    def save(self, *args, **kwargs):
        if self.status in (self.Status.PRESENT, self.Status.LATE) and not self.checked_in_at:
            self.checked_in_at = timezone.now()
        elif self.status in (self.Status.ABSENT, self.Status.EXCUSED):
            self.checked_in_at = None
        super().save(*args, **kwargs)

    @property
    def tone(self) -> str:
        return {
            self.Status.PRESENT: "success",
            self.Status.LATE: "warning",
            self.Status.ABSENT: "danger",
            self.Status.EXCUSED: "info",
        }[self.status]
