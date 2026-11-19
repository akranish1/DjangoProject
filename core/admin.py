from django.contrib import admin

from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "description")
    list_filter = ("action", "created_at")
    search_fields = ("description", "actor__username", "actor__email")
    date_hierarchy = "created_at"
    readonly_fields = ("actor", "action", "description", "target_url", "created_at")

    def has_add_permission(self, request):
        return False
