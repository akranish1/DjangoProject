"""Cross-cutting helpers shared by every app."""
import csv

from django.http import HttpResponse

from .models import ActivityLog


def log_activity(actor, action: str, description: str, target_url: str = "") -> ActivityLog:
    """Record an auditable platform activity entry."""
    return ActivityLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        description=description[:255],
        target_url=(target_url or "")[:300],
    )


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.META.get("REMOTE_ADDR")


def csv_response(filename: str, headers: list[str], rows: list[list]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerows(rows)
    return response


def paginate(queryset, page_number, per_page: int):
    """Return a Page object without importing Paginator everywhere."""
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

    paginator = Paginator(queryset, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)
