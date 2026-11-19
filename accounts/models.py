from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models
from django.urls import reverse
from django.utils import timezone


class UserManager(DjangoUserManager):
    """Manager that normalises e-mail addresses and keeps them unique."""

    use_in_migrations = True

    def _create_user(self, username, email, password, **extra_fields):
        if not username:
            raise ValueError("The username must be set.")
        email = self.normalize_email(email or "")
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)

    def organizers(self):
        return self.filter(role=User.Role.ORGANIZER)

    def participants(self):
        return self.filter(role=User.Role.PARTICIPANT)


class User(AbstractUser):
    """Platform user. A single model covers all three roles."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        ORGANIZER = "organizer", "Organizer"
        PARTICIPANT = "participant", "Participant"

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PARTICIPANT, db_index=True)
    email_notifications = models.BooleanField(default=True)

    objects = UserManager()
    REQUIRED_FIELDS = ["email", "first_name", "last_name"]

    class Meta:
        ordering = ["first_name", "last_name", "username"]
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        if self.email:
            self.email = type(self).objects.normalize_email(self.email).lower()
        super().save(*args, **kwargs)

    # -- role helpers ------------------------------------------------------- #
    @property
    def is_admin(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_organizer(self) -> bool:
        return self.role == self.Role.ORGANIZER

    @property
    def is_participant(self) -> bool:
        return self.role == self.Role.PARTICIPANT

    @property
    def display_name(self) -> str:
        return self.get_full_name().strip() or self.username

    @property
    def initials(self) -> str:
        first = (self.first_name or self.username or "?")[:1]
        last = (self.last_name or "")[:1]
        return (first + last).upper()

    @property
    def role_label(self) -> str:
        return self.get_role_display()

    def can_manage_event(self, event) -> bool:
        return self.is_admin or event.organizer_id == self.pk

    def get_absolute_url(self):
        return reverse("accounts:profile")

    def mark_active(self):
        if not self.is_active:
            self.is_active = True
            self.save(update_fields=["is_active"])

    def deactivate(self):
        if self.is_active:
            self.is_active = False
            self.save(update_fields=["is_active"])


class Profile(models.Model):
    """Extra, non-auth data for a user."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    organization = models.CharField(max_length=160, blank=True)
    city = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    bio = models.TextField(max_length=600, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.user}"

    @property
    def completion(self) -> int:
        fields = [self.avatar, self.phone, self.job_title, self.organization, self.city, self.bio]
        filled = sum(1 for field in fields if bool(field))
        return int(filled / len(fields) * 100)


class LoginAudit(models.Model):
    """Lightweight record of sign-in activity used by the admin dashboard."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_audits")
    logged_in_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-logged_in_at"]
        indexes = [models.Index(fields=["user", "-logged_in_at"])]

    def __str__(self):
        return f"{self.user} @ {self.logged_in_at:%Y-%m-%d %H:%M}"
