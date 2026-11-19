from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import ActivityLog
from core.services import log_activity
from events.models import Event
from registrations.services import notify_event_update

from .forms import AnnouncementForm
from .models import Announcement, Notification


@login_required
def notification_list(request):
    queryset = Notification.objects.filter(recipient=request.user).select_related("event", "actor")
    state = request.GET.get("state", "all")
    if state == "unread":
        queryset = queryset.filter(is_read=False)
    elif state == "read":
        queryset = queryset.filter(is_read=True)
    kind = request.GET.get("kind", "")
    if kind:
        queryset = queryset.filter(kind=kind)

    paginator = Paginator(queryset, 20)
    page = paginator.get_page(request.GET.get("page"))
    context = {
        "page_obj": page,
        "notifications": page.object_list,
        "state": state,
        "kind": kind,
        "unread_count": Notification.objects.filter(recipient=request.user, is_read=False).count(),
        "kind_choices": Notification.Kind.choices,
    }
    return render(request, "announcements/notification_list.html", context)


@login_required
@require_POST
def notification_open(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.mark_read()
    return redirect(notification.link or "announcements:notifications")


@login_required
@require_POST
def mark_all_read(request):
    updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    messages.success(request, f"{updated} notification{'s' if updated != 1 else ''} marked as read.")
    return redirect(request.POST.get("next") or "announcements:notifications")


@login_required
@require_POST
def notification_mark_read(request, pk):
    """Async endpoint used by the notification drawer."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.mark_read()
    data = {
        "ok": True,
        "id": notification.pk,
        "unread": Notification.objects.filter(recipient=request.user, is_read=False).count(),
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("accept", "").startswith("application/json"):
        return JsonResponse(data)
    return redirect(request.META.get("HTTP_REFERER", "announcements:notifications"))


def announcement_detail(request, pk):
    """Public for published events; drafts/archives stay private to the organiser."""
    announcement = get_object_or_404(
        Announcement.objects.select_related("event", "event__venue", "event__category", "author"), pk=pk,
    )
    event = announcement.event
    is_manager = request.user.is_authenticated and request.user.can_manage_event(event)
    if not is_manager and event.status != "published":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        messages.error(request, "That announcement is not available.")
        return redirect("dashboard:router")
    return render(request, "announcements/announcement_detail.html", {"announcement": announcement, "event": event})


@login_required
def announcement_create(request, slug):
    event = get_object_or_404(Event.objects.select_related("venue", "category"), slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You can only post announcements for events you organise.")
        return redirect("dashboard:organizer")

    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        announcement = form.save(commit=False)
        announcement.event = event
        announcement.author = request.user
        announcement.save()
        if form.cleaned_data.get("notify"):
            announcement.notified_count = notify_event_update(
                event, request.user, f"New announcement: {announcement.title}",
                announcement.body[:300],
                kind=Notification.Kind.ANNOUNCEMENT,
            )
            announcement.save(update_fields=["notified_count"])
        log_activity(
            request.user, ActivityLog.Action.ANNOUNCEMENT_POSTED,
            f"Announcement “{announcement.title}” posted for “{event.title}”",
            event.get_absolute_url(),
        )
        messages.success(
            request,
            "Announcement published" + (f" and {announcement.notified_count} participant(s) notified." if announcement.notified_count else "."),
        )
        return redirect("events:manage", slug=event.slug)
    if request.method == "POST":
        messages.error(request, "Please fix the errors below and try again.")
    return render(request, "announcements/announcement_form.html", {"form": form, "event": event})


@login_required
@require_POST
def announcement_delete(request, pk):
    announcement = get_object_or_404(Announcement.objects.select_related("event"), pk=pk)
    if not request.user.can_manage_event(announcement.event):
        messages.error(request, "You cannot delete that announcement.")
        return redirect("dashboard:router")
    event = announcement.event
    title = announcement.title
    announcement.delete()
    log_activity(request.user, ActivityLog.Action.ANNOUNCEMENT_POSTED, f"Announcement “{title}” deleted", event.get_absolute_url())
    messages.success(request, "Announcement deleted.")
    return redirect("events:manage", slug=event.slug)


@login_required
@require_POST
def announcement_toggle_pin(request, pk):
    announcement = get_object_or_404(Announcement.objects.select_related("event"), pk=pk)
    if not request.user.can_manage_event(announcement.event):
        messages.error(request, "You cannot update that announcement.")
        return redirect("dashboard:router")
    announcement.is_pinned = not announcement.is_pinned
    announcement.save(update_fields=["is_pinned", "updated_at"])
    messages.success(request, "Announcement pinned." if announcement.is_pinned else "Announcement unpinned.")
    return redirect("events:manage", slug=announcement.event.slug)
