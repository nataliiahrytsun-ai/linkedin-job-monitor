"""Atomic lifecycle operations for source-neutral scrape runs."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Protocol, cast

from django.apps import apps  # type: ignore[import-untyped]
from django.db import IntegrityError, transaction  # type: ignore[import-untyped]

from scraping.sources.base import SourceError
from scraping.sources.resolution import resolve_legacy_company_source


class CompanyRecord(Protocol):
    pk: int | None
    source: str
    source_jobs_url: str | None
    is_active: bool
    last_scraped_at: datetime | None
    last_scrape_status: str


class CompanySourceRecord(Protocol):
    pk: int | None
    company_id: int
    approval_status: str
    is_active: bool

    @property
    def company(self) -> CompanyRecord: ...


class ScrapeRunRecord(Protocol):
    pk: int | None
    company: CompanyRecord
    company_source: CompanySourceRecord
    company_source_id: int | None
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


class InactiveCompanySourceError(RunLifecycleError):
    """Only an approved active CompanySource can start a new run."""


class RunNotSavedError(RunLifecycleError):
    """The supplied run is not a persisted ScrapeRun row."""


class DuplicateRunningRunError(RunLifecycleError):
    """The CompanySource already has a RUNNING run."""


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


def _company_source_model() -> Any:
    return apps.get_model("companies", "CompanySource")


def _scrape_run_model() -> Any:
    return apps.get_model("scrape_runs", "ScrapeRun")


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _validated_company_source(company_source: CompanySourceRecord) -> Any:
    model = _company_source_model()
    source_pk = getattr(company_source, "pk", None)
    if (
        not isinstance(company_source, model)
        or source_pk is None
        or getattr(getattr(company_source, "_state", None), "adding", True)
        or not model.objects.filter(pk=source_pk).exists()
    ):
        raise CompanyNotSavedError("company source must already be saved")
    stored_source = model.objects.select_for_update().select_related("company").get(pk=source_pk)
    if company_source.company_id != stored_source.company_id:
        raise CompanyNotSavedError("company source company does not match its persisted ownership")
    return stored_source


def _is_duplicate_running_constraint(error: IntegrityError) -> bool:
    message = str(error).lower()
    return (
        "uniq_running_run_source" in message
        or "uniq_running_run_company" in message
        or (
            "unique constraint failed" in message
            and (
                "scrape_runs_scraperun.company_source_id" in message
                or "scrape_runs_scraperun.company_id" in message
            )
        )
    )


def _aggregate_company_state(stored_company: Any) -> None:
    """Recompute the transitional Company cache from executable source runs."""
    source_ids = list(
        _company_source_model()
        .objects.filter(
            company_id=stored_company.pk,
            approval_status="approved",
            is_active=True,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    latest_by_source: dict[int, Any] = {}
    if source_ids:
        for run in (
            _scrape_run_model()
            .objects.filter(company_source_id__in=source_ids)
            .order_by("company_source_id", "-started_at", "-pk")
        ):
            latest_by_source.setdefault(run.company_source_id, run)

    latest_runs = tuple(latest_by_source.values())
    statuses = {run.status for run in latest_runs}
    if "running" in statuses:
        aggregate_status = "running"
    elif "failed" in statuses:
        aggregate_status = "failed"
    elif "partial" in statuses:
        aggregate_status = "partial"
    elif source_ids and len(latest_runs) == len(source_ids) and statuses == {"success"}:
        aggregate_status = "success"
    else:
        aggregate_status = "never"

    latest_terminal = (
        _scrape_run_model()
        .objects.filter(
            company_source_id__in=source_ids,
            finished_at__isnull=False,
        )
        .order_by("-finished_at", "-pk")
        .values_list("finished_at", flat=True)
        .first()
        if source_ids
        else None
    )
    stored_company.last_scrape_status = aggregate_status
    stored_company.last_scraped_at = latest_terminal
    stored_company.save(update_fields=("last_scrape_status", "last_scraped_at"))


def recompute_company_scrape_state(*, company_id: int) -> None:
    """Race-safely refresh Company status/timestamp from source-level runs."""
    if type(company_id) is not int or company_id < 1:
        raise CompanyNotSavedError("company_id must identify a saved company")
    with transaction.atomic():
        try:
            stored_company = _company_model().objects.select_for_update().get(pk=company_id)
        except _company_model().DoesNotExist as error:
            raise CompanyNotSavedError("company must already be saved") from error
        _aggregate_company_state(stored_company)


def start_scrape_run(
    *,
    company_source: CompanySourceRecord | None = None,
    company: CompanyRecord | None = None,
    started_at: datetime,
) -> ScrapeRunRecord:
    """Create one source-owned RUNNING run and mark its Company RUNNING."""
    if not _is_aware(started_at):
        raise InvalidRunTimestampError("started_at must be timezone-aware")
    if company_source is None:
        if company is None:
            raise CompanyNotSavedError("company source is required")
        try:
            company_source = cast(
                CompanySourceRecord,
                resolve_legacy_company_source(company),
            )
        except SourceError as error:
            raise CompanyNotSavedError(str(error)) from error
    elif company is not None:
        raise CompanyNotSavedError("provide company_source or legacy company, not both")

    try:
        with transaction.atomic():
            stored_source = _validated_company_source(company_source)
            stored_company = (
                _company_model().objects.select_for_update().get(pk=stored_source.company_id)
            )
            if not stored_company.is_active:
                raise InactiveCompanyError("inactive company cannot start a run")
            if stored_source.approval_status != "approved" or not stored_source.is_active:
                raise InactiveCompanySourceError("company source must be approved and active")

            run = _scrape_run_model().objects.create(
                company=stored_company,
                company_source=stored_source,
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
            _aggregate_company_state(stored_company)
            company_source.company.last_scrape_status = stored_company.last_scrape_status
            company_source.company.last_scraped_at = stored_company.last_scraped_at
            return cast(ScrapeRunRecord, run)
    except IntegrityError as error:
        if _is_duplicate_running_constraint(error):
            raise DuplicateRunningRunError(
                "company source already has a RUNNING scrape run"
            ) from error
        raise RunLifecycleDatabaseError("database rejected scrape-run start") from error


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
            raise InvalidRunCountersError(f"{name} must be a non-negative int, excluding bool")
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
        elapsed.days * 86_400 + elapsed.seconds
    ) * 1_000_000 + elapsed.microseconds
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
    return (
        model.objects.select_for_update().select_related("company", "company_source").get(pk=run_pk)
    )


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
    """Atomically finish one source RUNNING run and recompute Company state.

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
                raise InvalidRunTransitionError("only a RUNNING scrape run can be finished")
            if (
                stored_run.company_source_id is None
                or stored_run.company_source.company_id != stored_run.company_id
            ):
                raise InvalidRunTransitionError(
                    "RUNNING scrape run must have consistent company source ownership"
                )
            if not _is_aware(stored_run.started_at):
                raise InvalidRunTimestampError("stored started_at must be timezone-aware")
            if finished_at < stored_run.started_at:
                raise InvalidRunTimestampError("finished_at cannot be earlier than started_at")

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

            _aggregate_company_state(stored_company)
            return cast(ScrapeRunRecord, stored_run)
    except IntegrityError as error:
        raise RunLifecycleDatabaseError("database rejected scrape-run completion") from error
