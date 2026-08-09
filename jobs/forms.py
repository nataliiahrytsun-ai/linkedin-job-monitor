"""GET filters for the global jobs overview."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from django import forms  # type: ignore[import-untyped]

from companies.models import Company
from jobs.models import JobPosting


class JobFilterForm(forms.Form):
    """Validate optional query parameters without changing stored data."""

    q = forms.CharField(
        label="Job title",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Search job titles"}),
    )
    company = forms.ChoiceField(label="Company", required=False)
    company_type = forms.ChoiceField(
        label="Company type",
        required=False,
        choices=(
            ("", "All company types"),
            (Company.CompanyType.CLIENT.value, "Customer"),
            (Company.CompanyType.SUPPLIER.value, "Supplier"),
            (Company.CompanyType.OTHER.value, "Other"),
        ),
    )
    country = forms.CharField(
        label="Country",
        required=False,
        widget=forms.Select(),
    )
    location = forms.CharField(label="Location", required=False)
    status = forms.ChoiceField(
        label="Status",
        required=False,
        choices=(
            ("", "All statuses"),
            (JobPosting.Status.ACTIVE.value, "Active"),
            (JobPosting.Status.NOT_FOUND.value, "Not found"),
            (JobPosting.Status.CLOSED.value, "Closed"),
        ),
    )
    workplace_type = forms.ChoiceField(
        label="Workplace",
        required=False,
        choices=(
            ("", "All workplace types"),
            (JobPosting.WorkplaceType.REMOTE.value, "Remote"),
            (JobPosting.WorkplaceType.HYBRID.value, "Hybrid"),
            (JobPosting.WorkplaceType.ONSITE.value, "Onsite"),
        ),
    )
    published_from = forms.DateField(
        label="Published from",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    published_to = forms.DateField(
        label="Published to",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    first_seen_from = forms.DateField(
        label="First seen from",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    first_seen_to = forms.DateField(
        label="First seen to",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(
        self,
        *args: Any,
        companies: Iterable[tuple[int, str]] = (),
        countries: Iterable[str] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        company_field = cast(forms.ChoiceField, self.fields["company"])
        company_field.choices = (
            ("", "All companies"),
            *((str(company_id), name) for company_id, name in companies),
        )
        country_field = cast(forms.Select, self.fields["country"].widget)
        country_field.choices = (
            ("", "All countries"),
            *((country, country) for country in countries),
        )
