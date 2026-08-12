"""Offline-testable LinkedIn listing contract; production crawling is disabled."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from scrapling.parser import Selector

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

LINKEDIN_HOST = "www.linkedin.com"
DEFAULT_CONTINUATION_START = 25
DEFAULT_CONTINUATION_STEP = 25
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_REQUESTS = 100
DEFAULT_OVERLAP_LIMIT = 2

type LinkedInPageGet = Callable[[str], str | bytes]

_LISTING_PATH = re.compile(
    r"^/jobs/(?P<slug>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-jobs-worldwide)/?$"
)
_JOB_ID_PATTERNS = (
    re.compile(r"urn:li:jobPosting:(\d+)"),
    re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)"),
    re.compile(r"(?:^|[?&])(?:currentJobId|jobId)=(\d+)(?:&|$)"),
    re.compile(r"^\d+$"),
)
_BLOCKED_PATH_MARKERS = ("/login", "/uas/login", "/authwall", "/checkpoint")
_BLOCK_TEXT_MARKERS = (
    "access denied",
    "not authorized to access",
    "before accessing linkedin",
    "sign in to linkedin",
    "verify you are human",
    "security verification",
)
_CAPTCHA_SELECTOR = (
    "form[id*='captcha'], form[class*='captcha'], iframe[src*='captcha'], "
    "iframe[src*='recaptcha'], .g-recaptcha, .h-captcha, .captcha-challenge"
)


@dataclass(frozen=True, slots=True)
class LinkedInSourceIdentity:
    slug: str
    company_ids: tuple[str, ...]
    canonical_url: str


@dataclass(frozen=True, slots=True)
class LinkedInListingRecord:
    source_job_id: str
    title: str
    company: str | None
    location: str | None
    published_at: str | None
    source_job_url: str


def _linkedin_host(hostname: str | None) -> bool:
    normalized = (hostname or "").casefold()
    return normalized == "linkedin.com" or normalized.endswith(".linkedin.com")


def linkedin_source_from_url(source_jobs_url: str | None) -> LinkedInSourceIdentity:
    """Validate and canonicalize one public company-jobs listing identity."""
    if source_jobs_url is None or not source_jobs_url.strip():
        raise SourceError("LinkedIn company jobs URL is missing")
    try:
        parsed = urlsplit(source_jobs_url.strip())
        port = parsed.port
    except ValueError as error:
        raise SourceError("LinkedIn company jobs URL is invalid") from error
    if (
        parsed.scheme.casefold() != "https"
        or not _linkedin_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise SourceError("LinkedIn company jobs URL must use uncredentialed HTTPS")
    match = _LISTING_PATH.fullmatch(parsed.path)
    if match is None:
        raise SourceError("LinkedIn company jobs URL path is unsupported")

    query_pairs = parse_qsl(parsed.query)
    allowed_query_keys = {
        "f_C",
        "trk",
        "currentJobId",
        "position",
        "pageNum",
    }
    if any(key not in allowed_query_keys for key, _value in query_pairs):
        raise SourceError("LinkedIn company jobs URL contains unsupported query fields")
    values = [value for key, value in query_pairs if key == "f_C"]
    raw_ids = [part.strip() for value in values for part in value.split(",")]
    if not raw_ids or any(not item.isdigit() for item in raw_ids):
        raise SourceError("LinkedIn company jobs URL requires numeric f_C IDs")
    company_ids = tuple(sorted({str(int(item)) for item in raw_ids}, key=int))
    canonical_url = urlunsplit(
        (
            "https",
            LINKEDIN_HOST,
            f"/jobs/{match.group('slug')}",
            urlencode({"f_C": ",".join(company_ids)}),
            "",
        )
    )
    return LinkedInSourceIdentity(
        slug=match.group("slug"),
        company_ids=company_ids,
        canonical_url=canonical_url,
    )


def linkedin_continuation_url(identity: LinkedInSourceIdentity, start: int) -> str:
    """Build the confirmed public guest continuation shape."""
    if type(start) is not int or start < 0:
        raise ValueError("start must be a non-negative int, excluding bool")
    return urlunsplit(
        (
            "https",
            LINKEDIN_HOST,
            f"/jobs-guest/jobs/api/seeMoreJobPostings/{identity.slug}",
            urlencode({"f_C": ",".join(identity.company_ids), "start": start}),
            "",
        )
    )


def linkedin_job_id(*values: str | None) -> str | None:
    """Extract the confirmed numeric LinkedIn Job Posting identity shapes."""
    for value in values:
        if not value:
            continue
        for pattern in _JOB_ID_PATTERNS:
            match = pattern.search(value)
            if match is not None:
                return match.group(1) if match.lastindex else match.group(0)
    return None


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(unescape(str(value)).split())
    return cleaned or None


def _first_text(node: Selector, selector: str) -> str | None:
    selected = node.css(selector)
    return _clean(selected[0].text) if selected else None


def _canonical_job_url(value: str | None, *, base_url: str) -> str | None:
    if not value:
        return None
    parsed = urlsplit(urljoin(base_url, unescape(value)))
    if parsed.scheme.casefold() != "https" or not _linkedin_host(parsed.hostname):
        return None
    job_id = linkedin_job_id(urlunsplit(parsed))
    if job_id is None:
        return None
    return f"https://{LINKEDIN_HOST}/jobs/view/{job_id}"


def _published_at(node: Selector, *, requests_made: int) -> str | None:
    values = node.css("time.job-search-card__listdate::attr(datetime)")
    if not values:
        return None
    value = _clean(values.get())
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise SourceError(
            "LinkedIn listing card contains an invalid publication date",
            requests_made=requests_made,
        ) from error


def _record_from_card(
    node: Selector, *, base_url: str, requests_made: int
) -> LinkedInListingRecord:
    links = node.css("a.base-card__full-link[href*='/jobs/view/']")
    link = links[0] if links else None
    href = _clean(link.attrib.get("href")) if link is not None else None
    job_url = _canonical_job_url(href, base_url=base_url)
    job_id = linkedin_job_id(
        node.attrib.get("data-entity-urn"),
        node.attrib.get("data-job-id"),
        node.attrib.get("data-occludable-job-id"),
        href,
    )
    title = _first_text(node, "h3.base-search-card__title")
    if title is None and link is not None:
        title = _first_text(link, "span.sr-only") or _clean(link.attrib.get("aria-label"))
    if job_id is None or job_url is None or title is None:
        raise SourceError(
            "LinkedIn listing contains a malformed job card",
            requests_made=requests_made,
        )
    return LinkedInListingRecord(
        source_job_id=job_id,
        title=title,
        company=_first_text(node, "h4.base-search-card__subtitle"),
        location=_first_text(node, ".job-search-card__location"),
        published_at=_published_at(node, requests_made=requests_made),
        source_job_url=job_url,
    )


def _blocked_response(
    page: Selector, *, page_url: str, has_job_cards: bool
) -> bool:
    path = urlsplit(page_url).path.casefold()
    if any(marker in path for marker in _BLOCKED_PATH_MARKERS):
        return True
    if page.css(_CAPTCHA_SELECTOR):
        return True
    visible = " ".join(
        page.xpath(
            "//body//text()[not(ancestor::script) and not(ancestor::style) "
            "and not(ancestor::template) and not(ancestor::noscript) "
            "and not(ancestor::*[@hidden or @aria-hidden='true' or "
            "contains(translate(@style, ' ', ''), 'display:none') or "
            "contains(translate(@style, ' ', ''), 'visibility:hidden')])]"
        ).getall()
    ).casefold()
    return not has_job_cards and any(marker in visible for marker in _BLOCK_TEXT_MARKERS)


def _looks_like_listing_page(page: Selector) -> bool:
    return bool(
        page.css(
            ".results-context-header, .jobs-search__results-list, "
            ".infinite-scroller, .base-search-card, .job-search-card"
        )
    )


def parse_linkedin_listing(
    html: str | bytes,
    *,
    page_url: str,
    requests_made: int = 0,
) -> tuple[LinkedInListingRecord, ...]:
    """Parse one listing fragment, failing closed on malformed or blocked pages."""
    try:
        text = html.decode("utf-8") if isinstance(html, bytes) else html
    except UnicodeDecodeError as error:
        raise SourceError(
            "LinkedIn listing response is not valid UTF-8",
            requests_made=requests_made,
        ) from error
    page = Selector(content=text, url=page_url)
    cards = page.css("div.job-search-card")
    if _blocked_response(page, page_url=page_url, has_job_cards=bool(cards)):
        raise SourceError(
            "LinkedIn listing returned a challenge, login, or access-denied page",
            requests_made=requests_made,
        )
    if not cards:
        loose_links = page.css("a[href*='/jobs/view/']")
        if loose_links:
            raise SourceError(
                "LinkedIn listing contains unstructured job links",
                requests_made=requests_made,
            )
        if _looks_like_listing_page(page):
            return ()
        raise SourceError(
            "LinkedIn listing response is malformed or not a listing page",
            requests_made=requests_made,
        )
    return tuple(
        _record_from_card(card, base_url=page_url, requests_made=requests_made)
        for card in cards
    )


def _source_record(record: LinkedInListingRecord) -> SourceRecord:
    return {
        "source": "linkedin",
        "source_job_id": record.source_job_id,
        "source_job_url": record.source_job_url,
        "title": record.title,
        "location": record.location,
        "country": None,
        "city": None,
        "workplace_type": None,
        "employment_type": None,
        "published_at": record.published_at,
        "description": None,
        "job_function": None,
        "seniority_level": None,
        "industry": None,
    }


class LinkedInSourceAdapter:
    """Collect a complete fixture-backed snapshot; no production transport exists."""

    def __init__(
        self,
        *,
        page_get: LinkedInPageGet | None = None,
        continuation_start: int = DEFAULT_CONTINUATION_START,
        continuation_step: int = DEFAULT_CONTINUATION_STEP,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        overlap_limit: int = DEFAULT_OVERLAP_LIMIT,
    ) -> None:
        for name, value in (
            ("continuation_start", continuation_start),
            ("continuation_step", continuation_step),
            ("max_pages", max_pages),
            ("max_requests", max_requests),
            ("overlap_limit", overlap_limit),
        ):
            minimum = 0 if name == "continuation_start" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an int greater than or equal to {minimum}")
        self._page_get = page_get
        self._continuation_start = continuation_start
        self._continuation_step = continuation_step
        self._max_pages = max_pages
        self._max_requests = max_requests
        self._overlap_limit = overlap_limit

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        identity = linkedin_source_from_url(company.source_jobs_url)
        if self._page_get is None:
            raise SourceError(
                "LinkedIn production network execution is disabled",
                requests_made=0,
            )

        records: list[SourceRecord] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        seen_content: set[str] = set()
        overlap_batches = 0
        requests_made = 0
        page_url = identity.canonical_url

        while True:
            if requests_made >= self._max_requests or requests_made >= self._max_pages:
                raise SourceError(
                    "LinkedIn pagination limit reached before an empty batch",
                    requests_made=requests_made,
                )
            if page_url in seen_urls:
                raise SourceError(
                    "LinkedIn pagination repeated a URL before an empty batch",
                    requests_made=requests_made,
                )
            seen_urls.add(page_url)
            requests_made += 1
            try:
                body = self._page_get(page_url)
            except Exception as error:
                raise SourceError(
                    "LinkedIn offline page transport failed",
                    requests_made=requests_made,
                ) from error
            if not isinstance(body, str | bytes):
                raise SourceError(
                    "LinkedIn offline page transport returned an invalid body",
                    requests_made=requests_made,
                )
            encoded = body.encode("utf-8") if isinstance(body, str) else body
            digest = hashlib.sha256(encoded).hexdigest()
            if digest in seen_content:
                raise SourceError(
                    "LinkedIn pagination repeated content before an empty batch",
                    requests_made=requests_made,
                )
            seen_content.add(digest)

            listing_records = parse_linkedin_listing(
                body,
                page_url=page_url,
                requests_made=requests_made,
            )
            if not listing_records:
                return SourceBatch(records=tuple(records), requests_made=requests_made)

            new_records = 0
            for listing_record in listing_records:
                source_job_id = listing_record.source_job_id
                if source_job_id in seen_ids:
                    continue
                seen_ids.add(source_job_id)
                records.append(_source_record(listing_record))
                new_records += 1
            if new_records:
                overlap_batches = 0
            else:
                overlap_batches += 1
                if overlap_batches > self._overlap_limit:
                    raise SourceError(
                        "LinkedIn pagination exhausted overlap allowance before an empty batch",
                        requests_made=requests_made,
                    )

            offset = self._continuation_start + (
                requests_made - 1
            ) * self._continuation_step
            page_url = linkedin_continuation_url(identity, offset)
