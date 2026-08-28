from django import forms

from .models import Calendar, CalendarEventRule, SavedLink


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

    def clean_cal_url(self):
        cal_url = self.cleaned_data["cal_url"]
        if Calendar.objects.filter(cal_url=cal_url).exists():
            raise forms.ValidationError("This calendar has already been added.")
        return cal_url


class CalendarEditForm(forms.ModelForm):
    class Meta:
        model = Calendar
        fields = ("name", "cal_url", "website_url")
        labels = {"cal_url": "Calendar URL", "website_url": "Website URL"}
        widgets = {
            "cal_url": forms.URLInput(attrs={"autocomplete": "url"}),
            "website_url": forms.URLInput(attrs={"autocomplete": "url"}),
        }


class SavedLinkForm(forms.ModelForm):
    class Meta:
        model = SavedLink
        fields = ("url",)
        widgets = {
            "url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://example.com/schedule",
                    "autocomplete": "url",
                    "aria-label": "URL",
                }
            )
        }


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
