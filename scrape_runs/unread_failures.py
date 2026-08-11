"""Session-scoped acknowledgement for failed scrape runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q  # type: ignore[import-untyped]
from django.http import HttpRequest  # type: ignore[import-untyped]
from django.utils import timezone  # type: ignore[import-untyped]
from django.utils.dateparse import parse_datetime  # type: ignore[import-untyped]

from scrape_runs.models import ScrapeRun

_SESSION_FINISHED_AT_KEY = "seen_failed_run_finished_at"
_SESSION_RUN_ID_KEY = "seen_failed_run_id"


@dataclass(frozen=True, order=True, slots=True)
class FailureBoundary:
    """A stable ordering boundary for terminal failed runs."""

    finished_at: datetime
    run_id: int


def latest_failure_boundary() -> FailureBoundary | None:
    """Return the newest failed run using terminal time and ID ordering."""
    values = (
        ScrapeRun.objects.filter(status=ScrapeRun.Status.FAILED)
        .order_by("-finished_at", "-pk")
        .values_list("finished_at", "pk")
        .first()
    )
    if values is None:
        return None
    finished_at, run_id = values
    assert finished_at is not None
    return FailureBoundary(finished_at=finished_at, run_id=run_id)


def unread_failure_count(
    request: HttpRequest, *, through: FailureBoundary | None
) -> int:
    """Count failed runs after the session marker within one dashboard snapshot."""
    if through is None:
        return 0
    failures = ScrapeRun.objects.filter(status=ScrapeRun.Status.FAILED).filter(
        Q(finished_at__lt=through.finished_at)
        | Q(finished_at=through.finished_at, pk__lte=through.run_id)
    )
    seen = _session_boundary(request)
    if seen is not None:
        failures = failures.filter(
            Q(finished_at__gt=seen.finished_at)
            | Q(finished_at=seen.finished_at, pk__gt=seen.run_id)
        )
    return int(failures.count())


def acknowledge_failure(request: HttpRequest, raw_run_id: str) -> None:
    """Advance the session marker through one real failed run, never beyond it."""
    try:
        run_id = int(raw_run_id)
    except ValueError:
        return
    if run_id < 1:
        return
    values = (
        ScrapeRun.objects.filter(pk=run_id, status=ScrapeRun.Status.FAILED)
        .values_list("finished_at", "pk")
        .first()
    )
    if values is None:
        return
    finished_at, confirmed_run_id = values
    assert finished_at is not None
    boundary = FailureBoundary(finished_at, confirmed_run_id)
    seen = _session_boundary(request)
    if seen is not None and seen >= boundary:
        return
    request.session[_SESSION_FINISHED_AT_KEY] = boundary.finished_at.isoformat()
    request.session[_SESSION_RUN_ID_KEY] = boundary.run_id


def _session_boundary(request: HttpRequest) -> FailureBoundary | None:
    raw_finished_at = request.session.get(_SESSION_FINISHED_AT_KEY)
    raw_run_id = request.session.get(_SESSION_RUN_ID_KEY)
    if not isinstance(raw_finished_at, str) or type(raw_run_id) is not int:
        return None
    finished_at = parse_datetime(raw_finished_at)
    if finished_at is None or not timezone.is_aware(finished_at) or raw_run_id < 1:
        return None
    return FailureBoundary(finished_at=finished_at, run_id=raw_run_id)
