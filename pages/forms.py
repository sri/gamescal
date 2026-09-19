from django import forms

from .models import (
    Calendar,
    CalendarEventRule,
    CalendarVisibilityRule,
    SavedLink,
)
from .services import MAX_DOWNLOAD_BYTES


class CalendarImportForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=False,
        help_text="Optional. The name in the calendar feed will be used when available.",
    )
    cal_url = forms.URLField(
        max_length=2000,
        required=False,
        label="Calendar URL",
        help_text="Paste an HTTP or HTTPS URL for an iCalendar (.ics) feed.",
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://example.com/schedule.ics",
                "autocomplete": "url",
            }
        ),
    )
    ics_file = forms.FileField(
        required=False,
        label="ICS file",
        help_text="Upload an iCalendar (.ics) file up to 2 MB.",
        widget=forms.ClearableFileInput(
            attrs={"accept": ".ics,text/calendar,application/ics"}
        ),
    )
    website_url = forms.URLField(
        max_length=2000,
        required=False,
        label="Website URL",
        help_text="Optional link to the calendar's website.",
        widget=forms.URLInput(attrs={"placeholder": "https://example.com"}),
    )
    is_mine = forms.BooleanField(
        required=False,
        label="My calendar",
        help_text="Treat every event from this calendar as mine.",
    )
    team_aliases = forms.CharField(
        required=False,
        label="My team names",
        help_text="Team names in this feed that should count as mine, one per line.",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Falcons 7 Black\nPhoenix Falcons",
            }
        ),
    )

    def clean_cal_url(self):
        cal_url = self.cleaned_data.get("cal_url")
        if cal_url and Calendar.objects.filter(cal_url=cal_url).exists():
            raise forms.ValidationError("This calendar has already been added.")
        return cal_url

    def clean_ics_file(self):
        ics_file = self.cleaned_data.get("ics_file")
        if ics_file and ics_file.size > MAX_DOWNLOAD_BYTES:
            raise forms.ValidationError(
                "The calendar file is too large (maximum 2 MB)."
            )
        return ics_file

    def clean(self):
        cleaned_data = super().clean()
        cal_url = cleaned_data.get("cal_url")
        ics_file = cleaned_data.get("ics_file")
        if cal_url and ics_file:
            raise forms.ValidationError(
                "Use either a calendar URL or an ICS file, not both."
            )
        if (
            not cal_url
            and not ics_file
            and not self.has_error("cal_url")
            and not self.has_error("ics_file")
        ):
            raise forms.ValidationError("Enter a calendar URL or choose an ICS file.")
        return cleaned_data


class CalendarEditForm(forms.ModelForm):
    def clean_cal_url(self):
        return self.cleaned_data.get("cal_url") or None

    class Meta:
        model = Calendar
        fields = ("name", "cal_url", "website_url", "is_mine", "team_aliases")
        labels = {
            "cal_url": "Calendar URL",
            "website_url": "Website URL",
            "is_mine": "My calendar",
            "team_aliases": "My team names",
        }
        widgets = {
            "cal_url": forms.URLInput(attrs={"autocomplete": "url"}),
            "website_url": forms.URLInput(attrs={"autocomplete": "url"}),
            "team_aliases": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Falcons 7 Black\nPhoenix Falcons",
                }
            ),
        }


class EventTimeForm(forms.Form):
    starts_at = forms.DateTimeField(
        label="Starts at",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M:%S",
            attrs={"type": "datetime-local", "class": "form-control", "step": "1"},
        ),
    )
    ends_at = forms.DateTimeField(
        label="Ends at",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M:%S",
            attrs={"type": "datetime-local", "class": "form-control", "step": "1"},
        ),
    )
    is_all_day = forms.BooleanField(
        required=False,
        label="All day",
        help_text=(
            "For all-day events, use midnight at the start and midnight after the last day."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean(self):
        data = super().clean()
        start, end = data.get("starts_at"), data.get("ends_at")
        if start and end:
            if end <= start:
                self.add_error("ends_at", "End must be after start.")
            if data.get("is_all_day") and (
                any((start.hour, start.minute, start.second, start.microsecond))
                or any((end.hour, end.minute, end.second, end.microsecond))
            ):
                raise forms.ValidationError(
                    "All-day events must start and end at midnight."
                )
        return data


class SavedLinkForm(forms.ModelForm):
    class Meta:
        model = SavedLink
        fields = ("name", "url")
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Name (optional)",
                    "aria-label": "Name (optional)",
                }
            ),
            "url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://example.com/schedule",
                    "autocomplete": "url",
                    "aria-label": "URL",
                }
            ),
        }


class CalendarVisibilityRuleForm(forms.ModelForm):
    class Meta:
        model = CalendarVisibilityRule
        fields = ("name", "action", "match_field", "pattern", "priority", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Show teams we follow"}),
            "pattern": forms.TextInput(attrs={"placeholder": "Falcons"}),
        }

    def clean_pattern(self):
        pattern = self.cleaned_data["pattern"].strip()
        if not pattern:
            raise forms.ValidationError("Enter text to match.")
        return pattern


class CalendarEventRuleForm(forms.ModelForm):
    class Meta:
        model = CalendarEventRule
        fields = ("name", "match_field", "pattern", "event_type", "priority", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Hide practices"}),
            "pattern": forms.TextInput(attrs={"placeholder": "practice"}),
        }

    def clean_pattern(self):
        pattern = self.cleaned_data["pattern"].strip()
        if not pattern:
            raise forms.ValidationError("Enter text to match.")
        return pattern
