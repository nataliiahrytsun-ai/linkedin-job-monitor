"""Extensible technical-signal detectors for supported and unknown ATS pages."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from discovery.network import CrawledPage, canonicalize_url
from scraping.sources.base import SourceError
from scraping.sources.darwinbox import darwinbox_source_from_url
from scraping.sources.dreamjobs import dreamjobs_source_from_url
from scraping.sources.jazzhr import jazzhr_source_from_url
from scraping.sources.lever import lever_site_from_url
from scraping.sources.registry import registered_source_keys, user_selectable_source_keys


@dataclass(frozen=True, slots=True)
class Detection:
    platform: str
    confidence: int
    evidence: tuple[str, ...]
    canonical_url: str
    supported: bool
    reason: str = ""
    redirects: tuple[str, ...] = ()


class PlatformDetector(Protocol):
    """One independently extensible platform detector."""

    def detect(self, page: CrawledPage) -> tuple[Detection, ...]: ...


type SourceCanonicalizer = Callable[[str], str]
type TenantIdentity = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class SourceDiscoveryHints:
    """Adapter-owned safe clues used by bounded source discovery."""

    platform: str
    host_patterns: tuple[str, ...]
    url_patterns: tuple[str, ...]
    search_hints: tuple[str, ...]
    technical_signals: tuple[str, ...]
    detector: PlatformDetector
    canonicalize: SourceCanonicalizer
    tenant_identity: TenantIdentity


def _validated(platform: str, url: str) -> str | None:
    try:
        return discovery_hints_for(platform).canonicalize(url)
    except (KeyError, SourceError, AttributeError):
        return None


type SignalProbe = Callable[[str, str, str], tuple[int, tuple[str, ...]] | None]


@dataclass(frozen=True, slots=True)
class RegisteredPlatformDetector:
    platform: str
    probe: SignalProbe

    def detect(self, page: CrawledPage) -> tuple[Detection, ...]:
        detections: list[Detection] = []
        body = page.body.lower()
        supported = self.platform in set(registered_source_keys())
        for url in (page.url, *page.links):
            host = (urlsplit(url).hostname or "").lower()
            signal = self.probe(host, url.lower(), body)
            if signal is None:
                continue
            canonical = _validated(self.platform, url)
            if canonical is None:
                continue
            confidence, evidence = signal
            detections.append(
                Detection(
                    self.platform,
                    confidence,
                    evidence,
                    canonical,
                    supported,
                    redirects=page.redirects,
                )
            )
        return tuple(detections)


def _lever_signal(host: str, _url: str, _body: str) -> tuple[int, tuple[str, ...]] | None:
    return (98, ("jobs.lever.co host",)) if host == "jobs.lever.co" else None


def _jazzhr_signal(host: str, _url: str, body: str) -> tuple[int, tuple[str, ...]] | None:
    if host.endswith("applytojob.com") or "jazzhr" in body or "resumator" in body:
        return 96 if host.endswith("applytojob.com") else 86, ("JazzHR technical signal",)
    return None


def _dreamjobs_signal(host: str, _url: str, body: str) -> tuple[int, tuple[str, ...]] | None:
    vendor_hosts = {"dream.jobs", "www.dream.jobs", "business.dream.jobs", "api.dream.jobs"}
    if host in vendor_hosts:
        return None
    if (
        host.endswith("dream.jobs")
        or "api.dream.jobs" in body
        or ("__next_data__" in body and "dream" in body)
    ):
        if host.endswith("dream.jobs"):
            return 97, ("DreamJobs hosted domain",)
        if "api.dream.jobs" in body and "__next_data__" in body:
            return 95, ("DreamJobs API and Next.js metadata",)
        return 88, ("DreamJobs technical signal",)
    return None


def _darwinbox_signal(host: str, url: str, body: str) -> tuple[int, tuple[str, ...]] | None:
    if "darwinbox" in host or "darwinbox" in body or "/ms/candidate" in url:
        return 94, ("Darwinbox technical signal",)
    return None


def _lever_canonicalize(url: str) -> str:
    return f"https://jobs.lever.co/{lever_site_from_url(url)}"


def _jazzhr_canonicalize(url: str) -> str:
    return jazzhr_source_from_url(url).listing_url


def _dreamjobs_canonicalize(url: str) -> str:
    return dreamjobs_source_from_url(url).listing_url


def _darwinbox_canonicalize(url: str) -> str:
    location = darwinbox_source_from_url(url)
    if location.company_id == "main":
        return f"{location.scheme}://{location.host}/ms/candidate/careers"
    return (
        f"{location.scheme}://{location.host}/ms/candidatev2/"
        f"{location.company_id}/careers"
    )


def _lever_tenant(url: str) -> str:
    return lever_site_from_url(url).casefold()


def _jazzhr_tenant(url: str) -> str:
    return jazzhr_source_from_url(url).host.casefold()


def _dreamjobs_tenant(url: str) -> str:
    return dreamjobs_source_from_url(url).host.casefold()


def _darwinbox_tenant(url: str) -> str:
    location = darwinbox_source_from_url(url)
    return f"{location.host.casefold()}:{location.company_id.casefold()}"


_DISCOVERY_HINTS: tuple[SourceDiscoveryHints, ...] = (
    SourceDiscoveryHints(
        platform="darwinbox",
        host_patterns=("*.darwinbox.com",),
        url_patterns=("/ms/candidate/careers", "/ms/candidatev2/<company>/careers"),
        search_hints=("Darwinbox careers",),
        technical_signals=("darwinbox host", "/ms/candidate route"),
        detector=RegisteredPlatformDetector("darwinbox", _darwinbox_signal),
        canonicalize=_darwinbox_canonicalize,
        tenant_identity=_darwinbox_tenant,
    ),
    SourceDiscoveryHints(
        platform="dreamjobs",
        host_patterns=("custom HTTPS host", "*.dream.jobs"),
        url_patterns=("/jobs",),
        search_hints=("DreamJobs careers",),
        technical_signals=("DreamJobs API", "Next.js metadata"),
        detector=RegisteredPlatformDetector("dreamjobs", _dreamjobs_signal),
        canonicalize=_dreamjobs_canonicalize,
        tenant_identity=_dreamjobs_tenant,
    ),
    SourceDiscoveryHints(
        platform="jazzhr",
        host_patterns=("*.applytojob.com",),
        url_patterns=("/apply", "/apply/jobs"),
        search_hints=("JazzHR jobs",),
        technical_signals=("JazzHR", "Resumator", "applytojob host"),
        detector=RegisteredPlatformDetector("jazzhr", _jazzhr_signal),
        canonicalize=_jazzhr_canonicalize,
        tenant_identity=_jazzhr_tenant,
    ),
    SourceDiscoveryHints(
        platform="lever",
        host_patterns=("jobs.lever.co",),
        url_patterns=("/<tenant>",),
        search_hints=("Lever jobs",),
        technical_signals=("jobs.lever.co host",),
        detector=RegisteredPlatformDetector("lever", _lever_signal),
        canonicalize=_lever_canonicalize,
        tenant_identity=_lever_tenant,
    ),
)
_DISCOVERY_HINTS_BY_PLATFORM = {hint.platform: hint for hint in _DISCOVERY_HINTS}
SUPPORTED_DETECTORS = tuple(hint.detector for hint in _DISCOVERY_HINTS)


def registered_discovery_hints() -> tuple[SourceDiscoveryHints, ...]:
    """Return hints for every production adapter and fail if registry metadata lags."""
    expected = set(user_selectable_source_keys())
    actual = set(_DISCOVERY_HINTS_BY_PLATFORM)
    if expected != actual:
        missing = ", ".join(sorted(expected - actual)) or "none"
        extra = ", ".join(sorted(actual - expected)) or "none"
        raise RuntimeError(f"Discovery hints mismatch; missing={missing}; extra={extra}")
    return tuple(_DISCOVERY_HINTS_BY_PLATFORM[key] for key in sorted(expected))


def discovery_hints_for(platform: str) -> SourceDiscoveryHints:
    return _DISCOVERY_HINTS_BY_PLATFORM[platform.strip().casefold()]


def source_identity(platform: str, url: str) -> tuple[str, str]:
    hint = discovery_hints_for(platform)
    canonical = hint.canonicalize(url)
    return hint.platform, hint.tenant_identity(canonical)


def detect_page(page: CrawledPage) -> tuple[Detection, ...]:
    body = page.body.lower()
    detections = [
        detection for detector in SUPPORTED_DETECTORS for detection in detector.detect(page)
    ]
    unsupported_platform = next(
        (
            platform
            for platform, token in (
                ("greenhouse", "greenhouse.io"),
                ("workable", "workable.com"),
                ("smartrecruiters", "smartrecruiters.com"),
                ("teamtailor", "teamtailor"),
            )
            if token in body or token in page.url.lower()
        ),
        None,
    )
    if not detections and unsupported_platform:
        detections.append(
            Detection(
                unsupported_platform,
                75,
                ("Unregistered ATS asset or host",),
                canonicalize_url(page.url),
                False,
                (
                    "No registered adapter matches this platform; investigate its public "
                    "contract and implement a new source adapter."
                ),
                page.redirects,
            )
        )
    unique: dict[tuple[str, str], Detection] = {}
    for detection in detections:
        key = (detection.platform, detection.canonical_url)
        if key not in unique or unique[key].confidence < detection.confidence:
            unique[key] = detection
    return tuple(unique.values())
