"""Shared form helpers that keep widget markup consistent across the project."""
from django import forms

TEXT_INPUTS = (
    forms.TextInput, forms.EmailInput, forms.URLInput, forms.NumberInput,
    forms.PasswordInput, forms.Textarea, forms.DateInput, forms.DateTimeInput,
)

DATE_WIDGET_TYPES = {forms.DateInput: "date", forms.DateTimeInput: "datetime-local"}


def style_field(field: forms.Field, name: str | None = None) -> None:
    """Attach design-system classes (and sensible HTML attributes) to a field."""
    widget = field.widget
    if isinstance(widget, forms.CheckboxInput):
        widget.attrs.setdefault("class", "checkbox-input")
    elif isinstance(widget, forms.Select):
        widget.attrs.setdefault("class", "select-input")
    elif isinstance(widget, forms.ClearableFileInput):
        widget.attrs.setdefault("class", "file-input")
    elif isinstance(widget, forms.Textarea):
        widget.attrs.setdefault("class", "textarea-input")
        widget.attrs.setdefault("rows", 4)
    elif isinstance(widget, TEXT_INPUTS):
        widget.attrs.setdefault("class", "text-input")
    if isinstance(widget, forms.DateInput) and not isinstance(widget, forms.DateTimeInput):
        widget.input_type = "date"
    if isinstance(widget, forms.DateTimeInput):
        widget.input_type = "datetime-local"
    if field.required:
        widget.attrs.setdefault("aria-required", "true")
    field_name = name or getattr(field, "name", None)
    if field.help_text and field_name and not widget.attrs.get("aria-describedby"):
        widget.attrs["aria-describedby"] = f"help_{field_name}"


class StyledFormMixin:
    """Applies :func:`style_field` to every field of the form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            style_field(field, name)
