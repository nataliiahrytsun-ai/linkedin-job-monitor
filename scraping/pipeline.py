"""Source-neutral orchestration for vacancy source batches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Literal, Protocol, cast

from django.db import (  # type: ignore[import-untyped]
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.utils import timezone  # type: ignore[import-untyped]

from scraping.db_concurrency import database_write_guard
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
from scraping.sources.base import SourceAdapter, SourceError, SourceRecord
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.resolution import resolve_legacy_company_source

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
_SQLITE_FAILED_RUN_FINALIZATION_ATTEMPTS = 3
_SQLITE_FAILED_RUN_RETRY_DELAY_SECONDS = 0.05


class CompanyRecord(Protocol):
    """Persisted company attributes retained for transitional background APIs."""

    pk: int | None
    source: str
    source_jobs_url: str | None
    is_active: bool
    last_scraped_at: datetime | None
    last_scrape_status: str


class CompanySourceRecord(Protocol):
    """Explicit source ownership required by the production pipeline."""

    pk: int | None
    company_id: int
    source: str
    source_jobs_url: str | None
    approval_status: str
    is_active: bool

    @property
    def company(self) -> CompanyRecord: ...


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Committed outcome of one complete source pipeline run."""

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
    """Safe diagnostic for one recoverable source record error."""

    record_index: int
    stage: Literal["normalization", "persistence"]
    safe_error_type: str
    safe_message: str


FixturePipelineResult = PipelineResult


class PipelineError(Exception):
    """A source batch failed atomically and its run was finalized FAILED."""

    def __init__(self, message: str, *, scrape_run: ScrapeRunRecord) -> None:
        super().__init__(message)
        self.scrape_run = scrape_run


FixturePipelineError = PipelineError


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
    """Every source record failed expected per-job processing."""


def _validate_timestamps(*, started_at: datetime, finished_at: datetime) -> None:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise InvalidPipelineTimestampError("started_at must be timezone-aware")
    if finished_at.tzinfo is None or finished_at.utcoffset() is None:
        raise InvalidPipelineTimestampError("finished_at must be timezone-aware")
    if finished_at < started_at:
        raise InvalidPipelineTimestampError(
            "finished_at cannot be earlier than started_at"
        )


def _clock_timestamp(
    *, clock: Callable[[], datetime], name: str, started_at: datetime | None = None
) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPipelineTimestampError(f"{name} must be timezone-aware")
    if started_at is not None and value < started_at:
        raise InvalidPipelineTimestampError(f"{name} cannot be earlier than started_at")
    return value


def _optional_string(record: SourceRecord, field: str, *, index: int) -> str | None:
    value = record.get(field)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"source record {index} field {field} must be a string or null")


def _published_at(record: SourceRecord, *, index: int) -> datetime | None:
    value = record.get("published_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"source record {index} field published_at must be an ISO datetime or null"
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"source record {index} field published_at must be an ISO datetime or null"
        ) from error


def _normalize_source_record(
    record: SourceRecord, *, index: int
) -> NormalizedJobPosting:
    unexpected_fields = set(record) - _NORMALIZATION_FIELDS
    if unexpected_fields:
        raise ValueError(
            f"source record {index} contains unsupported fields: "
            + ", ".join(sorted(unexpected_fields))
        )

    source = _optional_string(record, "source", index=index)
    if source is None:
        raise ValueError(f"source record {index} requires source")
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
    if type(error) is SourceError:
        detail = str(error).strip()
        if detail:
            return f"Source pipeline failed: {detail}"
    return f"Source pipeline failed: {type(error).__name__}"


def _is_sqlite_lock_error(error: OperationalError) -> bool:
    message = str(error).lower()
    return connection.vendor == "sqlite" and (
        "database is locked" in message or "database table is locked" in message
    )


