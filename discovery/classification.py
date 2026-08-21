"""Independent official-site and job-source classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from discovery.models import DiscoveryCandidate
from discovery.network import UnsafeUrlError, canonicalize_url, validate_public_url


@dataclass(frozen=True, slots=True)
class JobSourceClassification:
    eligibility: str
    confidence: int
    reason: str


_JOB_TERMS = ("career", "careers", "job", "jobs", "vacan", "opening", "karriere")
_NON_JOB_TERMS = ("/article", "/blog", "/news", "/contact", "/people", "/person/")
_EXCLUDED_UNKNOWN_SOURCE_HOSTS = (
    "facebook.com",
    "glassdoor.com",
    "indeed.com",
    "instagram.com",
    "leadiq.com",
    "linkedin.com",
    "naukri.com",
    "twitter.com",
    "x.com",
    "youtube.com",
)
_EXCLUDED_UNKNOWN_SOURCE_SEGMENTS = {
    "article",
    "articles",
    "blog",
    "blogs",
    "employee",
    "employees",
    "event",
    "events",
    "insight",
    "insights",
    "news",
    "people",
    "person",
    "press",
    "press-release",
    "press-releases",
    "profile",
    "profiles",
    "resource",
    "resources",
}
_EXCLUDED_UNKNOWN_SOURCE_SEGMENT_FRAGMENTS = (
    "application",
    "apply",
    "cookie",
    "legal",
    "login",
    "privacy",
    "signin",
    "sign-in",
    "terms",
    "wp-login",
)


def is_excluded_unknown_source_url(url: str) -> bool:
    """Reject URLs that should never become unknown/custom inventory sources."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if any(
        host == blocked or host.endswith(f".{blocked}")
        for blocked in _EXCLUDED_UNKNOWN_SOURCE_HOSTS
    ):
        return True
    segments = tuple(segment.casefold() for segment in parsed.path.split("/") if segment)
    if set(segments) & _EXCLUDED_UNKNOWN_SOURCE_SEGMENTS:
        return True
    return any(
        fragment in segment
        for segment in segments
        for fragment in _EXCLUDED_UNKNOWN_SOURCE_SEGMENT_FRAGMENTS
    )


def classify_job_source(
    url: str,
    *,
    title: str = "",
    description: str = "",
    kind: str = "",
    platform: str = "",
    supported: bool = False,
) -> JobSourceClassification:
    """Classify job usefulness without treating official-site rejection as evidence."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.casefold()
    searchable = f"{path} {title.casefold()} {description.casefold()}"
    has_job_signal = any(term in searchable for term in _JOB_TERMS)

    if platform:
        if supported:
            return JobSourceClassification(
                str(DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS),
                95,
                "Registered adapter and platform signal are available.",
            )
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS),
            85,
            "A careers platform was identified, but no usable adapter is available.",
        )
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        if "/jobs" in path or has_job_signal:
            return JobSourceClassification(
                str(DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD),
                82,
                "LinkedIn Jobs may contain vacancies for this company.",
            )
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE),
            90,
            "LinkedIn profile or company page has no jobs-specific URL signal.",
        )
    if (host == "naukri.com" or host.endswith(".naukri.com")) and has_job_signal:
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD),
            78,
            "Naukri result appears to be a company jobs page.",
        )
    if any(term in path for term in _NON_JOB_TERMS) and not has_job_signal:
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE),
            85,
            "Search result is an informational page without vacancy relevance.",
        )
    if "leadiq.com" in host and not has_job_signal:
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE),
            88,
            "Company-directory result has no vacancy relevance.",
        )
    if str(kind) == str(DiscoveryCandidate.Kind.CAREERS) and has_job_signal:
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE),
            72,
            "The URL has company careers or jobs signals.",
        )
    if has_job_signal:
        return JobSourceClassification(
            str(DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE),
            65,
            "The URL has careers or jobs signals but its platform is unrecognized.",
        )
    return JobSourceClassification(
        str(DiscoveryCandidate.JobSourceEligibility.UNCERTAIN),
        35,
        "The result may need a person to determine whether it contains vacancies.",
    )


def _strong_job_source_evidence(candidate: DiscoveryCandidate) -> bool:
    if candidate.job_source_confidence < 75:
        return False
    evidence_text = " ".join(str(item).casefold() for item in (candidate.evidence or ()))
    if not evidence_text:
        return False
    blocked_fragments = (
        "apply",
        "privacy",
        "terms",
        "cookie",
        "login",
        "signin",
        "social",
        "linkedin",
        "facebook",
        "instagram",
        "twitter",
        "x.com",
        "email",
    )
    if any(fragment in evidence_text for fragment in blocked_fragments):
        return False
    searchable = f"{candidate.reason.casefold()} {evidence_text}"
    job_markers = (
        "job",
        "jobs",
        "career",
        "careers",
        "vacanc",
        "opening",
        "position",
        "positions",
        "role",
        "roles",
    )
    return any(marker in searchable for marker in job_markers)


def _has_confirmed_listing_evidence(candidate: DiscoveryCandidate) -> bool:
    evidence = " ".join(str(item).casefold() for item in (candidate.evidence or ()))
    return any(
        marker in evidence
        for marker in (
            "confirmed careers listing",
            "confirmed job listing",
            "individual job links",
            "individual vacancy links",
            "multiple job links",
            "multiple vacancy links",
        )
    )


def is_generic_fallback_eligible(candidate: DiscoveryCandidate) -> bool:
    """Allow generic fallback only for clearly public, non-ATS job sources."""
    if candidate.is_ignored:
        return False
    if candidate.decision == DiscoveryCandidate.Decision.REJECTED:
        return False
    if candidate.supported:
        return False

    try:
        canonical = canonicalize_url(candidate.canonical_url or candidate.discovered_url)
        validate_public_url(canonical)
    except (TypeError, ValueError, UnsafeUrlError):
        return False

    if is_excluded_unknown_source_url(canonical):
        return False

    status = str(candidate.job_source_eligibility)
    if status == str(DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS):
        return False
    if status == str(DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD):
        return False
    if status == str(DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE):
        return False
    if status == str(DiscoveryCandidate.JobSourceEligibility.UNCERTAIN):
        return False
    if status == str(DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE):
        return _has_confirmed_listing_evidence(candidate)
    if status == str(DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS):
        return _strong_job_source_evidence(candidate)
    if status == str(DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE):
        return _strong_job_source_evidence(candidate) and candidate.job_source_confidence >= 80
    return False
