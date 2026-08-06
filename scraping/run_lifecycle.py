"""Atomic lifecycle operations for source-neutral scrape runs."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Protocol, cast

from django.apps import apps  # type: ignore[import-untyped]
from django.db import IntegrityError, transaction  # type: ignore[import-untyped]


class CompanyRecord(Protocol):
    pk: int | None
    is_active: bool
    last_scraped_at: datetime | None
    last_scrape_status: str


class ScrapeRunRecord(Protocol):
    pk: int | None
    company: CompanyRecord
    started_at: datetime
    finished_at: datetime | None
    status: str
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    requests_made: int
    duration_seconds: Decimal | None
    error_message: str

    def refresh_from_db(self) -> None: ...


class TerminalRunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RunLifecycleError(Exception):
    """Base error for rejected or failed scrape-run lifecycle operations."""


class CompanyNotSavedError(RunLifecycleError):
    """The supplied company is not a persisted Company row."""


class InactiveCompanyError(RunLifecycleError):
    """An inactive company cannot start a new run."""


class RunNotSavedError(RunLifecycleError):
    """The supplied run is not a persisted ScrapeRun row."""


class DuplicateRunningRunError(RunLifecycleError):
    """The company already has a RUNNING run."""


class InvalidRunTransitionError(RunLifecycleError):
    """The requested lifecycle transition is not allowed."""


class InvalidRunCountersError(RunLifecycleError, ValueError):
    """One or more terminal run counters are invalid."""


class InvalidRunTimestampError(RunLifecycleError, ValueError):
    """A required run timestamp is naive or chronologically invalid."""


class InvalidRunErrorMessageError(RunLifecycleError, ValueError):
    """A terminal run error message does not match its status."""


class RunLifecycleDatabaseError(RunLifecycleError):
    """A database constraint rejected a lifecycle operation."""


def _company_model() -> Any:
    return apps.get_model("companies", "Company")


def _scrape_run_model() -> Any:
    return apps.get_model("scrape_runs", "ScrapeRun")


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _validated_company(company: CompanyRecord) -> Any:
    model = _company_model()
    company_pk = getattr(company, "pk", None)
    if (
        not isinstance(company, model)
        or company_pk is None
        or getattr(getattr(company, "_state", None), "adding", True)
        or not model.objects.filter(pk=company_pk).exists()
    ):
        raise CompanyNotSavedError("company must already be saved")
    return model.objects.select_for_update().get(pk=company_pk)


def _is_duplicate_running_constraint(error: IntegrityError) -> bool:
    message = str(error).lower()
    return "uniq_running_run_company" in message or (
        "unique constraint failed" in message
        and "scrape_runs_scraperun.company_id" in message
    )


def start_scrape_run(
    *, company: CompanyRecord, started_at: datetime
) -> ScrapeRunRecord:
    """Atomically create one RUNNING run and mark its company RUNNING."""
    if not _is_aware(started_at):
        raise InvalidRunTimestampError("started_at must be timezone-aware")

    try:
        with transaction.atomic():
            stored_company = _validated_company(company)
            if not stored_company.is_active:
                raise InactiveCompanyError("inactive company cannot start a run")

            run = _scrape_run_model().objects.create(
                company=stored_company,
                started_at=started_at,
                status="running",
                finished_at=None,
                duration_seconds=None,
                jobs_found=0,
                jobs_created=0,
                jobs_updated=0,
                requests_made=0,
                error_message="",
            )
            stored_company.last_scrape_status = "running"
            stored_company.save(update_fields=("last_scrape_status",))
            company.last_scrape_status = "running"
            return cast(ScrapeRunRecord, run)
    except IntegrityError as error:
        if _is_duplicate_running_constraint(error):
            raise DuplicateRunningRunError(
                "company already has a RUNNING scrape run"
            ) from error
        raise RunLifecycleDatabaseError(
            "database rejected scrape-run start"
        ) from error


def _validated_terminal_status(status: TerminalRunStatus | str) -> TerminalRunStatus:
    try:
        return TerminalRunStatus(status)
    except ValueError as error:
        raise InvalidRunTransitionError(
            "terminal status must be success, partial, or failed"
        ) from error


def _validated_counters(
    *, jobs_found: int, jobs_created: int, jobs_updated: int, requests_made: int
) -> None:
    counters = {
        "jobs_found": jobs_found,
        "jobs_created": jobs_created,
        "jobs_updated": jobs_updated,
        "requests_made": requests_made,
    }
    for name, value in counters.items():
        if type(value) is not int or value < 0:
            raise InvalidRunCountersError(
                f"{name} must be a non-negative int, excluding bool"
            )
    if jobs_created > jobs_found:
        raise InvalidRunCountersError("jobs_created cannot exceed jobs_found")
    if jobs_updated > jobs_found:
        raise InvalidRunCountersError("jobs_updated cannot exceed jobs_found")


def _validated_error_message(status: TerminalRunStatus, error_message: str) -> str:
    if not isinstance(error_message, str):
        raise InvalidRunErrorMessageError("error_message must be a string")
    normalized = error_message.strip()
    if status is TerminalRunStatus.SUCCESS:
        return ""
    if not normalized:
        raise InvalidRunErrorMessageError(
            f"{status.value.upper()} requires a non-empty error_message"
        )
    return normalized


def _duration_seconds(*, started_at: datetime, finished_at: datetime) -> Decimal:
    elapsed = finished_at - started_at
    total_microseconds = (
        (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000
        + elapsed.microseconds
    )
    return (Decimal(total_microseconds) / Decimal(1_000_000)).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )


def _validated_stored_run(scrape_run: ScrapeRunRecord) -> Any:
    model = _scrape_run_model()
    run_pk = getattr(scrape_run, "pk", None)
    if (
        not isinstance(scrape_run, model)
        or run_pk is None
        or getattr(getattr(scrape_run, "_state", None), "adding", True)
        or not model.objects.filter(pk=run_pk).exists()
    ):
        raise RunNotSavedError("scrape_run must already be saved")
    return model.objects.select_for_update().select_related("company").get(pk=run_pk)


def finish_scrape_run(
    *,
    scrape_run: ScrapeRunRecord,
    status: TerminalRunStatus | str,
    finished_at: datetime,
    jobs_found: int,
    jobs_created: int,
    jobs_updated: int,
    requests_made: int,
    error_message: str = "",
) -> ScrapeRunRecord:
    """Atomically finish a RUNNING run and synchronize its company status.

    PARTIAL requires a non-empty explanation because it is not a complete
    successful snapshot. This service deliberately performs no reconciliation.
    """
    terminal_status = _validated_terminal_status(status)
    if not _is_aware(finished_at):
        raise InvalidRunTimestampError("finished_at must be timezone-aware")
    _validated_counters(
        jobs_found=jobs_found,
        jobs_created=jobs_created,
        jobs_updated=jobs_updated,
        requests_made=requests_made,
    )
    normalized_error = _validated_error_message(terminal_status, error_message)

    try:
        with transaction.atomic():
            stored_run = _validated_stored_run(scrape_run)
            if stored_run.status != "running":
                raise InvalidRunTransitionError(
                    "only a RUNNING scrape run can be finished"
                )
            if not _is_aware(stored_run.started_at):
                raise InvalidRunTimestampError(
                    "stored started_at must be timezone-aware"
                )
            if finished_at < stored_run.started_at:
                raise InvalidRunTimestampError(
                    "finished_at cannot be earlier than started_at"
                )

            stored_company = (
                _company_model().objects.select_for_update().get(pk=stored_run.company_id)
            )
            stored_run.status = terminal_status.value
            stored_run.finished_at = finished_at
            stored_run.duration_seconds = _duration_seconds(
                started_at=stored_run.started_at,
                finished_at=finished_at,
            )
            stored_run.jobs_found = jobs_found
            stored_run.jobs_created = jobs_created
            stored_run.jobs_updated = jobs_updated
            stored_run.requests_made = requests_made
            stored_run.error_message = normalized_error
            stored_run.save(
                update_fields=(
                    "status",
                    "finished_at",
                    "duration_seconds",
                    "jobs_found",
                    "jobs_created",
                    "jobs_updated",
                    "requests_made",
                    "error_message",
                )
            )

            stored_company.last_scrape_status = terminal_status.value
            stored_company.last_scraped_at = finished_at
            stored_company.save(
                update_fields=("last_scrape_status", "last_scraped_at")
            )
            return cast(ScrapeRunRecord, stored_run)
    except IntegrityError as error:
        raise RunLifecycleDatabaseError(
            "database rejected scrape-run completion"
        ) from error
