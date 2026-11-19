from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Announcement(models.Model):
    """Organizer broadcast attached to a single event."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="announcements")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcements",
    )
    title = models.CharField(max_length=160)
    body = models.TextField(max_length=2000)
    is_pinned = models.BooleanField(default=False, help_text="Pinned announcements appear first.")
    notified_count = models.PositiveIntegerField(default=0, help_text="Number of participants notified.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [models.Index(fields=["event", "-is_pinned", "-created_at"])]

    def __str__(self):
        return f"{self.title} ({self.event.title})"

    def get_absolute_url(self):
        return reverse("announcements:detail", kwargs={"pk": self.pk})

    @property
    def is_edited(self) -> bool:
        return (self.updated_at - self.created_at).total_seconds() > 60


class Notification(models.Model):
    """In-app notification delivered to a single user."""

    class Kind(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        EVENT = "event", "Event update"
        ANNOUNCEMENT = "announcement", "Announcement"
        ATTENDANCE = "attendance", "Attendance"
        SYSTEM = "system", "System"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_notifications",
    )
    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM, db_index=True)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=400, blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.title} → {self.recipient}"

    def mark_read(self, save=True):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            if save:
                self.save(update_fields=["is_read", "read_at"])

    @property
    def icon(self) -> str:
        return {
            self.Kind.REGISTRATION: "ticket",
            self.Kind.EVENT: "calendar",
            self.Kind.ANNOUNCEMENT: "megaphone",
            self.Kind.ATTENDANCE: "check",
            self.Kind.SYSTEM: "bell",
        }.get(self.kind, "bell")
