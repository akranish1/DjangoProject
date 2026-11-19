from django import forms

from core.forms import StyledFormMixin

from .models import Attendance, Registration


class RegistrationForm(StyledFormMixin, forms.Form):
    """Participant-facing registration form (notes are optional)."""

    notes = forms.CharField(
        required=False, max_length=240, label="Anything we should know?",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Dietary needs, accessibility requests, questions…"}),
    )
    agree = forms.BooleanField(
        label="I understand the event details and the cancellation policy",
        error_messages={"required": "Please confirm before registering."},
    )


class CancelRegistrationForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        required=False, max_length=240, label="Reason (optional)",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Let the organiser know why you are cancelling…"}),
    )
    confirm = forms.BooleanField(
        label="Yes, cancel this registration",
        error_messages={"required": "Tick the box to confirm the cancellation."},
    )


class AttendanceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["status", "note"]
        widgets = {"note": forms.TextInput(attrs={"placeholder": "Optional note, e.g. arrived late"})}


class BulkAttendanceForm(forms.Form):
    """Rendered row-by-row on the attendance page; one select per registration."""

    registration_ids = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, registrations=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.registrations = list(registrations or [])
        for registration in self.registrations:
            self.fields[f"status_{registration.pk}"] = forms.ChoiceField(
                choices=Attendance.Status.choices,
                required=False,
                initial=getattr(getattr(registration, "attendance", None), "status", Attendance.Status.PRESENT),
                widget=forms.Select(attrs={"class": "select-input select-sm", "data-registration": registration.pk}),
            )
            self.fields[f"note_{registration.pk}"] = forms.CharField(
                required=False, max_length=200,
                initial=getattr(getattr(registration, "attendance", None), "note", ""),
                widget=forms.TextInput(attrs={"class": "text-input text-sm", "placeholder": "Note (optional)"}),
            )


class ParticipantStatusForm(StyledFormMixin, forms.Form):
    """Organizer action: change a single registration's status."""

    status = forms.ChoiceField(
        choices=[c for c in Registration._meta.get_field("status").choices if c[0] != "waitlisted"],
    )
    reason = forms.CharField(required=False, max_length=240, widget=forms.TextInput(attrs={"placeholder": "Optional note"}))
