"""Forms for source-neutral company management."""

from typing import Any, ClassVar

from django import forms

from companies.models import Company
from scraping.sources.registry import (
    normalize_source_key,
    registered_source_keys,
    user_selectable_source_keys,
)


class SourceChoiceField(forms.ChoiceField):  # type: ignore[misc]
    """Validate canonicalized source keys against the current adapter registry."""

    def to_python(self, value: object) -> str:
        return normalize_source_key(super().to_python(value))


def _source_label(source_key: str) -> str:
    """Build a readable label without duplicating the registry's source list."""
    return source_key.replace("_", " ").replace("-", " ").title()


def _source_choices() -> tuple[tuple[str, str], ...]:
    return tuple(
        (source_key, _source_label(source_key))
        for source_key in user_selectable_source_keys()
    )


class CompanyForm(forms.ModelForm):
    """Create or edit the user-managed fields of a company."""

    company_type = forms.ChoiceField(
        label="Company type",
        initial=Company.CompanyType.OTHER.value,
        choices=(
            (Company.CompanyType.CLIENT.value, "Customer"),
            (Company.CompanyType.SUPPLIER.value, "Supplier"),
            (Company.CompanyType.OTHER.value, "Other"),
        ),
    )

    source = SourceChoiceField(
        label="Source",
        choices=(),
        help_text="Select a supported vacancy source.",
    )

    source_jobs_url = forms.URLField(
        label="Source jobs URL",
        required=False,
        assume_scheme="https",
        help_text="Optional public listing URL used by the source adapter.",
        widget=forms.URLInput(attrs={"placeholder": "https://jobs.example.com/company"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        source_choices = list(_source_choices())
        current_source = normalize_source_key(getattr(self.instance, "source", ""))
        if (
            self.instance.pk is not None
            and current_source in registered_source_keys()
            and current_source not in user_selectable_source_keys()
        ):
            source_choices.insert(
                0,
                (current_source, f"{_source_label(current_source)} (internal)"),
            )
        self.fields["source"].choices = tuple(source_choices)

    def clean_source(self) -> str:
        """Apply the model's canonical source format before constraint validation."""
        return str(self.cleaned_data["source"]).strip().lower()

    class Meta:
        model = Company
        fields = ("name", "company_type", "source", "source_jobs_url", "is_active")
        labels: ClassVar[dict[str, str]] = {
            "name": "Company name",
            "company_type": "Company type",
            "is_active": "Monitoring active",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "is_active": "Inactive companies remain stored but cannot start a monitoring run.",
        }
