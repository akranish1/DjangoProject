from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import organizer_required
from announcements.models import Announcement, Notification
from core.models import ActivityLog
from core.services import log_activity, paginate
from registrations.models import Attendance, Registration, RegistrationStatus
from registrations.services import attendance_summary

from .forms import (
    CategoryForm, EventCancelForm, EventForm, EventFilterForm, EventSessionFormSet, VenueForm,
)
from .models import Category, Event, EventSession, EventStatus, Venue
from .selectors import discovery_queryset, filter_options
from .services import (
    EventStateError, broadcast_event_update, cancel_event, complete_event, duplicate_event,
    publish_event, unpublish_event,
)


# --------------------------------------------------------------------------- #
# Discovery (public)
# --------------------------------------------------------------------------- #
def event_list(request):
    form, queryset = discovery_queryset(request.GET)
    paginator = Paginator(queryset, settings.EVENTS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    context = {
        "form": form,
        "page_obj": page,
        "events": page.object_list,
        "total": paginator.count,
        "options": filter_options(),
        "active_filters": form.is_valid() and any(
            value for key, value in form.cleaned_data.items() if key != "sort" and value
        ),
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "events/partials/event_grid.html", context)
    return render(request, "events/event_list.html", context)


def event_detail(request, slug):
    queryset = Event.objects.select_related("category", "venue", "organizer", "organizer__profile").prefetch_related(
        Prefetch("sessions", queryset=EventSession.objects.order_by("start_time")),
        Prefetch("announcements", queryset=Announcement.objects.select_related("author")),
    )
    event = get_object_or_404(queryset, slug=slug)
    user = request.user
    is_manager = user.is_authenticated and user.can_manage_event(event)
    if event.status != EventStatus.PUBLISHED and not is_manager:
        raise Http404("This event is not available.")

    registrations = event.registrations.select_related("participant", "participant__profile", "attendance")
    user_registration = None
    if user.is_authenticated:
        user_registration = next(
            (r for r in registrations if r.participant_id == user.pk), None,
        )
    can_register, register_reason = event.can_register(user)
    summary = attendance_summary(event)

    context = {
        "event": event,
        "sessions": event.sessions.all(),
        "announcements": event.announcements.all(),
        "registration": user_registration,
        "can_register": can_register,
        "register_reason": register_reason,
        "is_manager": is_manager,
        "waitlist_count": sum(1 for r in registrations if r.status == RegistrationStatus.WAITLISTED),
        "rating_hint": min(event.occupancy, 100),
        "summary": summary,
    }
    return render(request, "events/event_detail.html", context)


# --------------------------------------------------------------------------- #
# Organizer management
# --------------------------------------------------------------------------- #
def _management_queryset():
    return Event.objects.with_management_data().select_related("organizer")


@login_required
def event_manage(request, slug):
    event = get_object_or_404(_management_queryset().prefetch_related("sessions"), slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You can only manage events you organise.")
        return redirect("dashboard:organizer")
    registrations = (
        event.registrations.select_related("participant", "participant__profile", "attendance")
        .filter(status__in=[RegistrationStatus.CONFIRMED, RegistrationStatus.ATTENDED, RegistrationStatus.WAITLISTED])
        .order_by("registered_at")
    )
    context = {
        "event": event,
        "registrations": registrations,
        "waitlist": [r for r in registrations if r.status == RegistrationStatus.WAITLISTED],
        "announcements": event.announcements.select_related("author"),
        "summary": attendance_summary(event),
        "sessions": event.sessions.all(),
    }
    return render(request, "events/event_manage.html", context)


@organizer_required
def event_create(request):
    form = EventForm(request.POST or None, request.FILES or None, user=request.user)
    formset = EventSessionFormSet(request.POST or None, instance=Event())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            event = form.save()
            formset.instance = event
            formset.save()
            log_activity(
                request.user, ActivityLog.Action.EVENT_CREATED,
                f"“{event.title}” created ({event.get_status_display().lower()})", event.get_absolute_url(),
            )
        messages.success(
            request,
            f"Event “{event.title}” saved as {event.get_status_display().lower()}."
            + (" Publish it when you are ready." if event.status == EventStatus.DRAFT else ""),
        )
        return redirect("events:manage", slug=event.slug)
    if request.method == "POST":
        messages.error(request, "We could not save the event. Please review the highlighted fields.")
    return render(request, "events/event_form.html", {"form": form, "formset": formset, "event": None})


@organizer_required
def event_update(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You can only edit your own events.")
        return redirect("dashboard:organizer")
    if event.status == EventStatus.CANCELLED and not request.user.is_admin:
        messages.warning(request, "Cancelled events can only be edited by an administrator.")
        return redirect("events:manage", slug=event.slug)

    form = EventForm(request.POST or None, request.FILES or None, instance=event, user=request.user)
    formset = EventSessionFormSet(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            previous_capacity = Event.objects.values_list("capacity", flat=True).get(pk=event.pk)
            event = form.save()
            formset.save()
            log_activity(request.user, ActivityLog.Action.EVENT_UPDATED, f"“{event.title}” updated", event.get_absolute_url())
            capacity_dropped = event.capacity < previous_capacity
        messages.success(request, "Event updated successfully.")
        if capacity_dropped:
            notify = broadcast_event_update(
                event, request.user, "Capacity reduced",
                f"The capacity for “{event.title}” was reduced to {event.capacity} seats. "
                f"You can export the participants list to check your confirmed attendees.",
            )
            messages.info(request, f"{notify} registered participant(s) were notified of the capacity change.")
        return redirect("events:manage", slug=event.slug)
    if request.method == "POST":
        messages.error(request, "We could not save your changes. Please fix the errors below.")
    return render(request, "events/event_form.html", {"form": form, "formset": formset, "event": event})


@organizer_required
def event_delete(request, slug):
    event = get_object_or_404(_management_queryset(), slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot delete that event.")
        return redirect("dashboard:organizer")
    if request.method == "POST":
        title = event.title
        event.delete()
        log_activity(request.user, ActivityLog.Action.EVENT_DELETED, f"“{title}” deleted")
        messages.success(request, f"Event “{title}” and all of its registrations were deleted.")
        return redirect("dashboard:organizer")
    return render(request, "events/event_confirm_delete.html", {"event": event})


@organizer_required
@require_POST
def event_publish(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot publish that event.")
        return redirect("dashboard:organizer")
    action = request.POST.get("action", "publish")
    try:
        if action == "unpublish":
            unpublish_event(event, request.user)
            messages.success(request, f"“{event.title}” is back in draft.")
        else:
            publish_event(event, request.user)
            messages.success(request, f"“{event.title}” is now live for participants.")
    except EventStateError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or reverse("events:manage", kwargs={"slug": event.slug}))


@organizer_required
def event_cancel(request, slug):
    event = get_object_or_404(_management_queryset(), slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot cancel that event.")
        return redirect("dashboard:organizer")
    form = EventCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancel_event(event, request.user, form.cleaned_data["reason"])
        except EventStateError as exc:
            messages.error(request, str(exc))
            return redirect("events:manage", slug=event.slug)
        messages.success(request, "Event cancelled and every registered participant was notified in-app.")
        return redirect("events:manage", slug=event.slug)
    if request.method == "POST":
        messages.error(request, "Please confirm the cancellation.")
    return render(request, "events/event_cancel.html", {"event": event, "form": form})


@organizer_required
@require_POST
def event_complete(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot update that event.")
        return redirect("dashboard:organizer")
    try:
        complete_event(event, request.user)
        messages.success(request, f"“{event.title}” marked as completed.")
    except EventStateError as exc:
        messages.error(request, str(exc))
    return redirect("events:manage", slug=event.slug)


@organizer_required
@require_POST
def event_duplicate(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot duplicate that event.")
        return redirect("dashboard:organizer")
    copy = duplicate_event(event, request.user)
    messages.success(request, "Event duplicated as a draft. Update the dates and publish when ready.")
    return redirect("events:update", slug=copy.slug)


@organizer_required
def event_broadcast(request, slug):
    """Send a free-form in-app notification to every registrant of an event."""
    event = get_object_or_404(Event, slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You cannot message the participants of that event.")
        return redirect("dashboard:organizer")
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        body = (request.POST.get("message") or "").strip()
        if len(title) < 3 or len(body) < 5:
            messages.error(request, "Please provide a subject (3+ characters) and a message (5+ characters).")
        else:
            sent = broadcast_event_update(event, request.user, title[:160], body[:400])
            log_activity(
                request.user, ActivityLog.Action.ANNOUNCEMENT_POSTED,
                f"Direct message sent to {sent} participant(s) of “{event.title}”", event.get_absolute_url(),
            )
            messages.success(request, f"Notification delivered to {sent} participant(s).")
            return redirect("events:manage", slug=event.slug)
    return render(request, "events/event_broadcast.html", {"event": event})


# --------------------------------------------------------------------------- #
# Venues & categories
# --------------------------------------------------------------------------- #
@organizer_required
def venue_list(request):
    venues = Venue.objects.annotate(
        event_count=Count("events", distinct=True),
        upcoming_count=Count("events", filter=Q(events__start_time__gte=timezone.now()), distinct=True),
    ).order_by("city", "name")
    search = (request.GET.get("q") or "").strip()
    if search:
        venues = venues.filter(Q(name__icontains=search) | Q(city__icontains=search) | Q(state__icontains=search))
    page = paginate(venues, request.GET.get("page"), settings.TABLE_PER_PAGE)
    return render(request, "events/venue_list.html", {"page_obj": page, "venues": page.object_list, "q": search})


@organizer_required
def venue_create(request):
    form = VenueForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        venue = form.save(commit=False)
        venue.created_by = request.user
        venue.save()
        log_activity(request.user, ActivityLog.Action.VENUE_SAVED, f"Venue “{venue.name}” created")
        messages.success(request, f"Venue “{venue.name}” added.")
        next_url = request.GET.get("next")
        return redirect(next_url or "events:venues")
    if request.method == "POST":
        messages.error(request, "Please fix the errors below.")
    return render(request, "events/venue_form.html", {"form": form, "venue": None})


@organizer_required
def venue_update(request, pk):
    venue = get_object_or_404(Venue, pk=pk)
    if not (request.user.is_admin or venue.created_by_id in (None, request.user.pk)):
        messages.error(request, "You can only edit venues that you added.")
        return redirect("events:venues")
    form = VenueForm(request.POST or None, instance=venue)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_activity(request.user, ActivityLog.Action.VENUE_SAVED, f"Venue “{venue.name}” updated")
        messages.success(request, "Venue updated.")
        return redirect("events:venues")
    if request.method == "POST":
        messages.error(request, "Please fix the errors below.")
    return render(request, "events/venue_form.html", {"form": form, "venue": venue})


@login_required
def category_list(request):
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage categories.")
        return redirect("dashboard:router")
    categories = Category.objects.annotate(
        event_count=Count("events", distinct=True),
        published_count=Count("events", filter=Q(events__status=EventStatus.PUBLISHED), distinct=True),
    )
    return render(request, "events/category_list.html", {"categories": categories})


@login_required
def category_create(request):
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage categories.")
        return redirect("dashboard:router")
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save()
        log_activity(request.user, ActivityLog.Action.CATEGORY_SAVED, f"Category “{category.name}” created")
        messages.success(request, f"Category “{category.name}” created.")
        return redirect("events:categories")
    return render(request, "events/category_form.html", {"form": form, "category": None})


@login_required
def category_update(request, slug):
    if not request.user.is_admin:
        messages.error(request, "Only administrators can manage categories.")
        return redirect("dashboard:router")
    category = get_object_or_404(Category, slug=slug)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_activity(request.user, ActivityLog.Action.CATEGORY_SAVED, f"Category “{category.name}” updated")
        messages.success(request, "Category updated.")
        return redirect("events:categories")
    return render(request, "events/category_form.html", {"form": form, "category": category})
