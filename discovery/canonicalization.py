"""Conservative URL identities for logical Discovery sources."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from discovery.detectors import catalog_source_identity, source_identity
from discovery.network import canonicalize_url

_TRACKING_QUERY_KEYS = {
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}
_LISTING_MARKERS = ("career", "job", "opening", "position", "vacan")
_POSITIVE_PAGE = re.compile(r"[1-9][0-9]*", flags=re.ASCII)
_PAGE_PATH_SUFFIX = re.compile(r"(.*)/page/([1-9][0-9]*)", flags=re.IGNORECASE | re.ASCII)


def _is_tracking_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized.startswith("utm_") or normalized in _TRACKING_QUERY_KEYS


def _is_listing_context(*, host: str, path: str) -> bool:
    searchable = f"{host} {path}".casefold()
    return any(marker in searchable for marker in _LISTING_MARKERS)


def canonicalize_source_candidate_url(url: str) -> str:
    """Remove only source-irrelevant tracking and explicit listing pagination."""
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/") or "/"
    page_suffix = _PAGE_PATH_SUFFIX.fullmatch(path)
    if page_suffix is not None:
        base_path = page_suffix.group(1) or "/"
        if _is_listing_context(host=host, path=base_path):
            path = base_path.rstrip("/") or "/"

    listing_context = _is_listing_context(host=host, path=path)
    path_listing_context = _is_listing_context(host="", path=path)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_tracking_key(key):
            continue
        if (
            (
                (key.casefold() == "page" and listing_context)
                or (key.casefold() == "p" and path_listing_context)
            )
            and _POSITIVE_PAGE.fullmatch(value)
        ):
            continue
        query.append((key, value))

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path.rstrip("/") or "/",
            urlencode(query, doseq=True),
            "",
        )
    )


def logical_source_identity(platform: str, url: str) -> tuple[str, str]:
    """Return an ATS-aware identity, with a conservative URL fallback."""
    platform_key = platform.strip().casefold()
    canonical = canonicalize_source_candidate_url(url)
    if platform_key and platform_key != "generic":
        for identity in (source_identity, catalog_source_identity):
            try:
                return identity(platform_key, canonical)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    return platform_key, canonical


def source_candidate_rank(
    *, platform: str, url: str, supported: bool
) -> tuple[int, int, int, int, str]:
    """Return a deterministic best-first rank for equivalent candidates."""
    canonical = canonicalize_source_candidate_url(url)
    parsed = urlsplit(canonical)
    segments = tuple(segment.casefold() for segment in parsed.path.split("/") if segment)
    direct_listing = bool(segments) and segments[-1] in {
        "career",
        "careers",
        "job",
        "jobs",
        "opening",
        "openings",
        "positions",
        "vacancies",
    }
    return (
        0 if supported and platform.strip() else 1,
        0 if canonical == canonicalize_url(url).rstrip("/") else 1,
        0 if direct_listing else 1,
        len(segments),
        canonical,
    )
