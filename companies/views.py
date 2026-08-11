"""Server-rendered company management views."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from companies.forms import CompanyForm
from companies.models import Company
from jobs.forms import CompanyJobFilterForm
from jobs.models import JobPosting
from jobs.views import _apply_filters
from scrape_runs.models import ScrapeRun
from scraping.background import (
    BackgroundExecutionError,
    BackgroundRunAlreadyScheduledError,
    ControlledBackgroundExecutor,
)

background_executor = ControlledBackgroundExecutor()


def company_list(request: HttpRequest) -> HttpResponse:
    """Show all configured companies in a stable order."""
    companies = Company.objects.order_by("name", "pk")
    return render(request, "companies/company_list.html", {"companies": companies})


def company_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show one company and its saved vacancies without starting a run."""
    company = get_object_or_404(Company, pk=pk)
    company_jobs = company.job_postings.all()
    stored_countries = (
        company_jobs.exclude(country__isnull=True)
        .exclude(country="")
        .values_list("country", flat=True)
        .distinct()
    )
    countries = tuple(
        sorted(
            {
                country
                for country in stored_countries
                if country is not None and country.strip()
            },
            key=str.casefold,
        )
    )
    filter_form = CompanyJobFilterForm(request.GET or None, countries=countries)
    jobs = company_jobs
    if filter_form.is_bound:
        filter_form.is_valid()
        jobs = _apply_filters(jobs, filter_form.cleaned_data)
    jobs = jobs.order_by("-last_seen_at", "-pk")
    active_job_count = company.job_postings.filter(
        status=JobPosting.Status.ACTIVE
    ).count()
    latest_run = company.scrape_runs.order_by("-started_at", "-pk").first()
    watch_after_run_id = _watch_after_run_id(request)
    if latest_run and latest_run.status == ScrapeRun.Status.RUNNING:
        company_run_polling = {
            "baseline_run_id": latest_run.pk,
            "mode": "running",
        }
    elif watch_after_run_id is not None and (
        latest_run is None or latest_run.pk == watch_after_run_id
    ):
        company_run_polling = {
            "baseline_run_id": watch_after_run_id,
            "mode": "new",
        }
    else:
        company_run_polling = None
    return render(
        request,
        "companies/company_detail.html",
        {
            "company": company,
            "filter_form": filter_form,
            "has_any_jobs": company_jobs.exists(),
            "jobs": jobs,
            "active_job_count": active_job_count,
            "company_run_polling": company_run_polling,
        },
    )


def _watch_after_run_id(request: HttpRequest) -> int | None:
    """Return a valid post-submission baseline from the detail-page query."""
    raw_run_id = request.GET.get("watch_after")
    if raw_run_id is None:
        return None
    try:
        run_id = int(raw_run_id)
    except ValueError:
        return None
    return run_id if run_id >= 0 else None


def company_create(request: HttpRequest) -> HttpResponse:
    """Create a company using Post/Redirect/Get."""
    form = CompanyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        messages.success(request, f"Company “{company.name}” was added.")
        return redirect("companies:list")
    return render(
        request,
        "companies/company_form.html",
        {"form": form, "page_title": "Add company", "submit_label": "Add company"},
    )


def company_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit the user-managed fields of an existing company."""
    company = get_object_or_404(Company, pk=pk)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        saved_company = form.save()
        messages.success(request, f"Company “{saved_company.name}” was updated.")
        return redirect("companies:list")
    return render(
        request,
        "companies/company_form.html",
        {"form": form, "page_title": "Edit company", "submit_label": "Save changes"},
    )


@require_POST
def company_toggle_active(request: HttpRequest, pk: int) -> HttpResponse:
    """Enable or disable monitoring without deleting company history."""
    company = get_object_or_404(Company, pk=pk)
    company.is_active = not company.is_active
    company.save(update_fields=("is_active", "updated_at"))
    state = "activated" if company.is_active else "deactivated"
    messages.success(request, f"Company “{company.name}” was {state}.")
    return redirect("companies:list")


@require_POST
def company_update_jobs(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue the existing fixture pipeline for one eligible company."""
    company = get_object_or_404(Company, pk=pk)

    if not company.is_active:
        messages.error(request, "Activate this company before updating jobs.")
        return redirect("companies:detail", pk=company.pk)
    if ScrapeRun.objects.filter(company=company, status=ScrapeRun.Status.RUNNING).exists():
        messages.warning(
            request,
            "A job update is already running for this company.",
        )
        return redirect("companies:detail", pk=company.pk)

    latest_run_id = (
        ScrapeRun.objects.filter(company=company)
        .order_by("-started_at", "-pk")
        .values_list("pk", flat=True)
        .first()
    )
    try:
        background_executor.submit_pipeline(company=company)
    except BackgroundRunAlreadyScheduledError:
        messages.warning(
            request,
            "A job update is already running for this company.",
        )
    except BackgroundExecutionError:
        messages.error(request, "Job update could not be started.")
    else:
        messages.success(request, "Job update started.")
        detail_url = reverse("companies:detail", args=(company.pk,))
        return redirect(f"{detail_url}?watch_after={latest_run_id or 0}")
    return redirect("companies:detail", pk=company.pk)
