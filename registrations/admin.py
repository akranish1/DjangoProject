from django.contrib import admin
from django.utils.html import format_html

from .models import Attendance, Registration, WaitlistEntry


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("ticket_code", "event", "participant", "status", "registered_at", "attendance_state")
    list_filter = ("status", "event__category", "registered_at", "event")
    search_fields = ("ticket_code", "participant__username", "participant__email", "event__title")
    autocomplete_fields = ("event", "participant", "cancelled_by")
    date_hierarchy = "registered_at"
    readonly_fields = ("ticket_code", "registered_at", "updated_at", "cancelled_at")
    list_select_related = ("event", "participant")

    @admin.display(description="Attendance")
    def attendance_state(self, obj):
        attendance = getattr(obj, "attendance", None)
        if not attendance:
            return "—"
        tone = {"present": "#0e9f6e", "late": "#c27803", "absent": "#e02424", "excused": "#3f83f8"}
        return format_html(
            '<strong style="color:{}">{}</strong>', tone.get(attendance.status, "#333"),
            attendance.get_status_display(),
        )


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("registration", "status", "checked_in_at", "marked_by", "updated_at")
    list_filter = ("status", "checked_in_at")
    search_fields = ("registration__ticket_code", "registration__participant__email", "note")
    autocomplete_fields = ("registration", "marked_by")
    date_hierarchy = "checked_in_at"


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("registration", "position", "created_at")
    list_filter = ("position",)
    search_fields = ("registration__ticket_code",)
    autocomplete_fields = ("registration",)
