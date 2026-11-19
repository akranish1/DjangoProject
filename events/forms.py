from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from core.forms import StyledFormMixin

from .models import Category, Event, EventSession, EventStatus, Venue

DATETIME_INPUT_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


class EventForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title", "summary", "description", "category", "venue", "banner",
            "start_time", "end_time", "registration_deadline", "capacity", "price",
            "status", "is_featured",
        ]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "registration_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.Textarea(attrs={"rows": 7, "data-counter": "true"}),
            "summary": forms.TextInput(attrs={"placeholder": "One line that sells the event (max 240 characters)"}),
            "capacity": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "price": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
        }
        help_texts = {
            "summary": "Shown on event cards in search results.",
            "banner": "JPG or PNG, at least 1200×600 px. Leave empty to use a generated gradient banner.",
            "is_featured": "Highlight this event on the landing page (administrators only).",
            "status": "Draft events stay hidden from participants until published.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.all()
        self.fields["category"].empty_label = "Select a category…"
        self.fields["venue"].queryset = Venue.objects.order_by("name", "city")
        self.fields["venue"].empty_label = "Select a venue…"
        for name in ("start_time", "end_time", "registration_deadline"):
            self.fields[name].input_formats = DATETIME_INPUT_FORMATS
        self.fields["status"].choices = [
            (EventStatus.DRAFT, "Draft - only visible to me"),
            (EventStatus.PUBLISHED, "Published - open to participants"),
        ]
        if not (user and user.is_admin):
            self.fields.pop("is_featured", None)
        else:
            self.fields["is_featured"].help_text = self.Meta.help_texts["is_featured"]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 5:
            raise forms.ValidationError("Give the event a descriptive title (at least 5 characters).")
        return title

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        deadline = cleaned.get("registration_deadline")
        capacity, venue = cleaned.get("capacity"), cleaned.get("venue")

        if start and end and end <= start:
            self.add_error("end_time", "The end time must be after the start time.")
        if deadline and start and deadline > start:
            self.add_error("registration_deadline", "Registration must close on or before the event starts.")
        if deadline and deadline < timezone.now() and cleaned.get("status") == EventStatus.PUBLISHED:
            self.add_error("registration_deadline", "A published event needs a future registration deadline.")
        if capacity and venue and venue.capacity and capacity > venue.capacity:
            self.add_error(
                "capacity",
                f"{venue.name} can host at most {venue.capacity} attendees. Choose a smaller capacity or another venue.",
            )
        return cleaned

    def save(self, commit=True):
        event = super().save(commit=False)
        if self.user and event.pk is None:
            event.organizer = self.user
        if commit:
            event.save()
        return event


class EventSessionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EventSession
        fields = ["title", "speaker", "location", "start_time", "end_time", "description", "order"]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.TextInput(attrs={"placeholder": "Optional short description"}),
            "order": forms.NumberInput(attrs={"min": 0, "step": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("start_time", "end_time"):
            self.fields[name].input_formats = DATETIME_INPUT_FORMATS
        self.fields["order"].required = False


EventSessionFormSet = inlineformset_factory(
    Event, EventSession, form=EventSessionForm, extra=2, can_delete=True, max_num=25,
)


class EventFilterForm(forms.Form):
    """Discovery filters - rendered as a GET form, never mutating data."""

    SORT_CHOICES = [
        ("soonest", "Starting soonest"),
        ("latest", "Starting latest"),
        ("newest", "Recently added"),
        ("popular", "Most popular"),
        ("title", "Title A → Z"),
    ]
    AVAILABILITY_CHOICES = [
        ("", "Any availability"),
        ("available", "Seats available"),
        ("almost", "Almost full"),
        ("full", "Sold out"),
    ]
    DATE_CHOICES = [
        ("", "Any date"),
        ("today", "Today"),
        ("tomorrow", "Tomorrow"),
        ("week", "Next 7 days"),
        ("weekend", "This weekend"),
        ("month", "Next 30 days"),
        ("past", "Past events"),
    ]

    q = forms.CharField(
        required=False, label="Search",
        widget=forms.TextInput(attrs={"class": "text-input", "placeholder": "Search events, venues, organizers…",
                                      "autocomplete": "off", "type": "search"}),
    )
    category = forms.ChoiceField(
        choices=[], required=False, label="Category",
        widget=forms.Select(attrs={"class": "select-input"}),
    )
    venue = forms.ModelChoiceField(
        queryset=Venue.objects.all(), required=False, label="Venue",
        empty_label="All venues", widget=forms.Select(attrs={"class": "select-input"}),
    )
    date = forms.ChoiceField(choices=DATE_CHOICES, required=False, label="When", widget=forms.Select(attrs={"class": "select-input"}))
    availability = forms.ChoiceField(choices=AVAILABILITY_CHOICES, required=False, label="Availability",
                                     widget=forms.Select(attrs={"class": "select-input"}))
    sort = forms.ChoiceField(choices=SORT_CHOICES, required=False, label="Sort", widget=forms.Select(attrs={"class": "select-input"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", "All categories")] + [
            (category.slug, category.name) for category in Category.objects.all()
        ]
        self.fields["venue"].queryset = Venue.objects.order_by("city", "name")

    def clean_q(self):
        return (self.cleaned_data.get("q") or "").strip()[:120]


class VenueForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Venue
        fields = ["name", "address_line", "city", "state", "country", "postal_code", "capacity", "map_url", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3, "maxlength": 500})}

    def clean_capacity(self):
        capacity = self.cleaned_data.get("capacity") or 0
        return capacity

    def clean(self):
        cleaned = super().clean()
        name, city = cleaned.get("name"), cleaned.get("city")
        if name and city:
            duplicates = Venue.objects.filter(name__iexact=name.strip(), city__iexact=city.strip())
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError("A venue with this name already exists in that city.")
        return cleaned


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "description", "color", "icon"]
        widgets = {"color": forms.TextInput(attrs={"type": "color", "class": "color-input"})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicates = Category.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("That category already exists.")
        return name


class EventCancelForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        max_length=500, label="Why is this event being cancelled?",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "This message is sent to every registered participant."}),
    )
    confirm = forms.BooleanField(
        label="I understand all registrations will be cancelled",
        error_messages={"required": "Please confirm the cancellation."},
    )
