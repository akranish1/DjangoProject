from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import organizer_required
from core.services import csv_response, paginate
from events.models import Event, EventStatus

from .forms import AttendanceForm, BulkAttendanceForm, CancelRegistrationForm, ParticipantStatusForm, RegistrationForm
from .models import Attendance, Registration, RegistrationStatus, SEAT_HOLDING_STATUSES
from .services import (
    RegistrationError, attendance_summary, bulk_mark_attendance, cancel_registration,
    mark_attendance, register_for_event,
)


def _registration_block_reason(event, user, existing):
    """Return a user-facing reason when registration is impossible, else None.

    Capacity is intentionally *not* a hard block: a full event accepts
    registrations that are placed on the waitlist by the service layer.
    """
    if existing and existing.status in SEAT_HOLDING_STATUSES:
        return "You are already registered for this event."
    if existing and existing.status == RegistrationStatus.WAITLISTED:
        return "You are already on the waitlist for this event."
    if event.status == EventStatus.DRAFT:
        return "This event has not been published yet."
    if event.status == EventStatus.CANCELLED:
        return "This event has been cancelled."
    if event.status == EventStatus.COMPLETED:
        return "This event has already finished."
    if event.registration_closed:
        return (
            "Registration closed on "
            f"{timezone.localtime(event.registration_deadline):%d %b %Y, %H:%M}."
        )
    if event.organizer_id == user.pk:
        return "You organise this event."
    return None


# --------------------------------------------------------------------------- #
# Participant flows
# --------------------------------------------------------------------------- #
@login_required
def register_for_event_view(request, slug):
    event = get_object_or_404(Event.objects.select_related("venue", "category", "organizer"), slug=slug)
    if event.status != EventStatus.PUBLISHED and not request.user.can_manage_event(event):
        raise Http404("Event not available.")

    existing = Registration.objects.filter(event=event, participant=request.user).first()
    # A full event is not a blocker: the participant joins the managed waitlist.
    hard_block = _registration_block_reason(event, request.user, existing)
    can_register = hard_block is None
    form = RegistrationForm(request.POST or None)

    if request.method == "POST":
        if hard_block and not (existing and existing.status == RegistrationStatus.CANCELLED):
            messages.error(request, hard_block)
            return redirect("events:detail", slug=event.slug)
        if form.is_valid():
            try:
                registration, created = register_for_event(event, request.user, form.cleaned_data["notes"])
            except RegistrationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("events:detail", slug=event.slug)
            if registration.status == RegistrationStatus.WAITLISTED:
                messages.warning(
                    request,
                    f"{event.title} is at capacity. You are number {registration.waitlist_position} on the waitlist "
                    "and we will confirm your seat automatically if one frees up.",
                )
            else:
                messages.success(
                    request,
                    f"You're registered for {event.title}! Ticket {registration.ticket_code}. "
                    "We saved it to your dashboard.",
                )
            return redirect("registrations:detail", ticket_code=registration.ticket_code)
        messages.error(request, "Please review the form and try again.")

    context = {
        "event": event,
        "form": form,
        "can_register": can_register,
        "reason": hard_block or ("This event is full - submitting now joins the waitlist." if event.is_full else ""),
        "joins_waitlist": event.is_full and can_register,
        "existing": existing,
    }
    return render(request, "registrations/register.html", context)


