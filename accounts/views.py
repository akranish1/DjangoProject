from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from core.models import ActivityLog
from core.services import client_ip, log_activity
from .forms import LoginForm, ProfileForm, RegistrationForm, StyledPasswordChangeForm, UserDetailsForm
from .models import LoginAudit, Profile


@never_cache
@csrf_protect
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:router")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            Profile.objects.get_or_create(user=user)
        login(request, user, backend="accounts.backends.EmailOrUsernameBackend")
        log_activity(user, ActivityLog.Action.SIGNUP, f"{user.display_name} created a {user.get_role_display()} account")
        messages.success(request, f"Welcome aboard, {user.first_name or user.username}! Your account is ready.")
        return redirect("dashboard:router")
    if request.method == "POST":
        messages.error(request, "Please correct the highlighted fields and try again.")
    return render(request, "accounts/register.html", {"form": form})


class EventHubLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.get_user()
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)
        LoginAudit.objects.create(
            user=user,
            ip_address=client_ip(self.request),
            user_agent=self.request.META.get("HTTP_USER_AGENT", "")[:255],
        )
        log_activity(user, ActivityLog.Action.LOGIN, f"{user.display_name} signed in")
        messages.success(self.request, f"Signed in as {user.display_name} ({user.get_role_display()}).")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Sign-in failed. Please check your credentials.")
        return super().form_invalid(form)


class EventHubLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            messages.info(request, "You have been signed out.")
        return super().dispatch(request, *args, **kwargs)


@login_required
def profile(request):
    user_form = UserDetailsForm(instance=request.user, prefix="user")
    profile_form = ProfileForm(instance=request.user.profile, prefix="profile")
    if request.method == "POST":
        if "save_details" in request.POST:
            user_form = UserDetailsForm(request.POST, instance=request.user, prefix="user")
            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your account details were updated.")
                return redirect("accounts:profile")
        elif "save_profile" in request.POST:
            profile_form = ProfileForm(
                request.POST, request.FILES, instance=request.user.profile, prefix="profile"
            )
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile was updated.")
                return redirect("accounts:profile")
        messages.error(request, "We could not save your changes. Please review the errors below.")
    # Recent activity for the sidebar of the profile page
    history = (
        request.user.activities.select_related("actor")[:8]
        if request.user.role != "participant"
        else None
    )
    context = {
        "user_form": user_form,
        "profile_form": profile_form,
        "history": history,
        "login_history": request.user.login_audits.all()[:4],
    }
    return render(request, "accounts/profile.html", context)


class EventHubPasswordChangeView(PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed successfully.")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "Password not changed. Please fix the errors below.")
        return super().form_invalid(form)
