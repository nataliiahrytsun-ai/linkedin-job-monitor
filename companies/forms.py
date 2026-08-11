"""Forms for Company and CompanySource management."""

from typing import Any, ClassVar

from django import forms

from companies.models import Company, CompanySource
from scraping.sources.base import SourceError
from scraping.sources.lever import lever_site_from_url
from scraping.sources.registry import normalize_source_key, user_selectable_source_keys


class SourceChoiceField(forms.ChoiceField):  # type: ignore[misc]
    """Validate canonicalized source keys against the current adapter registry."""

    def to_python(self, value: object) -> str:
        return normalize_source_key(super().to_python(value))


def source_label(source_key: str) -> str:
    """Build a readable label without duplicating the registry's source list."""
    return source_key.replace("_", " ").replace("-", " ").title()


def source_choices() -> tuple[tuple[str, str], ...]:
    """Return user-manageable choices from the immutable registry API."""
    return tuple((key, source_label(key)) for key in user_selectable_source_keys())


def validate_source_configuration(*, source: str, source_jobs_url: str | None) -> None:
    """Validate source configuration offline with the adapter's existing contract."""
    normalized_source = normalize_source_key(source)
    if normalized_source not in user_selectable_source_keys():
        raise forms.ValidationError("Select a supported production source.")
    if normalized_source == "lever":
        try:
            lever_site_from_url(source_jobs_url)
        except SourceError as error:
            raise forms.ValidationError(str(error)) from error


class CompanyForm(forms.ModelForm):
    """Create or edit Company-level fields without mutating legacy source fields."""

    company_type = forms.ChoiceField(
        label="Company type",
        initial=Company.CompanyType.OTHER.value,
        choices=(
            (Company.CompanyType.CLIENT.value, "Customer"),
            (Company.CompanyType.SUPPLIER.value, "Supplier"),
            (Company.CompanyType.OTHER.value, "Other"),
        ),
    )

    class Meta:
        model = Company
        fields = ("name", "company_type", "is_active")
        labels: ClassVar[dict[str, str]] = {
            "name": "Company name",
            "company_type": "Company type",
            "is_active": "Monitoring active",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "is_active": "Inactive companies remain stored but cannot start a monitoring run.",
        }


class CompanySourceForm(forms.ModelForm):
    """Create or safely edit one user-manageable CompanySource."""

    source = SourceChoiceField(
        label="Source",
        choices=(),
        help_text="The platform is immutable after this source is created.",
    )
    source_jobs_url = forms.URLField(
        label="Jobs URL",
        required=True,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://jobs.lever.co/company"}),
    )
    def __init__(self, *args: Any, company: Company, **kwargs: Any) -> None:
        self.company = company
        super().__init__(*args, **kwargs)
        choices = list(source_choices())
        if self.instance.pk is not None:
            current = normalize_source_key(self.instance.source)
            if current and current not in {value for value, _label in choices}:
                choices.insert(0, (current, f"{source_label(current)} (internal)"))
            self.fields["source"].disabled = True
        self.fields["source"].choices = tuple(choices)

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        source = normalize_source_key(str(cleaned_data.get("source", "")))
        source_jobs_url = cleaned_data.get("source_jobs_url")
        if self.instance.pk is None:
            self.instance.company = self.company
            self.instance.approval_status = CompanySource.ApprovalStatus.APPROVED
            self.instance.is_active = True
        if source and source_jobs_url:
            try:
                validate_source_configuration(
                    source=source,
                    source_jobs_url=str(source_jobs_url),
                )
            except forms.ValidationError as error:
                self.add_error("source_jobs_url", error)
        duplicate = CompanySource.objects.filter(
            company=self.company,
            source=source,
            source_jobs_url=source_jobs_url,
        )
        if self.instance.pk is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if source and source_jobs_url and duplicate.exists():
            self.add_error("source_jobs_url", "This job source is already configured.")
        return cleaned_data

    class Meta:
        model = CompanySource
        fields = ("source", "source_jobs_url")