def _finish_failed_run(
    *,
    scrape_run: ScrapeRunRecord,
    finished_at: datetime,
    requests_made: int,
    error_message: str,
) -> ScrapeRunRecord:
    """Retry only terminal FAILED persistence after transient SQLite locks."""
    for attempt in range(_SQLITE_FAILED_RUN_FINALIZATION_ATTEMPTS):
        try:
            return finish_scrape_run(
                scrape_run=scrape_run,
                status=TerminalRunStatus.FAILED,
                finished_at=finished_at,
                jobs_found=0,
                jobs_created=0,
                jobs_updated=0,
                requests_made=requests_made,
                error_message=error_message,
            )
        except OperationalError as finalization_error:
            final_attempt = attempt + 1 == _SQLITE_FAILED_RUN_FINALIZATION_ATTEMPTS
            if final_attempt or not _is_sqlite_lock_error(finalization_error):
                raise
            close_old_connections()
            sleep(_SQLITE_FAILED_RUN_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable failed-run finalization state")


def _process_record(
    *,
    company_source: CompanySourceRecord,
    record: SourceRecord,
    record_index: int,
    seen_at: datetime,
) -> PersistenceResult:
    try:
        normalized = _normalize_source_record(record, index=record_index)
    except ValueError as error:
        raise _RecoverableJobProcessingError(
            stage="normalization",
            original=error,
        ) from error

    try:
        return persist_job_posting(
            company_source=company_source,
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


def run_source_pipeline(
    *,
    company_source: CompanySourceRecord | None = None,
    company: CompanyRecord | None = None,
    adapter: SourceAdapter,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    recover_job_errors: bool = False,
    clock: Callable[[], datetime] = timezone.now,
) -> PipelineResult:
    """Run one adapter batch through the common backend pipeline.

    Strict mode remains the default and fails the whole batch on a per-job
    validation or persistence error. With ``recover_job_errors=True``, expected
    per-job errors are isolated by savepoints; a mixed batch commits as PARTIAL
    without reconciliation, while an all-invalid batch still fails atomically.
    """
    if type(recover_job_errors) is not bool:
        raise InvalidPipelineConfigurationError(
            "recover_job_errors must be a bool"
        )
    if not callable(clock):
        raise InvalidPipelineConfigurationError("clock must be callable")
    if company_source is None:
        if company is None:
            raise InvalidPipelineConfigurationError(
                "company_source or legacy company is required"
            )
        company_source = cast(
            CompanySourceRecord,
            resolve_legacy_company_source(company),
        )
    elif company is not None:
        raise InvalidPipelineConfigurationError(
            "provide company_source or legacy company, not both"
        )

    resolved_started_at = started_at or _clock_timestamp(
        clock=clock,
        name="started_at",
    )
    if finished_at is not None:
        _validate_timestamps(
            started_at=resolved_started_at,
            finished_at=finished_at,
        )
    elif started_at is not None and (
        started_at.tzinfo is None or started_at.utcoffset() is None
    ):
        raise InvalidPipelineTimestampError("started_at must be timezone-aware")

    scrape_run = start_scrape_run(
        company_source=company_source,
        started_at=resolved_started_at,
    )

    requests_made = 0
    try:
        batch = adapter.fetch(company=company_source)
        requests_made = batch.requests_made
        records = batch.records
        # Fetching is deliberately complete before this short, SQLite-serialized
        # persistence/reconciliation phase.
        with database_write_guard(), transaction.atomic():
            seen_job_ids: list[int] = []
            jobs_created = 0
            jobs_updated = 0
            jobs_unchanged = 0
            job_errors: list[JobProcessingError] = []

            for index, record in enumerate(records):
                observed_at = finished_at or _clock_timestamp(
                    clock=clock,
                    name="observed_at",
                    started_at=resolved_started_at,
                )
                try:
                    with transaction.atomic():
                        persisted = _process_record(
                            company_source=company_source,
                            record=record,
                            record_index=index,
                            seen_at=observed_at,
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
                    "all source job records failed processing"
                )

            if jobs_failed:
                terminal_finished_at = finished_at or _clock_timestamp(
                    clock=clock,
                    name="finished_at",
                    started_at=resolved_started_at,
                )
                completed_run = finish_scrape_run(
                    scrape_run=scrape_run,
                    status=TerminalRunStatus.PARTIAL,
                    finished_at=terminal_finished_at,
                    jobs_found=jobs_found,
                    jobs_created=jobs_created,
                    jobs_updated=jobs_updated,
                    requests_made=batch.requests_made,
                    error_message=(
                        f"{jobs_failed} of {jobs_found} job records failed processing"
                    ),
                )
                reconciliation = None
            else:
                terminal_finished_at = finished_at or _clock_timestamp(
                    clock=clock,
                    name="finished_at",
                    started_at=resolved_started_at,
                )
                completed_run = finish_scrape_run(
                    scrape_run=scrape_run,
                    status=TerminalRunStatus.SUCCESS,
                    finished_at=terminal_finished_at,
                    jobs_found=jobs_found,
                    jobs_created=jobs_created,
                    jobs_updated=jobs_updated,
                    requests_made=batch.requests_made,
                )
                reconciliation = reconcile_jobs_after_successful_run(
                    scrape_run=cast(ReconciliationScrapeRunRecord, completed_run),
                    seen_job_posting_ids=seen_job_ids,
                    miss_threshold=miss_threshold,
                )

        return PipelineResult(
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
        if isinstance(error, SourceError):
            requests_made = error.requests_made
        failure_finished_at = finished_at or _clock_timestamp(
            clock=clock,
            name="finished_at",
            started_at=resolved_started_at,
        )
        failed_run = _finish_failed_run(
            scrape_run=scrape_run,
            finished_at=failure_finished_at,
            requests_made=requests_made,
            error_message=_safe_failure_message(error),
        )
        raise PipelineError(
            "source pipeline failed; batch changes were rolled back",
            scrape_run=failed_run,
        ) from error


def run_fixture_pipeline(
    *,
    company_source: CompanySourceRecord | None = None,
    company: CompanyRecord | None = None,
    fixture_path: Path,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    recover_job_errors: bool = False,
    clock: Callable[[], datetime] = timezone.now,
) -> PipelineResult:
    """Backward-compatible wrapper for explicit local fixture runs."""
    return run_source_pipeline(
        company_source=company_source,
        company=company,
        adapter=FixtureSourceAdapter(fixture_path),
        started_at=started_at,
        finished_at=finished_at,
        miss_threshold=miss_threshold,
        recover_job_errors=recover_job_errors,
        clock=clock,
    )
