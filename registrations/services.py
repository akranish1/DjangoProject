"""Transactional registration, cancellation and waitlist logic.

Everything that touches seat counts runs inside ``transaction.atomic`` and locks
the event row (``select_for_update``) so concurrent requests cannot overbook.
On SQLite the write lock is taken for the whole transaction, on PostgreSQL the
row lock guarantees correctness.  Database constraints back every rule up.
"""
import random
import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Max
from django.utils import timezone

from announcements.models import Notification
from core.models import ActivityLog
from core.services import log_activity
from events.models import Event, EventStatus

from .models import Registration, RegistrationStatus, SEAT_HOLDING_STATUSES, WaitlistEntry


class RegistrationError(ValidationError):
    """Raised when a registration cannot be performed (shown to the user)."""


#: SQLite (used for local development) locks the whole database while a write
#: transaction is open, so overlapping requests can raise a transient
#: "database (table) is locked" error.  PostgreSQL only ever raises
#: OperationalError for genuinely broken connections, where retrying is cheap.
LOCK_RETRIES = 6


def _run_with_lock_retries(operation, *args, **kwargs):
    """Execute ``operation`` retrying transient database-lock failures."""
    last_error = None
    for attempt in range(LOCK_RETRIES):
        try:
            return operation(*args, **kwargs)
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_error = exc
            time.sleep(0.06 * (attempt + 1) + random.random() * 0.04)
    raise RegistrationError(
        "The system is handling a lot of registrations right now. Please try again in a moment."
    ) from last_error


def _lock_event(event: Event) -> Event:
    """Re-read the event inside the transaction with a row lock."""
    return Event.objects.select_for_update().get(pk=event.pk)


def _seats_taken(event: Event) -> int:
    return Registration.objects.filter(event=event, status__in=SEAT_HOLDING_STATUSES).count()


def register_for_event(event: Event, participant, notes: str = "") -> tuple[Registration, bool]:
    """Register ``participant`` for ``event``.

    Returns ``(registration, created)``.  Raises :class:`RegistrationError`
    with a human readable message when the rules are violated.
    """
    return _run_with_lock_retries(_register_for_event, event, participant, notes)


def _register_for_event(event: Event, participant, notes: str = "") -> tuple[Registration, bool]:
    if participant.role == "organizer":
        raise RegistrationError("Organizer accounts cannot register for events.")
    if event.organizer_id == participant.pk:
        raise RegistrationError("You cannot register for an event you organise.")

    existing = Registration.objects.filter(event=event, participant=participant).first()
    if existing and existing.status in SEAT_HOLDING_STATUSES:
        raise RegistrationError("You are already registered for this event.")
    if existing and existing.status == RegistrationStatus.WAITLISTED:
        raise RegistrationError("You are already on the waitlist for this event.")

    with transaction.atomic():
        locked = _lock_event(event)

        if locked.status == EventStatus.DRAFT:
            raise RegistrationError("This event has not been published yet.")
        if locked.status == EventStatus.CANCELLED:
            raise RegistrationError("This event has been cancelled.")
        if locked.status == EventStatus.COMPLETED:
            raise RegistrationError("This event has already finished.")
        if timezone.now() > locked.registration_deadline:
            raise RegistrationError("The registration deadline for this event has passed.")

        taken = _seats_taken(locked)
        status = RegistrationStatus.CONFIRMED if taken < locked.capacity else RegistrationStatus.WAITLISTED
        if existing:  # re-registering after a cancellation reuses the row
            registration = existing
            registration.status = status
            registration.notes = notes or registration.notes
            registration.cancelled_at = None
            registration.cancelled_by = None
            registration.cancellation_reason = ""
            registration.save()
            created = False
        else:
            registration = Registration(event=locked, participant=participant, status=status, notes=notes)
            try:
                registration.save()
            except IntegrityError as exc:
                raise RegistrationError("You are already registered for this event.") from exc
            created = True

        if status == RegistrationStatus.WAITLISTED:
            last = WaitlistEntry.objects.filter(
                registration__event=locked
            ).aggregate(top=Max("position"))["top"] or 0
            WaitlistEntry.objects.update_or_create(
                registration=registration, defaults={"position": last + 1}
            )
            registration.waitlist_position = last + 1
            registration.save(update_fields=["waitlist_position"])
        else:
            WaitlistEntry.objects.filter(registration=registration).delete()
            registration.waitlist_position = None

        Notification.objects.create(
            recipient=participant,
            event=locked,
            title=f"Registration {'confirmed' if status == RegistrationStatus.CONFIRMED else 'waitlisted'}",
            message=(
                f"You are registered for {locked.title} on "
                f"{timezone.localtime(locked.start_time):%d %b %Y, %H:%M}."
                if status == RegistrationStatus.CONFIRMED
                else f"{locked.title} is full. You are on the waitlist and will be promoted automatically."
            ),
            kind=Notification.Kind.REGISTRATION,
            link=registration.get_absolute_url(),
        )
        if status == RegistrationStatus.CONFIRMED:
            _notify_organizer(locked, participant)
        log_activity(
            participant, ActivityLog.Action.REGISTRATION_CREATED,
            f"{participant.display_name} registered for “{locked.title}” ({registration.get_status_display()})",
            locked.get_absolute_url(),
        )
    return registration, created


