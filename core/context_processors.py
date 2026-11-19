from django.conf import settings


def site_context(request):
    """Site-wide template variables (branding + navigation state)."""
    user = getattr(request, "user", None)
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "DEBUG": settings.DEBUG,
        "current_user": user if user and user.is_authenticated else None,
        "unread_notifications": _unread_count(user),
    }


def _unread_count(user) -> int:
    if not user or not user.is_authenticated:
        return 0
    from announcements.models import Notification

    return Notification.objects.filter(recipient=user, is_read=False).count()
