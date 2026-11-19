"""Event lifecycle operations (publish, cancel, complete, duplicate)."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from announcements.models import Notification
from core.models import ActivityLog
from core.services import log_activity
from registrations.models import Registration, RegistrationStatus
from registrations.services import notify_event_update

from .models import Event, EventStatus


class EventStateError(Exception):
    """Raised for invalid lifecycle transitions - message is shown to the user."""


ALLOWED_TRANSITIONS = {
    EventStatus.DRAFT: {EventStatus.PUBLISHED, EventStatus.CANCELLED},
    EventStatus.PUBLISHED: {EventStatus.DRAFT, EventStatus.CANCELLED, EventStatus.COMPLETED},
    EventStatus.CANCELLED: {EventStatus.DRAFT},
    EventStatus.COMPLETED: {EventStatus.PUBLISHED},
}


def can_transition(event: Event, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(event.status, set())


@transaction.atomic
def publish_event(event: Event, actor) -> Event:
    event = Event.objects.select_for_update().get(pk=event.pk)
    if event.status == EventStatus.PUBLISHED:
        raise EventStateError("This event is already published.")
    if not can_transition(event, EventStatus.PUBLISHED):
        raise EventStateError(f"A {event.get_status_display().lower()} event cannot be published.")
    if event.registration_deadline < timezone.now():
        raise EventStateError("Update the registration deadline before publishing - it is already in the past.")
    event.status = EventStatus.PUBLISHED
    event.cancellation_reason = ""
    event.save(update_fields=["status", "cancellation_reason", "updated_at"])
    log_activity(actor, ActivityLog.Action.EVENT_PUBLISHED, f"“{event.title}” published", event.get_absolute_url())
    return event


@transaction.atomic
def unpublish_event(event: Event, actor) -> Event:
    event = Event.objects.select_for_update().get(pk=event.pk)
    if event.status != EventStatus.PUBLISHED:
        raise EventStateError("Only published events can be moved back to draft.")
    event.status = EventStatus.DRAFT
    event.save(update_fields=["status", "updated_at"])
    log_activity(actor, ActivityLog.Action.EVENT_UPDATED, f"“{event.title}” moved back to draft", event.get_absolute_url())
    return event


@transaction.atomic
def cancel_event(event: Event, actor, reason: str) -> Event:
    event = Event.objects.select_for_update().get(pk=event.pk)
    if event.status == EventStatus.CANCELLED:
        raise EventStateError("This event is already cancelled.")
    if not can_transition(event, EventStatus.CANCELLED):
        raise EventStateError("Completed events cannot be cancelled.")
    event.status = EventStatus.CANCELLED
    event.cancellation_reason = (reason or "").strip()[:500]
    event.save(update_fields=["status", "cancellation_reason", "updated_at"])

    holders = (
        Registration.objects.filter(event=event, status__in=[RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED])
        .select_related("participant")
    )
    now = timezone.now()
    affected = list(holders)
    Registration.objects.filter(pk__in=[r.pk for r in affected]).update(
        status=RegistrationStatus.CANCELLED, cancelled_at=now, cancelled_by=actor,
        cancellation_reason="Event cancelled by organiser",
    )
    Notification.objects.bulk_create([
        Notification(
            recipient_id=registration.participant_id,
            event=event,
            actor=actor,
            kind=Notification.Kind.EVENT,
            title="Event cancelled",
            message=f"“{event.title}” has been cancelled. {event.cancellation_reason}".strip(),
            link=event.get_absolute_url(),
        )
        for registration in affected
    ])
    log_activity(
        actor, ActivityLog.Action.EVENT_CANCELLED,
        f"“{event.title}” cancelled ({len(affected)} registration(s) released)", event.get_absolute_url(),
    )
    return event


@transaction.atomic
def complete_event(event: Event, actor) -> Event:
    event = Event.objects.select_for_update().get(pk=event.pk)
    if event.status != EventStatus.PUBLISHED:
        raise EventStateError("Only published events can be marked as completed.")
    event.status = EventStatus.COMPLETED
    event.save(update_fields=["status", "updated_at"])
    log_activity(actor, ActivityLog.Action.EVENT_COMPLETED, f"“{event.title}” marked completed", event.get_absolute_url())
    return event


@transaction.atomic
def duplicate_event(event: Event, actor) -> Event:
    """Clone an event (and its schedule) as a fresh draft."""
    copy = Event.objects.create(
        organizer=actor if actor.role == "organizer" else event.organizer,
        category=event.category,
        venue=event.venue,
        title=f"{event.title} (copy)"[:180],
        summary=event.summary,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
        registration_deadline=event.registration_deadline,
        capacity=event.capacity,
        price=event.price,
        status=EventStatus.DRAFT,
    )
    for session in event.sessions.all():
        session.pk = None
        session.event = copy
        session.save()
    log_activity(actor, ActivityLog.Action.EVENT_CREATED, f"“{event.title}” duplicated as a draft", copy.get_absolute_url())
    return copy


def events_needing_completion():
    """Published events that finished more than a day ago (admin housekeeping)."""
    cutoff = timezone.now() - timedelta(days=1)
    return Event.objects.filter(status=EventStatus.PUBLISHED, end_time__lt=cutoff)


def broadcast_event_update(event: Event, actor, title: str, message: str) -> int:
    return notify_event_update(event, actor, title, message, kind=Notification.Kind.EVENT)
