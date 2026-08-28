from django import forms

from .models import (
    Calendar,
    CalendarEventRule,
    CalendarVisibilityRule,
    SavedLink,
)


class CalendarImportForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=False,
        help_text="Optional. The name in the calendar feed will be used when available.",
    )
    cal_url = forms.URLField(
        max_length=2000,
        label="Calendar URL",
        help_text="An HTTP or HTTPS URL for an iCalendar (.ics) feed.",
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://example.com/schedule.ics",
                "autocomplete": "url",
            }
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
        cal_url = self.cleaned_data["cal_url"]
        if Calendar.objects.filter(cal_url=cal_url).exists():
            raise forms.ValidationError("This calendar has already been added.")
        return cal_url


class CalendarEditForm(forms.ModelForm):
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
