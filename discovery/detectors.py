"""Conservative, source-neutral public ATS classification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from discovery.network import CrawledPage, UnsafeUrlError, canonicalize_url
from scraping.sources.registry import registered_source_keys, user_selectable_source_keys


@dataclass(frozen=True, slots=True)
class Detection:
    """A platform classification, independent of adapter implementation."""

    platform: str
    confidence: int
    evidence: tuple[str, ...]
    canonical_url: str
    supported: bool
    reason: str = ""
    redirects: tuple[str, ...] = ()


class PlatformDetector(Protocol):
    def detect(self, page: CrawledPage) -> tuple[Detection, ...]: ...


type SourceCanonicalizer = Callable[[str], str]
type TenantIdentity = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class SourceDiscoveryHints:
    """Adapter-owned hints used only for bounded registered-adapter search."""

    platform: str
    host_patterns: tuple[str, ...]
    url_patterns: tuple[str, ...]
    search_hints: tuple[str, ...]
    technical_signals: tuple[str, ...]
    detector: PlatformDetector
    canonicalize: SourceCanonicalizer
    tenant_identity: TenantIdentity


def _url_parts(url: str) -> tuple[str, str, tuple[str, ...]]:
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    return (
        canonical,
        (parsed.hostname or "").casefold(),
        tuple(segment for segment in parsed.path.split("/") if segment),
    )


def _root(url: str, *parts: str) -> str:
    canonical, host, _segments = _url_parts(url)
    parsed = urlsplit(canonical)
    path = "/" + "/".join(parts) if parts else "/"
    return urlunsplit((parsed.scheme, host, path, "", ""))


def _first_path_root(url: str) -> str:
    canonical, _host, segments = _url_parts(url)
    return _root(canonical, segments[0]) if segments else _root(canonical)


def _host_root(url: str) -> str:
    return _root(url)


def _lever_canonicalize(url: str) -> str:
    canonical, host, segments = _url_parts(url)
    if host != "jobs.lever.co" or not segments:
        raise ValueError("Lever URL must use jobs.lever.co with a tenant path")
    return _root(canonical, segments[0])


def _jazzhr_canonicalize(url: str) -> str:
    canonical, host, _segments = _url_parts(url)
    if not host.endswith(".applytojob.com"):
        raise ValueError("JazzHR URL must use an applytojob tenant host")
    return _root(canonical, "apply")


def _darwinbox_canonicalize(url: str) -> str:
    canonical, host, segments = _url_parts(url)
    if (
        not host.endswith("darwinbox.com")
        or len(segments) < 2
        or segments[:2]
        != (
            "ms",
            "candidate",
        )
    ):
        if (
            not host.endswith("darwinbox.com")
            or len(segments) < 4
            or segments[0] != "ms"
            or segments[1] != "candidatev2"
            or segments[3] != "careers"
        ):
            raise ValueError("Darwinbox URL must use a public candidate careers route")
        return _root(canonical, "ms", "candidatev2", segments[2], "careers")
    return _root(canonical, "ms", "candidate", "careers")


def _dreamjobs_canonicalize(url: str) -> str:
    canonical, _host, segments = _url_parts(url)
    if not segments or segments[0] != "jobs":
        raise ValueError("DreamJobs URL must use a /jobs listing")
    return _root(canonical, "jobs")


def _zoho_recruit_canonicalize(url: str) -> str:
    canonical, _host, segments = _url_parts(url)
    if len(segments) < 2 or segments[0].casefold() != "jobs":
        raise ValueError("Zoho Recruit URL must use a /jobs/<page> listing")
    return _root(canonical, "jobs", segments[1])


def _host_tenant(url: str) -> str:
    return urlsplit(url).hostname.casefold()  # type: ignore[union-attr]


def _path_tenant(url: str) -> str:
    _canonical, host, segments = _url_parts(url)
    return f"{host}:{segments[0].casefold() if segments else ''}"


def _darwinbox_tenant(url: str) -> str:
    _canonical, host, segments = _url_parts(url)
    company_id = segments[2] if len(segments) > 3 and segments[1] == "candidatev2" else "main"
    return f"{host}:{company_id.casefold()}"


@dataclass(frozen=True, slots=True)
class CatalogPlatformDetector:
    platform: str
    matcher: Callable[[str, str, tuple[str, ...], str], tuple[int, tuple[str, ...]] | None]
    canonicalize: SourceCanonicalizer
    tenant_identity: TenantIdentity

    def detect(self, page: CrawledPage) -> tuple[Detection, ...]:
        detections: list[Detection] = []
        for url in (page.url, *page.links):
            try:
                canonical, host, segments = _url_parts(url)
            except UnsafeUrlError:
                continue
            signal = self.matcher(host, canonical, segments, page.body)
            if signal is None:
                continue
            try:
                listing_url = self.canonicalize(canonical)
            except (UnsafeUrlError, ValueError):
                continue
            confidence, evidence = signal
            detections.append(
                Detection(
                    platform=self.platform,
                    confidence=confidence,
                    evidence=evidence,
                    canonical_url=listing_url,
                    supported=self.platform in set(registered_source_keys()),
                    redirects=page.redirects,
                )
            )
        return tuple(detections)


def _host_exact(
    expected: str, label: str
) -> Callable[[str, str, tuple[str, ...], str], tuple[int, tuple[str, ...]] | None]:
    def match(
        host: str, _url: str, segments: tuple[str, ...], _body: str
    ) -> tuple[int, tuple[str, ...]] | None:
        return (98, (label,)) if host == expected and bool(segments) else None

    return match


def _host_suffix(
    expected: str, label: str
) -> Callable[[str, str, tuple[str, ...], str], tuple[int, tuple[str, ...]] | None]:
    def match(
        host: str, _url: str, _segments: tuple[str, ...], _body: str
    ) -> tuple[int, tuple[str, ...]] | None:
        return (96, (label,)) if host.endswith(expected) and host != expected else None

    return match


def _darwinbox_match(
    host: str, _url: str, segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    if not host.endswith("darwinbox.com"):
        return None
    if segments[:3] == ("ms", "candidate", "careers"):
        return 98, ("Darwinbox public candidate careers route",)
    if (
        len(segments) >= 4
        and segments[0] == "ms"
        and segments[1] == "candidatev2"
        and segments[3] == "careers"
    ):
        return 98, ("Darwinbox public candidate-v2 careers route",)
    return None


def _dreamjobs_match(
    host: str, _url: str, segments: tuple[str, ...], body: str
) -> tuple[int, tuple[str, ...]] | None:
    if host in {"dream.jobs", "www.dream.jobs", "business.dream.jobs", "api.dream.jobs"}:
        return None
    if host.endswith(".dream.jobs") and segments[:1] == ("jobs",):
        return 98, ("DreamJobs hosted /jobs listing",)
    if (
        segments[:1] == ("jobs",)
        and "api.dream.jobs" in body.casefold()
        and "__next_data__" in body.casefold()
    ):
        return 95, ("DreamJobs API and Next.js listing metadata",)
    return None


def _zoho_recruit_match(
    host: str, _url: str, segments: tuple[str, ...], body: str
) -> tuple[int, tuple[str, ...]] | None:
    if len(segments) < 2 or segments[0].casefold() != "jobs":
        return None
    if host.endswith(".zohorecruit.com"):
        return 98, ("Zoho Recruit public career-site host",)

    lowered = body.casefold()
    signals = (
        "static.zohocdn.com/recruit/" in lowered,
        'id="jobs"' in lowered,
        'id="meta"' in lowered,
        'id="career-website-main"' in lowered,
    )
    if all(signals):
        return 96, ("Zoho Recruit embedded jobs and career-site assets",)
    return None


def _workday_match(
    host: str, _url: str, _segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    return (
        (96, ("Workday public myworkdayjobs host",))
        if host.endswith(".myworkdayjobs.com")
        else None
    )


def _greenhouse_match(
    host: str, _url: str, segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and segments:
        return 96, ("Greenhouse public job-board host",)
    return None


def _smartrecruiters_match(
    host: str, _url: str, segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    if host == "jobs.smartrecruiters.com" and segments:
        return 96, ("SmartRecruiters public jobs host",)
    return None


def _workable_match(
    host: str, _url: str, segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    if host == "apply.workable.com" and segments:
        return 96, ("Workable public apply host",)
    return None


def _ashby_match(
    host: str, _url: str, segments: tuple[str, ...], _body: str
) -> tuple[int, tuple[str, ...]] | None:
    if host == "jobs.ashbyhq.com" and segments:
        return 96, ("Ashby public jobs host",)
    return None


_CATALOG: tuple[CatalogPlatformDetector, ...] = (
    CatalogPlatformDetector(
        "lever",
        _host_exact("jobs.lever.co", "Lever public jobs host"),
        _lever_canonicalize,
        _path_tenant,
    ),
    CatalogPlatformDetector(
        "darwinbox",
        _darwinbox_match,
        _darwinbox_canonicalize,
        _darwinbox_tenant,
    ),
    CatalogPlatformDetector(
        "jazzhr",
        _host_suffix(".applytojob.com", "JazzHR technical signal"),
        _jazzhr_canonicalize,
        _host_tenant,
    ),
    CatalogPlatformDetector("dreamjobs", _dreamjobs_match, _dreamjobs_canonicalize, _host_tenant),
    CatalogPlatformDetector(
        "zoho_recruit",
        _zoho_recruit_match,
        _zoho_recruit_canonicalize,
        _host_tenant,
    ),
    CatalogPlatformDetector("workday", _workday_match, _host_root, _host_tenant),
    CatalogPlatformDetector("greenhouse", _greenhouse_match, _first_path_root, _path_tenant),
    CatalogPlatformDetector(
        "personio",
        _host_suffix(".jobs.personio.de", "Personio public jobs host"),
        _host_root,
        _host_tenant,
    ),
    CatalogPlatformDetector(
        "personio",
        _host_suffix(".jobs.personio.com", "Personio public jobs host"),
        _host_root,
        _host_tenant,
    ),
    CatalogPlatformDetector(
        "smartrecruiters",
        _smartrecruiters_match,
        _first_path_root,
        _path_tenant,
    ),
    CatalogPlatformDetector("workable", _workable_match, _first_path_root, _path_tenant),
    CatalogPlatformDetector("ashby", _ashby_match, _first_path_root, _path_tenant),
    CatalogPlatformDetector(
        "teamtailor",
        _host_suffix(".teamtailor.com", "Teamtailor public careers host"),
        _host_root,
        _host_tenant,
    ),
)


_DISCOVERY_HINTS: tuple[SourceDiscoveryHints, ...] = (
    SourceDiscoveryHints(
        "darwinbox",
        ("*.darwinbox.com",),
        ("/ms/candidate/careers", "/ms/candidatev2/<company>/careers"),
        ("Darwinbox careers",),
        ("public candidate careers route",),
        CatalogPlatformDetector(
            "darwinbox",
            _darwinbox_match,
            _darwinbox_canonicalize,
            _darwinbox_tenant,
        ),
        _darwinbox_canonicalize,
        _darwinbox_tenant,
    ),
    SourceDiscoveryHints(
        "dreamjobs",
        ("custom HTTPS host", "*.dream.jobs"),
        ("/jobs",),
        ("DreamJobs careers",),
        ("DreamJobs API", "Next.js metadata"),
        CatalogPlatformDetector(
            "dreamjobs",
            _dreamjobs_match,
            _dreamjobs_canonicalize,
            _host_tenant,
        ),
        _dreamjobs_canonicalize,
        _host_tenant,
    ),
    SourceDiscoveryHints(
        "jazzhr",
        ("*.applytojob.com",),
        ("/apply", "/apply/jobs"),
        ("JazzHR jobs",),
        ("applytojob host",),
        CatalogPlatformDetector(
            "jazzhr",
            _host_suffix(".applytojob.com", "JazzHR technical signal"),
            _jazzhr_canonicalize,
            _host_tenant,
        ),
        _jazzhr_canonicalize,
        _host_tenant,
    ),
    SourceDiscoveryHints(
        "lever",
        ("jobs.lever.co",),
        ("/<tenant>",),
        ("Lever jobs",),
        ("jobs.lever.co host",),
        CatalogPlatformDetector(
            "lever",
            _host_exact("jobs.lever.co", "Lever public jobs host"),
            _lever_canonicalize,
            _path_tenant,
        ),
        _lever_canonicalize,
        _path_tenant,
    ),
    SourceDiscoveryHints(
        "zoho_recruit",
        ("*.zohorecruit.com", "custom HTTPS host"),
        ("/jobs/<page>",),
        ("Zoho Recruit careers",),
        ("Zoho Recruit assets", "embedded jobs metadata"),
        CatalogPlatformDetector(
            "zoho_recruit",
            _zoho_recruit_match,
            _zoho_recruit_canonicalize,
            _host_tenant,
        ),
        _zoho_recruit_canonicalize,
        _host_tenant,
    ),
)
_DISCOVERY_HINTS_BY_PLATFORM = {hint.platform: hint for hint in _DISCOVERY_HINTS}


def registered_discovery_hints() -> tuple[SourceDiscoveryHints, ...]:
    """Return adapter-search hints for each user-selectable production adapter."""
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


def catalog_source_identity(platform: str, url: str) -> tuple[str, str]:
    platform_key = platform.strip().casefold()
    for detector in _CATALOG:
        if detector.platform != platform_key:
            continue
        canonical = detector.canonicalize(url)
        return detector.platform, detector.tenant_identity(canonical)
    raise KeyError(platform)


def detect_page(page: CrawledPage) -> tuple[Detection, ...]:
    """Classify direct public vendor URLs; page text is never a sole signal."""
    unique: dict[tuple[str, str], Detection] = {}
    for detector in _CATALOG:
        for detection in detector.detect(page):
            key = (detection.platform, detection.canonical_url)
            if key not in unique or unique[key].confidence < detection.confidence:
                unique[key] = detection
    return tuple(unique.values())
