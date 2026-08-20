"""Read-only presentation state for the unified Sources Manager."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from companies.forms import validate_source_configuration
from companies.models import CompanySource
from discovery.canonicalization import (
    canonicalize_source_candidate_url,
    logical_source_identity,
    source_candidate_rank,
)
from discovery.classification import (
    JobSourceClassification,
    classify_job_source,
    is_excluded_unknown_source_url,
    is_generic_fallback_eligible,
)
from discovery.models import DiscoveryAdapterCheck, DiscoveryCandidate, DiscoveryRun
from discovery.network import UnsafeUrlError
from scraping.sources.registry import registered_source_keys


def _url_key(url: str | None) -> str:
    if not url:
        return ""
    try:
        return canonicalize_source_candidate_url(url)
    except UnsafeUrlError:
        return url.strip().rstrip("/")


def equivalent_source(candidate: DiscoveryCandidate) -> CompanySource | None:
    if candidate.company_source_id is not None:
        return candidate.company_source
    candidate_platform = candidate.platform.strip().casefold()
    if not candidate_platform and is_generic_fallback_eligible(candidate):
        candidate_platform = "generic"
    candidate_key = logical_source_identity(candidate_platform, candidate.canonical_url)
    return next(
        (
            source
            for source in CompanySource.objects.filter(company_id=candidate.run.company_id)
            if source.source_jobs_url
            and logical_source_identity(source.source, source.source_jobs_url) == candidate_key
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class CandidatePresentation:
    candidate: DiscoveryCandidate
    state: str
    state_label: str
    adapter_available: bool
    validation_status: str
    linked_source: CompanySource | None
    can_connect: bool
    can_confirm: bool
    can_revalidate: bool
    draft_action: str
    task_draft: str
    platform_label: str
    short_url: str
    display_url: str
    evidence: tuple[str, ...]
    origin_label: str
    canonical_identity: str
    display_name: str
    category_label: str
    confidence_label: str
    adapter_status_label: str
    short_reason: str
    can_ignore: bool
    can_restore: bool
    listing_confirmed: bool


@dataclass(frozen=True, slots=True)
class AdapterCheckPresentation:
    check: DiscoveryAdapterCheck
    platform_label: str
    status_label: str
    status_tone: str


@dataclass(frozen=True, slots=True)
class DiscoveryCoverage:
    registered: int
    checked: int
    sources_found: int
    already_connected: int
    not_found: int
    not_checked: int
    partial: bool
    checks: tuple[AdapterCheckPresentation, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryResultPresentation:
    status_label: str
    status_tone: str
    message: str
    connected: int
    new: int
    needs_review: int
    adapter_required: int
    investigation_required: int
    rejected: int
    official_url_label: str
    careers_url_label: str
    actionable: tuple[CandidatePresentation, ...]
    connected_items: tuple[CandidatePresentation, ...]
    additional_items: tuple[CandidatePresentation, ...]
    rejected_items: tuple[CandidatePresentation, ...]
    summary_text: str


_STATE_LABELS = {
    "ready_to_connect": "Ready to connect",
    "generic_available": "Generic fallback available",
    "connected": "Connected",
    "adapter_required": "New adapter needed",
    "investigation_required": "New adapter needed",
    "needs_review": "Review",
    "not_a_job_source": "Not a job source",
    "ignored": "",
}

_CATEGORY_LABELS = {
    DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS: "ATS",
    DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS: "ATS",
    DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD: "External job board",
    DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE: "Company jobs page",
    DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE: "Possible job source",
    DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE: "Not a job source",
    DiscoveryCandidate.JobSourceEligibility.UNCERTAIN: "Uncertain",
}

_PLATFORM_LABELS = {
    "ashby": "Ashby",
    "darwinbox": "Darwinbox",
    "dreamjobs": "DreamJobs",
    "greenhouse": "Greenhouse",
    "jazzhr": "JazzHR",
    "lever": "Lever",
    "personio": "Personio",
    "smartrecruiters": "SmartRecruiters",
    "teamtailor": "Teamtailor",
    "workable": "Workable",
    "workday": "Workday",
    "zoho_recruit": "Zoho Recruit",
}

_CHECK_LABELS = {
    DiscoveryAdapterCheck.Status.ALREADY_CONNECTED: "Connected",
    DiscoveryAdapterCheck.Status.FOUND: "Found",
    DiscoveryAdapterCheck.Status.NOT_FOUND: "Not found",
    DiscoveryAdapterCheck.Status.NOT_CHECKED: "Not checked",
    DiscoveryAdapterCheck.Status.SEARCH_FAILED: "Search failed",
    DiscoveryAdapterCheck.Status.VALIDATION_FAILED: "Validation failed",
}

_CHECK_TONES = {
    DiscoveryAdapterCheck.Status.ALREADY_CONNECTED: "connected",
    DiscoveryAdapterCheck.Status.FOUND: "ready",
    DiscoveryAdapterCheck.Status.NOT_FOUND: "neutral",
    DiscoveryAdapterCheck.Status.NOT_CHECKED: "neutral",
    DiscoveryAdapterCheck.Status.SEARCH_FAILED: "attention",
    DiscoveryAdapterCheck.Status.VALIDATION_FAILED: "attention",
}

_LISTING_EVIDENCE_MARKERS = (
    "ats api",
    "ats-specific asset",
    "ats-specific metadata",
    "confirmed careers listing",
    "confirmed job listing",
    "graphql jobs",
    "graphql vacancy",
    "individual job links",
    "individual vacancy links",
    "job listing json",
    "job listing schema",
    "jobposting json",
    "jobposting schema",
    "jobs endpoint",
    "listing page contains vacancies",
    "multiple job links",
    "multiple vacancy links",
    "repeated job card",
    "unregistered ats asset or host",
    "vacancies endpoint",
)


def _platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(
        platform, platform.replace("_", " ").title() or "Unrecognized source"
    )


def _short_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path.rstrip("/")
    display = f"{host}{path}" if host else url
    return f"{display[:57]}…" if len(display) > 60 else display


def _effective_job_source(candidate: DiscoveryCandidate) -> JobSourceClassification:
    inferred = classify_job_source(
        candidate.discovered_url or candidate.canonical_url,
        kind=candidate.kind,
        platform=candidate.platform,
        supported=candidate.supported,
    )
    if candidate.job_source_eligibility == DiscoveryCandidate.JobSourceEligibility.UNCERTAIN:
        return inferred
    return JobSourceClassification(
        candidate.job_source_eligibility,
        candidate.job_source_confidence or inferred.confidence,
        inferred.reason,
    )


def _has_confirmed_listing(candidate: DiscoveryCandidate) -> bool:
    """Require persisted technical/listing evidence before offering adapter work."""
    if (
        candidate.kind == DiscoveryCandidate.Kind.SOURCE
        and candidate.platform
        and not candidate.supported
        and candidate.job_source_eligibility
        == DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS
    ):
        return True
    if is_excluded_unknown_source_url(candidate.canonical_url or candidate.discovered_url):
        return False
    evidence = " ".join(str(item).casefold() for item in candidate.evidence)
    return any(marker in evidence for marker in _LISTING_EVIDENCE_MARKERS)


def _display_name(candidate: DiscoveryCandidate, eligibility: str) -> str:
    parsed = urlsplit(candidate.discovered_url or candidate.canonical_url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.casefold()
    if candidate.platform:
        return _platform_label(candidate.platform)
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "LinkedIn jobs" if "/jobs" in path else "LinkedIn"
    if host == "naukri.com" or host.endswith(".naukri.com"):
        return "Naukri company jobs" if "job" in path else "Naukri"
    if host == "leadiq.com" or host.endswith(".leadiq.com"):
        return "LeadIQ"
    if eligibility == DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE:
        return "Unrecognized careers source"
    if eligibility == DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE:
        return "Possible job source"
    return "Unrecognized source"


def _origin_label(candidate: DiscoveryCandidate) -> str:
    if any(str(item).startswith("Fallback query:") for item in candidate.evidence):
        return "Fallback search"
    labels = {
        DiscoveryCandidate.Origin.EXISTING_SOURCE: "Existing source",
        DiscoveryCandidate.Origin.CURRENT_DISCOVERY: "Current discovery",
        DiscoveryCandidate.Origin.PREVIOUS_DISCOVERY: "Previous discovery",
        DiscoveryCandidate.Origin.ADAPTER_SEARCH: "Adapter search",
    }
    return labels.get(candidate.origin, candidate.get_origin_display())


def _canonical_identity(candidate: DiscoveryCandidate) -> str:
    platform, identity = logical_source_identity(
        candidate.platform,
        candidate.canonical_url,
    )
    return f"{platform}:{identity}"


def _candidate_rank(candidate: DiscoveryCandidate) -> tuple[object, ...]:
    rank = source_candidate_rank(
        platform=candidate.platform,
        url=candidate.canonical_url,
        supported=candidate.supported,
    )
    return (*rank[:-1], -candidate.confidence, rank[-1], candidate.pk)


def _task_draft(candidate: DiscoveryCandidate, state: str) -> str:
    run = candidate.run
    platform = candidate.platform or "Unrecognized"
    missing = ["public listing contract", "pagination", "field mapping"]
    if not candidate.platform:
        missing.append("platform identity")
    return "\n".join(
        (
            "Adapter research task",
            f"Company: {run.company.name}",
            f"Source URL: {candidate.canonical_url}",
            f"Likely platform: {platform}",
            f"Careers URL: {run.careers_url or 'Not found'}",
            "Reason: no registered adapter supports this source.",
            "Research needed: " + ", ".join(missing) + ".",
            "For connection, this public source must first be researched and a new "
            "adapter developed. Do not assume implementation is feasible until its "
            "public contract has been verified.",
        )
    )


def present_candidate(candidate: DiscoveryCandidate) -> CandidatePresentation:
    adapter_available = candidate.platform in set(registered_source_keys())
    validation_ok = False
    validation_status = "Not validated"
    if adapter_available and candidate.canonical_url:
        try:
            validate_source_configuration(
                source=candidate.platform,
                source_jobs_url=candidate.canonical_url,
            )
        except ValidationError:
            validation_status = "Validation failed"
        else:
            validation_ok = True
            validation_status = "Validated"
    linked_source = equivalent_source(candidate)
    job_source = _effective_job_source(candidate)
    eligibility = job_source.eligibility
    listing_confirmed = _has_confirmed_listing(candidate)
    if candidate.is_ignored:
        state = "ignored"
    elif linked_source is not None:
        state = "connected"
    elif is_generic_fallback_eligible(candidate):
        state = "generic_available"
    elif eligibility in {
        DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS,
        DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD,
    } and listing_confirmed:
        state = "adapter_required"
    elif eligibility in {
        DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE,
        DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE,
    } and listing_confirmed:
        state = "investigation_required"
    elif eligibility == DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE:
        state = "not_a_job_source"
    elif candidate.decision == DiscoveryCandidate.Decision.NEEDS_REVIEW:
        state = "needs_review"
    elif (
        eligibility == DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS
        and adapter_available
        and validation_ok
        and candidate.supported
        and candidate.decision != DiscoveryCandidate.Decision.REJECTED
    ):
        state = "ready_to_connect"
    else:
        state = "not_a_job_source"
    can_connect = state in {"ready_to_connect", "generic_available"}
    can_confirm = (
        state == "needs_review"
        and candidate.kind == DiscoveryCandidate.Kind.SOURCE
        and candidate.supported
        and adapter_available
        and validation_ok
        and candidate.decision == DiscoveryCandidate.Decision.NEEDS_REVIEW
    )
    draft_action = (
        "Adapter task"
        if state in {"adapter_required", "investigation_required"}
        else ""
    )
    adapter_status = {
        "ready_to_connect": "Supported — ready to connect",
        "generic_available": "Generic fallback available",
        "connected": "Already connected",
        "adapter_required": "Adapter not implemented",
        "investigation_required": "Unknown / Custom",
        "not_a_job_source": "Not a job source",
        "ignored": "Ignored by user",
        "needs_review": "Review required",
    }[state]
    display_url = (
        candidate.discovered_url
        if candidate.kind == DiscoveryCandidate.Kind.OFFICIAL_SITE
        and eligibility != DiscoveryCandidate.JobSourceEligibility.UNCERTAIN
        else _url_key(candidate.canonical_url)
    )
    display_name = _display_name(candidate, eligibility)
    return CandidatePresentation(
        candidate=candidate,
        state=state,
        state_label=_STATE_LABELS[state],
        adapter_available=adapter_available,
        validation_status=validation_status,
        linked_source=linked_source,
        can_connect=can_connect,
        can_confirm=can_confirm,
        can_revalidate=state in {
            "adapter_required",
            "investigation_required",
            "needs_review",
        },
        draft_action=draft_action,
        task_draft=_task_draft(candidate, state),
        platform_label=display_name,
        short_url=_short_url(display_url),
        display_url=display_url,
        evidence=tuple(str(item) for item in candidate.evidence),
        origin_label=_origin_label(candidate),
        canonical_identity=_canonical_identity(candidate),
        display_name=display_name,
        category_label=_CATEGORY_LABELS[eligibility],
        confidence_label=f"{candidate.confidence}% confidence",
        adapter_status_label=adapter_status,
        short_reason=job_source.reason,
        can_ignore=state not in {"connected", "ignored"},
        can_restore=state == "ignored",
        listing_confirmed=listing_confirmed,
    )


def latest_candidate_presentations(
    run: DiscoveryRun | None,
) -> tuple[CandidatePresentation, ...]:
    if run is None:
        return ()
    candidates = run.candidates.select_related("company_source", "run__company").order_by("pk")
    return tuple(present_candidate(candidate) for candidate in candidates)


def company_candidate_presentations(
    *, company_id: int
) -> tuple[CandidatePresentation, ...]:
    """Return the company's published actionable candidate inventory.

    Candidate rows are written while discovery is running, so publication is
    deliberately gated by the owning run. Failed and incomplete snapshots do
    not replace the last usable result. Newer records with the same canonical
    source identity supersede older records, including an explicit ignore or
    rejection.
    """
    incomplete_run_ids = set(
        DiscoveryAdapterCheck.objects.filter(
            run__company_id=company_id,
            status__in={
                DiscoveryAdapterCheck.Status.NOT_CHECKED,
                DiscoveryAdapterCheck.Status.SEARCH_FAILED,
            },
        ).values_list("run_id", flat=True)
    )
    finished_candidates = (
        DiscoveryCandidate.objects.filter(run__company_id=company_id)
        .exclude(
            run__status__in={
                DiscoveryRun.Status.RUNNING,
                DiscoveryRun.Status.FAILED,
            }
        )
        .select_related("company_source", "run__company")
        .order_by("-run__started_at", "-run_id", "-pk")
    )
    ordered_candidates = (
        *finished_candidates.exclude(run_id__in=incomplete_run_ids),
        *finished_candidates.filter(run_id__in=incomplete_run_ids),
    )
    candidates = tuple(
        candidate
        for _run_id, run_candidates in groupby(
            ordered_candidates,
            key=lambda candidate: candidate.run_id,
        )
        for candidate in sorted(run_candidates, key=_candidate_rank)
    )
    seen: set[str] = set()
    published: list[CandidatePresentation] = []
    for candidate in candidates:
        identity = _canonical_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        if (
            candidate.is_ignored
            or candidate.decision == DiscoveryCandidate.Decision.REJECTED
        ):
            continue
        presentation = present_candidate(candidate)
        if presentation.linked_source is not None:
            continue
        if presentation.state in {
            "ready_to_connect",
            "generic_available",
            "adapter_required",
            "investigation_required",
            "needs_review",
        }:
            published.append(presentation)
    return tuple(published)


def discovery_coverage(run: DiscoveryRun | None) -> DiscoveryCoverage | None:
    if run is None:
        return None
    raw_checks = tuple(
        run.adapter_checks.select_related("candidate", "company_source").order_by("platform")
    )
    checks = tuple(
        AdapterCheckPresentation(
            check=check,
            platform_label=_platform_label(check.platform),
            status_label=_CHECK_LABELS[check.status],
            status_tone=_CHECK_TONES[check.status],
        )
        for check in raw_checks
    )
    found_keys: set[tuple[str, str]] = set()
    for candidate in run.candidates.filter(kind=DiscoveryCandidate.Kind.SOURCE):
        found_keys.add(logical_source_identity(candidate.platform, candidate.canonical_url))
    incomplete_statuses = {
        DiscoveryAdapterCheck.Status.NOT_CHECKED,
        DiscoveryAdapterCheck.Status.SEARCH_FAILED,
    }
    return DiscoveryCoverage(
        registered=len(raw_checks),
        checked=sum(
            check.status != DiscoveryAdapterCheck.Status.NOT_CHECKED for check in raw_checks
        ),
        sources_found=len(found_keys),
        already_connected=sum(
            check.status == DiscoveryAdapterCheck.Status.ALREADY_CONNECTED
            for check in raw_checks
        ),
        not_found=sum(
            check.status == DiscoveryAdapterCheck.Status.NOT_FOUND for check in raw_checks
        ),
        not_checked=sum(
            check.status == DiscoveryAdapterCheck.Status.NOT_CHECKED for check in raw_checks
        ),
        partial=any(check.status in incomplete_statuses for check in raw_checks),
        checks=checks,
    )


def discovery_result_presentation(
    run: DiscoveryRun | None,
    candidates: tuple[CandidatePresentation, ...],
    coverage: DiscoveryCoverage | None,
    *,
    connected_source_count: int = 0,
) -> DiscoveryResultPresentation | None:
    if run is None:
        return None
    visible_candidates = tuple(
        item
        for item in candidates
        if item.linked_source is None
        and (
            item.listing_confirmed
            or item.can_connect
            or item.can_confirm
        )
    )
    actionable = tuple(
        item for item in visible_candidates if item.state not in {"not_a_job_source", "ignored"}
    )
    rejected_items = tuple(
        item for item in visible_candidates if item.state == "ignored"
    )
    messages = {
        DiscoveryRun.Status.RUNNING: "Discovery is running.",
        DiscoveryRun.Status.CONNECTED: "A validated source was connected.",
        DiscoveryRun.Status.ALREADY_CONNECTED: "All discovered sources are already connected.",
        DiscoveryRun.Status.NEEDS_REVIEW: "Some results require attention.",
        DiscoveryRun.Status.UNSUPPORTED: "A careers source needs an adapter.",
        DiscoveryRun.Status.NOT_FOUND: "No supported careers source was confirmed.",
        DiscoveryRun.Status.FAILED: "Discovery could not be completed.",
    }
    tones = {
        DiscoveryRun.Status.RUNNING: "neutral",
        DiscoveryRun.Status.CONNECTED: "connected",
        DiscoveryRun.Status.ALREADY_CONNECTED: "connected",
        DiscoveryRun.Status.NEEDS_REVIEW: "attention",
        DiscoveryRun.Status.UNSUPPORTED: "attention",
        DiscoveryRun.Status.NOT_FOUND: "neutral",
        DiscoveryRun.Status.FAILED: "failed",
    }
    connected_items: tuple[CandidatePresentation, ...] = ()
    additional_items = actionable
    additional_count = len(additional_items)
    connected_label = (
        f"{connected_source_count} source"
        f"{'s' if connected_source_count != 1 else ''}"
    )
    additional_label = (
        f"{additional_count} additional source"
        f"{'s' if additional_count != 1 else ''} found."
    )
    if run.status == DiscoveryRun.Status.RUNNING:
        summary_text = "Searching for job sources…"
    elif run.status == DiscoveryRun.Status.FAILED:
        summary_text = (
            "Discovery needs search configuration. Supply the official domain to bypass "
            "search, or configure the approved search provider."
            if run.error_code == "SearchConfigurationError"
            else "Discovery could not be completed. Existing sources remain connected."
        )
    elif coverage is not None and coverage.partial:
        summary_text = "Search incomplete — some sources could not be checked."
    elif connected_source_count and additional_count:
        summary_text = f"{connected_label} already connected · {additional_label}"
    elif connected_source_count:
        confirmed = (
            coverage is not None
            and coverage.registered > 0
            and coverage.already_connected >= connected_source_count
        )
        connected_summary = (
            f"{connected_source_count} connected source"
            f"{'s' if connected_source_count != 1 else ''} confirmed"
            if confirmed
            else f"{connected_label} already connected"
        )
        summary_text = f"{connected_summary} · No additional sources found."
    elif additional_count:
        summary_text = additional_label.capitalize()
    else:
        summary_text = "No job sources found."
    return DiscoveryResultPresentation(
        status_label=run.get_status_display(),
        status_tone=tones[run.status],
        message=messages[run.status],
        connected=sum(item.state == "connected" for item in actionable),
        new=sum(item.state == "ready_to_connect" for item in actionable),
        needs_review=sum(item.state == "needs_review" for item in actionable),
        adapter_required=sum(item.state == "adapter_required" for item in actionable),
        investigation_required=sum(
            item.state == "investigation_required" for item in actionable
        ),
        rejected=len(rejected_items),
        official_url_label=(
            _short_url(run.official_website_url) if run.official_website_url else ""
        ),
        careers_url_label=_short_url(run.careers_url) if run.careers_url else "",
        actionable=actionable,
        connected_items=connected_items,
        additional_items=additional_items,
        rejected_items=rejected_items,
        summary_text=summary_text,
    )
