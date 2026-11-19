from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class EventStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    CANCELLED = "cancelled", "Cancelled"
    COMPLETED = "completed", "Completed"


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=EventStatus.PUBLISHED)

    def upcoming(self, now=None):
        now = now or timezone.now()
        return self.filter(end_time__gte=now)

    def past(self, now=None):
        now = now or timezone.now()
        return self.filter(end_time__lt=now)

    def for_user(self, user):
        """Events the given user may manage."""
        if user.is_admin:
            return self
        return self.filter(organizer=user)

    def with_card_data(self):
        """Everything the event card needs, in one query."""
        return self.select_related("category", "venue", "organizer").annotate(
            spots=Count("registrations", filter=Q(registrations__status="confirmed"), distinct=True)
        )

    def with_management_data(self, now=None):
        now = now or timezone.now()
        return self.select_related("category", "venue", "organizer").annotate(
            spots=Count("registrations", filter=Q(registrations__status="confirmed"), distinct=True),
            cancelled_count=Count("registrations", filter=Q(registrations__status="cancelled"), distinct=True),
            attendance_count=Count(
                "registrations__attendance",
                filter=Q(registrations__attendance__status__in=["present", "late"]),
                distinct=True,
            ),
            announcement_count=Count("announcements", distinct=True),
            is_past=Q(end_time__lt=now),
        )

    def search(self, term):
        if not term:
            return self
        return self.filter(
            Q(title__icontains=term)
            | Q(description__icontains=term)
            | Q(category__name__icontains=term)
            | Q(venue__name__icontains=term)
            | Q(venue__city__icontains=term)
            | Q(organizer__first_name__icontains=term)
            | Q(organizer__last_name__icontains=term)
        ).distinct()


class Category(models.Model):
    ICON_CHOICES = [
        ("spark", "Spark"), ("music", "Music"), ("code", "Technology"), ("briefcase", "Business"),
        ("trophy", "Sports"), ("palette", "Arts"), ("graduation", "Education"), ("heart", "Community"),
        ("globe", "Travel"), ("leaf", "Wellness"),
    ]

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.CharField(max_length=240, blank=True)
    color = models.CharField(
        max_length=7, default="#6366f1",
        validators=[RegexValidator(r"^#(?:[0-9a-fA-F]{3}){1,2}$", "Enter a valid hex colour, e.g. #6366f1.")],
    )
    icon = models.CharField(max_length=24, choices=ICON_CHOICES, default="spark")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:100]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f"{reverse('events:list')}?category={self.slug}"


class Venue(models.Model):
    name = models.CharField(max_length=150)
    address_line = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    postal_code = models.CharField(max_length=16, blank=True)
    capacity = models.PositiveIntegerField(
        default=0, help_text="0 means unlimited / not tracked. Used to validate event capacity.",
    )
    map_url = models.URLField(blank=True, help_text="Optional link to an online map.")
    description = models.TextField(blank=True, max_length=500)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="venues_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "city"], name="unique_venue_name_per_city"),
        ]
        indexes = [models.Index(fields=["city", "is_active"])]

    def __str__(self):
        return f"{self.name}, {self.city}"

    @property
    def full_address(self):
        parts = [self.address_line, self.city, self.state, self.postal_code, self.country]
        return ", ".join(part for part in parts if part)

    @property
    def capacity_label(self):
        return f"{self.capacity} seats" if self.capacity else "Unlimited"