@login_required
def my_registrations(request):
    status = request.GET.get("status", "active")
    queryset = Registration.objects.with_related().filter(participant=request.user)
    if status == "active":
        queryset = queryset.filter(status__in=SEAT_HOLDING_STATUSES)
    elif status == "waitlisted":
        queryset = queryset.filter(status=RegistrationStatus.WAITLISTED)
    elif status == "cancelled":
        queryset = queryset.filter(status=RegistrationStatus.CANCELLED)
    elif status == "past":
        queryset = queryset.filter(event__end_time__lt=timezone.now())
    search = (request.GET.get("q") or "").strip()
    if search:
        queryset = queryset.filter(Q(event__title__icontains=search) | Q(ticket_code__icontains=search))

    counts = Registration.objects.filter(participant=request.user).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status__in=SEAT_HOLDING_STATUSES)),
        waitlisted=Count("id", filter=Q(status=RegistrationStatus.WAITLISTED)),
        cancelled=Count("id", filter=Q(status=RegistrationStatus.CANCELLED)),
        attended=Count("id", filter=Q(status=RegistrationStatus.ATTENDED)),
    )
    page = paginate(queryset.order_by("-registered_at"), request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {
        "page_obj": page,
        "registrations": page.object_list,
        "status": status,
        "q": search,
        "counts": counts,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "registrations/partials/registration_table.html", context)
    return render(request, "registrations/my_registrations.html", context)


def _get_own_registration(request, ticket_code):
    registration = get_object_or_404(
        Registration.objects.select_related(
            "event", "event__venue", "event__category", "event__organizer", "participant", "attendance",
        ),
        ticket_code=ticket_code,
    )
    if registration.participant_id != request.user.pk and not request.user.can_manage_event(registration.event):
        raise Http404("Registration not found.")
    return registration


@login_required
def registration_detail(request, ticket_code):
    registration = _get_own_registration(request, ticket_code)
    return render(request, "registrations/registration_detail.html", {"registration": registration, "event": registration.event})


@login_required
def cancel_registration_view(request, ticket_code):
    registration = _get_own_registration(request, ticket_code)
    is_manager = request.user.can_manage_event(registration.event)
    form = CancelRegistrationForm(request.POST or None)
    if not registration.can_be_cancelled and not (is_manager and registration.status in SEAT_HOLDING_STATUSES):
        messages.warning(request, "This registration can no longer be cancelled.")
        return redirect("registrations:detail", ticket_code=registration.ticket_code)

    if request.method == "POST" and form.is_valid():
        try:
            cancel_registration(registration, request.user, form.cleaned_data.get("reason", ""))
        except RegistrationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Your registration was cancelled. Any waitlisted participant has been promoted.")
            if is_manager and registration.participant_id != request.user.pk:
                return redirect("events:manage", slug=registration.event.slug)
            return redirect("registrations:mine")
    elif request.method == "POST":
        messages.error(request, "Please tick the confirmation box to cancel.")

    return render(
        request, "registrations/cancel.html",
        {"registration": registration, "event": registration.event, "form": form, "is_manager": is_manager},
    )


# --------------------------------------------------------------------------- #
# Organizer flows
# --------------------------------------------------------------------------- #
def _managed_event(request, slug):
    event = get_object_or_404(Event.objects.select_related("venue", "category", "organizer"), slug=slug)
    if not request.user.can_manage_event(event):
        messages.error(request, "You can only manage participants for events you organise.")
        return None
    return event


@organizer_required
def event_participants(request, slug):
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    queryset = event.registrations.select_related("participant", "participant__profile", "attendance")
    status = request.GET.get("status", "active")
    if status == "active":
        queryset = queryset.filter(status__in=SEAT_HOLDING_STATUSES)
    elif status in {choice for choice, _ in RegistrationStatus.choices}:
        queryset = queryset.filter(status=status)
    search = (request.GET.get("q") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(participant__first_name__icontains=search)
            | Q(participant__last_name__icontains=search)
            | Q(participant__email__icontains=search)
            | Q(ticket_code__icontains=search)
        )
    page = paginate(queryset.order_by("-registered_at"), request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {
        "event": event,
        "page_obj": page,
        "registrations": page.object_list,
        "status": status,
        "q": search,
        "summary": attendance_summary(event),
        "status_choices": RegistrationStatus.choices,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "registrations/partials/participant_rows.html", context)
    return render(request, "registrations/event_participants.html", context)


@organizer_required
@require_POST
def update_registration_status(request, pk):
    registration = get_object_or_404(
        Registration.objects.select_related("event", "participant", "participant__profile"), pk=pk,
    )
    event = _managed_event(request, registration.event.slug)
    if event is None:
        return redirect("dashboard:organizer")

    form = ParticipantStatusForm(request.POST)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if not form.is_valid():
        if wants_json:
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        messages.error(request, "Invalid status update.")
        return redirect("registrations:participants", slug=event.slug)

    new_status = form.cleaned_data["status"]
    if new_status == RegistrationStatus.CANCELLED:
        try:
            cancel_registration(registration, request.user, form.cleaned_data.get("reason", ""))
        except RegistrationError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "errors": {"__all__": exc.messages}}, status=400)
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"{registration.participant.display_name}'s registration was cancelled.")
    else:
        registration.status = new_status
        registration.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{registration.participant.display_name} is now marked {registration.get_status_display().lower()}.")
    if wants_json:
        return JsonResponse({
            "ok": True,
            "status": registration.status,
            "label": registration.get_status_display(),
            "tone": registration.status_tone,
        })
    return redirect(request.POST.get("next") or reverse("registrations:participants", kwargs={"slug": event.slug}))


@organizer_required
def attendance_sheet(request, slug):
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    status = request.GET.get("status", "active")
    queryset = event.registrations.select_related("participant", "participant__profile", "attendance")
    if status == "confirmed":
        queryset = queryset.filter(status__in=[RegistrationStatus.CONFIRMED, RegistrationStatus.ATTENDED])
    elif status == "all":
        queryset = queryset.filter(status__in=SEAT_HOLDING_STATUSES)
    else:
        queryset = queryset.filter(status__in=SEAT_HOLDING_STATUSES)
    registrations = list(queryset.order_by("participant__first_name", "participant__last_name"))
    form = BulkAttendanceForm(registrations=registrations)
    rows = [
        {
            "registration": registration,
            "status_field": form[f"status_{registration.pk}"],
            "note_field": form[f"note_{registration.pk}"],
        }
        for registration in registrations
    ]
    return render(
        request, "registrations/attendance.html",
        {
            "event": event, "form": form, "registrations": registrations, "rows": rows,
            "status_filter": status, "summary": attendance_summary(event),
            "attendance_choices": Attendance.Status.choices,
        },
    )


