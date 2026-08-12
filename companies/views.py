"""Server-rendered company management views."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from companies.forms import CompanyForm, CompanySourceForm, validate_source_configuration
from companies.models import Company, CompanySource
from jobs.forms import CompanyJobFilterForm
from jobs.models import JobPosting
from jobs.views import _apply_filters
from scrape_runs.models import ScrapeRun
from scraping.background import (
    BackgroundExecutionError,
    ControlledBackgroundExecutor,
)
from scraping.sources.registry import (
    executable_source_keys,
    normalize_source_key,
    source_unavailability_message,
    user_selectable_source_keys,
)

background_executor = ControlledBackgroundExecutor(
    max_workers=settings.JOB_MONITOR_BACKGROUND_MAX_WORKERS
)


def _manage_sources_url(company_pk: int) -> str:
    return f"{reverse('companies:detail', args=(company_pk,))}?manage_sources=1"


def company_list(request: HttpRequest) -> HttpResponse:
    """Show all configured companies in a stable order."""
    companies = Company.objects.order_by("name", "pk")
    return render(request, "companies/company_list.html", {"companies": companies})


def company_detail(
    request: HttpRequest,
    pk: int,
    *,
    add_source_form: CompanySourceForm | None = None,
    edit_source_form: CompanySourceForm | None = None,
    edit_source_pk: int | None = None,
    auto_open_source_dialog: str | None = None,
) -> HttpResponse:
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
    running_source_runs = ScrapeRun.objects.filter(
        company_source_id=OuterRef("pk"),
        status=ScrapeRun.Status.RUNNING,
    )
    company_sources = list(
        company.sources.annotate(has_running_run=Exists(running_source_runs)).order_by(
            "pk"
        )
    )
    if auto_open_source_dialog is None and request.GET.get("manage_sources") == "1":
        auto_open_source_dialog = "job-sources-dialog"
    selectable_sources = set(user_selectable_source_keys())
    executable_sources = set(executable_source_keys())
    source_rows = []
    for source in company_sources:
        source_edit_form = (
            edit_source_form
            if edit_source_pk == source.pk and edit_source_form is not None
            else CompanySourceForm(
                company=company,
                instance=source,
                auto_id=f"id_source_{source.pk}_%s",
            )
        )
        source_rows.append(
            {
                "source": source,
                "edit_form": source_edit_form,
                "edit_dialog_id": f"edit-source-dialog-{source.pk}",
                "is_auto_open": auto_open_source_dialog
                == f"edit-source-dialog-{source.pk}",
                "is_manageable": normalize_source_key(source.source)
                in selectable_sources,
                "availability_message": source_unavailability_message(source.source),
                "has_running_run": source.has_running_run,
            }
        )
    source_count = len(company_sources)
    active_source_count = sum(
        source.is_active
        and normalize_source_key(source.source) in executable_sources
        for source in company_sources
    )
    if add_source_form is None:
        add_source_form = CompanySourceForm(
            company=company,
            auto_id="id_add_source_%s",
        )
    watch_after_run_id = _watch_after_run_id(request)
    watched_source_ids = _watch_source_ids(request, company_id=company.pk)
    running_run_ids = tuple(
        company.scrape_runs.filter(status=ScrapeRun.Status.RUNNING)
        .order_by("company_source_id", "pk")
        .values_list("pk", flat=True)
    )
    if watched_source_ids:
        company_run_polling = {
            "baseline_run_id": watch_after_run_id or 0,
            "expected_source_ids": ",".join(map(str, watched_source_ids)),
            "expected_run_ids": "",
            "mode": "submission",
        }
    elif running_run_ids:
        company_run_polling = {
            "baseline_run_id": 0,
            "expected_source_ids": "",
            "expected_run_ids": ",".join(map(str, running_run_ids)),
            "mode": "running",
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
            "active_source_count": active_source_count,
            "source_count": source_count,
            "source_rows": source_rows,
            "add_source_form": add_source_form,
            "form": edit_source_form if edit_source_form is not None else add_source_form,
            "auto_open_source_dialog": auto_open_source_dialog,
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


def _watch_source_ids(request: HttpRequest, *, company_id: int) -> tuple[int, ...]:
    """Return bounded submitted source IDs that belong to this Company."""
    parsed: list[int] = []
    for token in request.GET.get("watch_sources", "").split(",")[:100]:
        try:
            source_id = int(token)
        except ValueError:
            continue
        if source_id > 0 and source_id not in parsed:
            parsed.append(source_id)
    if not parsed:
        return ()
    valid_ids = set(
        Company.objects.get(pk=company_id)
        .sources.filter(pk__in=parsed)
        .values_list("pk", flat=True)
    )
    return tuple(source_id for source_id in parsed if source_id in valid_ids)


def company_create(request: HttpRequest) -> HttpResponse:
    """Create a company using Post/Redirect/Get."""
    form = CompanyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        company = form.save()
        messages.success(request, f"Company “{company.name}” was added.")
        return redirect("companies:detail", pk=company.pk)
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
        return redirect("companies:detail", pk=saved_company.pk)
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


def company_source_create(request: HttpRequest, company_pk: int) -> HttpResponse:
    """Create one manually approved CompanySource using PRG."""
    company = get_object_or_404(Company, pk=company_pk)
    form = CompanySourceForm(request.POST or None, company=company)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, "This job source could not be saved safely.")
        else:
            messages.success(request, "Job source was added.")
            return redirect(_manage_sources_url(company.pk))
    if request.method == "POST":
        return company_detail(
            request,
            company.pk,
            add_source_form=form,
            auto_open_source_dialog="add-source-dialog",
        )
    return render(
        request,
        "companies/company_source_form.html",
        {
            "company": company,
            "form": form,
            "page_title": "Add job source",
            "submit_label": "Add source",
        },
    )


def company_source_edit(
    request: HttpRequest,
    company_pk: int,
    source_pk: int,
) -> HttpResponse:
    """Edit URL/state while preserving immutable source provenance."""
    company = get_object_or_404(Company, pk=company_pk)
    source = get_object_or_404(CompanySource, pk=source_pk, company=company)
    form = CompanySourceForm(request.POST or None, company=company, instance=source)
    if request.method == "POST" and source.scrape_runs.filter(
        status=ScrapeRun.Status.RUNNING
    ).exists():
        form.add_error(None, "This source is currently running. Wait until it finishes.")
    elif request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, "This job source could not be saved safely.")
        else:
            messages.success(request, "Job source was updated.")
            return redirect(_manage_sources_url(company.pk))
    if request.method == "POST":
        return company_detail(
            request,
            company.pk,
            edit_source_form=form,
            edit_source_pk=source.pk,
            auto_open_source_dialog=f"edit-source-dialog-{source.pk}",
        )
    return render(
        request,
        "companies/company_source_form.html",
        {
            "company": company,
            "source": source,
            "form": form,
            "page_title": "Edit job source",
            "submit_label": "Save source",
        },
    )


@require_POST
def company_source_toggle_active(
    request: HttpRequest,
    company_pk: int,
    source_pk: int,
) -> HttpResponse:
    """Safely activate or deactivate one source scoped to its Company."""
    company = get_object_or_404(Company, pk=company_pk)
    source = get_object_or_404(CompanySource, pk=source_pk, company=company)
    if normalize_source_key(source.source) not in user_selectable_source_keys():
        messages.error(request, "This internal source is not user-manageable.")
        return redirect(_manage_sources_url(company.pk))
    if source.scrape_runs.filter(status=ScrapeRun.Status.RUNNING).exists():
        messages.error(
            request,
            "This source is currently running. Wait until it finishes.",
        )
        return redirect(_manage_sources_url(company.pk))
    if source.is_active:
        source.is_active = False
        source.save(update_fields=("is_active", "updated_at"))
        messages.success(request, "Job source was deactivated.")
        return redirect(_manage_sources_url(company.pk))
    if source.approval_status != CompanySource.ApprovalStatus.APPROVED:
        messages.error(request, "Only an approved source can be activated.")
        return redirect(_manage_sources_url(company.pk))
    try:
        validate_source_configuration(
            source=source.source,
            source_jobs_url=source.source_jobs_url,
        )
    except ValidationError as error:
        messages.error(request, str(error))
        return redirect(_manage_sources_url(company.pk))
    source.is_active = True
    source.save(update_fields=("is_active", "updated_at"))
    messages.success(request, "Job source was activated.")
    return redirect(_manage_sources_url(company.pk))


@require_POST
def company_update_jobs(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue all executable CompanySources for one active Company."""
    company = get_object_or_404(Company, pk=pk)

    if not company.is_active:
        messages.error(request, "Activate this company before updating jobs.")
        return redirect("companies:detail", pk=company.pk)
    latest_run_id = (
        ScrapeRun.objects.filter(company=company)
        .order_by("-started_at", "-pk")
        .values_list("pk", flat=True)
        .first()
    )
    try:
        submission = background_executor.submit_company(company=company)
    except BackgroundExecutionError:
        messages.error(request, "Job update could not be started.")
    else:
        submitted_count = len(submission.submitted_source_ids)
        already_count = len(submission.already_running_source_ids)
        failed_count = len(submission.failed_source_ids)
        if submitted_count:
            text = (
                "Job update started."
                if submitted_count == 1
                else f"Job updates started for {submitted_count} sources."
            )
            if already_count or failed_count:
                messages.warning(request, text)
            else:
                messages.success(request, text)
        elif already_count:
            messages.warning(request, "A job update is already running.")
        else:
            messages.error(request, "Job update could not be started.")

        if not submitted_count:
            return redirect("companies:detail", pk=company.pk)
        detail_url = reverse("companies:detail", args=(company.pk,))
        source_ids = ",".join(map(str, submission.submitted_source_ids))
        return redirect(
            f"{detail_url}?watch_after={latest_run_id or 0}&watch_sources={source_ids}"
        )
    return redirect("companies:detail", pk=company.pk)
