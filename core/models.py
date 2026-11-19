from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    """Platform activity trail surfaced on the administrator dashboard."""

    class Action(models.TextChoices):
        SIGNUP = "signup", "Account created"
        LOGIN = "login", "Signed in"
        EVENT_CREATED = "event_created", "Event created"
        EVENT_UPDATED = "event_updated", "Event updated"
        EVENT_PUBLISHED = "event_published", "Event published"
        EVENT_CANCELLED = "event_cancelled", "Event cancelled"
        EVENT_COMPLETED = "event_completed", "Event completed"
        EVENT_DELETED = "event_deleted", "Event deleted"
        REGISTRATION_CREATED = "registration_created", "Registration created"
        REGISTRATION_CANCELLED = "registration_cancelled", "Registration cancelled"
        ATTENDANCE_MARKED = "attendance_marked", "Attendance marked"
        ANNOUNCEMENT_POSTED = "announcement_posted", "Announcement posted"
        USER_ROLE_CHANGED = "user_role_changed", "User role changed"
        USER_STATUS_CHANGED = "user_status_changed", "User status changed"
        VENUE_SAVED = "venue_saved", "Venue saved"
        CATEGORY_SAVED = "category_saved", "Category saved"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="activities",
    )
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    description = models.CharField(max_length=255)
    target_url = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Activity log entry"
        indexes = [models.Index(fields=["-created_at", "action"])]

    def __str__(self):
        return f"{self.get_action_display()}: {self.description}"
