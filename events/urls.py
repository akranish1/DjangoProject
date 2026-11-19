from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("new/", views.event_create, name="create"),
    path("venues/", views.venue_list, name="venues"),
    path("venues/new/", views.venue_create, name="venue_create"),
    path("venues/<int:pk>/edit/", views.venue_update, name="venue_update"),
    path("categories/", views.category_list, name="categories"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<slug:slug>/edit/", views.category_update, name="category_update"),
    path("<slug:slug>/", views.event_detail, name="detail"),
    path("<slug:slug>/manage/", views.event_manage, name="manage"),
    path("<slug:slug>/edit/", views.event_update, name="update"),
    path("<slug:slug>/delete/", views.event_delete, name="delete"),
    path("<slug:slug>/publish/", views.event_publish, name="publish"),
    path("<slug:slug>/cancel/", views.event_cancel, name="cancel"),
    path("<slug:slug>/complete/", views.event_complete, name="complete"),
    path("<slug:slug>/duplicate/", views.event_duplicate, name="duplicate"),
    path("<slug:slug>/broadcast/", views.event_broadcast, name="broadcast"),
]
