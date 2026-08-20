"""Race-safe hard deletion of one Company and its owned monitoring data."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from companies.models import Company, CompanySource
from jobs.models import JobPosting
from scrape_runs.models import ScrapeRun
from scraping.db_concurrency import database_write_guard
from scraping.sources.registry import normalize_source_key, user_deletable_source_keys


class CompanyDeletionError(Exception):
    """Base error for a rejected Company hard delete."""


class CompanyDeletionBlockedError(CompanyDeletionError):
    """A Company with a RUNNING source run cannot be deleted."""


class CompanySourceDeletionError(Exception):
    """Base error for a rejected CompanySource hard delete."""


class CompanySourceDeletionBlockedError(CompanySourceDeletionError):
    """A CompanySource with a RUNNING run cannot be deleted."""


class CompanySourceDeletionNotAllowedError(CompanySourceDeletionError):
    """An internal or otherwise non-user-manageable source cannot be deleted."""


@dataclass(frozen=True, slots=True)
class CompanyDeletionResult:
    """Counts for the Company graph removed by one committed hard delete."""

    company_name: str
    sources_deleted: int
    jobs_deleted: int
    runs_deleted: int


@dataclass(frozen=True, slots=True)
class CompanySourceDeletionResult:
    """Counts for one source-scoped cleanup and retained run history."""

    source_name: str
    jobs_deleted: int
    runs_preserved: int


def delete_company_source(
    *, company_id: int, company_source_id: int
) -> CompanySourceDeletionResult:
    """Delete one user-managed source and only its owned jobs atomically."""
    with database_write_guard(), transaction.atomic():
        source = CompanySource.objects.select_for_update().get(
            pk=company_source_id,
            company_id=company_id,
        )
        source_name = normalize_source_key(source.source)
        if source_name not in set(user_deletable_source_keys()):
            raise CompanySourceDeletionNotAllowedError(
                "This internal source is not user-manageable."
            )
        source_runs = ScrapeRun.objects.filter(company_source_id=company_source_id)
        if source_runs.filter(status=ScrapeRun.Status.RUNNING).exists():
            raise CompanySourceDeletionBlockedError(
                "Source cannot be deleted while its run is still running."
            )

        runs_preserved = source_runs.count()
        owned_jobs = JobPosting.objects.filter(company_source_id=company_source_id)
        jobs_deleted = owned_jobs.count()
        owned_jobs.delete()
        source.delete()

        return CompanySourceDeletionResult(
            source_name=source_name,
            jobs_deleted=jobs_deleted,
            runs_preserved=runs_preserved,
        )


def delete_company(*, company_id: int) -> CompanyDeletionResult:
    """Delete one complete Company graph or roll back without partial changes."""
    with database_write_guard(), transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company_id)
        company_name = company.name
        if ScrapeRun.objects.filter(
            company_id=company_id,
            status=ScrapeRun.Status.RUNNING,
        ).exists():
            raise CompanyDeletionBlockedError(
                "Company cannot be deleted while a source run is still running."
            )

        sources_deleted = CompanySource.objects.filter(company_id=company_id).count()
        jobs_deleted = JobPosting.objects.filter(company_id=company_id).count()
        terminal_runs = ScrapeRun.objects.filter(company_id=company_id).exclude(
            status=ScrapeRun.Status.RUNNING
        )
        runs_deleted = terminal_runs.count()

        JobPosting.objects.filter(company_id=company_id).delete()
        terminal_runs.delete()
        CompanySource.objects.filter(company_id=company_id).delete()
        company.delete()

        return CompanyDeletionResult(
            company_name=company_name,
            sources_deleted=sources_deleted,
            jobs_deleted=jobs_deleted,
            runs_deleted=runs_deleted,
        )
