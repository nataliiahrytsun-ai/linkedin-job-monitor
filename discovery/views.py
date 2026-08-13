"""Minimal source-discovery UI endpoints."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from companies.models import Company
from discovery.models import DiscoveryCandidate
from discovery.presentation import present_candidate
from discovery.service import confirm_candidate, revalidate_candidate, set_candidate_ignored
from job_monitor.background import background_executor
from scraping.background import BackgroundExecutionError, BackgroundRunAlreadyScheduledError


def _manager_url(company_pk: int, tab: str) -> str:
    return (
        f"{reverse('companies:detail', args=(company_pk,))}"
        f"?manage_sources=1&source_tab={tab}"
    )


@require_POST
def start(request: HttpRequest, company_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    supplied_domain = request.POST.get("domain", "").strip()[:253]
    try:
        background_executor.submit_discovery(company=company, supplied_domain=supplied_domain)
    except BackgroundRunAlreadyScheduledError:
        messages.warning(request, "Source discovery is already running for this company.")
    except BackgroundExecutionError:
        messages.error(request, "Source discovery could not be started.")
    else:
        messages.success(request, "Source discovery started in the background.")
    return redirect(_manager_url(company.pk, "discovered"))


@require_POST
def confirm(request: HttpRequest, company_pk: int, candidate_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    try:
        candidate = DiscoveryCandidate.objects.select_related(
            "run__company", "company_source"
        ).get(pk=candidate_pk, run__company=company)
        if not present_candidate(candidate).can_confirm:
            raise ValueError("Candidate cannot be confirmed")
        confirmed = confirm_candidate(candidate_id=candidate_pk, company_id=company.pk)
    except (
        DiscoveryCandidate.DoesNotExist,
        ValueError,
        LookupError,
        ValidationError,
        IntegrityError,
    ):
        messages.error(request, "This discovery candidate cannot be confirmed.")
    else:
        messages.success(request, f"{confirmed.platform.title()} was connected successfully.")
        return redirect(_manager_url(company.pk, "connected"))
    return redirect(_manager_url(company.pk, "discovered"))


@require_POST
def connect(request: HttpRequest, company_pk: int, candidate_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    try:
        candidate = DiscoveryCandidate.objects.select_related(
            "run__company", "company_source"
        ).get(pk=candidate_pk, run__company=company)
        if not present_candidate(candidate).can_connect:
            raise ValueError("Candidate is not ready")
        connected = confirm_candidate(candidate_id=candidate.pk, company_id=company.pk)
    except (
        DiscoveryCandidate.DoesNotExist,
        ValueError,
        LookupError,
        ValidationError,
        IntegrityError,
    ):
        messages.error(request, "This discovery candidate could not be connected.")
        return redirect(_manager_url(company.pk, "discovered"))
    messages.success(request, f"{connected.platform.title()} was connected successfully.")
    return redirect(_manager_url(company.pk, "connected"))


@require_POST
def connect_selected(request: HttpRequest, company_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    raw_ids = request.POST.getlist("candidate_ids")[:20]
    selected_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            candidate_id = int(raw_id)
        except ValueError:
            continue
        if candidate_id not in selected_ids:
            selected_ids.append(candidate_id)
    if not selected_ids:
        messages.error(request, "Select at least one ready candidate.")
        return redirect(_manager_url(company.pk, "discovered"))
    connected_count = 0
    seen_configs: set[tuple[str, str]] = set()
    for candidate_id in selected_ids:
        try:
            candidate = DiscoveryCandidate.objects.select_related(
                "run__company", "company_source"
            ).get(pk=candidate_id, run__company=company)
            presentation = present_candidate(candidate)
            config_key = (candidate.platform.casefold(), candidate.canonical_url.rstrip("/"))
            if not presentation.can_connect or config_key in seen_configs:
                raise ValueError("Candidate is not independently connectable")
            seen_configs.add(config_key)
            connected = confirm_candidate(candidate_id=candidate.pk, company_id=company.pk)
        except (
            DiscoveryCandidate.DoesNotExist,
            ValueError,
            LookupError,
            ValidationError,
            IntegrityError,
        ):
            messages.error(request, f"Candidate #{candidate_id} could not be connected.")
        else:
            connected_count += 1
            messages.success(
                request,
                f"{connected.platform.title()} candidate #{candidate_id} was connected.",
            )
    return redirect(
        _manager_url(company.pk, "connected" if connected_count else "discovered")
    )


@require_POST
def revalidate(request: HttpRequest, company_pk: int, candidate_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    try:
        candidate = revalidate_candidate(candidate_id=candidate_pk, company_id=company.pk)
    except (ValueError, LookupError, ValidationError):
        messages.error(request, "The saved candidate could not be revalidated.")
    else:
        if candidate.decision == DiscoveryCandidate.Decision.SELECTED:
            messages.success(request, "The saved candidate is ready to connect.")
        else:
            messages.warning(request, "The saved candidate still requires investigation.")
    return redirect(_manager_url(company.pk, "discovered"))


@require_POST
def ignore(request: HttpRequest, company_pk: int, candidate_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    try:
        set_candidate_ignored(
            candidate_id=candidate_pk,
            company_id=company.pk,
            ignored=True,
        )
    except (DiscoveryCandidate.DoesNotExist, ValueError):
        messages.error(request, "This candidate could not be ignored.")
    else:
        messages.success(request, "Candidate ignored. You can restore it later.")
    return redirect(_manager_url(company.pk, "discovered"))


@require_POST
def restore(request: HttpRequest, company_pk: int, candidate_pk: int) -> HttpResponse:
    company = get_object_or_404(Company, pk=company_pk)
    try:
        set_candidate_ignored(
            candidate_id=candidate_pk,
            company_id=company.pk,
            ignored=False,
        )
    except (DiscoveryCandidate.DoesNotExist, ValueError):
        messages.error(request, "This candidate could not be restored.")
    else:
        messages.success(request, "Candidate restored.")
    return redirect(_manager_url(company.pk, "discovered"))
