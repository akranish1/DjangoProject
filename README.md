# EventHub — Event Management System

A complete, production-minded event management platform built with **Django (MVT) + Django Templates, custom CSS and vanilla JavaScript**.
Three roles (Administrator, Organiser, Participant), full event lifecycle, transactional registration with waitlists, attendance,
announcements and in-app notifications — all server-rendered with progressive enhancement.

No React/Vue/Angular/Next.js, no Bootstrap/Tailwind/jQuery, no DRF, no separate frontend app, no external notification service.

---

## 1. Feature overview

| Area | What is implemented |
| --- | --- |
| **Authentication** | Custom user model with roles, sign-up (participant or organiser), login with username **or** email, logout, profile management, password change, password reset (console email in dev), login audit trail, role-based dashboards |
| **Events** | Full CRUD, categories, venues, banner upload *or* generated CSS gradient banner, running-order sessions, capacity, price, registration deadline, statuses `Draft / Published / Cancelled / Completed`, lifecycle services (publish, unpublish, cancel with fan-out notifications, complete, duplicate, broadcast) |
| **Discovery** | Keyword search across title/description/category/venue/organiser, category & venue filters, date windows (today, tomorrow, 7 days, weekend, 30 days, past), availability filter (available / almost full / sold out), 5 sort orders, pagination, live availability & status badges |
| **Registration** | One-click register with optional note, unique `EV-XXXXXXX` ticket codes, duplicate prevention (service + DB constraint), overbooking prevention (`select_for_update` + `ATOMIC` transactions), deadline enforcement, managed waitlist with automatic promotion, self-service cancellation with deadlines, re-registration after cancelling, registration history |
| **Attendance** | Attendance sheet per event, bulk save, quick check-in by ticket code (JSON + fallback), check-in timestamps, `present/late/absent/excused`, attendance summary & rate, CSV export, attendance history for participants |
| **Announcements & notifications** | Organiser announcements (pin, notify all registrants), direct participant broadcasts, in-app notifications with unread badge, drawer, filters, mark-as-read / mark-all-read |
| **Dashboards** | Organiser: event counts, registration totals, seats free/taken, occupancy, 14-day registration chart (server-aggregated), upcoming events, recent registrations, attendance overview, drafts & sold-out alerts. Participant: upcoming tickets, history, waitlists, attendance, announcements, recommendations. Administrator: platform user/event/registration statistics, growth chart, top events/organisers, category split, activity log, user & role management, CSV exports |
| **Security** | Server-side permission checks on every view (role decorators + object-level ownership), CSRF, password validation, session hardening, secure settings profile for production, activity audit log |

---

## 2. Quick start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 2. Install dependencies (SQLite needs nothing else)
pip install -r requirements.txt

# 3. Configure environment (optional for local development)
cp .env.example .env                 # then edit DJANGO_SECRET_KEY etc.

# 4. Create the database schema
python manage.py migrate

# 5. Load realistic demo data (idempotent - run it as often as you like)
python manage.py seed_demo

# 6. Run the development server
python manage.py runserver 0.0.0.0:8000
```

Open <http://127.0.0.1:8000/>.

### Quality gates

```bash
python manage.py check                 # system checks
python manage.py test                  # 144 automated tests
python smoke_test.py                   # walks every page for all three roles
python manage.py seed_demo --refresh   # rebuild demo events/registrations
```

---



All participants follow the pattern `<first>.<last>` + `demo1234` (`kabir.participant`, `meera.participant`,
`vikram.participant`, `zoya.participant`, `nikhil.participant`, `tanvi.participant`).

Login accepts either the username or the email address. **Delete or change these accounts before any real deployment.**

---

## 4. Project layout

```
eventhub/
├── config/                 # settings (env-driven), URLs, WSGI/ASGI, tiny .env loader
├── core/                   # landing page, activity log, shared form/template helpers,
│   ├── management/commands/seed_demo.py
│   └── templatetags/ui.py  # query_string, percent, status_tone ... helpers
├── accounts/               # custom user + roles, profile, auth backend, decorators
├── events/                 # Category, Venue, Event, EventSession, forms, selectors, services, views
├── registrations/          # Registration, WaitlistEntry, Attendance + registration/attendance services
├── announcements/          # Announcement, Notification + notification centre
├── dashboard/              # role dashboards, admin user management, metrics JSON endpoints
├── templates/              # base shells, partials (cards, pagination, forms), every page
├── static/css/app.css      # the design system (tokens → components → layout → responsive)
├── static/js/app.js        # progressive enhancement (vanilla JS only)
├── media/                  # uploaded banners & avatars (created at runtime)
└── smoke_test.py           # page-by-page role smoke test
```

### Architecture notes

* **Business rules live in services** (`events/services.py`, `registrations/services.py`) so views stay thin and
  the same rules apply to the UI, the Django admin and the seed command.
* **Seat integrity** — `register_for_event()` runs inside `transaction.atomic()`, locks the event row with
  `select_for_update()`, counts seats, and only then writes. Database constraints back every rule up:
  * `unique_active_registration_per_event` — one active registration per participant per event
  * `unique_waitlist_per_event` — one waitlist entry per participant
  * `registration_status_is_valid`, `event_ends_after_start`, `event_deadline_before_start`,
    `event_capacity_at_least_one`, `session_ends_after_start`, `unique_venue_name_per_city`
* **SQLite lock retries** — development SQLite locks the database during writes; the service retries transient
  `database is locked` errors a bounded number of times, then reports a friendly message. On PostgreSQL the same
  code path is a no-op.
* **Query efficiency** — `select_related` / `prefetch_related` on every list, `Count(... filter=Q(...))` annotations
  for seats, waitlists, attendance and dashboards; no N+1 loops in templates.
* **Progressive enhancement** — every workflow (register, cancel, attendance, announcements, status changes,
  notifications) works as a normal form POST. JavaScript adds filter-as-you-type, confirm dialogs, toasts,
  notification drawer, check-in and SVG charts that read real JSON endpoints.

---

## 5. Environment & production guidance

All secrets come from environment variables (`.env` is loaded automatically at startup, real env vars win):

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | **required in production** |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | host/origin allow-lists |
| `DB_ENGINE` | `sqlite` (default) or `postgres` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_CONN_MAX_AGE` | PostgreSQL connection |
| `DJANGO_EMAIL_BACKEND` | console in dev, SMTP in production |
| `SITE_NAME`, `SITE_TAGLINE`, `EVENTS_PER_PAGE`, `TABLE_PER_PAGE` | branding & pagination |

