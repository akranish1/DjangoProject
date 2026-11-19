from django import forms

from core.forms import StyledFormMixin

from .models import Announcement


class AnnouncementForm(StyledFormMixin, forms.ModelForm):
    notify = forms.BooleanField(
        required=False, initial=True, label="Send an in-app notification to all registered participants",
    )

    class Meta:
        model = Announcement
        fields = ["title", "body", "is_pinned"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 6, "maxlength": 2000, "data-counter": "true",
                                          "placeholder": "Share schedule changes, venue details, what to bring…"}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. Doors open at 9:00 AM"}),
        }

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if len(body) < 10:
            raise forms.ValidationError("Please write at least a short paragraph (10+ characters).")
        return body
