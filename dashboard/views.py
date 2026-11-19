from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import admin_required, organizer_required
from accounts.forms import UserRoleForm
from announcements.models import Announcement, Notification
from core.models import ActivityLog
from core.services import csv_response, log_activity, paginate
from events.models import Category, Event, EventStatus, Venue
from registrations.models import Attendance, Registration, RegistrationStatus, SEAT_HOLDING_STATUSES

User = get_user_model()


@login_required
def router(request):
    """Send each role to its own dashboard."""
    if request.user.is_admin:
        return redirect("dashboard:admin")
    if request.user.is_organizer:
        return redirect("dashboard:organizer")
    return redirect("dashboard:participant")


# --------------------------------------------------------------------------- #
# Organizer
# --------------------------------------------------------------------------- #
@organizer_required
def organizer_dashboard(request):
    user = request.user
    now = timezone.now()
    events = Event.objects.for_user(user) if not user.is_admin else Event.objects.all()
    managed = events.with_management_data(now)

    totals = events.aggregate(
        total=Count("id"),
        published=Count("id", filter=Q(status=EventStatus.PUBLISHED)),
        drafts=Count("id", filter=Q(status=EventStatus.DRAFT)),
        cancelled=Count("id", filter=Q(status=EventStatus.CANCELLED)),
        completed=Count("id", filter=Q(status=EventStatus.COMPLETED)),
    )
    registration_stats = Registration.objects.filter(event__in=events).aggregate(
        total=Count("id"),
        confirmed=Count("id", filter=Q(status=RegistrationStatus.CONFIRMED)),
        attended=Count("id", filter=Q(status=RegistrationStatus.ATTENDED)),
        waitlisted=Count("id", filter=Q(status=RegistrationStatus.WAITLISTED)),
        cancelled=Count("id", filter=Q(status=RegistrationStatus.CANCELLED)),
    )
    capacity = events.aggregate(seats=Sum("capacity"))["seats"] or 0
    seats_used = registration_stats["confirmed"] + registration_stats["attended"]

    upcoming = list(
        managed.filter(end_time__gte=now).order_by("start_time")[:5]
    )
    recent_registrations = (
        Registration.objects.with_related()
        .filter(event__in=events)
        .order_by("-registered_at")[:8]
    )
    attendance_by_event = list(
        events.with_management_data(now).filter(registrations__isnull=False)
        .annotate(
            marked=Count("registrations__attendance", distinct=True),
            attended=Count(
                "registrations__attendance",
                filter=Q(registrations__attendance__status__in=["present", "late"]),
                distinct=True,
            ),
        )
        .distinct()
        .order_by("-start_time")[:5]
    )
    context = {
        "totals": totals,
        "registration_stats": registration_stats,
        "capacity": capacity,
        "seats_used": seats_used,
        "seats_free": max(capacity - seats_used, 0),
        "occupancy": round(seats_used / capacity * 100) if capacity else 0,
        "upcoming_events": upcoming,
        "recent_registrations": recent_registrations,
        "attendance_by_event": attendance_by_event,
        "announcements": Announcement.objects.filter(event__in=events).select_related("event")[:5],
        "drafts": managed.filter(status=EventStatus.DRAFT).order_by("-updated_at")[:4],
        "status_filter": "organizer",
    }
    return render(request, "dashboard/organizer.html", context)


