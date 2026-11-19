from django.urls import path

from . import views

app_name = "registrations"

urlpatterns = [
    path("mine/", views.my_registrations, name="mine"),
    path("attendance/", views.my_attendance, name="my_attendance"),
    path("<str:ticket_code>/", views.registration_detail, name="detail"),
    path("<str:ticket_code>/cancel/", views.cancel_registration_view, name="cancel"),
    path("events/<slug:slug>/register/", views.register_for_event_view, name="register"),
    path("events/<slug:slug>/participants/", views.event_participants, name="participants"),
    path("events/<slug:slug>/participants/export/", views.export_participants, name="export"),
    path("events/<slug:slug>/attendance/", views.attendance_sheet, name="attendance"),
    path("events/<slug:slug>/attendance/save/", views.attendance_save, name="attendance_save"),
    path("events/<slug:slug>/attendance/check-in/", views.check_in, name="check_in"),
    path("events/<slug:slug>/attendance/summary/", views.attendance_summary_view, name="attendance_summary"),
    path("records/<int:pk>/status/", views.update_registration_status, name="update_status"),
]
