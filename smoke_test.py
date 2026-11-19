"""Development smoke test: walks every important page for each role.

    python smoke_test.py          # uses the local sqlite database

It exercises the real URL config, views, templates and permissions so that
template or query errors surface before manual QA.  Exit code is non-zero when
any page fails.
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client  # noqa: E402

from events.models import Event  # noqa: E402
from registrations.models import Registration  # noqa: E402

FAILURES = []
CHECKED = []


def check(label, response, expected=(200,)):
    status = response.status_code
    CHECKED.append((label, status))
    if status not in expected:
        FAILURES.append(f"{label}: HTTP {status} (expected {expected})")
        return False
    if status == 200:
        body = response.content.decode(errors="ignore")
        for marker in ("Traceback", "TemplateDoesNotExist", "TemplateSyntaxError"):
            if marker in body:
                FAILURES.append(f"{label}: page contains {marker}")
                return False
    return True


def as_user(username, password):
    client = Client()
    assert client.login(username=username, password=password), f"cannot log in as {username}"
    return client


def main():
    anonymous = Client()
    published = Event.objects.published().first()
    draft = Event.objects.filter(status="draft").first()
    any_event = published or Event.objects.first()
    ticket = (
        Registration.objects.filter(status="confirmed", participant__username="aarav.participant").first()
        or Registration.objects.filter(status="confirmed").first()
    )

    print("Public pages")
    for entry in [
        ("landing", "/", (200,)),
        ("event list", "/events/", (200,)),
        ("event list + filters", "/events/?q=summit&sort=popular&availability=available", (200,)),
        ("event list + past", "/events/?date=past", (200,)),
        ("venues (anon -> login)", "/events/venues/", (302,)),
        ("login", "/accounts/login/", (200,)),
        ("register", "/accounts/register/", (200,)),
        ("password reset", "/accounts/password/reset/", (200,)),
        ("protected page (anon -> login)", "/dashboard/organizer/", (302,)),
        ("404 page", "/nope/", (404,)),
    ]:
        label, url, expected = (list(entry) + [(200,)])[:3]
        check(label, anonymous.get(url), expected=expected)

    if published:
        check("event detail (anon)", anonymous.get(f"/events/{published.slug}/"))
        check("register page (anon -> login)",
              anonymous.get(f"/registrations/events/{published.slug}/register/"), expected=(302,))
        check("detail with ajax grid", anonymous.get("/events/",
              HTTP_X_REQUESTED_WITH="XMLHttpRequest"))

    print("Participant")
    participant = as_user("aarav.participant", "demo1234")
    for entry in [
        ("dashboard router redirect", "/dashboard/", (302,)),
        ("participant dashboard", "/dashboard/participant/"),
        ("my registrations", "/registrations/mine/"),
        ("my registrations ajax", "/registrations/mine/"),
        ("attendance history", "/registrations/attendance/"),
        ("notifications", "/notifications/"),
        ("profile", "/accounts/profile/"),
        ("password change", "/accounts/password/change/"),
    ]:
        label, url, expected = (list(entry) + [(200,)])[:3]
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if "ajax" in label else {}
        check(label, participant.get(url, **headers), expected=expected)
    if ticket:
        check("ticket page", participant.get(f"/registrations/{ticket.ticket_code}/"))
    check("cannot open organizer dashboard", participant.get("/dashboard/organizer/"), expected=(403,))
    check("cannot open admin users", participant.get("/dashboard/admin/users/"), expected=(403,))

    print("Organizer")
    organizer = as_user("priya.organizer", "demo1234")
    for entry in [
        ("organizer dashboard", "/dashboard/organizer/"),
        ("dashboard router redirect", "/dashboard/", (302,)),
        ("chart data", "/dashboard/organizer/chart-data/"),
        ("event create", "/events/new/"),
        ("venues", "/events/venues/"),
        ("venue create", "/events/venues/new/"),
        ("notifications", "/notifications/"),
        ("participant ticket page (organizer view)", "/registrations/mine/"),
    ]:
        check(label, organizer.get(url))
    own = Event.objects.filter(organizer__username="priya.organizer").first()
    if own:
        for label, url in [
            ("manage", f"/events/{own.slug}/manage/"),
            ("edit", f"/events/{own.slug}/edit/"),
            ("participants", f"/registrations/events/{own.slug}/participants/"),
            ("participants ajax", f"/registrations/events/{own.slug}/participants/"),
            ("attendance sheet", f"/registrations/events/{own.slug}/attendance/"),
            ("attendance summary", f"/registrations/events/{own.slug}/attendance/summary/"),
            ("export csv", f"/registrations/events/{own.slug}/participants/export/"),
            ("broadcast", f"/events/{own.slug}/broadcast/"),
            ("announcement form", f"/notifications/events/{own.slug}/announcements/new/"),
            ("cancel page", f"/events/{own.slug}/cancel/"),
            ("delete page", f"/events/{own.slug}/delete/"),
        ]:
            headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if "ajax" in label else {}
            check(label, organizer.get(url, **headers))
    if draft and draft.organizer_id != own.organizer_id if own else True:
        check("cannot manage another organiser's draft",
              organizer.get(f"/events/{draft.slug}/manage/"), expected=(302, 403)) if draft else None
    check("cannot open platform admin", organizer.get("/dashboard/admin/"), expected=(403,))

    print("Administrator")
    admin = as_user("admin", "admin1234")
    for entry in [
        ("dashboard", "/dashboard/admin/"),
        ("chart data", "/dashboard/admin/chart-data/"),
        ("users", "/dashboard/admin/users/"),
        ("users ajax", "/dashboard/admin/users/"),
        ("events", "/dashboard/admin/events/"),
        ("registrations", "/dashboard/admin/registrations/"),
        ("activity log", "/dashboard/admin/activity/"),
        ("categories", "/events/categories/"),
        ("category create", "/events/categories/new/"),
        ("users csv", "/dashboard/admin/users/export/"),
        ("registrations csv", "/dashboard/admin/registrations/export/"),
        ("django admin", "/django-admin/"),
    ]:
        label, url, expected = (list(entry) + [(200,)])[:3]
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if "ajax" in label else {}
        check(label, admin.get(url), expected=expected)
    user = Event.objects.published().first()
    check("django admin events", admin.get("/django-admin/events/event/"))
    first_user = django.contrib.auth.get_user_model().objects.first()
    check("admin user detail", admin.get(f"/dashboard/admin/users/{first_user.pk}/"))

    print(f"\nChecked {len(CHECKED)} pages")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  ✗ {failure}")
        return 1
    print("All pages responded correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