When `DJANGO_DEBUG=False` the project automatically enables HSTS, SSL redirect, secure/HTTP-only cookies,
`X-Content-Type-Options`, `Referrer-Policy: same-origin` and `X-Frame-Options: DENY`, and refuses to boot with the
development secret key.

```bash
# PostgreSQL example
DB_ENGINE=postgres DB_NAME=eventhub DB_USER=eventhub DB_PASSWORD=secret \
DJANGO_DEBUG=False DJANGO_SECRET_KEY='<generated>' DJANGO_ALLOWED_HOSTS=events.example.com \
python manage.py migrate

python manage.py collectstatic --noinput     # serve static/ via nginx or WhiteNoise
python manage.py createsuperuser             # admin role is assigned automatically
```

Deployment checklist: set a strong `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, real hosts, a production email
backend, run `migrate` + `collectstatic`, serve `media/` from your web server, and remove the demo accounts.

---

## 6. Roles & permissions (enforced server-side)

| Capability | Participant | Organiser | Administrator |
| --- | :--: | :--: | :--: |
| Discover / view published events | ✅ | ✅ | ✅ |
| Register, cancel, view own tickets & attendance | ✅ | – | – |
| Create & edit events, venues, schedules, announcements | – | ✅ (own) | ✅ (any) |
| Publish / cancel / complete / duplicate events | – | ✅ (own) | ✅ (any) |
| View participant lists, mark attendance, check in, export CSV | – | ✅ (own) | ✅ (any) |
| Manage users, roles, categories, platform statistics | – | – | ✅ |
| Activity log & Django admin | – | – | ✅ |

Attempting to manage another organiser's event returns **403** (or a redirect with a clear message for
navigation-style actions); participants cannot open organiser/admin areas at all.

---

## 7. Testing

`python manage.py test` runs **144 tests** covering:

* **accounts** — sign-up validation (duplicate email/username, password mismatch, terms), login by username/email,
  inactive users, logout, password change, password reset email, profile updates, role routing.
* **events** — slug uniqueness, date/capacity validation, DB check constraints, availability properties,
  CRUD, ownership permissions, draft privacy, search/filter/sort/pagination, AJAX partials, lifecycle services.
* **registrations** — duplicate prevention (service *and* constraint), capacity & waitlist, deadline enforcement,
  waitlist promotion on cancellation, cancellation rules, re-registration, private tickets, status updates,
  CSV export, attendance marking rules/timestamps/summary, bulk sheet, check-in, and **concurrency tests** that
  fire 12 simultaneous registrations at a 5-seat event (never overbooks) and 6 simultaneous registrations by the
  same user (exactly one row).
* **announcements** — posting + notification fan-out, validation, ownership, pin/delete, broadcasts, notification
  centre filters, mark-as-read endpoints.
* **dashboard** — every metric asserted against the database, organiser isolation, role-change guard rails,
  activation toggles, admin-only access, CSV exports.
* **core** — landing page statistics, 404/403 pages, activity log, end-to-end journey
  (organiser creates event → participants register → waitlist → cancellation promotes → announcement notifies →
  check-in → admin statistics).

---

## 8. Design system

`static/css/app.css` defines the whole visual language: colour tokens, typography scale, spacing, radii, shadows,
buttons, badges, cards, statistics, tables, forms, alerts, empty states, pagination, timeline, progress meters,
notification drawer, modals (native `<dialog>`), ticket stubs and an event-banner generator with six gradient
themes used when no image is uploaded. `static/js/app.js` (vanilla, no dependencies) adds confirmations, toasts,
async filtering, notification interactions, check-in, status updates and hand-drawn SVG charts.

Accessibility: semantic landmarks, skip link, visible focus rings, `aria-*` wiring on forms/menus/dialogs, keyboard
support (Escape closes menus, Enter activates notifications), labels on every control, `prefers-reduced-motion`
support and print styles for tickets.

---

## 9. Known limitations

* Payments are not implemented (`price` is informational); no external services are used by design.
* Email is console-based in development; configure SMTP for real password-reset delivery.
* Search is ORM `icontains` — swap in PostgreSQL full-text search for very large datasets.
* `NO_SHOW` registration status is set manually by organisers/admins (there is no cron job to auto-expire no-shows).
