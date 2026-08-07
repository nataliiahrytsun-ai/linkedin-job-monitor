"""Server-rendered company management views."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from companies.forms import CompanyForm
from companies.models import Company
from jobs.models import JobPosting


def company_list(request: HttpRequest) -> HttpResponse:
    """Show all configured companies in a stable order."""
    companies = Company.objects.order_by("name", "pk")
    return render(request, "companies/company_list.html", {"companies": companies})


def company_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show one company and its saved vacancies without starting a run."""
    company = get_object_or_404(Company, pk=pk)
    jobs = company.job_postings.order_by("-last_seen_at", "-pk")
    active_job_count = company.job_postings.filter(
        status=JobPosting.Status.ACTIVE
    ).count()
    return render(
        request,
        "companies/company_detail.html",
        {
            "company": company,
            "jobs": jobs,
            "active_job_count": active_job_count,
        },
    )


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
