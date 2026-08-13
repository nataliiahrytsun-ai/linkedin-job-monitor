"""Independent official-site and job-source classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from discovery.models import DiscoveryCandidate


@dataclass(frozen=True, slots=True)
class JobSourceClassification:
    eligibility: str
    confidence: int
    reason: str


_JOB_TERMS = ("career", "careers", "job", "jobs", "vacan", "opening", "karriere")
_NON_JOB_TERMS = ("/article", "/blog", "/news", "/contact", "/people", "/person/")


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
                DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS,
                95,
                "Registered adapter and platform signal are available.",
            )
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS,
            85,
            "A careers platform was identified, but no usable adapter is available.",
        )
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        if "/jobs" in path or has_job_signal:
            return JobSourceClassification(
                DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD,
                82,
                "LinkedIn Jobs may contain vacancies for this company.",
            )
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE,
            90,
            "LinkedIn profile or company page has no jobs-specific URL signal.",
        )
    if (host == "naukri.com" or host.endswith(".naukri.com")) and has_job_signal:
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD,
            78,
            "Naukri result appears to be a company jobs page.",
        )
    if any(term in path for term in _NON_JOB_TERMS) and not has_job_signal:
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE,
            85,
            "Search result is an informational page without vacancy relevance.",
        )
    if "leadiq.com" in host and not has_job_signal:
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.NOT_A_JOB_SOURCE,
            88,
            "Company-directory result has no vacancy relevance.",
        )
    if kind == DiscoveryCandidate.Kind.CAREERS and has_job_signal:
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE,
            72,
            "The URL has company careers or jobs signals.",
        )
    if has_job_signal:
        return JobSourceClassification(
            DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE,
            65,
            "The URL has careers or jobs signals but its platform is unrecognized.",
        )
    return JobSourceClassification(
        DiscoveryCandidate.JobSourceEligibility.UNCERTAIN,
        35,
        "The result may need a person to determine whether it contains vacancies.",
    )
