"""Reconcile persisted jobs after one complete successful scrape run."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from django.apps import apps  # type: ignore[import-untyped]
from django.db import transaction  # type: ignore[import-untyped]
from django.utils import timezone  # type: ignore[import-untyped]

DEFAULT_SUCCESSFUL_MISS_THRESHOLD = 2


class ScrapeRunRecord(Protocol):
    """Persisted run attributes required by reconciliation."""

    pk: int | None
    company_id: int
    company_source_id: int | None
    status: str
    finished_at: object | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Counts describing the rows and changes committed by reconciliation.

    ``miss_counters_incremented`` counts counters whose value increased. A
    previously inconsistent NOT_FOUND row below the threshold is normalized to
    the threshold and included. ``closed_jobs_unchanged`` counts CLOSED jobs
    whose explicit status was preserved, even when a seen job's stale counter
    was reset.
    """

    total_source_jobs: int
    seen_jobs: int
    unseen_jobs: int
    miss_counters_reset: int
    miss_counters_incremented: int
    jobs_marked_not_found: int
    closed_jobs_unchanged: int

    @property
    def total_company_jobs(self) -> int:
        """Compatibility alias; the count is now scoped to one CompanySource."""
        return self.total_source_jobs


class ReconciliationError(Exception):
    """Base error for rejected job reconciliation operations."""


class RunNotSavedError(ReconciliationError):
    """The supplied run is not a persisted ScrapeRun row."""


class InvalidReconciliationRunError(ReconciliationError):
    """The supplied run is not a completed successful run."""


class InvalidSeenJobError(ReconciliationError, ValueError):
    """A seen PK is invalid, missing, or belongs to another company."""


class InvalidMissThresholdError(ReconciliationError, ValueError):
    """The successful-miss threshold is invalid."""


def _scrape_run_model() -> Any:
    return apps.get_model("scrape_runs", "ScrapeRun")


def _job_posting_model() -> Any:
    return apps.get_model("jobs", "JobPosting")


def _validated_threshold(miss_threshold: int) -> int:
    if type(miss_threshold) is not int or miss_threshold < 1:
        raise InvalidMissThresholdError(
            "miss_threshold must be an int greater than or equal to 1, excluding bool"
        )
    return miss_threshold


def _validated_seen_ids(seen_job_posting_ids: Iterable[int]) -> set[int]:
    try:
        values = set(seen_job_posting_ids)
    except TypeError as error:
        raise InvalidSeenJobError(
            "seen_job_posting_ids must be an iterable of integer database PKs"
        ) from error
    if any(type(value) is not int for value in values):
        raise InvalidSeenJobError(
            "seen_job_posting_ids must contain only integer database PKs, excluding bool"
        )
    return values


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

    stored_run = (
        model.objects.select_for_update()
        .select_related("company_source")
        .get(pk=run_pk)
    )
    if stored_run.status != "success" or stored_run.finished_at is None:
        raise InvalidReconciliationRunError(
            "reconciliation requires a finished SUCCESS scrape run"
        )
    if stored_run.company_id is None:
        raise InvalidReconciliationRunError(
            "scrape_run must belong to a saved company"
        )
    if stored_run.company_source_id is None:
        raise InvalidReconciliationRunError(
            "scrape_run must belong to a saved company source"
        )
    if stored_run.company_source.company_id != stored_run.company_id:
        raise InvalidReconciliationRunError(
            "scrape_run company source must belong to its company"
        )
    return stored_run


def _validated_source_jobs(
    *, company_source_id: int, seen_ids: set[int]
) -> list[Any]:
    model = _job_posting_model()
    source_jobs = list(
        model.objects.select_for_update()
        .filter(company_source_id=company_source_id)
        .order_by("pk")
    )
    source_job_ids = {job.pk for job in source_jobs}
    unknown_ids = seen_ids - source_job_ids
    if not unknown_ids:
        return source_jobs

    existing_unknown_ids = set(
        model.objects.filter(pk__in=unknown_ids).values_list("pk", flat=True)
    )
    foreign_ids = unknown_ids & existing_unknown_ids
    if foreign_ids:
        raise InvalidSeenJobError(
            "seen job PKs belong to another company source: "
            + ", ".join(str(pk) for pk in sorted(foreign_ids))
        )
    raise InvalidSeenJobError(
        "seen job PKs do not exist: "
        + ", ".join(str(pk) for pk in sorted(unknown_ids))
    )


def reconcile_jobs_after_successful_run(
    *,
    scrape_run: ScrapeRunRecord,
    seen_job_posting_ids: Iterable[int],
    miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
) -> ReconciliationResult:
    """Reconcile one CompanySource snapshot after a finished SUCCESS run.

    Seen rows have stale successful-miss counters reset without changing their
    status. Unseen ACTIVE rows accumulate successful misses and become
    NOT_FOUND at the configured threshold. NOT_FOUND counters are capped at
    that threshold, while CLOSED rows retain their explicit status and unseen
    counters.

    Future pipeline orchestration must wrap ``finish_scrape_run(SUCCESS)`` and
    this call in one outer ``transaction.atomic()`` so a reconciliation failure
    can also roll back the terminal transition.
    """
    threshold = _validated_threshold(miss_threshold)
    seen_ids = _validated_seen_ids(seen_job_posting_ids)

    with transaction.atomic():
        stored_run = _validated_stored_run(scrape_run)
        source_jobs = _validated_source_jobs(
            company_source_id=stored_run.company_source_id,
            seen_ids=seen_ids,
        )

        reset_count = 0
        incremented_count = 0
        marked_not_found_count = 0
        closed_count = 0
        counter_only_jobs: list[Any] = []
        status_changed_jobs: list[Any] = []
        changed_at = timezone.now()

        for job in source_jobs:
            changed = False
            status_changed = False
            if job.status == "closed":
                closed_count += 1

            if job.pk in seen_ids:
                if job.consecutive_successful_misses != 0:
                    job.consecutive_successful_misses = 0
                    reset_count += 1
                    changed = True
            elif job.status == "active":
                previous_counter = job.consecutive_successful_misses
                next_counter = min(previous_counter + 1, threshold)
                if next_counter != previous_counter:
                    job.consecutive_successful_misses = next_counter
                    changed = True
                if next_counter > previous_counter:
                    incremented_count += 1
                if previous_counter + 1 >= threshold:
                    job.status = "not_found"
                    marked_not_found_count += 1
                    status_changed = True
                    changed = True
            elif job.status == "not_found":
                capped_counter = threshold
                if job.consecutive_successful_misses != capped_counter:
                    if job.consecutive_successful_misses < capped_counter:
                        incremented_count += 1
                    job.consecutive_successful_misses = capped_counter
                    changed = True

            if changed:
                job.updated_at = changed_at
                if status_changed:
                    status_changed_jobs.append(job)
                else:
                    counter_only_jobs.append(job)

        if counter_only_jobs:
            _job_posting_model().objects.bulk_update(
                counter_only_jobs,
                fields=("consecutive_successful_misses", "updated_at"),
            )
        if status_changed_jobs:
            _job_posting_model().objects.bulk_update(
                status_changed_jobs,
                fields=("consecutive_successful_misses", "status", "updated_at"),
            )

        return ReconciliationResult(
            total_source_jobs=len(source_jobs),
            seen_jobs=len(seen_ids),
            unseen_jobs=len(source_jobs) - len(seen_ids),
            miss_counters_reset=reset_count,
            miss_counters_incremented=incremented_count,
            jobs_marked_not_found=marked_not_found_count,
            closed_jobs_unchanged=closed_count,
        )
