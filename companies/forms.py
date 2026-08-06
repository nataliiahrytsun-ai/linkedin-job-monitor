"""Forms for source-neutral company management."""

from typing import ClassVar

from django import forms

from companies.models import Company


class CompanyForm(forms.ModelForm):
    """Create or edit the user-managed fields of a company."""

    source_jobs_url = forms.URLField(
        label="Source jobs URL",
        required=False,
        assume_scheme="https",
        help_text="Optional public listing URL used by the source adapter.",
        widget=forms.URLInput(attrs={"placeholder": "https://jobs.example.com/company"}),
    )

    def clean_source(self) -> str:
        """Apply the model's canonical source format before constraint validation."""
        return str(self.cleaned_data["source"]).strip().lower()

    class Meta:
        model = Company
        fields = ("name", "company_type", "source", "source_jobs_url", "is_active")
        labels: ClassVar[dict[str, str]] = {
            "name": "Company name",
            "company_type": "Company type",
            "source": "Vacancy source",
            "is_active": "Monitoring active",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "source": "Short adapter name for the permitted vacancy source.",
            "is_active": "Inactive companies remain stored but cannot start a monitoring run.",
        }
