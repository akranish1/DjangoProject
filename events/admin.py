from django.contrib import admin
from django.utils.html import format_html

from registrations.models import Registration

from .models import Category, Event, EventImage, EventSession, Venue


class EventSessionInline(admin.TabularInline):
    model = EventSession
    extra = 0
    fields = ("title", "speaker", "location", "start_time", "end_time", "order")


class RegistrationInline(admin.TabularInline):
    model = Registration
    extra = 0
    can_delete = False
    fields = ("participant", "status", "ticket_code", "registered_at")
    readonly_fields = ("registered_at",)
    show_change_link = True


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "icon", "color_chip", "event_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Colour")
    def color_chip(self, obj):
        return format_html('<span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:{}"></span> {}', obj.color, obj.color)

    @admin.display(description="Events")
    def event_count(self, obj):
        return obj.events.count()


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "capacity", "is_active", "event_count")
    list_filter = ("city", "is_active", "state")
    search_fields = ("name", "city", "address_line")
    list_editable = ("is_active",)

    @admin.display(description="Events")
    def event_count(self, obj):
        return obj.events.count()


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "start_time", "venue", "organizer", "capacity", "seats", "category")
    list_filter = ("status", "category", "is_featured", "venue")
    search_fields = ("title", "summary", "description", "organizer__username", "organizer__email")
    autocomplete_fields = ("organizer", "category", "venue")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "start_time"
    inlines = [EventSessionInline, RegistrationInline]
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("venue", "organizer", "category")
    actions = ("mark_published", "mark_completed", "mark_cancelled")

    @admin.display(description="Seats")
    def seats(self, obj):
        return f"{obj.seats_taken}/{obj.capacity}"

    @admin.action(description="Publish selected events")
    def mark_published(self, request, queryset):
        from .services import EventStateError, publish_event

        published = 0
        for event in queryset:
            try:
                publish_event(event, request.user)
                published += 1
            except EventStateError:
                continue
        self.message_user(request, f"{published} event(s) published.")

    @admin.action(description="Mark selected events completed")
    def mark_completed(self, request, queryset):
        from .services import EventStateError, complete_event

        done = 0
        for event in queryset:
            try:
                complete_event(event, request.user)
                done += 1
            except EventStateError:
                continue
        self.message_user(request, f"{done} event(s) completed.")

    @admin.action(description="Cancel selected events (notifies participants)")
    def mark_cancelled(self, request, queryset):
        from .services import EventStateError, cancel_event

        cancelled = 0
        for event in queryset:
            try:
                cancel_event(event, request.user, "Cancelled by administrator.")
                cancelled += 1
            except EventStateError:
                continue
        self.message_user(request, f"{cancelled} event(s) cancelled.")


@admin.register(EventSession)
class EventSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "speaker", "start_time", "end_time")
    list_filter = ("event",)
    search_fields = ("title", "speaker", "event__title")
    autocomplete_fields = ("event",)


@admin.register(EventImage)
class EventImageAdmin(admin.ModelAdmin):
    list_display = ("event", "caption", "uploaded_at")
    search_fields = ("event__title", "caption")
    autocomplete_fields = ("event",)
