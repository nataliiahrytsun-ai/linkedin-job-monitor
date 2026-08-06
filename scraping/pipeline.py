"""Source-neutral orchestration for the local fixture backend pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from django.db import transaction  # type: ignore[import-untyped]

from scraping.normalization import NormalizedJobPosting, normalize_job_posting
from scraping.persistence import (
    JobPersistenceError,
    PersistenceOutcome,
    PersistenceResult,
    persist_job_posting,
)
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
    jobs_failed: int
    errors: tuple[JobProcessingError, ...]
    reconciliation: ReconciliationResult | None


@dataclass(frozen=True, slots=True)
class JobProcessingError:
    """Safe diagnostic for one recoverable fixture record error."""

    record_index: int
    stage: Literal["normalization", "persistence"]
    safe_error_type: str
    safe_message: str


class FixturePipelineError(Exception):
    """A fixture batch failed atomically and its run was finalized FAILED."""

    def __init__(self, message: str, *, scrape_run: ScrapeRunRecord) -> None:
        super().__init__(message)
        self.scrape_run = scrape_run


class InvalidPipelineTimestampError(ValueError):
    """The deterministic fixture pipeline timestamps are invalid."""


class InvalidPipelineConfigurationError(ValueError):
    """A fixture pipeline execution option is invalid."""


class _RecoverableJobProcessingError(Exception):
    def __init__(
        self,
        *,
        stage: Literal["normalization", "persistence"],
        original: Exception,
    ) -> None:
        super().__init__(stage)
        self.stage = stage
        self.original = original


class AllJobRecordsFailedError(Exception):
    """Every fixture record failed expected per-job processing."""


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


def _process_record(
    *,
    company: CompanyRecord,
    record: FixtureRecord,
    record_index: int,
    seen_at: datetime,
) -> PersistenceResult:
    try:
        normalized = _normalize_fixture_record(record, index=record_index)
    except ValueError as error:
        raise _RecoverableJobProcessingError(
            stage="normalization",
            original=error,
        ) from error

    try:
        return persist_job_posting(
            company=company,
            job=normalized,
            seen_at=seen_at,
        )
    except JobPersistenceError as error:
        raise _RecoverableJobProcessingError(
            stage="persistence",
            original=error,
        ) from error


def _safe_job_error(
    *, record_index: int, error: _RecoverableJobProcessingError
) -> JobProcessingError:
    return JobProcessingError(
        record_index=record_index,
        stage=error.stage,
        safe_error_type=type(error.original).__name__,
        safe_message=f"Job record failed during {error.stage}.",
    )


def run_fixture_pipeline(
    *,
    company: CompanyRecord,
    fixture_path: Path,
    started_at: datetime,
    finished_at: datetime,
    miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    recover_job_errors: bool = False,
) -> FixturePipelineResult:
    """Run one local fixture batch through the source-neutral backend.

    Strict mode remains the default and fails the whole batch on a per-job
    validation or persistence error. With ``recover_job_errors=True``, expected
    per-job errors are isolated by savepoints; a mixed batch commits as PARTIAL
    without reconciliation, while an all-invalid batch still fails atomically.
    """
    _validate_timestamps(started_at=started_at, finished_at=finished_at)
    if type(recover_job_errors) is not bool:
        raise InvalidPipelineConfigurationError(
            "recover_job_errors must be a bool"
        )
    scrape_run = start_scrape_run(company=company, started_at=started_at)

    try:
        records = load_fixture_records(fixture_path)
        with transaction.atomic():
            seen_job_ids: list[int] = []
            jobs_created = 0
            jobs_updated = 0
            jobs_unchanged = 0
            job_errors: list[JobProcessingError] = []

            for index, record in enumerate(records):
                try:
                    with transaction.atomic():
                        persisted = _process_record(
                            company=company,
                            record=record,
                            record_index=index,
                            seen_at=finished_at,
                        )
                except _RecoverableJobProcessingError as error:
                    if not recover_job_errors:
                        raise error.original from error
                    job_errors.append(_safe_job_error(record_index=index, error=error))
                    continue

                seen_job_ids.append(persisted.job_posting.pk)
                if persisted.outcome is PersistenceOutcome.CREATED:
                    jobs_created += 1
                elif persisted.outcome is PersistenceOutcome.UPDATED:
                    jobs_updated += 1
                else:
                    jobs_unchanged += 1

            jobs_found = len(records)
            jobs_failed = len(job_errors)
            jobs_succeeded = jobs_created + jobs_updated + jobs_unchanged
            if jobs_failed and jobs_succeeded == 0:
                raise AllJobRecordsFailedError(
                    "all fixture job records failed processing"
                )

            if jobs_failed:
                completed_run = finish_scrape_run(
                    scrape_run=scrape_run,
                    status=TerminalRunStatus.PARTIAL,
                    finished_at=finished_at,
                    jobs_found=jobs_found,
                    jobs_created=jobs_created,
                    jobs_updated=jobs_updated,
                    requests_made=0,
                    error_message=(
                        f"{jobs_failed} of {jobs_found} job records failed processing"
                    ),
                )
                reconciliation = None
            else:
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
            jobs_failed=jobs_failed,
            errors=tuple(job_errors),
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