def cancel_registration(registration: Registration, actor, reason: str = "") -> Registration:
    """Cancel a registration. Organizers/admins may cancel any time; participants obey deadlines."""
    return _run_with_lock_retries(_cancel_registration, registration, actor, reason)


def _cancel_registration(registration: Registration, actor, reason: str = "") -> Registration:
    with transaction.atomic():
        registration = (
            Registration.objects.select_for_update().select_related("event", "participant").get(pk=registration.pk)
        )
        event = _lock_event(registration.event)

        if registration.status not in (RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED):
            raise RegistrationError("This registration is already cancelled.")

        is_owner = actor.pk == registration.participant_id
        is_manager = actor.is_admin or event.organizer_id == actor.pk
        if is_owner and not is_manager and not registration.can_be_cancelled:
            if event.status == EventStatus.CANCELLED:
                raise RegistrationError("This event was cancelled by the organiser.")
            raise RegistrationError(
                "Registration can no longer be cancelled because the deadline has passed or the event has started."
            )
        if not is_owner and not is_manager:
            raise RegistrationError("You are not allowed to cancel this registration.")

        registration.status = RegistrationStatus.CANCELLED
        registration.cancelled_at = timezone.now()
        registration.cancelled_by = actor
        registration.cancellation_reason = (reason or "")[:240]
        registration.waitlist_position = None
        registration.save()
        WaitlistEntry.objects.filter(registration=registration).delete()

        promoted = _promote_from_waitlist(event)
        Notification.objects.create(
            recipient=registration.participant,
            event=event,
            title="Registration cancelled",
            message=(
                f"Your registration for {event.title} was cancelled"
                + (f" ({reason})." if reason else ".")
                + (" A waitlisted participant has been promoted." if promoted else "")
            ),
            kind=Notification.Kind.REGISTRATION,
            link=event.get_absolute_url(),
        )
        log_activity(
            actor, ActivityLog.Action.REGISTRATION_CANCELLED,
            f"Registration for “{event.title}” cancelled for {registration.participant.display_name}",
            event.get_absolute_url(),
        )
    return registration


def _promote_from_waitlist(event: Event) -> Registration | None:
    """Promote the oldest waitlisted registration if a seat is free. Caller holds the lock."""
    taken = _seats_taken(event)
    if taken >= event.capacity:
        return None
    entry = (
        WaitlistEntry.objects.filter(registration__event=event, registration__status=RegistrationStatus.WAITLISTED)
        .select_related("registration", "registration__participant")
        .order_by("position")
        .first()
    )
    if not entry:
        return None
    registration = entry.registration
    registration.status = RegistrationStatus.CONFIRMED
    registration.waitlist_position = None
    registration.save(update_fields=["status", "waitlist_position", "updated_at"])
    entry.delete()
    _reindex_waitlist(event)
    Notification.objects.create(
        recipient=registration.participant,
        event=event,
        title="You are off the waitlist 🎉",
        message=f"A seat opened up for {event.title}. Your registration is now confirmed.",
        kind=Notification.Kind.REGISTRATION,
        link=registration.get_absolute_url(),
    )
    return registration


def _reindex_waitlist(event: Event) -> None:
    entries = (
        WaitlistEntry.objects.filter(registration__event=event, registration__status=RegistrationStatus.WAITLISTED)
        .order_by("position", "created_at")
    )
    for index, entry in enumerate(entries, start=1):
        if entry.position != index:
            entry.position = index
            entry.save(update_fields=["position"])
            Registration.objects.filter(pk=entry.registration_id).update(waitlist_position=index)


def _notify_organizer(event: Event, participant) -> None:
    if event.organizer_id == participant.pk:
        return
    Notification.objects.create(
        recipient=event.organizer,
        event=event,
        title="New registration",
        message=f"{participant.display_name} registered for {event.title}.",
        kind=Notification.Kind.SYSTEM,
        link=event.get_absolute_url(),
    )


def notify_event_update(event: Event, actor, title: str, message: str, kind: str = "event") -> int:
    """Fan out a notification to every active registrant of an event."""
    recipients = (
        Registration.objects.filter(event=event, status__in=SEAT_HOLDING_STATUSES)
        .values_list("participant_id", flat=True)
        .distinct()
    )
    recipient_ids = [rid for rid in recipients if rid != actor.pk]
    notifications = [
        Notification(recipient_id=rid, event=event, title=title, message=message, kind=kind,
                     link=event.get_absolute_url())
        for rid in recipient_ids
    ]
    Notification.objects.bulk_create(notifications)
    return len(notifications)


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
def mark_attendance(registration: Registration, actor, status: str, note: str = "") -> "Attendance":
    """Create or update the attendance record for a registration.

    Only the owning organizer (or an admin) may record attendance.
    """
    return _run_with_lock_retries(_mark_attendance, registration, actor, status, note)


