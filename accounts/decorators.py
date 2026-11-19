from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Restrict a view to the given roles (platform admins always pass)."""

    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.is_admin or request.user.role in roles:
                return view(request, *args, **kwargs)
            raise PermissionDenied("Your account does not have access to this area.")

        return _wrapped

    return decorator


organizer_required = role_required("organizer")
admin_required = role_required("admin")
participant_required = role_required("participant")


def organizer_owns_event(view):
    """Object-level guard: only the owning organizer (or an admin) may proceed."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        from events.models import Event

        slug = kwargs.get("slug") or kwargs.get("event_slug")
        event = Event.objects.select_related("venue", "category").filter(slug=slug).first()
        if event is None:
            raise PermissionDenied("Event not found or you may not manage it.")
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.can_manage_event(event):
            messages.error(request, "You can only manage events that you own.")
            raise PermissionDenied("You are not the organiser of this event.")
        request.event = event
        return view(request, *args, **kwargs)

    return _wrapped