@organizer_required
@require_POST
def attendance_save(request, slug):
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    payload = {}
    for key, value in request.POST.items():
        if key.startswith("status_"):
            registration_id = key.split("_", 1)[1]
            payload.setdefault(registration_id, {})["status"] = value
        elif key.startswith("note_"):
            registration_id = key.split("_", 1)[1]
            payload.setdefault(registration_id, {})["note"] = value
    updated = bulk_mark_attendance(event, request.user, payload)
    if updated:
        messages.success(request, f"Attendance saved for {updated} participant(s).")
    else:
        messages.info(request, "No attendance changes were submitted.")
    return redirect("registrations:attendance", slug=event.slug)


@organizer_required
@require_POST
def check_in(request, slug):
    """Fast check-in by ticket code (organizer desk flow)."""
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    code = (request.POST.get("ticket_code") or "").strip().upper()
    registration = Registration.objects.filter(event=event, ticket_code__iexact=code).select_related("participant").first()
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if registration is None:
        if wants_json:
            return JsonResponse({"ok": False, "message": "No registration found for that ticket code."}, status=404)
        messages.error(request, f"No registration found for ticket {code or '(empty)'}.")
        return redirect("registrations:attendance", slug=event.slug)
    try:
        attendance = mark_attendance(registration, request.user, Attendance.Status.PRESENT, "Checked in at the door")
    except RegistrationError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "message": "; ".join(exc.messages)}, status=400)
        messages.error(request, "; ".join(exc.messages))
        return redirect("registrations:attendance", slug=event.slug)
    message = f"{registration.participant.display_name} checked in at {timezone.localtime(attendance.checked_in_at):%H:%M}."
    if wants_json:
        return JsonResponse({"ok": True, "message": message, "name": registration.participant.display_name,
                             "checked_in_at": timezone.localtime(attendance.checked_in_at).strftime("%H:%M")})
    messages.success(request, message)
    return redirect("registrations:attendance", slug=event.slug)


@organizer_required
def attendance_summary_view(request, slug):
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    marked = (
        Attendance.objects.filter(registration__event=event)
        .select_related("registration", "registration__participant", "marked_by")
        .order_by("-checked_in_at", "registration__participant__first_name")
    )
    context = {"event": event, "summary": attendance_summary(event), "attendance_records": marked}
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "registrations/partials/attendance_summary.html", context)
    return render(request, "registrations/attendance_summary.html", context)


@organizer_required
def export_participants(request, slug):
    event = _managed_event(request, slug)
    if event is None:
        return redirect("dashboard:organizer")
    rows = []
    for registration in event.registrations.select_related("participant", "attendance").order_by("registered_at"):
        attendance = getattr(registration, "attendance", None)
        rows.append([
            registration.participant.display_name,
            registration.participant.email,
            registration.ticket_code,
            registration.get_status_display(),
            timezone.localtime(registration.registered_at).strftime("%Y-%m-%d %H:%M"),
            attendance.get_status_display() if attendance else "Not marked",
            timezone.localtime(attendance.checked_in_at).strftime("%Y-%m-%d %H:%M") if attendance and attendance.checked_in_at else "",
            registration.notes,
        ])
    filename = f"{event.slug}-participants.csv"
    return csv_response(
        filename,
        ["Name", "Email", "Ticket", "Registration status", "Registered at", "Attendance", "Checked in at", "Notes"],
        rows,
    )


@login_required
def my_attendance(request):
    """Participant view of their attendance record."""
    registrations = (
        Registration.objects.with_related()
        .filter(participant=request.user)
        .order_by("-event__start_time")
    )
    stats = {
        "attended": registrations.filter(status=RegistrationStatus.ATTENDED).count(),
        "upcoming": registrations.filter(
            status__in=SEAT_HOLDING_STATUSES, event__start_time__gte=timezone.now()
        ).count(),
        "no_show": registrations.filter(status=RegistrationStatus.NO_SHOW).count(),
        "cancelled": registrations.filter(status=RegistrationStatus.CANCELLED).count(),
    }
    page = paginate(registrations, request.GET.get("page"), settings.TABLE_PER_PAGE)
    context = {"page_obj": page, "registrations": page.object_list, "stats": stats}
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "registrations/partials/attendance_history.html", context)
    return render(request, "registrations/my_attendance.html", context)
