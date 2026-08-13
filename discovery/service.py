"""Source-discovery orchestration, persistence, and approval decisions."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from companies.forms import validate_source_configuration
from companies.models import Company, CompanySource
from discovery.classification import classify_job_source
from discovery.detectors import (
    Detection,
    SourceDiscoveryHints,
    detect_page,
    registered_discovery_hints,
    source_identity,
)
from discovery.models import DiscoveryAdapterCheck, DiscoveryCandidate, DiscoveryRun
from discovery.network import (
    BoundedCrawler,
    CrawledPage,
    CrawlError,
    ScraplingTransport,
    UnsafeUrlError,
    canonicalize_url,
)
from discovery.search import (
    SearchConfigurationError,
    SearchProvider,
    SearchProviderError,
    SearchResult,
    TavilyEmptyResultsError,
    configured_search_provider,
)
from scraping.sources.base import SourceError
from scraping.sources.registry import registered_source_keys

logger = logging.getLogger(__name__)

BLOCKED_OFFICIAL_HOSTS = (
    "linkedin.com",
    "xing.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "glassdoor.com",
    "indeed.com",
    "monster.com",
    "ziprecruiter.com",
    "jobs.cz",
    "jobstreet.com",
    "wellfound.com",
    "careerbuilder.com",
    "simplyhired.com",
    "jooble.org",
    "talent.com",
    "google.com",
    "bing.com",
    "search.yahoo.com",
    "jobs.lever.co",
    "applytojob.com",
    "darwinbox.com",
    "dream.jobs",
    "boards.greenhouse.io",
    "greenhouse.io",
    "workable.com",
    "smartrecruiters.com",
    "teamtailor.com",
    "duckduckgo.com",
    "yahoo.com",
)


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    run_id: int
    status: str


@dataclass(frozen=True, slots=True)
class OfficialSiteCandidate:
    discovered_url: str
    canonical_url: str
    title: str
    confidence: int
    accepted: bool
    evidence: tuple[str, ...]
    reason: str


class DiscoveryCrawler(Protocol):
    @property
    def errors(self) -> Sequence[str]: ...

    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryService:
    """Injectable orchestration facade used by production and offline tests."""

    search_provider: SearchProvider | None = None
    crawler: DiscoveryCrawler | None = None

    def run(self, *, company_id: int, supplied_domain: str = "") -> DiscoveryOutcome:
        return run_discovery(
            company_id=company_id,
            supplied_domain=supplied_domain,
            search_provider=self.search_provider,
            crawler=self.crawler,
        )


def _domain_seed(domain: str) -> str:
    value = domain.strip()
    if not value:
        raise ValueError("domain is empty")
    return canonicalize_url(value if "://" in value else f"https://{value}")


def _official_score(company_name: str, url: str, title: str = "") -> int:
    host = (urlsplit(url).hostname or "").lower()
    normalized_name = "".join(
        character for character in company_name.lower() if character.isalnum()
    )
    normalized_host = "".join(character for character in host if character.isalnum())
    score = 25
    if normalized_name and (
        normalized_name in normalized_host or normalized_host in normalized_name
    ):
        score += 55
    if company_name.lower() in title.lower():
        score += 15
    if host.startswith("www."):
        score += 5
    return min(score, 100)


def _blocked_official_host(host: str) -> str | None:
    return next(
        (
            blocked
            for blocked in BLOCKED_OFFICIAL_HOSTS
            if host == blocked or host.endswith(f".{blocked}")
        ),
        None,
    )


def _classify_official_candidate(
    *,
    company_name: str,
    url: str,
    title: str,
    manual: bool,
    description: str = "",
    search_score: float | None = None,
) -> OfficialSiteCandidate:
    try:
        discovered = canonicalize_url(url)
    except UnsafeUrlError as error:
        return OfficialSiteCandidate(url, url, title, 0, False, (), str(error))
    parsed = urlsplit(discovered)
    host = parsed.hostname or ""
    blocked = _blocked_official_host(host)
    if blocked:
        label = "LinkedIn" if blocked == "linkedin.com" else blocked
        return OfficialSiteCandidate(
            discovered,
            discovered,
            title,
            0,
            False,
            (f"Blocked official-site host: {blocked}",),
            f"{label} is a social network, job search, or aggregator, not an official site.",
        )
    first_label = host.split(".", 1)[0]
    if first_label in {"career", "careers", "job", "jobs"}:
        return OfficialSiteCandidate(
            discovered,
            discovered,
            title,
            0,
            False,
            ("Career or jobs subdomain",),
            "A career or jobs host is not the official corporate website.",
        )
    if manual and (parsed.path not in {"", "/"} or parsed.query):
        return OfficialSiteCandidate(
            discovered,
            discovered,
            title,
            20,
            False,
            ("Manual value contains a deep path or query",),
            "Manual official domain must identify the company's root website.",
        )
    root = f"{parsed.scheme}://{parsed.netloc}/"
    identity_metadata = f"{title} {description[:1000]}"
    score = 100 if manual else _official_score(company_name, root, identity_metadata)
    evidence = ["Manual official root domain" if manual else "Search result"]
    if search_score is not None:
        evidence.append(f"Search relevance score: {search_score:.3f}")
    if discovered != root:
        evidence.append("Deep search result classified through its origin domain")
    accepted = manual or score >= 70
    return OfficialSiteCandidate(
        discovered,
        root,
        title,
        score,
        accepted,
        tuple(evidence),
        "" if accepted else "Company identity is insufficiently supported by title and domain.",
    )


def _search_candidates(
    company_name: str, results: tuple[SearchResult, ...]
) -> tuple[OfficialSiteCandidate, ...]:
    candidates: dict[str, OfficialSiteCandidate] = {}
    for result in results:
        candidate = _classify_official_candidate(
            company_name=company_name,
            url=result.url,
            title=result.title,
            manual=False,
            description=result.description,
            search_score=result.score,
        )
        existing = candidates.get(candidate.canonical_url)
        if existing is None or candidate.confidence > existing.confidence:
            candidates[candidate.canonical_url] = candidate
    return tuple(candidates.values())


def _normalized_identity(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _confirm_official_identity(
    *, company_name: str, candidate: OfficialSiteCandidate, pages: tuple[CrawledPage, ...]
) -> tuple[bool, tuple[str, ...]]:
    candidate_origin = urlsplit(candidate.canonical_url).netloc
    page = next(
        (
            page
            for page in pages
            if urlsplit(page.url).netloc == candidate_origin and page.depth == 0
        ),
        None,
    )
    if page is None:
        return False, ()
    company_identity = _normalized_identity(company_name)
    host_identity = _normalized_identity(candidate_origin.removeprefix("www."))
    body_identity = _normalized_identity(page.body[:200_000])
    evidence: list[str] = []
    identity_confirmed = False
    if company_identity and company_identity in host_identity:
        evidence.append("Company identity matches the official domain")
        identity_confirmed = True
    if company_identity and company_identity in body_identity:
        evidence.append("Company identity appears in public homepage metadata or text")
        identity_confirmed = True
    if page.links:
        evidence.append("Official site links to a public careers candidate")
    return identity_confirmed, tuple(evidence)


def _persist_official_candidate(
    *, run: DiscoveryRun, candidate: OfficialSiteCandidate, decision: str
) -> None:
    job_source = classify_job_source(
        candidate.discovered_url,
        title=candidate.title,
        kind=DiscoveryCandidate.Kind.OFFICIAL_SITE,
    )
    explicitly_not_official = any(
        marker in candidate.reason
        for marker in ("not an official site", "not the official corporate website")
    )
    useful_job_source = job_source.eligibility in {
        DiscoveryCandidate.JobSourceEligibility.SUPPORTED_ATS,
        DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS,
        DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD,
        DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE,
        DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE,
    }
    DiscoveryCandidate.objects.create(
        run=run,
        kind=DiscoveryCandidate.Kind.OFFICIAL_SITE,
        discovered_url=candidate.discovered_url,
        canonical_url=candidate.canonical_url,
        confidence=candidate.confidence,
        job_source_confidence=job_source.confidence,
        evidence=list(candidate.evidence),
        supported=False,
        decision=(
            DiscoveryCandidate.Decision.NEEDS_REVIEW
            if decision == DiscoveryCandidate.Decision.REJECTED and useful_job_source
            else decision
        ),
        reason=candidate.reason,
        official_site_eligibility=(
            DiscoveryCandidate.OfficialSiteEligibility.OFFICIAL_SITE
            if candidate.accepted
            else (
                DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
                if explicitly_not_official
                else DiscoveryCandidate.OfficialSiteEligibility.UNCERTAIN
            )
        ),
        job_source_eligibility=job_source.eligibility,
    )


def _source_key(platform: str, url: str) -> tuple[str, str]:
    try:
        return source_identity(platform, url)
    except (KeyError, SourceError, UnsafeUrlError, ValueError, AttributeError):
        try:
            fallback_url = canonicalize_url(url).rstrip("/")
        except UnsafeUrlError:
            fallback_url = url.strip().rstrip("/")
        return platform.strip().casefold(), fallback_url


def _candidate_by_source_key(
    run: DiscoveryRun,
    *,
    platform: str,
    canonical_url: str,
) -> DiscoveryCandidate | None:
    wanted = _source_key(platform, canonical_url)
    return next(
        (
            candidate
            for candidate in run.candidates.filter(
                kind=DiscoveryCandidate.Kind.SOURCE,
                platform=platform,
            )
            if _source_key(candidate.platform, candidate.canonical_url) == wanted
        ),
        None,
    )


def _upsert_source_candidate(
    *,
    run: DiscoveryRun,
    detection: Detection,
    origin: str,
    evidence: Sequence[str] = (),
    company_source: CompanySource | None = None,
    decision: str | None = None,
    reason: str = "",
) -> DiscoveryCandidate:
    source_identity(detection.platform, detection.canonical_url)
    validate_source_configuration(
        source=detection.platform,
        source_jobs_url=detection.canonical_url,
    )
    existing_candidate = _candidate_by_source_key(
        run,
        platform=detection.platform,
        canonical_url=detection.canonical_url,
    )
    linked_source = company_source or _equivalent_company_source(
        company=run.company,
        platform=detection.platform,
        canonical_url=detection.canonical_url,
    )
    candidate_decision = decision or (
        DiscoveryCandidate.Decision.ALREADY_CONNECTED
        if linked_source is not None
        else DiscoveryCandidate.Decision.SELECTED
    )
    combined_evidence = list(
        dict.fromkeys([*evidence, *detection.evidence])
    )
    job_source = classify_job_source(
        detection.canonical_url,
        kind=DiscoveryCandidate.Kind.SOURCE,
        platform=detection.platform,
        supported=detection.supported,
    )
    if existing_candidate is not None:
        if decision is None and existing_candidate.decision in {
            DiscoveryCandidate.Decision.ALREADY_CONNECTED,
            DiscoveryCandidate.Decision.CONNECTED,
            DiscoveryCandidate.Decision.NEEDS_REVIEW,
            DiscoveryCandidate.Decision.REJECTED,
        }:
            candidate_decision = existing_candidate.decision
        existing_candidate.confidence = max(
            existing_candidate.confidence,
            detection.confidence,
        )
        existing_candidate.job_source_confidence = max(
            existing_candidate.job_source_confidence,
            job_source.confidence,
        )
        existing_candidate.evidence = list(
            dict.fromkeys([*existing_candidate.evidence, *combined_evidence])
        )
        existing_candidate.redirects = list(
            dict.fromkeys([*existing_candidate.redirects, *detection.redirects])
        )
        existing_candidate.supported = True
        existing_candidate.official_site_eligibility = (
            DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
        )
        existing_candidate.job_source_eligibility = job_source.eligibility
        existing_candidate.company_source = linked_source
        existing_candidate.decision = candidate_decision
        if linked_source is not None:
            existing_candidate.origin = DiscoveryCandidate.Origin.EXISTING_SOURCE
            existing_candidate.reason = "Existing source was preserved"
        elif origin == DiscoveryCandidate.Origin.ADAPTER_SEARCH:
            existing_candidate.origin = origin
            existing_candidate.reason = reason or "Validated by adapter-specific discovery"
        elif reason:
            existing_candidate.reason = reason
        existing_candidate.save(
            update_fields=(
                "confidence",
                "job_source_confidence",
                "evidence",
                "redirects",
                "supported",
                "official_site_eligibility",
                "job_source_eligibility",
                "company_source",
                "decision",
                "origin",
                "reason",
            )
        )
        return existing_candidate
    return DiscoveryCandidate.objects.create(
        run=run,
        kind=DiscoveryCandidate.Kind.SOURCE,
        discovered_url=detection.canonical_url,
        canonical_url=detection.canonical_url,
        platform=detection.platform,
        confidence=detection.confidence,
        job_source_confidence=job_source.confidence,
        evidence=combined_evidence,
        redirects=list(detection.redirects),
        supported=True,
        official_site_eligibility=(
            DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
        ),
        job_source_eligibility=job_source.eligibility,
        decision=candidate_decision,
        reason=(
            "Existing source was preserved"
            if linked_source is not None
            else reason or "Validated source candidate"
        ),
        origin=(
            DiscoveryCandidate.Origin.EXISTING_SOURCE
            if linked_source is not None
            else origin
        ),
        company_source=linked_source,
    )


def _equivalent_company_source(
    *,
    company: Company,
    platform: str,
    canonical_url: str,
) -> CompanySource | None:
    wanted = _source_key(platform, canonical_url)
    return next(
        (
            source
            for source in CompanySource.objects.filter(
                company=company,
                source=platform,
            )
            if source.source_jobs_url
            and _source_key(source.source, source.source_jobs_url) == wanted
        ),
        None,
    )


def _seed_source_inventory(run: DiscoveryRun) -> None:
    hints = {hint.platform: hint for hint in registered_discovery_hints()}
    for hint in hints.values():
        DiscoveryAdapterCheck.objects.create(
            run=run,
            platform=hint.platform,
            status=DiscoveryAdapterCheck.Status.NOT_CHECKED,
            reason="Adapter discovery check has not run yet",
        )
    for source in run.company.sources.order_by("pk"):
        hint = hints.get(source.source.strip().casefold())
        if hint is None or not source.source_jobs_url:
            continue
        try:
            canonical = hint.canonicalize(source.source_jobs_url)
            is_connected = (
                source.approval_status == CompanySource.ApprovalStatus.APPROVED
                and source.is_active
            )
            source_decision = (
                DiscoveryCandidate.Decision.ALREADY_CONNECTED
                if is_connected
                else (
                    DiscoveryCandidate.Decision.REJECTED
                    if source.approval_status
                    in {
                        CompanySource.ApprovalStatus.BLOCKED,
                        CompanySource.ApprovalStatus.REJECTED,
                    }
                    else DiscoveryCandidate.Decision.NEEDS_REVIEW
                )
            )
            detection = Detection(
                hint.platform,
                100,
                ("Existing CompanySource",),
                canonical,
                True,
            )
            candidate = _upsert_source_candidate(
                run=run,
                detection=detection,
                origin=DiscoveryCandidate.Origin.EXISTING_SOURCE,
                company_source=source,
                decision=source_decision,
                reason=(
                    "Existing approval and active state were preserved"
                    if is_connected
                    else "Existing source state requires review and was preserved"
                ),
            )
        except (SourceError, ValidationError, ValueError, AttributeError):
            DiscoveryAdapterCheck.objects.filter(
                run=run,
                platform=hint.platform,
            ).update(
                status=DiscoveryAdapterCheck.Status.VALIDATION_FAILED,
                reason="Existing source URL failed current adapter validation",
                company_source=source,
            )
            continue
        DiscoveryAdapterCheck.objects.filter(
            run=run,
            platform=hint.platform,
        ).update(
            status=(
                DiscoveryAdapterCheck.Status.ALREADY_CONNECTED
                if is_connected
                else DiscoveryAdapterCheck.Status.FOUND
            ),
            reason=(
                "Existing approved and active CompanySource"
                if is_connected
                else "Existing CompanySource state was preserved"
            ),
            candidate=candidate,
            company_source=source,
        )
    previous_candidates = (
        DiscoveryCandidate.objects.filter(
            run__company=run.company,
            kind=DiscoveryCandidate.Kind.SOURCE,
        )
        .exclude(run=run)
        .exclude(decision=DiscoveryCandidate.Decision.REJECTED)
        .order_by("-run__started_at", "-pk")
    )
    for previous in previous_candidates:
        hint = hints.get(previous.platform.strip().casefold())
        if hint is None:
            continue
        check = DiscoveryAdapterCheck.objects.get(run=run, platform=hint.platform)
        if check.status == DiscoveryAdapterCheck.Status.ALREADY_CONNECTED:
            continue
        try:
            canonical = hint.canonicalize(previous.canonical_url)
            detection = Detection(
                hint.platform,
                previous.confidence,
                tuple(previous.evidence),
                canonical,
                True,
                redirects=tuple(previous.redirects),
            )
            candidate = _upsert_source_candidate(
                run=run,
                detection=detection,
                origin=DiscoveryCandidate.Origin.PREVIOUS_DISCOVERY,
            )
        except (SourceError, ValidationError, ValueError, AttributeError):
            continue
        check.status = DiscoveryAdapterCheck.Status.FOUND
        check.reason = "Validated candidate retained from previous discovery"
        check.candidate = candidate
        check.save(update_fields=("status", "reason", "candidate"))


def _result_has_company_identity(
    company_names: Sequence[str], result: SearchResult
) -> bool:
    searchable = _normalized_identity(
        f"{result.title} {result.description[:1000]} "
        f"{urlsplit(result.url).hostname or ''}"
    )
    return any(
        identity and identity in searchable
        for company_name in company_names
        if (identity := _normalized_identity(company_name))
    )


def _company_search_names(
    company_name: str, results: Sequence[SearchResult]
) -> tuple[str, ...]:
    """Extract a bounded, explicitly stated former/trading company name."""
    names = [company_name]
    escaped = re.escape(company_name)
    patterns = (
        rf"{escaped}\s*\((?:the\s+)?trading name of\s+([^)]+)\)",
        rf"{escaped}\s*,?\s*formerly(?: known as)?\s+([^.;|]+)",
    )
    for result in results:
        text = f"{result.title}\n{result.description[:4000]}"
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            alias = " ".join(match.group(1).split()).strip(" -")[:120]
            if alias and alias.casefold() not in {name.casefold() for name in names}:
                names.append(alias)
            if len(names) >= 3:
                return tuple(names)
    return tuple(names)


def _embedded_source_urls(result: SearchResult, hint: SourceDiscoveryHints) -> tuple[str, ...]:
    """Return bounded source-shaped URLs explicitly present in result text."""
    text = f"{result.title}\n{result.description[:4000]}"
    urls: list[str] = []
    for raw in re.findall(r"https?://[^\s<>\"']+", text, flags=re.IGNORECASE)[:10]:
        candidate = raw.rstrip(".,;:!?)]}")
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold()
        if (
            hint.platform == "darwinbox"
            and host.endswith(".darwinbox.com")
            and parsed.path.rstrip("/") == "/ms/candidate"
        ):
            candidate = f"{parsed.scheme}://{host}/ms/candidate/careers"
        try:
            canonical = canonicalize_url(candidate)
        except UnsafeUrlError:
            continue
        if canonical not in urls:
            urls.append(canonical)
    return tuple(urls)


def _darwinbox_customer_aliases(
    company_name: str, results: Sequence[SearchResult]
) -> tuple[str, ...]:
    """Extract a bounded alias only from an explicit Darwinbox customer story."""
    company_words = company_name.casefold().split()
    if not company_words:
        return ()
    aliases: list[str] = []
    for result in results:
        host = (urlsplit(result.url).hostname or "").casefold()
        title = " ".join(result.title.split())
        if not (
            host.endswith(".darwinbox.com")
            and "customer success story" in title.casefold()
        ):
            continue
        alias = title.split("|", 1)[0].strip()[:120]
        alias_words = alias.casefold().split()
        if (
            len(alias_words) >= 2
            and alias_words[0] == company_words[0]
            and alias.casefold() != company_name.casefold()
            and alias.casefold() not in {item.casefold() for item in aliases}
        ):
            aliases.append(alias)
        if len(aliases) >= 2:
            break
    return tuple(aliases)


def _darwinbox_url_from_customer_alias(alias: str) -> str | None:
    """Build one conventional tenant route for a confirmed customer alias."""
    words = re.findall(r"[a-z0-9]+", alias.casefold())
    if len(words) < 2:
        return None
    tenant = f"{words[0]}{''.join(word[0] for word in words[1:])}"
    if len(tenant) < 3 or len(tenant) > 63:
        return None
    return f"https://{tenant}.darwinbox.com/ms/candidate/careers"


def _record_results_for_hint(
    *,
    run: DiscoveryRun,
    hint: SourceDiscoveryHints,
    results: Sequence[SearchResult],
    origin: str,
    query: str,
    company_names: Sequence[str] | None = None,
) -> tuple[DiscoveryCandidate, ...]:
    found: list[DiscoveryCandidate] = []
    accepted_names = company_names or (run.company.name,)
    for result in results:
        if not _result_has_company_identity(accepted_names, result):
            continue
        try:
            canonical = canonicalize_url(result.url)
        except UnsafeUrlError:
            continue
        synthetic_page = CrawledPage(
            canonical,
            canonical,
            f"{result.title}\n{result.description[:4000]}",
            _embedded_source_urls(result, hint),
            0,
        )
        for detection in hint.detector.detect(synthetic_page):
            try:
                candidate = _upsert_source_candidate(
                    run=run,
                    detection=detection,
                    origin=origin,
                    evidence=(
                        f"Discovery query: {query}",
                        "Source URL was present in company-related search evidence",
                    ),
                )
            except (SourceError, ValidationError, ValueError, AttributeError):
                continue
            if candidate not in found:
                found.append(candidate)
    return tuple(found)


def _adapter_sweep(
    *,
    run: DiscoveryRun,
    general_results: Sequence[SearchResult],
    search_once: Callable[[str], tuple[SearchResult, ...]],
    used_query_count: Callable[[], int],
    probe_source: Callable[[tuple[str, ...]], tuple[CrawledPage, ...]] | None = None,
) -> None:
    hints = registered_discovery_hints()
    company_names = _company_search_names(run.company.name, general_results)
    for hint in hints:
        check = DiscoveryAdapterCheck.objects.get(run=run, platform=hint.platform)
        if check.status in {
            DiscoveryAdapterCheck.Status.ALREADY_CONNECTED,
            DiscoveryAdapterCheck.Status.FOUND,
        }:
            continue
        general_found = _record_results_for_hint(
            run=run,
            hint=hint,
            results=general_results,
            origin=DiscoveryCandidate.Origin.CURRENT_DISCOVERY,
            query="general discovery search",
            company_names=company_names,
        )
        if general_found:
            check.status = DiscoveryAdapterCheck.Status.FOUND
            check.reason = "Found in general discovery results"
            check.candidate = general_found[0]
            check.save(update_fields=("status", "reason", "candidate"))
            continue
        if used_query_count() >= settings.SOURCE_DISCOVERY_SEARCH_MAX_QUERIES:
            check.reason = "Search-query limit reached before this adapter check"
            check.save(update_fields=("reason",))
            continue
        query = f'"{run.company.name}" {hint.search_hints[0]}'
        try:
            results = search_once(query)
        except (SearchProviderError, TimeoutError) as error:
            check.status = DiscoveryAdapterCheck.Status.SEARCH_FAILED
            check.reason = str(error)[:1000]
            check.save(update_fields=("status", "reason"))
            break
        found = _record_results_for_hint(
            run=run,
            hint=hint,
            results=results,
            origin=DiscoveryCandidate.Origin.ADAPTER_SEARCH,
            query=query,
            company_names=company_names,
        )
        customer_aliases: tuple[str, ...] = ()
        alias_search_error = ""
        if not found and hint.platform == "darwinbox":
            customer_aliases = _darwinbox_customer_aliases(run.company.name, results)
            retry_names = tuple(
                dict.fromkeys(
                    (
                        *company_names,
                        *customer_aliases,
                    )
                )
            )
            for alias in retry_names[1:2]:
                if used_query_count() >= settings.SOURCE_DISCOVERY_SEARCH_MAX_QUERIES:
                    break
                alias_query = f'"{alias}" "{hint.url_patterns[0]}"'
                try:
                    alias_results = search_once(alias_query)
                except (SearchProviderError, TimeoutError) as error:
                    alias_search_error = str(error)[:1000]
                    break
                found = _record_results_for_hint(
                    run=run,
                    hint=hint,
                    results=alias_results,
                    origin=DiscoveryCandidate.Origin.ADAPTER_SEARCH,
                    query=alias_query,
                    company_names=retry_names,
                )
                if found:
                    break
        if not found and probe_source is not None and customer_aliases:
            inferred_url = _darwinbox_url_from_customer_alias(customer_aliases[0])
            if inferred_url is not None:
                probe_pages = probe_source((inferred_url,))
                detections = tuple(
                    detection
                    for page in probe_pages
                    for detection in hint.detector.detect(page)
                    if detection.platform == hint.platform
                )
                if detections:
                    found = _record_crawled_detections(
                        run=run,
                        detections=detections,
                    )
                    for candidate in found:
                        candidate.evidence = [
                            *candidate.evidence,
                            "Darwinbox customer relationship matched company alias",
                            "Derived tenant route passed bounded public detector preflight",
                        ]
                        candidate.save(update_fields=("evidence",))
        check.status = (
            DiscoveryAdapterCheck.Status.FOUND
            if found
            else (
                DiscoveryAdapterCheck.Status.SEARCH_FAILED
                if alias_search_error
                else DiscoveryAdapterCheck.Status.NOT_FOUND
            )
        )
        check.reason = (
            "Found by adapter-specific search"
            if found
            else (
                alias_search_error
                or "Not found within bounded adapter-specific discovery"
            )
        )
        check.candidate = found[0] if found else None
        check.save(update_fields=("status", "reason", "candidate"))


def _record_crawled_detections(
    *, run: DiscoveryRun, detections: Sequence[Detection]
) -> tuple[DiscoveryCandidate, ...]:
    candidates: list[DiscoveryCandidate] = []
    for detection in detections:
        if detection.platform not in {hint.platform for hint in registered_discovery_hints()}:
            continue
        try:
            candidate = _upsert_source_candidate(
                run=run,
                detection=detection,
                origin=DiscoveryCandidate.Origin.CURRENT_DISCOVERY,
                decision=(
                    DiscoveryCandidate.Decision.ALREADY_CONNECTED
                    if _equivalent_company_source(
                        company=run.company,
                        platform=detection.platform,
                        canonical_url=detection.canonical_url,
                    )
                    else DiscoveryCandidate.Decision.NEEDS_REVIEW
                ),
                reason=detection.reason or "Automatic connection threshold was not met",
            )
        except (SourceError, ValidationError, UnsafeUrlError, ValueError, AttributeError):
            DiscoveryAdapterCheck.objects.filter(
                run=run,
                platform=detection.platform,
            ).exclude(status=DiscoveryAdapterCheck.Status.ALREADY_CONNECTED).update(
                status=DiscoveryAdapterCheck.Status.VALIDATION_FAILED,
                reason="Crawler candidate failed registered adapter validation",
            )
            continue
        check = DiscoveryAdapterCheck.objects.get(
            run=run,
            platform=detection.platform,
        )
        if check.status != DiscoveryAdapterCheck.Status.ALREADY_CONNECTED:
            check.status = DiscoveryAdapterCheck.Status.FOUND
            check.reason = "Found in bounded official-site crawl"
            check.candidate = candidate
            check.save(update_fields=("status", "reason", "candidate"))
        candidates.append(candidate)
    return tuple(candidates)


def _complete_crawl_checks(run: DiscoveryRun) -> None:
    run.adapter_checks.filter(status=DiscoveryAdapterCheck.Status.NOT_CHECKED).update(
        status=DiscoveryAdapterCheck.Status.NOT_FOUND,
        reason="No matching signal in the completed bounded official-site crawl",
    )


def _fallback_identity_evidence(
    *, company_name: str, official_url: str, result: SearchResult
) -> tuple[str, ...]:
    company_identity = _normalized_identity(company_name)
    result_host = (urlsplit(result.url).hostname or "").removeprefix("www.")
    official_host = (urlsplit(official_url).hostname or "").removeprefix("www.")
    searchable = _normalized_identity(
        f"{result_host} {result.title} {result.description[:1000]}"
    )
    evidence: list[str] = []
    if company_identity and company_identity in searchable:
        evidence.append("Company identity appears in the fallback result")
    if official_host and official_host in result.description.casefold():
        evidence.append("Fallback result references the official domain")
    return tuple(evidence)


def _persist_fallback_candidates(
    *,
    run: DiscoveryRun,
    company_name: str,
    official_url: str,
    results: tuple[SearchResult, ...],
    result_queries: dict[str, tuple[str, ...]],
) -> None:
    careers_url: str | None = None
    seen: set[str] = set()
    seen_sources: set[str] = set()
    for result in results:
        try:
            canonical = canonicalize_url(result.url)
        except UnsafeUrlError:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        host = urlsplit(canonical).hostname or ""
        query_evidence = [
            f"Fallback query: {query}" for query in result_queries.get(result.url, ())
        ]
        blocked = _blocked_official_host(host)
        synthetic_page = CrawledPage(
            canonical,
            canonical,
            f"{result.title}\n{result.description[:4000]}",
            (),
            0,
        )
        detections = detect_page(synthetic_page)
        identity_evidence = _fallback_identity_evidence(
            company_name=company_name,
            official_url=official_url,
            result=result,
        )
        if detections and identity_evidence:
            for detection in detections:
                if detection.canonical_url in seen_sources:
                    continue
                seen_sources.add(detection.canonical_url)
                try:
                    validate_source_configuration(
                        source=detection.platform,
                        source_jobs_url=detection.canonical_url,
                    )
                except (ValidationError, ValueError):
                    continue
                existing = _equivalent_company_source(
                    company=run.company,
                    platform=detection.platform,
                    canonical_url=detection.canonical_url,
                )
                decision = (
                    DiscoveryCandidate.Decision.ALREADY_CONNECTED
                    if existing is not None
                    else DiscoveryCandidate.Decision.NEEDS_REVIEW
                )
                _upsert_source_candidate(
                    run=run,
                    detection=detection,
                    origin=DiscoveryCandidate.Origin.CURRENT_DISCOVERY,
                    evidence=[
                        *query_evidence,
                        *identity_evidence,
                        "Candidate came from search fallback, not direct site verification",
                    ],
                    decision=decision,
                    reason=(
                        "Existing source was preserved"
                        if existing is not None
                        else (
                            "Direct official-site verification was unavailable; "
                            "manual review required"
                        )
                    ),
                    company_source=existing,
                )
                careers_url = careers_url or detection.canonical_url
            continue
        official_host = (urlsplit(official_url).hostname or "").removeprefix("www.")
        result_host = host.removeprefix("www.")
        path = urlsplit(canonical).path.casefold()
        looks_like_careers = any(
            token in f"{path} {result.title.casefold()}"
            for token in ("career", "jobs", "vacan", "karriere")
        )
        related_to_official = result_host == official_host or result_host.endswith(
            f".{official_host}"
        )
        accepted = bool(identity_evidence) and related_to_official and looks_like_careers
        blocked_label = "LinkedIn" if blocked == "linkedin.com" else blocked
        reason = (
            "Fallback careers result is related to the official domain but requires review"
            if accepted
            else (
                f"{blocked_label} is not an eligible fallback candidate"
                if blocked
                else "Fallback result did not establish company and careers relevance"
            )
        )
        job_source = classify_job_source(
            canonical,
            title=result.title,
            description=result.description,
            kind=DiscoveryCandidate.Kind.CAREERS,
        )
        useful_job_source = bool(identity_evidence) and job_source.eligibility in {
            DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS,
            DiscoveryCandidate.JobSourceEligibility.EXTERNAL_JOB_BOARD,
            DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE,
            DiscoveryCandidate.JobSourceEligibility.POSSIBLE_JOB_SOURCE,
        }
        DiscoveryCandidate.objects.create(
            run=run,
            kind=DiscoveryCandidate.Kind.CAREERS,
            discovered_url=canonical,
            canonical_url=canonical,
            confidence=65 if accepted else 0,
            job_source_confidence=job_source.confidence,
            evidence=[*query_evidence, *identity_evidence],
            supported=False,
            decision=(
                DiscoveryCandidate.Decision.NEEDS_REVIEW
                if accepted or useful_job_source
                else DiscoveryCandidate.Decision.REJECTED
            ),
            reason=reason,
            official_site_eligibility=(
                DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
                if blocked
                else DiscoveryCandidate.OfficialSiteEligibility.UNCERTAIN
            ),
            job_source_eligibility=job_source.eligibility,
        )
        if accepted:
            careers_url = careers_url or canonical
    run.careers_url = careers_url
    run.status = DiscoveryRun.Status.NEEDS_REVIEW
    run.summary = (
        "The official site could not be fetched; bounded search fallback candidates require review."
    )


def _connect(run: DiscoveryRun, detection: Detection, *, decision: str) -> DiscoveryCandidate:
    validate_source_configuration(
        source=detection.platform, source_jobs_url=detection.canonical_url
    )
    with transaction.atomic():
        source = _equivalent_company_source(
            company=run.company,
            platform=detection.platform,
            canonical_url=detection.canonical_url,
        )
        created = source is None
        if source is None:
            source = CompanySource.objects.create(
                company=run.company,
                source=detection.platform,
                source_jobs_url=detection.canonical_url,
                approval_status=CompanySource.ApprovalStatus.APPROVED,
                is_active=True,
            )
        if created:
            candidate_decision = decision
            candidate_reason = ""
        elif source.approval_status == CompanySource.ApprovalStatus.APPROVED:
            candidate_decision = DiscoveryCandidate.Decision.ALREADY_CONNECTED
            candidate_reason = "Existing approval and active state were preserved"
        elif source.approval_status in {
            CompanySource.ApprovalStatus.BLOCKED,
            CompanySource.ApprovalStatus.REJECTED,
        }:
            candidate_decision = DiscoveryCandidate.Decision.REJECTED
            candidate_reason = "Existing blocked or rejected approval was preserved"
        else:
            candidate_decision = DiscoveryCandidate.Decision.NEEDS_REVIEW
            candidate_reason = "Existing source still requires approval"
        candidate = _upsert_source_candidate(
            run=run,
            detection=detection,
            origin=DiscoveryCandidate.Origin.CURRENT_DISCOVERY,
            decision=candidate_decision,
            reason=candidate_reason,
            company_source=source,
        )
    return candidate


def run_discovery(
    *,
    company_id: int,
    supplied_domain: str = "",
    search_provider: SearchProvider | None = None,
    crawler: DiscoveryCrawler | None = None,
) -> DiscoveryOutcome:
    started = time.monotonic()

    def remaining_seconds() -> float:
        remaining = settings.SOURCE_DISCOVERY_TOTAL_TIMEOUT_SECONDS - (
            time.monotonic() - started
        )
        if remaining <= 0:
            raise TimeoutError("Source discovery exceeded its total time limit")
        return remaining

    company = Company.objects.get(pk=company_id)
    run = DiscoveryRun.objects.create(
        company=company, query=company.name, supplied_domain=supplied_domain.strip()
    )
    search_cache: dict[str, tuple[SearchResult, ...]] = {}
    active_provider = search_provider

    def search_once(query: str) -> tuple[SearchResult, ...]:
        nonlocal active_provider
        cached = search_cache.get(query)
        if cached is not None:
            return cached
        if len(search_cache) >= settings.SOURCE_DISCOVERY_SEARCH_MAX_QUERIES:
            raise SearchProviderError("Source discovery search-query budget was exhausted")
        remaining_seconds()
        active_provider = active_provider or configured_search_provider()
        results = active_provider.search(
            query,
            limit=settings.SOURCE_DISCOVERY_SEARCH_MAX_RESULTS,
        )
        search_cache[query] = results
        return results

    try:
        _seed_source_inventory(run)
        official_candidates: tuple[OfficialSiteCandidate, ...]
        general_results: tuple[SearchResult, ...] = ()
        if supplied_domain.strip():
            official_candidates = (
                _classify_official_candidate(
                    company_name=company.name,
                    url=_domain_seed(supplied_domain),
                    title="Manual company domain",
                    manual=True,
                ),
            )
        else:
            queries = (
                f'"{company.name}" official website',
                f'"{company.name}" official careers jobs',
            )
            general_results = tuple(
                {
                    result.url: result
                    for query in queries
                    for result in search_once(query)
                }.values()
            )
            if not general_results:
                raise TavilyEmptyResultsError("Tavily returned no search results")
            official_candidates = _search_candidates(company.name, general_results)
        rejected = tuple(item for item in official_candidates if not item.accepted)
        for candidate in rejected:
            _persist_official_candidate(
                run=run,
                candidate=candidate,
                decision=DiscoveryCandidate.Decision.REJECTED,
            )
        plausible = tuple(item for item in official_candidates if item.accepted)
        if not plausible:
            if not supplied_domain.strip():
                _adapter_sweep(
                    run=run,
                    general_results=general_results,
                    search_once=search_once,
                    used_query_count=lambda: len(search_cache),
                )
            run.status = (
                DiscoveryRun.Status.NEEDS_REVIEW
                if official_candidates
                else DiscoveryRun.Status.NOT_FOUND
            )
            run.summary = (
                "Search results were rejected as official sites and require review."
                if official_candidates
                else "No official website candidate was found."
            )
            return _finish(run)
        best = max(plausible, key=lambda item: item.confidence)
        best_score = best.confidence
        tied = tuple(item for item in plausible if item.confidence >= best_score - 5)
        run.official_website_url = best.canonical_url if len(tied) == 1 else None
        for candidate in tied:
            _persist_official_candidate(
                run=run,
                candidate=candidate,
                decision=(
                    DiscoveryCandidate.Decision.NEEDS_REVIEW
                    if len(tied) > 1
                    else DiscoveryCandidate.Decision.SELECTED
                ),
            )
        crawl = crawler or BoundedCrawler(
            transport=ScraplingTransport(),
            max_requests=settings.SOURCE_DISCOVERY_MAX_REQUESTS,
            max_depth=settings.SOURCE_DISCOVERY_MAX_DEPTH,
            max_redirects=settings.SOURCE_DISCOVERY_MAX_REDIRECTS,
            max_body_bytes=settings.SOURCE_DISCOVERY_MAX_BODY_BYTES,
            timeout_seconds=settings.SOURCE_DISCOVERY_TIMEOUT_SECONDS,
            total_timeout_seconds=remaining_seconds(),
        )
        remaining_seconds()
        pages = crawl.crawl(tuple(item.canonical_url for item in tied[:3]))
        remaining_seconds()
        if not pages and crawl.errors:
            safe_crawl_reason = "; ".join(crawl.errors)[:1000]
            official_record = DiscoveryCandidate.objects.filter(
                run=run,
                kind=DiscoveryCandidate.Kind.OFFICIAL_SITE,
                canonical_url=best.canonical_url,
            ).first()
            if official_record is not None:
                official_record.evidence = [
                    *official_record.evidence,
                    f"Direct crawler verification unavailable: {safe_crawl_reason}",
                ]
                official_record.reason = (
                    "Official candidate retained; direct public fetch could not be completed"
                )
                official_record.save(update_fields=("evidence", "reason"))
            fallback_queries = [f'"{company.name}" official careers jobs']
            site_query = f"site:{urlsplit(best.canonical_url).hostname} careers jobs"
            if site_query not in fallback_queries:
                fallback_queries.append(site_query)
            fallback_found: dict[str, SearchResult] = {}
            fallback_result_queries: dict[str, list[str]] = {}
            fallback_errors: list[SearchProviderError | TimeoutError] = []
            for fallback_query in fallback_queries:
                try:
                    for result in search_once(fallback_query):
                        fallback_found.setdefault(result.url, result)
                        fallback_result_queries.setdefault(result.url, []).append(
                            fallback_query
                        )
                except (SearchProviderError, TimeoutError) as fallback_error:
                    fallback_errors.append(fallback_error)
            if not fallback_found:
                fallback_error = (
                    fallback_errors[0]
                    if fallback_errors
                    else TavilyEmptyResultsError("Tavily returned no fallback results")
                )
                run.status = DiscoveryRun.Status.NEEDS_REVIEW
                run.summary = (
                    "The official site could not be fetched and search fallback was unavailable."
                )
                run.error_code = type(fallback_error).__name__
                run.error_message = str(fallback_error)[:2000]
                return _finish(run)
            _persist_fallback_candidates(
                run=run,
                company_name=company.name,
                official_url=best.canonical_url,
                results=tuple(fallback_found.values()),
                result_queries={
                    url: tuple(queries) for url, queries in fallback_result_queries.items()
                },
            )
            if not supplied_domain.strip():
                _adapter_sweep(
                    run=run,
                    general_results=tuple(fallback_found.values()),
                    search_once=search_once,
                    used_query_count=lambda: len(search_cache),
                    probe_source=crawl.crawl,
                )
            if fallback_errors:
                run.error_code = type(fallback_errors[0]).__name__
                run.error_message = str(fallback_errors[0])[:2000]
            return _finish(run)
        confirmed_candidates: list[OfficialSiteCandidate] = []
        for candidate in tied:
            confirmed, identity_evidence = _confirm_official_identity(
                company_name=company.name,
                candidate=candidate,
                pages=pages,
            )
            if confirmed or "Manual official root domain" in candidate.evidence:
                confirmed_candidates.append(candidate)
                DiscoveryCandidate.objects.filter(
                    run=run,
                    kind=DiscoveryCandidate.Kind.OFFICIAL_SITE,
                    canonical_url=candidate.canonical_url,
                ).update(
                    evidence=[*candidate.evidence, *identity_evidence],
                    reason="Official identity confirmed",
                )
        if not confirmed_candidates:
            run.official_website_url = None
            run.status = DiscoveryRun.Status.NEEDS_REVIEW
            run.summary = "Official-site identity could not be confirmed."
            return _finish(run)
        tied = tuple(confirmed_candidates)
        best_score = max(candidate.confidence for candidate in tied)
        best = max(tied, key=lambda candidate: candidate.confidence)
        run.official_website_url = best.canonical_url if len(tied) == 1 else None
        detections = tuple(detection for page in pages for detection in detect_page(page))
        _record_crawled_detections(run=run, detections=detections)
        if supplied_domain.strip():
            _complete_crawl_checks(run)
        else:
            _adapter_sweep(
                run=run,
                general_results=general_results,
                search_once=search_once,
                used_query_count=lambda: len(search_cache),
                probe_source=crawl.crawl,
            )
        careers = next(
            (
                link
                for page in pages
                for link in page.links
                if any(word in link.lower() for word in ("career", "jobs", "vacan", "karriere"))
            ),
            None,
        )
        run.careers_url = careers
        if not detections:
            if careers:
                career_urls = tuple(
                    dict.fromkeys(
                        link
                        for page in pages
                        for link in page.links
                        if any(
                            word in link.lower() for word in ("career", "jobs", "vacan", "karriere")
                        )
                    )
                )
                for career_url in career_urls:
                    DiscoveryCandidate.objects.create(
                        run=run,
                        kind=DiscoveryCandidate.Kind.CAREERS,
                        discovered_url=career_url,
                        canonical_url=career_url,
                        confidence=65,
                        job_source_confidence=72,
                        evidence=["Careers link found without a registered ATS signal"],
                        supported=False,
                        official_site_eligibility=(
                            DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
                        ),
                        job_source_eligibility=(
                            DiscoveryCandidate.JobSourceEligibility.COMPANY_JOBS_PAGE
                        ),
                        decision=DiscoveryCandidate.Decision.UNSUPPORTED,
                        reason=(
                            "No registered adapter matched; investigate the public contract "
                            "and implement a new source adapter."
                        ),
                    )
                if len(tied) > 1:
                    run.status = DiscoveryRun.Status.NEEDS_REVIEW
                    run.summary = "Multiple official identities or careers sites require review."
                else:
                    run.status = DiscoveryRun.Status.UNSUPPORTED
                    run.summary = (
                        "Careers page found, but its platform is unsupported or unknown."
                    )
            else:
                run.status = DiscoveryRun.Status.NOT_FOUND
                run.summary = "Official site found, but no careers source was confirmed."
            return _finish(run)
        detections = tuple(
            {(item.platform, item.canonical_url): item for item in detections}.values()
        )
        for detection in detections:
            if detection.supported:
                continue
            DiscoveryCandidate.objects.update_or_create(
                run=run,
                kind=DiscoveryCandidate.Kind.SOURCE,
                canonical_url=detection.canonical_url,
                defaults={
                    "discovered_url": detection.canonical_url,
                    "platform": detection.platform,
                    "confidence": detection.confidence,
                    "job_source_confidence": 85,
                    "evidence": list(detection.evidence),
                    "redirects": list(detection.redirects),
                    "supported": False,
                    "official_site_eligibility": (
                        DiscoveryCandidate.OfficialSiteEligibility.NOT_OFFICIAL_SITE
                    ),
                    "job_source_eligibility": (
                        DiscoveryCandidate.JobSourceEligibility.UNSUPPORTED_ATS
                    ),
                    "decision": DiscoveryCandidate.Decision.UNSUPPORTED,
                    "reason": detection.reason,
                    "origin": DiscoveryCandidate.Origin.CURRENT_DISCOVERY,
                },
            )
        if detections and run.careers_url is None:
            run.careers_url = detections[0].canonical_url
        strong = tuple(item for item in detections if item.supported and item.confidence >= 90)
        careers_confirmed = any(
            detection.canonical_url == page.url or detection.canonical_url in page.links
            for page in pages
            for detection in strong
        )
        if len(tied) == 1 and best_score >= 80 and len(strong) == 1 and careers_confirmed:
            candidate = _connect(run, strong[0], decision=DiscoveryCandidate.Decision.CONNECTED)
            run.status = (
                DiscoveryRun.Status.ALREADY_CONNECTED
                if candidate.decision == DiscoveryCandidate.Decision.ALREADY_CONNECTED
                else (
                    DiscoveryRun.Status.NEEDS_REVIEW
                    if candidate.decision
                    not in {
                        DiscoveryCandidate.Decision.CONNECTED,
                        DiscoveryCandidate.Decision.ALREADY_CONNECTED,
                    }
                    else DiscoveryRun.Status.CONNECTED
                )
            )
            run.careers_url = candidate.canonical_url
            run.summary = (
                "An existing non-approved source requires manual review."
                if run.status == DiscoveryRun.Status.NEEDS_REVIEW
                else f"Validated and connected {candidate.platform}."
            )
            return _finish(run)
        run.status = (
            DiscoveryRun.Status.NEEDS_REVIEW
            if any(item.supported for item in detections)
            else DiscoveryRun.Status.UNSUPPORTED
        )
        run.summary = (
            "Candidate sources require manual review."
            if run.status == DiscoveryRun.Status.NEEDS_REVIEW
            else "Only unsupported source candidates were found."
        )
        return _finish(run)
    except Exception as error:
        logger.exception(
            "Source discovery failed for company_id=%s with %s",
            company_id,
            type(error).__name__,
        )
        run.status = DiscoveryRun.Status.FAILED
        run.error_code = type(error).__name__
        run.error_message = (
            str(error)[:2000]
            if isinstance(
                error,
                SearchConfigurationError
                | SearchProviderError
                | CrawlError
                | TimeoutError,
            )
            else "Source discovery failed unexpectedly."
        )
        run.summary = "Discovery failed cleanly; no source was connected."
        return _finish(run)


def _finish(run: DiscoveryRun) -> DiscoveryOutcome:
    checks = tuple(run.adapter_checks.all())
    incomplete = any(
        check.status
        in {
            DiscoveryAdapterCheck.Status.NOT_CHECKED,
            DiscoveryAdapterCheck.Status.SEARCH_FAILED,
        }
        for check in checks
    )
    source_candidates = tuple(
        run.candidates.filter(kind=DiscoveryCandidate.Kind.SOURCE)
    )
    if (
        checks
        and not incomplete
        and source_candidates
        and all(candidate.company_source_id is not None for candidate in source_candidates)
        and all(
            candidate.decision == DiscoveryCandidate.Decision.ALREADY_CONNECTED
            for candidate in source_candidates
        )
        and run.status != DiscoveryRun.Status.FAILED
    ):
        run.status = DiscoveryRun.Status.ALREADY_CONNECTED
        run.summary = "All discovered sources are already connected."
    elif incomplete and run.status != DiscoveryRun.Status.FAILED:
        run.status = DiscoveryRun.Status.NEEDS_REVIEW
        prefix = "Partial discovery — some registered platforms were not checked."
        run.summary = f"{prefix} {run.summary}".strip()
    run.finished_at = timezone.now()
    run.save(
        update_fields=(
            "official_website_url",
            "careers_url",
            "status",
            "summary",
            "error_code",
            "error_message",
            "finished_at",
        )
    )
    return DiscoveryOutcome(run.pk, run.status)


def _company_candidate(*, candidate_id: int, company_id: int) -> DiscoveryCandidate:
    return DiscoveryCandidate.objects.select_related("run__company").get(
        pk=candidate_id, run__company_id=company_id
    )


def confirm_candidate(*, candidate_id: int, company_id: int) -> DiscoveryCandidate:
    candidate = _company_candidate(candidate_id=candidate_id, company_id=company_id)
    if not candidate.supported or candidate.decision not in {
        DiscoveryCandidate.Decision.NEEDS_REVIEW,
        DiscoveryCandidate.Decision.SELECTED,
    }:
        raise ValueError("Candidate is not eligible for confirmation")
    validate_source_configuration(
        source=candidate.platform,
        source_jobs_url=candidate.canonical_url,
    )
    existing = None
    for source in CompanySource.objects.filter(
        company=candidate.run.company,
        source=candidate.platform,
    ):
        try:
            source_url = canonicalize_url(source.source_jobs_url or "").rstrip("/")
        except UnsafeUrlError:
            continue
        if source_url == candidate.canonical_url.rstrip("/"):
            existing = source
            break
    source = existing or CompanySource.objects.create(
        company=candidate.run.company,
        source=candidate.platform,
        source_jobs_url=candidate.canonical_url,
        approval_status=CompanySource.ApprovalStatus.APPROVED,
        is_active=True,
    )
    if source.approval_status in {
        CompanySource.ApprovalStatus.BLOCKED,
        CompanySource.ApprovalStatus.REJECTED,
    }:
        raise ValueError("A blocked or rejected source cannot be confirmed through discovery")
    candidate.company_source = source
    candidate.decision = (
        DiscoveryCandidate.Decision.ALREADY_CONNECTED
        if existing is not None
        else DiscoveryCandidate.Decision.CONNECTED
    )
    candidate.reason = (
        "Existing source and its approval/active state were preserved"
        if existing is not None
        else "Manually confirmed by a user"
    )
    candidate.save(update_fields=("company_source", "decision", "reason"))
    candidate.run.status = (
        DiscoveryRun.Status.ALREADY_CONNECTED
        if existing is not None
        else DiscoveryRun.Status.CONNECTED
    )
    candidate.run.summary = (
        f"Existing {candidate.platform} source was linked without changing its state."
        if existing is not None
        else f"{candidate.platform} was connected after manual confirmation."
    )
    candidate.run.save(update_fields=("status", "summary"))
    return candidate


def revalidate_candidate(*, candidate_id: int, company_id: int) -> DiscoveryCandidate:
    """Re-check one saved URL against the current registry without web search."""
    candidate = _company_candidate(candidate_id=candidate_id, company_id=company_id)
    if candidate.decision == DiscoveryCandidate.Decision.REJECTED:
        raise ValueError("Rejected candidates cannot be revalidated")
    if not candidate.platform or candidate.platform not in set(registered_source_keys()):
        candidate.supported = False
        candidate.reason = "No registered adapter is available for the saved candidate"
        candidate.save(update_fields=("supported", "reason"))
        return candidate
    try:
        validate_source_configuration(
            source=candidate.platform,
            source_jobs_url=candidate.canonical_url,
        )
    except ValidationError as error:
        candidate.supported = True
        candidate.decision = DiscoveryCandidate.Decision.NEEDS_REVIEW
        candidate.reason = f"Saved URL validation failed: {error}"
        candidate.save(update_fields=("supported", "decision", "reason"))
        return candidate
    candidate.supported = True
    candidate.decision = DiscoveryCandidate.Decision.SELECTED
    candidate.reason = "Saved URL passed the current registered adapter validation"
    candidate.save(update_fields=("supported", "decision", "reason"))
    return candidate


def set_candidate_ignored(
    *, candidate_id: int, company_id: int, ignored: bool
) -> DiscoveryCandidate:
    """Persist a reversible user presentation choice without deleting evidence."""
    candidate = _company_candidate(candidate_id=candidate_id, company_id=company_id)
    if candidate.company_source_id is not None:
        raise ValueError("Connected candidates cannot be ignored")
    candidate.is_ignored = ignored
    candidate.save(update_fields=("is_ignored",))
    return candidate