@organizer_required
def organizer_chart_data(request):
    """JSON series for the organizer dashboard chart (real aggregation)."""
    events = Event.objects.for_user(request.user) if not request.user.is_admin else Event.objects.all()
    days = int(request.GET.get("days", 14))
    days = min(max(days, 7), 60)
    start = timezone.now() - timedelta(days=days - 1)
    daily = (
        Registration.objects.filter(event__in=events, registered_at__gte=start)
        .annotate(day=TruncDate("registered_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row["total"] for row in daily}
    labels, values = [], []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        labels.append(day.strftime("%d %b"))
        values.append(by_day.get(day, 0))
    by_category = list(
        events.values("category__name", "category__color").annotate(total=Count("id")).order_by("-total")[:6]
    )
    return JsonResponse({
        "ok": True,
        "registrations": {"labels": labels, "values": values},
        "categories": {
            "labels": [row["category__name"] for row in by_category],
            "values": [row["total"] for row in by_category],
            "colors": [row["category__color"] for row in by_category],
        },
    })


# --------------------------------------------------------------------------- #
# Participant
# --------------------------------------------------------------------------- #
@login_required
def participant_dashboard(request):
    user = request.user
    now = timezone.now()
    registrations = Registration.objects.with_related().filter(participant=user)
    upcoming = registrations.filter(status__in=SEAT_HOLDING_STATUSES, event__end_time__gte=now).order_by("event__start_time")
    stats = registrations.aggregate(
        total=Count("id"),
        confirmed=Count("id", filter=Q(status=RegistrationStatus.CONFIRMED)),
        attended=Count("id", filter=Q(status=RegistrationStatus.ATTENDED)),
        waitlisted=Count("id", filter=Q(status=RegistrationStatus.WAITLISTED)),
        cancelled=Count("id", filter=Q(status=RegistrationStatus.CANCELLED)),
    )
    notifications = Notification.objects.filter(recipient=user).select_related("event")[:6]
    unread = Notification.objects.filter(recipient=user, is_read=False).count()
    announcements = (
        Announcement.objects.filter(event__in=[r.event_id for r in upcoming])
        .select_related("event", "author")[:5]
    )
    # Recommendations: published upcoming events the user has not registered for
    taken_ids = registrations.values_list("event_id", flat=True)
    recommendations = (
        Event.objects.published().upcoming(now).with_card_data()
        .exclude(pk__in=list(taken_ids))
        .exclude(organizer=user)
        .order_by("start_time")[:3]
    )
    context = {
        "registrations": registrations.order_by("-registered_at")[:5],
        "upcoming": upcoming[:4],
        "upcoming_count": upcoming.count(),
        "stats": stats,
        "notifications": notifications,
        "unread": unread,
        "announcements": announcements,
        "recommendations": recommendations,
        "attendance_history": registrations.filter(event__end_time__lt=now).order_by("-event__start_time")[:5],
    }
    return render(request, "dashboard/participant.html", context)


# --------------------------------------------------------------------------- #
# Administrator
# --------------------------------------------------------------------------- #
@admin_required
def admin_dashboard(request):
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    user_stats = User.objects.aggregate(
        total=Count("id"),
        organizers=Count("id", filter=Q(role="organizer")),
        participants=Count("id", filter=Q(role="participant")),
        admins=Count("id", filter=Q(role="admin")),
        active=Count("id", filter=Q(is_active=True)),
        new_week=Count("id", filter=Q(date_joined__gte=week_ago)),
    )
    event_stats = Event.objects.aggregate(
        total=Count("id"),
        published=Count("id", filter=Q(status=EventStatus.PUBLISHED)),
        drafts=Count("id", filter=Q(status=EventStatus.DRAFT)),
        cancelled=Count("id", filter=Q(status=EventStatus.CANCELLED)),
        completed=Count("id", filter=Q(status=EventStatus.COMPLETED)),
        upcoming=Count("id", filter=Q(status=EventStatus.PUBLISHED, end_time__gte=now)),
    )
    registration_stats = Registration.objects.aggregate(
        total=Count("id"),
        confirmed=Count("id", filter=Q(status=RegistrationStatus.CONFIRMED)),
        waitlisted=Count("id", filter=Q(status=RegistrationStatus.WAITLISTED)),
        cancelled=Count("id", filter=Q(status=RegistrationStatus.CANCELLED)),
        attended=Count("id", filter=Q(status=RegistrationStatus.ATTENDED)),
        new_week=Count("id", filter=Q(registered_at__gte=week_ago)),
    )
    fill_rate = round((registration_stats["confirmed"] + registration_stats["attended"]) /
                      ((Event.objects.aggregate(seats=Sum("capacity"))["seats"]) or 1) * 100)

    context = {
        "user_stats": user_stats,
        "event_stats": event_stats,
        "registration_stats": registration_stats,
        "fill_rate": min(fill_rate, 100),
        "recent_users": User.objects.order_by("-date_joined")[:6],
        "recent_events": Event.objects.with_card_data().order_by("-created_at")[:6],
        "recent_registrations": Registration.objects.with_related().order_by("-registered_at")[:8],
        "activities": ActivityLog.objects.select_related("actor")[:12],
        "top_events": Event.objects.with_card_data().filter(status=EventStatus.PUBLISHED)
        .annotate(fill=Count("registrations", filter=Q(registrations__status__in=SEAT_HOLDING_STATUSES), distinct=True))
        .order_by("-fill", "-start_time")[:5],
        "top_organizers": User.objects.organizers().annotate(
            event_count=Count("organized_events", distinct=True),
            registration_count=Count("organized_events__registrations", distinct=True),
        ).order_by("-registration_count", "-event_count")[:5],
        "category_split": Category.objects.annotate(
            total=Count("events", distinct=True),
            seats=Count("events__registrations", filter=Q(events__registrations__status__in=SEAT_HOLDING_STATUSES), distinct=True),
        ).order_by("-total")[:6],
        "unread_notifications": Notification.objects.filter(is_read=False).count(),
    }
    return render(request, "dashboard/admin.html", context)


@admin_required
def admin_chart_data(request):
    days = min(max(int(request.GET.get("days", 14)), 7), 60)
    start = timezone.now() - timedelta(days=days - 1)
    signups = (
        User.objects.filter(date_joined__gte=start)
        .annotate(day=TruncDate("date_joined")).values("day").annotate(total=Count("id")).order_by("day")
    )
    registrations = (
        Registration.objects.filter(registered_at__gte=start)
        .annotate(day=TruncDate("registered_at")).values("day").annotate(total=Count("id")).order_by("day")
    )
    labels, signup_series, reg_series = [], [], []
    signup_map = {row["day"]: row["total"] for row in signups}
    reg_map = {row["day"]: row["total"] for row in registrations}
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        labels.append(day.strftime("%d %b"))
        signup_series.append(signup_map.get(day, 0))
        reg_series.append(reg_map.get(day, 0))
    return JsonResponse({
        "ok": True,
        "labels": labels,
        "series": [
            {"name": "New users", "values": signup_series, "color": "#6366f1"},
            {"name": "Registrations", "values": reg_series, "color": "#10b981"},
        ],
    })


@admin_required
def admin_users(request):
    queryset = User.objects.select_related("profile").annotate(
        event_count=Count("organized_events", distinct=True),
        registration_count=Count("registrations", distinct=True),
    )
    role = request.GET.get("role", "")
    state = request.GET.get("state", "")
    search = (request.GET.get("q") or "").strip()
    if role:
        queryset = queryset.filter(role=role)
    if state == "active":
        queryset = queryset.filter(is_active=True)
    elif state == "inactive":
        queryset = queryset.filter(is_active=False)
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) | Q(email__icontains=search)
            | Q(first_name__icontains=search) | Q(last_name__icontains=search)
        )
    page = paginate(queryset.order_by("-date_joined"), request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {
        "page_obj": page,
        "users": page.object_list,
        "role": role,
        "state": state,
        "q": search,
        "role_choices": User.Role.choices,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "dashboard/partials/user_rows.html", context)
    return render(request, "dashboard/admin_users.html", context)


@admin_required
def admin_user_detail(request, pk):
    user_obj = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    role_form = UserRoleForm(request.POST or None, instance=user_obj, prefix="role")
    if request.method == "POST" and "save_role" in request.POST and role_form.is_valid():
        previous = user_obj.role
        role_form.save()
        log_activity(
            request.user, ActivityLog.Action.USER_ROLE_CHANGED,
            f"{user_obj.display_name}: role changed {previous} → {user_obj.role}",
        )
        messages.success(request, f"{user_obj.display_name} is now a {user_obj.get_role_display()}.")
        return redirect("dashboard:admin_user", pk=user_obj.pk)
    context = {
        "user_obj": user_obj,
        "role_form": role_form,
        "registrations": user_obj.registrations.select_related("event", "attendance")[:10],
        "organized": user_obj.organized_events.with_card_data()[:10],
        "activities": user_obj.activities.all()[:10],
        "logins": user_obj.login_audits.all()[:5],
    }
    return render(request, "dashboard/admin_user_detail.html", context)


@admin_required
def admin_toggle_user(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        if user_obj.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
        elif user_obj.is_superuser and not request.user.is_superuser:
            messages.error(request, "Only a superuser can deactivate another superuser.")
        else:
            user_obj.is_active = not user_obj.is_active
            user_obj.save(update_fields=["is_active"])
            log_activity(
                request.user, ActivityLog.Action.USER_STATUS_CHANGED,
                f"{user_obj.display_name} {'activated' if user_obj.is_active else 'deactivated'}",
            )
            messages.success(request, f"{user_obj.display_name} {'activated' if user_obj.is_active else 'deactivated'}.")
    return redirect(request.POST.get("next") or "dashboard:admin_users")


@admin_required
def admin_events(request):
    queryset = Event.objects.with_management_data().select_related("organizer", "category", "venue")
    status = request.GET.get("status", "")
    search = (request.GET.get("q") or "").strip()
    category = request.GET.get("category", "")
    if status:
        queryset = queryset.filter(status=status)
    if category:
        queryset = queryset.filter(category__slug=category)
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search) | Q(organizer__username__icontains=search)
            | Q(venue__name__icontains=search) | Q(category__name__icontains=search)
        )
    page = paginate(queryset.order_by("-created_at"), request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {
        "page_obj": page, "events": page.object_list, "status": status, "q": search,
        "category_slug": category, "status_choices": EventStatus.choices, "categories": Category.objects.all(),
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "dashboard/partials/event_rows.html", context)
    return render(request, "dashboard/admin_events.html", context)


@admin_required
def admin_registrations(request):
    queryset = Registration.objects.with_related().select_related("attendance")
    status = request.GET.get("status", "")
    search = (request.GET.get("q") or "").strip()
    if status:
        queryset = queryset.filter(status=status)
    if search:
        queryset = queryset.filter(
            Q(participant__username__icontains=search) | Q(participant__email__icontains=search)
            | Q(ticket_code__icontains=search) | Q(event__title__icontains=search)
        )
    page = paginate(queryset.order_by("-registered_at"), request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {
        "page_obj": page, "registrations": page.object_list, "status": status, "q": search,
        "status_choices": RegistrationStatus.choices,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "dashboard/partials/registration_rows.html", context)
    return render(request, "dashboard/admin_registrations.html", context)


@admin_required
def admin_activity(request):
    queryset = ActivityLog.objects.select_related("actor")
    action = request.GET.get("action", "")
    if action:
        queryset = queryset.filter(action=action)
    page = paginate(queryset, request.GET.get("page"), 25)
    return render(request, "dashboard/admin_activity.html", {
        "page_obj": page, "activities": page.object_list, "action": action,
        "action_choices": ActivityLog.Action.choices,
    })


@admin_required
def export_users(request):
    rows = [
        [u.display_name, u.username, u.email, u.get_role_display(), "Active" if u.is_active else "Inactive",
         timezone.localtime(u.date_joined).strftime("%Y-%m-%d"), u.organized_events.count(), u.registrations.count()]
        for u in User.objects.all().order_by("date_joined")
    ]
    return csv_response("eventhub-users.csv", ["Name", "Username", "Email", "Role", "Status", "Joined", "Events", "Registrations"], rows)


@admin_required
def export_registrations(request):
    rows = [
        [r.ticket_code, r.event.title, r.participant.display_name, r.participant.email, r.get_status_display(),
         timezone.localtime(r.registered_at).strftime("%Y-%m-%d %H:%M")]
        for r in Registration.objects.with_related().order_by("-registered_at")
    ]
    return csv_response("eventhub-registrations.csv", ["Ticket", "Event", "Participant", "Email", "Status", "Registered at"], rows)
