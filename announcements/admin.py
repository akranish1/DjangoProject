from django.contrib import admin

from .models import Announcement, Notification


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "author", "is_pinned", "notified_count", "created_at")
    list_filter = ("is_pinned", "created_at", "event__category")
    search_fields = ("title", "body", "event__title")
    autocomplete_fields = ("event", "author")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "notified_count")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "kind", "is_read", "created_at")
    list_filter = ("kind", "is_read", "created_at")
    search_fields = ("title", "message", "recipient__username", "recipient__email")
    autocomplete_fields = ("recipient", "actor", "event")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "read_at")
