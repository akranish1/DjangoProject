"""Small presentation helpers used by the templates."""
from django import template
from django.utils import timezone
from django.utils.http import urlencode

register = template.Library()


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """Rebuild the current querystring with ``kwargs`` replaced/removed."""
    request = context.get("request")
    params = request.GET.copy() if request else None
    if params is None:
        params = {}
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    if "page" not in kwargs:
        params.pop("page", None)
    encoded = urlencode({k: v for k, v in params.items() if v not in ("", None, [])}, doseq=True)
    return f"?{encoded}" if encoded else "?"


@register.simple_tag(takes_context=True)
def query_with_page(context, page):
    request = context.get("request")
    params = request.GET.copy() if request else {}
    params["page"] = page
    return "?" + urlencode({k: v for k, v in params.items() if v not in ("", None)}, doseq=True)


@register.filter
def percent(part, whole):
    try:
        part, whole = float(part or 0), float(whole or 0)
    except (TypeError, ValueError):
        return 0
    if whole <= 0:
        return 0
    return round(part / whole * 100)


@register.filter
def fill_percent(part, whole):
    return min(percent(part, whole), 100)


@register.filter
def days_until(value):
    if not value:
        return None
    delta = value - timezone.now()
    return delta.days


@register.filter
def initials_of(value):
    return getattr(value, "initials", "?")


@register.filter
def status_tone(value):
    """Map any status string to a design-system tone name."""
    mapping = {
        "published": "success", "confirmed": "success", "present": "success", "active": "success",
        "draft": "neutral", "waitlisted": "warning", "late": "warning",
        "cancelled": "danger", "absent": "danger", "inactive": "danger", "full": "danger",
        "completed": "info", "excused": "info",
    }
    return mapping.get(str(value).lower(), "neutral")
