"""Fail-closed transitional resolution from Company to one CompanySource."""

from __future__ import annotations

from typing import Protocol, cast

from django.apps import apps  # type: ignore[import-untyped]

from scraping.sources.base import SourceConfiguration, SourceError
from scraping.sources.registry import normalize_source_key


class LegacyCompanyRecord(Protocol):
    pk: int | None
    source: str
    source_jobs_url: str | None


class LegacySourceResolutionError(SourceError):
    """Legacy Company execution cannot resolve exactly one compatible source."""


def _normalized_url(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def resolve_legacy_company_source(
    company: LegacyCompanyRecord,
) -> SourceConfiguration:
    """Resolve one approved active source matching the legacy Company fields."""
    company_model = apps.get_model("companies", "Company")
    source_model = apps.get_model("companies", "CompanySource")
    company_pk = getattr(company, "pk", None)
    if (
        not isinstance(company, company_model)
        or type(company_pk) is not int
        or getattr(getattr(company, "_state", None), "adding", True)
        or not company_model.objects.filter(pk=company_pk).exists()
    ):
        raise LegacySourceResolutionError("company must already be saved")

    executable_sources = list(
        source_model.objects.filter(
            company_id=company_pk,
            approval_status="approved",
            is_active=True,
        ).order_by("pk")[:2]
    )
    if len(executable_sources) != 1:
        raise LegacySourceResolutionError(
            "legacy company execution requires exactly one approved active source"
        )

    source = executable_sources[0]
    if normalize_source_key(source.source) != normalize_source_key(company.source):
        raise LegacySourceResolutionError(
            "legacy company source key does not match its executable CompanySource"
        )
    if _normalized_url(source.source_jobs_url) != _normalized_url(
        company.source_jobs_url
    ):
        raise LegacySourceResolutionError(
            "legacy company jobs URL does not match its executable CompanySource"
        )
    return cast(SourceConfiguration, source)
