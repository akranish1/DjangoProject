"""Read-side helpers: filtering, sorting and aggregation for the discovery pages."""
from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from .forms import EventFilterForm
from .models import Category, Event, EventStatus, Venue


def apply_date_filter(queryset, choice, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    if not choice:
        return queryset
    if choice == "today":
        return queryset.filter(start_time__date=today)
    if choice == "tomorrow":
        return queryset.filter(start_time__date=today + timedelta(days=1))
    if choice == "week":
        return queryset.filter(start_time__gte=now, start_time__lte=now + timedelta(days=7))
    if choice == "weekend":
        days_until_saturday = (5 - today.weekday()) % 7
        saturday = today + timedelta(days=days_until_saturday)
        return queryset.filter(start_time__date__gte=saturday, start_time__date__lte=saturday + timedelta(days=1))
    if choice == "month":
        return queryset.filter(start_time__gte=now, start_time__lte=now + timedelta(days=30))
    if choice == "past":
        return queryset.filter(end_time__lt=now)
    return queryset


def apply_availability_filter(queryset, choice, now=None):
    if not choice:
        return queryset
    if choice == "available":
        return queryset.filter(spots__lt=F("capacity"))
    if choice == "full":
        return queryset.filter(spots__gte=F("capacity"))
    if choice == "almost":
        # fewer than 15% of the seats remain
        return queryset.filter(spots__lt=F("capacity")).filter(spots__gte=F("capacity") * 0.85)
    return queryset


def apply_sort(queryset, choice, now=None):
    mapping = {
        "soonest": ["start_time"],
        "latest": ["-start_time"],
        "newest": ["-created_at"],
        "popular": ["-spots", "start_time"],
        "title": ["title"],
    }
    ordering = mapping.get(choice or "", ["start_time"])
    return queryset.order_by(*ordering)


def discovery_queryset(params, include_past=False, base=None):
    """Return ``(form, queryset)`` for the public event discovery page."""
    form = EventFilterForm(params or None)
    now = timezone.now()
    # public discovery only ever exposes published events
    queryset = (base if base is not None else Event.objects.published()).with_card_data()

    if form.is_valid():
        data = form.cleaned_data
        queryset = apply_date_filter(queryset, data.get("date"), now)
        if not include_past and data.get("date") != "past" and not data.get("date"):
            queryset = queryset.upcoming(now)
        queryset = queryset.search(data.get("q"))
        if data.get("category"):
            queryset = queryset.filter(category__slug=data["category"])
        if data.get("venue"):
            queryset = queryset.filter(venue=data["venue"])
        queryset = apply_availability_filter(queryset, data.get("availability"), now)
        queryset = apply_sort(queryset, data.get("sort"), now)
    else:
        if not include_past:
            queryset = queryset.upcoming(now)
        queryset = queryset.order_by("start_time")
    return form, queryset


def filter_options():
    """Choices for the discovery filter widgets."""
    return {
        "categories": Category.objects.annotate(
            live=Count("events", filter=Q(events__status=EventStatus.PUBLISHED, events__end_time__gte=timezone.now()))
        ).order_by("-live", "name"),
        "venues": Venue.objects.order_by("city", "name"),
    }
