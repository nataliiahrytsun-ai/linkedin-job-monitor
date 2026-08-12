"""Server-rendered project-level pages and dashboard actions."""

from django.contrib import messages
from django.db.models import Count, F, Q
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

MONITORING_BATCH_SESSION_KEY = "monitoring_batch_banner"


def _monitoring_batch_banner(request: HttpRequest) -> dict[str, str] | None:
    """Return an active Update-all banner and clear it after its batch finishes."""
    stored = request.session.get(MONITORING_BATCH_SESSION_KEY)
    if not isinstance(stored, dict):
        request.session.pop(MONITORING_BATCH_SESSION_KEY, None)
        return None

    source_ids = stored.get("source_ids")
    after_run_id = stored.get("after_run_id")
    message = stored.get("message")
    level = stored.get("level")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or not all(isinstance(source_id, int) and source_id > 0 for source_id in source_ids)
        or not isinstance(after_run_id, int)
        or after_run_id < 0
        or not isinstance(message, str)
        or level not in {"success", "warning"}
    ):
        request.session.pop(MONITORING_BATCH_SESSION_KEY, None)
        return None

    first_run_status_by_source: dict[int, str] = {}
    batch_runs = (
        ScrapeRun.objects.filter(
            company_source_id__in=source_ids,
            pk__gt=after_run_id,
        )
        .order_by("pk")
        .values_list("company_source_id", "status")
    )
    for company_source_id, status in batch_runs:
        if company_source_id is not None:
            first_run_status_by_source.setdefault(company_source_id, status)

    batch_complete = all(
        (status := first_run_status_by_source.get(source_id)) is not None
        and status != ScrapeRun.Status.RUNNING
        for source_id in source_ids
    )
    if batch_complete:
        request.session.pop(MONITORING_BATCH_SESSION_KEY, None)
        return None
    return {"message": message, "level": level}


def home(request: HttpRequest) -> HttpResponse:
    """Render the monitoring dashboard from persisted aggregate data."""
    review_counts = JobPosting.objects.aggregate(
        new_jobs=Count(
            "pk",
            filter=Q(last_reviewed_content_hash__isnull=True),
        ),
        updated_jobs=Count(
            "pk",
            filter=(
                Q(last_reviewed_content_hash__isnull=False)
                & ~Q(last_reviewed_content_hash=F("content_hash"))
            ),
        ),
    )
    new_jobs = review_counts["new_jobs"]
    updated_jobs = review_counts["updated_jobs"]
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
            "new_jobs": new_jobs,
            "updated_jobs": updated_jobs,
            "unreviewed_jobs": new_jobs + updated_jobs,
            "latest_successful_run": latest_successful_run,
            "running_runs": len(running_run_ids),
            "running_run_ids": running_run_ids,
            "latest_run_state": latest_run_state,
            "failed_runs": unread_failure_count(request, through=latest_failure),
            "latest_failed_run_id": (
                latest_failure.run_id if latest_failure is not None else None
            ),
            "monitoring_batch_banner": _monitoring_batch_banner(request),
        },
    )


@require_POST
def update_all(request: HttpRequest) -> HttpResponse:
    """Submit every executable source of every active Company."""
    active_companies = Company.objects.filter(is_active=True).order_by("pk")
    latest_run_id = (
        ScrapeRun.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    )
    started_count = 0
    submitted_source_ids: list[int] = []
    already_running_count = 0
    failed_count = 0

    for company in active_companies:
        try:
            submission = background_executor.submit_company(company=company)
        except BackgroundExecutionError:
            failed_count += 1
        else:
            started_count += len(submission.submitted_source_ids)
            submitted_source_ids.extend(submission.submitted_source_ids)
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

    if started_count:
        request.session[MONITORING_BATCH_SESSION_KEY] = {
            "source_ids": submitted_source_ids,
            "after_run_id": latest_run_id,
            "message": summary,
            "level": "warning" if already_running_count or failed_count else "success",
        }
    elif failed_count:
        messages.error(request, summary)
    elif already_running_count:
        messages.warning(request, summary)
    return redirect("home")
