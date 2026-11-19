from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.router, name="router"),
    path("organizer/", views.organizer_dashboard, name="organizer"),
    path("organizer/chart-data/", views.organizer_chart_data, name="organizer_chart"),
    path("participant/", views.participant_dashboard, name="participant"),
    path("admin/", views.admin_dashboard, name="admin"),
    path("admin/chart-data/", views.admin_chart_data, name="admin_chart"),
    path("admin/users/", views.admin_users, name="admin_users"),
    path("admin/users/export/", views.export_users, name="export_users"),
    path("admin/users/<int:pk>/", views.admin_user_detail, name="admin_user"),
    path("admin/users/<int:pk>/toggle/", views.admin_toggle_user, name="admin_toggle_user"),
    path("admin/events/", views.admin_events, name="admin_events"),
    path("admin/registrations/", views.admin_registrations, name="admin_registrations"),
    path("admin/registrations/export/", views.export_registrations, name="export_registrations"),
    path("admin/activity/", views.admin_activity, name="admin_activity"),
]
