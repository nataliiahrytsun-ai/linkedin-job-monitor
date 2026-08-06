"""Source-neutral orchestration for the local fixture backend pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from django.db import transaction  # type: ignore[import-untyped]

from scraping.normalization import NormalizedJobPosting, normalize_job_posting
from scraping.persistence import PersistenceOutcome, persist_job_posting
from scraping.reconciliation import (
    DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ReconciliationResult,
    reconcile_jobs_after_successful_run,
)
from scraping.reconciliation import (
    ScrapeRunRecord as ReconciliationScrapeRunRecord,
)
from scraping.run_lifecycle import (
    ScrapeRunRecord,
    TerminalRunStatus,
    finish_scrape_run,
    start_scrape_run,
)
from scraping.sources.fixture import FixtureRecord, load_fixture_records

_NORMALIZATION_FIELDS = frozenset(
    {
        "source",
        "source_job_id",
        "title",
        "country",
        "city",
        "location",
        "workplace_type",
        "employment_type",
        "seniority_level",
        "job_function",
        "industry",
        "published_at",
        "description",
        "source_job_url",
    }
)


class CompanyRecord(Protocol):
    """Persisted company attributes required by the composed services."""

    pk: int | None
    source: str
    is_active: bool
    last_scraped_at: datetime | None
    last_scrape_status: str


@dataclass(frozen=True, slots=True)
class FixturePipelineResult:
    """Committed outcome of one complete fixture pipeline run."""

    scrape_run: ScrapeRunRecord
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    jobs_unchanged: int
    reconciliation: ReconciliationResult


class FixturePipelineError(Exception):
    """A fixture batch failed atomically and its run was finalized FAILED."""

    def __init__(self, message: str, *, scrape_run: ScrapeRunRecord) -> None:
        super().__init__(message)
        self.scrape_run = scrape_run


class InvalidPipelineTimestampError(ValueError):
    """The deterministic fixture pipeline timestamps are invalid."""


def _validate_timestamps(*, started_at: datetime, finished_at: datetime) -> None:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise InvalidPipelineTimestampError("started_at must be timezone-aware")
    if finished_at.tzinfo is None or finished_at.utcoffset() is None:
        raise InvalidPipelineTimestampError("finished_at must be timezone-aware")
    if finished_at < started_at:
        raise InvalidPipelineTimestampError(
            "finished_at cannot be earlier than started_at"
        )


def _optional_string(record: FixtureRecord, field: str, *, index: int) -> str | None:
    value = record.get(field)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"fixture record {index} field {field} must be a string or null")


def _published_at(record: FixtureRecord, *, index: int) -> datetime | None:
    value = record.get("published_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"fixture record {index} field published_at must be an ISO datetime or null"
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"fixture record {index} field published_at must be an ISO datetime or null"
        ) from error


def _normalize_fixture_record(
    record: FixtureRecord, *, index: int
) -> NormalizedJobPosting:
    unexpected_fields = set(record) - _NORMALIZATION_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"fixture record {index} contains unsupported fields: "
            + ", ".join(sorted(unexpected_fields))
        )

    source = _optional_string(record, "source", index=index)
    if source is None:
        raise ValueError(f"fixture record {index} requires source")
    return normalize_job_posting(
        source=source,
        source_job_id=_optional_string(record, "source_job_id", index=index),
        title=_optional_string(record, "title", index=index),
        country=_optional_string(record, "country", index=index),
        city=_optional_string(record, "city", index=index),
        location=_optional_string(record, "location", index=index),
        workplace_type=_optional_string(record, "workplace_type", index=index),
        employment_type=_optional_string(record, "employment_type", index=index),
        seniority_level=_optional_string(record, "seniority_level", index=index),
        job_function=_optional_string(record, "job_function", index=index),
        industry=_optional_string(record, "industry", index=index),
        published_at=_published_at(record, index=index),
        description=_optional_string(record, "description", index=index),
        source_job_url=_optional_string(record, "source_job_url", index=index),
    )


def _safe_failure_message(error: Exception) -> str:
    return f"Fixture pipeline failed: {type(error).__name__}"


def run_fixture_pipeline(
    *,
    company: CompanyRecord,
    fixture_path: Path,
    started_at: datetime,
    finished_at: datetime,
    miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
) -> FixturePipelineResult:
    """Run one all-or-nothing local fixture batch through backend services."""
    _validate_timestamps(started_at=started_at, finished_at=finished_at)
    scrape_run = start_scrape_run(company=company, started_at=started_at)

    try:
        records = load_fixture_records(fixture_path)
        with transaction.atomic():
            seen_job_ids: list[int] = []
            jobs_created = 0
            jobs_updated = 0
            jobs_unchanged = 0

            for index, record in enumerate(records):
                normalized = _normalize_fixture_record(record, index=index)
                persisted = persist_job_posting(
                    company=company,
                    job=normalized,
                    seen_at=finished_at,
                )
                seen_job_ids.append(persisted.job_posting.pk)
                if persisted.outcome is PersistenceOutcome.CREATED:
                    jobs_created += 1
                elif persisted.outcome is PersistenceOutcome.UPDATED:
                    jobs_updated += 1
                else:
                    jobs_unchanged += 1

            jobs_found = len(records)
            completed_run = finish_scrape_run(
                scrape_run=scrape_run,
                status=TerminalRunStatus.SUCCESS,
                finished_at=finished_at,
                jobs_found=jobs_found,
                jobs_created=jobs_created,
                jobs_updated=jobs_updated,
                requests_made=0,
            )
            reconciliation = reconcile_jobs_after_successful_run(
                scrape_run=cast(ReconciliationScrapeRunRecord, completed_run),
                seen_job_posting_ids=seen_job_ids,
                miss_threshold=miss_threshold,
            )

        return FixturePipelineResult(
            scrape_run=completed_run,
            jobs_found=jobs_found,
            jobs_created=jobs_created,
            jobs_updated=jobs_updated,
            jobs_unchanged=jobs_unchanged,
            reconciliation=reconciliation,
        )
    except Exception as error:
        failed_run = finish_scrape_run(
            scrape_run=scrape_run,
            status=TerminalRunStatus.FAILED,
            finished_at=finished_at,
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            requests_made=0,
            error_message=_safe_failure_message(error),
        )
        raise FixturePipelineError(
            "fixture pipeline failed; batch changes were rolled back",
            scrape_run=failed_run,
        ) from error
