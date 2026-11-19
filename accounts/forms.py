from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

from core.forms import StyledFormMixin

from .models import Profile, User


class RegistrationForm(StyledFormMixin, UserCreationForm):
    """Public sign-up. Participants and organizers can self-register."""

    ROLE_CHOICES = [
        (User.Role.PARTICIPANT, "Participant - discover and attend events"),
        (User.Role.ORGANIZER, "Organizer - create and manage events"),
    ]

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField(label="Email address")
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=User.Role.PARTICIPANT, label="I am joining as")
    terms = forms.BooleanField(
        label="I agree to the platform terms and privacy policy",
        error_messages={"required": "Please accept the terms to create an account."},
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "email"]
        widgets = {"username": forms.TextInput(attrs={"autocomplete": "username"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "Password"
        self.fields["password2"].label = "Confirm password"
        self.fields["username"].help_text = "Letters, digits and @/./+/-/_ only."

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
        return user


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(label="Username or email", widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember_me = forms.BooleanField(required=False, initial=True, label="Keep me signed in")

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Those credentials did not match our records. Please check and try again.",
    }


class UserDetailsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "email_notifications"]
        labels = {"email_notifications": "Email me about my registrations and announcements"}

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["avatar", "phone", "job_title", "organization", "city", "website", "bio"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 4, "maxlength": 600, "data-counter": "true"})}

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and getattr(avatar, "size", 0) > 4 * 1024 * 1024:
            raise forms.ValidationError("Avatar images must be 4 MB or smaller.")
        return avatar


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Current password"
        self.fields["new_password1"].label = "New password"
        self.fields["new_password2"].label = "Confirm new password"


class UserRoleForm(StyledFormMixin, forms.ModelForm):
    """Used by platform administrators to change a user's role."""

    class Meta:
        model = User
        fields = ["role"]

    def clean_role(self):
        role = self.cleaned_data["role"]
        user = self.instance
        if user.is_superuser and role != User.Role.ADMIN:
            raise forms.ValidationError("Demote the superuser from the Django admin first.")
        if role != User.Role.ADMIN and user.organized_events.exists() and role == User.Role.PARTICIPANT:
            raise forms.ValidationError(
                "This user owns events. Reassign those events before switching them to Participant."
            )
        return role