class Event(models.Model):
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organized_events",
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="events")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    summary = models.CharField(max_length=240, blank=True, help_text="Shown on event cards.")
    description = models.TextField()
    banner = models.ImageField(upload_to="banners/%Y/%m/", blank=True, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    registration_deadline = models.DateTimeField()
    capacity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="0 for free events.")
    status = models.CharField(max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT, db_index=True)
    cancellation_reason = models.TextField(blank=True, max_length=500)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["-start_time"]
        constraints = [
            models.CheckConstraint(condition=Q(end_time__gt=F("start_time")), name="event_ends_after_start"),
            models.CheckConstraint(
                condition=Q(registration_deadline__lte=F("start_time")), name="event_deadline_before_start"
            ),
            models.CheckConstraint(condition=Q(capacity__gte=1), name="event_capacity_at_least_one"),
        ]
        indexes = [
            models.Index(fields=["status", "start_time"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["venue", "start_time"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return self.title

    # -- validation --------------------------------------------------------- #
    def clean(self):
        errors = {}
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "The end time must be after the start time."
        if self.start_time and self.registration_deadline and self.registration_deadline > self.start_time:
            errors["registration_deadline"] = "Registration must close on or before the event start time."
        if self.venue_id and self.capacity:
            venue_capacity = self.venue.capacity
            if venue_capacity and self.capacity > venue_capacity:
                errors["capacity"] = (
                    f"{self.venue.name} can host at most {venue_capacity} people. "
                    f"Lower the event capacity or pick a larger venue."
                )
        if self.organizer_id and not (self.organizer.is_admin or self.organizer.role == "organizer"):
            errors["organizer"] = "Only organizers can own events."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:180] or "event"
            slug, counter = base, 2
            while Event.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("events:detail", kwargs={"slug": self.slug})

    # -- derived state ------------------------------------------------------ #
    @property
    def seats_taken(self) -> int:
        if hasattr(self, "spots"):
            return self.spots
        return self.registrations.filter(status="confirmed").count()

    @property
    def seats_remaining(self) -> int:
        return max(self.capacity - self.seats_taken, 0)

    @property
    def is_full(self) -> bool:
        return self.seats_remaining <= 0

    @property
    def occupancy(self) -> int:
        if not self.capacity:
            return 0
        return min(round(self.seats_taken / self.capacity * 100), 100)

    @property
    def is_upcoming(self) -> bool:
        return self.end_time >= timezone.now()

    @property
    def is_ongoing(self) -> bool:
        now = timezone.now()
        return self.start_time <= now <= self.end_time

    @property
    def registration_closed(self) -> bool:
        return timezone.now() > self.registration_deadline

    @property
    def is_registration_open(self) -> bool:
        return self.status == EventStatus.PUBLISHED and not self.registration_closed and not self.is_full

    @property
    def availability_label(self) -> str:
        if self.status == EventStatus.CANCELLED:
            return "Cancelled"
        if self.status == EventStatus.COMPLETED or not self.is_upcoming:
            return "Event finished"
        if self.status == EventStatus.DRAFT:
            return "Not published"
        if self.is_full:
            return "Sold out"
        if self.seats_remaining <= max(3, int(self.capacity * 0.1)):
            return f"Only {self.seats_remaining} left"
        return "Registration open"

    @property
    def banner_theme(self) -> str:
        return f"theme-{(self.pk or 0) % 6}"

    @property
    def duration_label(self) -> str:
        delta = self.end_time - self.start_time
        hours, minutes = divmod(int(delta.total_seconds() // 60), 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours}h"
        return f"{minutes}m"

    @property
    def price_label(self) -> str:
        return "Free" if not self.price else f"₹{self.price:,.0f}"

    def user_registration(self, user):
        """Return the user's registration for this event, if any."""
        if not user or not user.is_authenticated:
            return None
        if hasattr(self, "_user_registrations"):
            return self._user_registrations.get(user.pk)
        return self.registrations.filter(participant=user).first()

    def can_register(self, user):
        """Return (allowed: bool, reason: str) for the given user."""
        if not user or not user.is_authenticated:
            return False, "Sign in to register for this event."
        if user.is_organizer and self.organizer_id == user.pk:
            return False, "You are the organizer of this event."
        if self.status == EventStatus.DRAFT:
            return False, "This event is still a draft."
        if self.status == EventStatus.CANCELLED:
            return False, "This event has been cancelled."
        if self.status == EventStatus.COMPLETED:
            return False, "This event has already finished."
        if self.registration_closed:
            return False, "Registration closed on " + timezone.localtime(self.registration_deadline).strftime("%d %b %Y, %H:%M") + "."
        existing = self.user_registration(user)
        if existing and existing.status == "confirmed":
            return False, "You are already registered."
        if self.is_full:
            return False, "This event is sold out."
        return True, "Register now"


class EventImage(models.Model):
    """Optional gallery images for an event detail page."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="events/gallery/%Y/%m/")
    caption = models.CharField(max_length=180, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"Image for {self.event.title}"


class EventSession(models.Model):
    """A single running-order entry inside an event (agenda / schedule)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sessions")
    title = models.CharField(max_length=160)
    speaker = models.CharField(max_length=160, blank=True)
    location = models.CharField(max_length=160, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    description = models.CharField(max_length=300, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["start_time", "order", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(end_time__gt=F("start_time")), name="session_ends_after_start"),
        ]

    def __str__(self):
        return f"{self.title} @ {self.event.title}"

    def clean(self):
        errors = {}
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "Session end time must be after its start time."
        if self.event_id and self.start_time and self.end_time:
            if self.start_time < self.event.start_time or self.end_time > self.event.end_time:
                errors["start_time"] = "Sessions must fall inside the event's start and end time."
        if errors:
            raise ValidationError(errors)

    @property
    def time_label(self):
        return f"{timezone.localtime(self.start_time):%H:%M} – {timezone.localtime(self.end_time):%H:%M}"
