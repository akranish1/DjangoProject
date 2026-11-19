from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.notification_list, name="notifications"),
    path("<int:pk>/open/", views.notification_open, name="open"),
    path("<int:pk>/read/", views.notification_mark_read, name="mark_read"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
    path("announcements/<int:pk>/", views.announcement_detail, name="detail"),
    path("events/<slug:slug>/announcements/new/", views.announcement_create, name="create"),
    path("announcements/<int:pk>/delete/", views.announcement_delete, name="delete"),
    path("announcements/<int:pk>/pin/", views.announcement_toggle_pin, name="toggle_pin"),
]
