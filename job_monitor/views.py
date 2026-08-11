"""Server-rendered project-level pages and dashboard actions."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from companies.models import Company
from companies.views import background_executor
from jobs.models import JobPosting
from scrape_runs.models import ScrapeRun
from scrape_runs.unread_failures import (
    latest_failure_boundary,
    unread_failure_count,
)
from scraping.background import (
    BackgroundExecutionError,
)


def home(request: HttpRequest) -> HttpResponse:
    """Render the monitoring dashboard from persisted aggregate data."""
    completed_statuses = (
        ScrapeRun.Status.SUCCESS,
        ScrapeRun.Status.PARTIAL,
        ScrapeRun.Status.FAILED,
    )
    latest_completed_jobs_created = (
        ScrapeRun.objects.filter(status__in=completed_statuses)
        .order_by("-finished_at", "-pk")
        .values_list("jobs_created", flat=True)
        .first()
    )
    latest_successful_run = (
        ScrapeRun.objects.filter(status=ScrapeRun.Status.SUCCESS)
        .order_by("-finished_at", "-pk")
        .values_list("finished_at", flat=True)
        .first()
    )
    running_run_ids = list(
        ScrapeRun.objects.filter(status=ScrapeRun.Status.RUNNING)
        .order_by("-started_at", "-pk")
        .values_list("pk", flat=True)
    )
    latest_run_state = None
    if running_run_ids:
        latest_run_state = (
            ScrapeRun.objects.order_by("-started_at", "-pk")
            .values("id", "status", "finished_at")
            .first()
        )
    latest_failure = latest_failure_boundary()

    return render(
        request,
        "home.html",
        {
            "monitored_companies": Company.objects.filter(is_active=True).count(),
            "active_jobs": JobPosting.objects.filter(
                status=JobPosting.Status.ACTIVE
            ).count(),
            "new_jobs": latest_completed_jobs_created or 0,
            "latest_successful_run": latest_successful_run,
            "running_runs": len(running_run_ids),
            "running_run_ids": running_run_ids,
            "latest_run_state": latest_run_state,
            "failed_runs": unread_failure_count(request, through=latest_failure),
            "latest_failed_run_id": (
                latest_failure.run_id if latest_failure is not None else None
            ),
        },
    )


@require_POST
def update_all(request: HttpRequest) -> HttpResponse:
    """Submit every executable source of every active Company."""
    active_companies = Company.objects.filter(is_active=True).order_by("pk")
    started_count = 0
    already_running_count = 0
    failed_count = 0

    for company in active_companies:
        try:
            submission = background_executor.submit_company(company=company)
        except BackgroundExecutionError:
            failed_count += 1
        else:
            started_count += len(submission.submitted_source_ids)
            already_running_count += len(submission.already_running_source_ids)
            failed_count += len(submission.failed_source_ids)

    if not (started_count or already_running_count or failed_count):
        messages.info(request, "No active companies to update.")
        return redirect("home")

    summary_parts = []
    if started_count:
        source_label = "source" if started_count == 1 else "sources"
        summary_parts.append(
            f"Monitoring started for {started_count} {source_label}."
        )
    if already_running_count:
        summary_parts.append(f"{already_running_count} already running.")
    if failed_count:
        summary_parts.append(f"{failed_count} could not be started.")
    summary = " ".join(summary_parts)

    if failed_count and not started_count:
        messages.error(request, summary)
    elif already_running_count or failed_count:
        messages.warning(request, summary)
    else:
        messages.success(request, summary)
    return redirect("home")
