"""Server-rendered views for saved job postings."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet  # type: ignore[import-untyped]
from django.http import HttpRequest, HttpResponse  # type: ignore[import-untyped]
from django.shortcuts import get_object_or_404, render  # type: ignore[import-untyped]

from companies.models import Company
from jobs.description import sanitize_job_description
from jobs.forms import JobFilterForm
from jobs.models import JobPosting
from jobs.review import annotate_review_state, filter_by_review_state, mark_job_reviewed


def _apply_filters(
    jobs: QuerySet[JobPosting], cleaned_data: dict[str, Any]
) -> QuerySet[JobPosting]:
    if company_id := cleaned_data.get("company"):
        jobs = jobs.filter(company_id=company_id)
    if company_type := cleaned_data.get("company_type"):
        jobs = jobs.filter(company__company_type=company_type)
    if country := cleaned_data.get("country"):
        jobs = jobs.filter(country__iexact=country.strip())
    if location := cleaned_data.get("location"):
        jobs = jobs.filter(location__icontains=location.strip())
    if query := cleaned_data.get("q"):
        jobs = jobs.filter(title__icontains=query.strip())
    if status := cleaned_data.get("status"):
        jobs = jobs.filter(status=status)
    if workplace_type := cleaned_data.get("workplace_type"):
        jobs = jobs.filter(workplace_type=workplace_type)
    jobs = filter_by_review_state(jobs, cleaned_data.get("review_state"))
    if published_from := cleaned_data.get("published_from"):
        jobs = jobs.filter(published_at__date__gte=published_from)
    if published_to := cleaned_data.get("published_to"):
        jobs = jobs.filter(published_at__date__lte=published_to)
    if first_seen_from := cleaned_data.get("first_seen_from"):
        jobs = jobs.filter(first_seen_at__date__gte=first_seen_from)
    if first_seen_to := cleaned_data.get("first_seen_to"):
        jobs = jobs.filter(first_seen_at__date__lte=first_seen_to)
    return jobs


def job_list(request: HttpRequest) -> HttpResponse:
    """Show all saved vacancies with optional combinable GET filters."""
    companies = tuple(Company.objects.order_by("name", "pk").values_list("pk", "name"))
    stored_countries = (
        JobPosting.objects.exclude(country__isnull=True)
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
    form = JobFilterForm(
        request.GET or None,
        companies=companies,
        countries=countries,
    )
    jobs = JobPosting.objects.select_related("company")
    if form.is_bound:
        form.is_valid()
        jobs = _apply_filters(jobs, form.cleaned_data)
    jobs = annotate_review_state(jobs).order_by(
        "-published_at", "-first_seen_at", "-pk"
    )

    return render(
        request,
        "jobs/job_list.html",
        {
            "filter_form": form,
            "has_any_jobs": JobPosting.objects.exists(),
            "jobs": jobs,
        },
    )


def job_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show all user-relevant stored information for one vacancy."""
    job = get_object_or_404(
        JobPosting.objects.select_related("company"),
        pk=pk,
    )
    response = render(
        request,
        "jobs/job_detail.html",
        {
            "job": job,
            "job_description_html": sanitize_job_description(job.description),
        },
    )
    mark_job_reviewed(job)
    return response
