"""Read-only views for persisted scrape-run activity and history."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from scrape_runs.models import ScrapeRun

HISTORY_PAGE_SIZE = 25


@require_GET
def scrape_run_list(request: HttpRequest) -> HttpResponse:
    """Show current activity and a bounded, newest-first run history."""
    runs = ScrapeRun.objects.select_related("company").order_by("-started_at", "-pk")
    page_obj = Paginator(runs, HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))
    page_obj.object_list = list(page_obj.object_list)
    latest_run = (
        page_obj.object_list[0]
        if page_obj.number == 1 and page_obj.object_list
        else runs.first()
    )
    running_runs = list(
        ScrapeRun.objects.filter(status=ScrapeRun.Status.RUNNING)
        .select_related("company")
        .order_by("-started_at", "-pk")
    )
    return render(
        request,
        "scrape_runs/scrape_run_list.html",
        {
            "page_obj": page_obj,
            "running_runs": running_runs,
            "running_run_ids": [run.pk for run in running_runs],
            "latest_run_state": _serialize_run(latest_run) if latest_run else None,
        },
    )


def _requested_run_ids(raw_ids: str) -> list[int]:
    """Parse a bounded list of positive database IDs; ignore invalid tokens."""
    parsed: list[int] = []
    for token in raw_ids.split(",")[:100]:
        try:
            run_id = int(token)
        except ValueError:
            continue
        if run_id > 0 and run_id not in parsed:
            parsed.append(run_id)
    return parsed


def _requested_company_id(raw_company_id: str) -> int | None:
    """Parse one optional positive company ID for scoped polling."""
    try:
        company_id = int(raw_company_id)
    except ValueError:
        return None
    return company_id if company_id > 0 else None


@require_GET
def scrape_run_status(request: HttpRequest) -> JsonResponse:
    """Return latest, running, and requested run state without starting work."""
    run_ids = _requested_run_ids(request.GET.get("ids", ""))
    company_source_ids = _requested_run_ids(
        request.GET.get("company_source_ids", "")
    )
    after_id = _requested_after_id(request.GET.get("after_id", ""))
    company_id = _requested_company_id(request.GET.get("company_id", ""))
    run_filter = Q(pk__in=run_ids) | Q(status=ScrapeRun.Status.RUNNING)
    if company_id is not None:
        run_filter &= Q(company_id=company_id)
    runs = (
        ScrapeRun.objects.filter(run_filter)
        .select_related("company")
        .order_by("-started_at", "-pk")
    )
    latest_run = (
        ScrapeRun.objects.select_related("company")
        .order_by("-started_at", "-pk")
        .first()
    )
    company_latest_run = None
    if company_id is not None:
        company_latest_run = (
            ScrapeRun.objects.filter(company_id=company_id)
            .select_related("company")
            .order_by("-started_at", "-pk")
            .first()
        )
    expected_source_runs: list[dict[str, object] | None] = []
    if company_id is not None:
        for company_source_id in company_source_ids:
            source_run = (
                ScrapeRun.objects.filter(
                    company_id=company_id,
                    company_source_id=company_source_id,
                    pk__gt=after_id,
                )
                .select_related("company")
                .order_by("-started_at", "-pk")
                .first()
            )
            expected_source_runs.append(
                _serialize_run(source_run) if source_run else None
            )
    submission_complete = bool(company_source_ids) and all(
        run is not None and bool(run["is_terminal"])
        for run in expected_source_runs
    )
    return JsonResponse(
        {
            "latest_run": _serialize_run(latest_run) if latest_run else None,
            "company_latest_run": (
                _serialize_run(company_latest_run) if company_latest_run else None
            ),
            "runs": [_serialize_run(run) for run in runs],
            "expected_source_runs": expected_source_runs,
            "submission_complete": submission_complete,
        }
    )


def _requested_after_id(raw_after_id: str) -> int:
    """Parse a non-negative post-submission run baseline."""
    try:
        after_id = int(raw_after_id)
    except ValueError:
        return 0
    return after_id if after_id >= 0 else 0


def _serialize_run(run: ScrapeRun) -> dict[str, object]:
    """Build the small polling representation from an existing DB record."""
    return {
        "id": run.pk,
        "company": run.company.name,
        "company_source_id": run.company_source_id,
        "status": run.status,
        "is_terminal": run.status != ScrapeRun.Status.RUNNING,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "jobs_found": run.jobs_found,
        "jobs_created": run.jobs_created,
        "jobs_updated": run.jobs_updated,
        "requests_made": run.requests_made,
        "duration_seconds": (
            str(run.duration_seconds) if run.duration_seconds is not None else None
        ),
        "error_message": run.error_message,
    }
