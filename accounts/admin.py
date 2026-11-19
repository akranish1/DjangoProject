from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html

from .models import LoginAudit, Profile, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0
    fields = ("avatar", "phone", "job_title", "organization", "city", "website", "bio")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "full_name", "email", "role", "is_active", "is_staff", "date_joined")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("-date_joined",)
    inlines = [ProfileInline]
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Platform role", {"fields": ("role", "email_notifications")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Platform role", {"fields": ("email", "first_name", "last_name", "role")}),
    )

    @admin.display(description="Name", ordering="first_name")
    def full_name(self, obj):
        return obj.display_name or "-"


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "city", "phone", "updated_at")
    search_fields = ("user__username", "user__email", "organization", "city")
    readonly_fields = ("created_at", "updated_at", "avatar_preview")

    @admin.display(description="Avatar")
    def avatar_preview(self, obj):
        if obj.avatar:
            return format_html('<img src="{}" style="height:64px;border-radius:50%" />', obj.avatar.url)
        return "-"


@admin.register(LoginAudit)
class LoginAuditAdmin(admin.ModelAdmin):
    list_display = ("user", "logged_in_at", "ip_address")
    list_filter = ("logged_in_at",)
    search_fields = ("user__username", "user__email", "ip_address")
    date_hierarchy = "logged_in_at"
    readonly_fields = ("user", "logged_in_at", "ip_address", "user_agent")
