from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from announcements.models import Notification
from events.models import Category, Event, EventStatus, Venue
from registrations.models import Registration

User = get_user_model()


def home(request):
    now = timezone.now()
    published = Event.objects.filter(status=EventStatus.PUBLISHED)
    featured = (
        published.upcoming(now)
        .select_related("category", "venue", "organizer")
        .annotate(spots=Count("registrations", filter=Q(registrations__status="confirmed")))
        .order_by("start_time")[:6]
    )
    stats = {
        "events": published.upcoming(now).count(),
        "organizers": User.objects.organizers().filter(is_active=True).count(),
        "participants": Registration.objects.filter(status="confirmed").values("participant").distinct().count(),
        "venues": Venue.objects.filter(is_active=True).count(),
    }
    featured_categories = (
        Category.objects.annotate(
            live_events=Count("events", filter=Q(events__status=EventStatus.PUBLISHED, events__start_time__gte=now))
        ).order_by("-live_events", "name")[:8]
    )
    context = {
        "featured_events": featured,
        "stats": stats,
        "categories": featured_categories,
        "recent_events": published.select_related("category", "venue").order_by("-created_at")[:3],
        "page": "home",
    }
    return render(request, "core/home.html", context)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)
