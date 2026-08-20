"""Bounded detection for one user-supplied public jobs URL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from lxml import etree  # type: ignore[import-untyped]

from companies.forms import source_label, validate_source_configuration
from discovery.classification import classify_job_source, is_generic_fallback_eligible
from discovery.detectors import Detection, detect_page, source_identity
from discovery.models import DiscoveryCandidate
from discovery.network import (
    BoundedCrawler,
    CrawledPage,
    CrawlError,
    ScraplingTransport,
    UnsafeUrlError,
    canonicalize_url,
    validate_public_url,
)
from scraping.sources.generic import extract_generic_candidates
from scraping.sources.registry import user_selectable_source_keys


class SourceAutoDetectionError(ValueError):
    """A supplied URL could not safely become a supported CompanySource."""


class SingleUrlCrawler(Protocol):
    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]: ...


type PublicUrlValidator = Callable[[str], tuple[str, frozenset[str]]]


@dataclass(frozen=True, slots=True)
class DetectedCompanySource:
    """A canonical source configuration proven by existing detection rules."""

    source: str
    source_jobs_url: str

    @property
    def label(self) -> str:
        return source_label(self.source)


def _supported_detection(detections: tuple[Detection, ...]) -> Detection | None:
    supported_keys = set(user_selectable_source_keys())
    supported = {
        (item.platform, item.canonical_url): item
        for item in detections
        if item.supported
        and item.platform in supported_keys
        and item.confidence >= 90
    }
    if len(supported) > 1:
        raise SourceAutoDetectionError(
            "More than one supported platform matched this URL. The source was not added."
        )
    return next(iter(supported.values()), None)


def _validated_detection(detection: Detection) -> DetectedCompanySource:
    try:
        validate_source_configuration(
            source=detection.platform,
            source_jobs_url=detection.canonical_url,
        )
    except ValidationError as error:
        raise SourceAutoDetectionError(str(error)) from error
    return DetectedCompanySource(detection.platform, detection.canonical_url)


def _detection_page(page: CrawledPage) -> CrawledPage:
    """Keep detection scoped to the supplied/final URL, not arbitrary page links."""
    return CrawledPage(
        requested_url=page.requested_url,
        url=page.url,
        body=page.body,
        links=(),
        depth=page.depth,
        redirects=page.redirects,
    )


def _generic_candidate(
    *, page: CrawledPage, detections: tuple[Detection, ...]
) -> DiscoveryCandidate:
    strongest = max(detections, key=lambda item: item.confidence, default=None)
    candidate_url = strongest.canonical_url if strongest is not None else page.url
    platform = strongest.platform if strongest is not None else ""
    supported = strongest.supported if strongest is not None else False
    path = urlsplit(candidate_url).path.casefold()
    kind = (
        DiscoveryCandidate.Kind.CAREERS
        if any(term in path for term in ("career", "job", "vacan", "opening"))
        else DiscoveryCandidate.Kind.SOURCE
    )
    classification = classify_job_source(
        candidate_url,
        description=page.body[:4000],
        kind=str(kind),
        platform=platform,
        supported=supported,
    )
    evidence = list(strongest.evidence) if strongest is not None else []
    evidence.extend(("User supplied this public URL", classification.reason))
    return DiscoveryCandidate(
        kind=kind,
        discovered_url=candidate_url,
        canonical_url=candidate_url,
        platform=platform,
        confidence=strongest.confidence if strongest is not None else 72,
        job_source_confidence=classification.confidence,
        evidence=evidence,
        redirects=list(page.redirects),
        supported=supported,
        decision=DiscoveryCandidate.Decision.NEEDS_REVIEW,
        reason=classification.reason,
        job_source_eligibility=classification.eligibility,
    )


def _default_crawler() -> BoundedCrawler:
    redirects = min(settings.SOURCE_DISCOVERY_MAX_REDIRECTS, 4)
    return BoundedCrawler(
        transport=ScraplingTransport(),
        max_requests=redirects + 1,
        max_depth=0,
        max_redirects=redirects,
        max_body_bytes=settings.SOURCE_DISCOVERY_MAX_BODY_BYTES,
        timeout_seconds=settings.SOURCE_DISCOVERY_TIMEOUT_SECONDS,
    )


def detect_company_source_url(
    url: str,
    *,
    crawler: SingleUrlCrawler | None = None,
    public_url_validator: PublicUrlValidator = validate_public_url,
) -> DetectedCompanySource:
    """Detect one supported ATS or eligible Generic source, otherwise fail closed."""
    try:
        canonical, _addresses = public_url_validator(url)
    except (TypeError, ValueError, UnsafeUrlError) as error:
        raise SourceAutoDetectionError(
            "Enter a public HTTP(S) jobs URL. Private, local, or unsafe URLs are not allowed."
        ) from error

    direct_page = CrawledPage(canonical, canonical, "", (), 0)
    direct = _supported_detection(detect_page(direct_page))
    if direct is not None:
        return _validated_detection(direct)

    try:
        pages = (crawler or _default_crawler()).crawl((canonical,))
    except (CrawlError, UnsafeUrlError, TimeoutError, OSError, RuntimeError, ImportError) as error:
        raise SourceAutoDetectionError(
            "The URL could not be fetched safely, so its job platform was not detected."
        ) from error
    if not pages:
        raise SourceAutoDetectionError(
            "The URL could not be fetched safely, so its job platform was not detected."
        )
    page = _detection_page(pages[0])
    detections = detect_page(page)
    supported = _supported_detection(detections)
    if supported is not None:
        return _validated_detection(supported)

    candidate = _generic_candidate(page=page, detections=detections)
    try:
        generic_jobs = extract_generic_candidates(page.body, base_url=page.url)
    except (etree.ParserError, etree.XMLSyntaxError, TypeError, ValueError):
        generic_jobs = ()
    if is_generic_fallback_eligible(candidate) and generic_jobs:
        return DetectedCompanySource("generic", canonicalize_url(candidate.canonical_url))

    raise SourceAutoDetectionError(
        "No supported job platform or eligible public careers page was detected. "
        "The source was not added."
    )


def source_configuration_identity(source: str, url: str) -> tuple[str, str]:
    """Return the same canonical identity used by source discovery deduplication."""
    if source == "generic":
        return source, canonicalize_url(url).rstrip("/")
    return source_identity(source, url)