def _mark_attendance(registration: Registration, actor, status: str, note: str = "") -> "Attendance":
    from .models import Attendance

    event = registration.event
    if not (actor.is_admin or event.organizer_id == actor.pk):
        raise RegistrationError("Only the organiser of this event can record attendance.")
    if registration.status == RegistrationStatus.CANCELLED:
        raise RegistrationError("Attendance cannot be recorded for a cancelled registration.")
    if status not in dict(Attendance.Status.choices):
        raise RegistrationError("Unknown attendance status.")

    with transaction.atomic():
        registration = Registration.objects.select_for_update().select_related("event", "participant").get(pk=registration.pk)
        attendance, _ = Attendance.objects.get_or_create(registration=registration, defaults={"marked_by": actor})
        attendance.status = status
        attendance.note = (note or "")[:200]
        attendance.marked_by = actor
        if status in (Attendance.Status.PRESENT, Attendance.Status.LATE):
            if not attendance.checked_in_at:
                attendance.checked_in_at = timezone.now()
        else:
            attendance.checked_in_at = None
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            attendance.full_clean(exclude=["marked_by"])
        except DjangoValidationError as exc:
            raise RegistrationError("; ".join(sum(exc.message_dict.values(), []))) from exc
        attendance.save()

        if status in (Attendance.Status.PRESENT, Attendance.Status.LATE):
            registration.status = RegistrationStatus.ATTENDED
        elif registration.status == RegistrationStatus.ATTENDED:
            registration.status = RegistrationStatus.CONFIRMED
        registration.save(update_fields=["status", "updated_at"])

        log_activity(
            actor, ActivityLog.Action.ATTENDANCE_MARKED,
            f"{registration.participant.display_name} marked {attendance.get_status_display().lower()} "
            f"for “{event.title}”",
            event.get_absolute_url(),
        )
    return attendance


def bulk_mark_attendance(event: Event, actor, payload: dict, default_status: str = "present") -> int:
    """Apply attendance for many registrations at once from the attendance sheet.

    ``payload`` maps ``registration_id -> {"status": str, "note": str}``.
    """
    from .models import Attendance

    if not (actor.is_admin or event.organizer_id == actor.pk):
        raise RegistrationError("Only the organiser of this event can record attendance.")

    updated = 0
    for registration in Registration.objects.filter(event=event).exclude(status=RegistrationStatus.CANCELLED):
        data = payload.get(str(registration.pk)) or payload.get(registration.pk)
        status = (data or {}).get("status") or None
        if not status:
            continue
        note = (data or {}).get("note", "")
        if status not in dict(Attendance.Status.choices):
            continue
        with transaction.atomic():
            attendance, _ = Attendance.objects.get_or_create(registration=registration, defaults={"marked_by": actor})
            attendance.status = status
            attendance.note = (note or "")[:200]
            attendance.marked_by = actor
            if status in (Attendance.Status.PRESENT, Attendance.Status.LATE) and not attendance.checked_in_at:
                attendance.checked_in_at = timezone.now()
            if status in (Attendance.Status.ABSENT, Attendance.Status.EXCUSED):
                attendance.checked_in_at = None
            attendance.save()
            registration.status = (
                RegistrationStatus.ATTENDED
                if status in (Attendance.Status.PRESENT, Attendance.Status.LATE)
                else RegistrationStatus.CONFIRMED
            )
            registration.save(update_fields=["status", "updated_at"])
            updated += 1
    if updated:
        log_activity(
            actor, ActivityLog.Action.ATTENDANCE_MARKED,
            f"Attendance sheet saved for “{event.title}” ({updated} participant{'s' if updated != 1 else ''})",
            event.get_absolute_url(),
        )
    return updated


def attendance_summary(event: Event) -> dict:
    """Aggregated attendance numbers for an event (single query)."""
    from django.db.models import Count, Q

    from .models import Attendance

    stats = Registration.objects.filter(event=event).aggregate(
        total=Count("id"),
        confirmed=Count("id", filter=Q(status=RegistrationStatus.CONFIRMED)),
        attended=Count("id", filter=Q(status=RegistrationStatus.ATTENDED)),
        cancelled=Count("id", filter=Q(status=RegistrationStatus.CANCELLED)),
        waitlisted=Count("id", filter=Q(status=RegistrationStatus.WAITLISTED)),
        present=Count("id", filter=Q(attendance__status=Attendance.Status.PRESENT)),
        late=Count("id", filter=Q(attendance__status=Attendance.Status.LATE)),
        absent=Count("id", filter=Q(attendance__status=Attendance.Status.ABSENT)),
        excused=Count("id", filter=Q(attendance__status=Attendance.Status.EXCUSED)),
    )
    marked = stats["present"] + stats["late"] + stats["absent"] + stats["excused"]
    stats["marked"] = marked
    stats["unmarked"] = max(stats["total"] - stats["cancelled"] - marked, 0)
    stats["attendance_rate"] = round((stats["present"] + stats["late"]) / (marked or 1) * 100)
    stats["capacity"] = event.capacity
    return stats
